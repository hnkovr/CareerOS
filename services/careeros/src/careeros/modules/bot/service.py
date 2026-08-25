"""The bot's only cross-module entry point (invariant 7).

Handlers never touch another module's ORM models; everything they need arrives
through here. In P0 this is a thin command dispatcher — the capture and triage
flows (GH #3-#5, #7) attach to it without changing the router.
"""

from __future__ import annotations

from typing import Any

import structlog

from careeros.core.auth import SINGLE_USER_ID
from careeros.core.config import Settings
from careeros.modules.bot.capture import looks_like_job_description
from careeros.modules.bot.client import TelegramClient
from careeros.modules.bot.formatting import escape_md, score_card
from careeros.modules.bot.keyboards import triage_keyboard
from careeros.modules.opportunities.enums import Source

log = structlog.get_logger(__name__)

HELP = (
    "*CareerOS*\n"
    "Forward me a job description and I will parse, dedupe and score it.\n\n"
    "/status — environment, database, webhook state\n"
    "/whoami — your chat id and whether you are the owner\n"
    "/help — this message"
)


class BotService:
    def __init__(
        self,
        settings: Settings,
        client: TelegramClient,
        sessionmaker: Any | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        # The handler runs in a background task, after the response has been sent
        # and the request's session closed, so capture must open its own.
        self._sessionmaker = sessionmaker

    async def handle(self, payload: dict) -> None:
        """Process one already-gated update. Never raises into the request path."""
        message = payload.get("message") or payload.get("edited_message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        text = (message.get("text") or "").strip()
        if chat_id is None or not text:
            return
        try:
            await self._dispatch(chat_id, text)
        except Exception:
            log.exception("bot.handler_failed", command=text.split()[0] if text else None)

    async def _dispatch(self, chat_id: int, text: str) -> None:
        command = text.split()[0].lower() if text.startswith("/") else None
        if command in ("/start", "/help"):
            await self._client.send_message(chat_id, HELP, parse_mode="MarkdownV2")
        elif command == "/whoami":
            owner = self._settings.tg_owner_chat_id
            await self._client.send_message(
                chat_id,
                escape_md(f"chat id: {chat_id}\nowner: {'yes' if chat_id == owner else 'no'}"),
                parse_mode="MarkdownV2",
            )
        elif command == "/status":
            await self._client.send_message(
                chat_id, escape_md(self._status()), parse_mode="MarkdownV2"
            )
        elif looks_like_job_description(text):
            await self._capture(chat_id, text)
        else:
            await self._client.send_message(
                chat_id,
                escape_md("Forward me a job description, or try /help."),
                parse_mode="MarkdownV2",
            )

    async def _capture(self, chat_id: int, text: str) -> None:
        """Ingest a forwarded job description and reply with its triage card."""
        if self._sessionmaker is None:
            await self._client.send_message(
                chat_id,
                escape_md("Capture is unavailable: no database configured."),
                parse_mode="MarkdownV2",
            )
            return

        from careeros.modules.ai.deps import build_ai_service
        from careeros.modules.opportunities.schemas import IngestRequest
        from careeros.modules.opportunities.service import OpportunityService
        from careeros.modules.vault.deps import get_vault

        async with self._sessionmaker() as session:
            service = OpportunityService(
                self._settings,
                get_vault(self._settings),
                build_ai_service(self._settings, session=session, user_id=SINGLE_USER_ID),
                session=session,
                user_id=SINGLE_USER_ID,
            )
            url = text.strip() if text.strip().startswith("http") else None
            detail = await service.ingest(
                IngestRequest(text=None if url else text, url=url, source=Source.manual)
            )
            await session.commit()

        await self._client.send_message(
            chat_id,
            score_card(detail),
            parse_mode="MarkdownV2",
            reply_markup=triage_keyboard(str(detail.id)),
        )

    def _status(self) -> str:
        s = self._settings
        return (
            f"env: {s.env}\n"
            f"bot: {'enabled' if s.tg_enabled else 'disabled'}\n"
            f"public url: {s.tg_public_url or '(unset — never contacts Telegram)'}\n"
            f"webhook path: {s.tg_webhook_path}\n"
            f"task runner: {s.task_runner}\n"
            "note: webhook ownership shown here is this process's startup claim; "
            "Telegram is the authority (just bot-webhook-info)"
        )

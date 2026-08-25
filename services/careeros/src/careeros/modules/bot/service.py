"""The bot's only cross-module entry point (invariant 7).

Handlers never touch another module's ORM models; everything they need arrives
through here. In P0 this is a thin command dispatcher — the capture and triage
flows (GH #3-#5, #7) attach to it without changing the router.
"""

from __future__ import annotations

import structlog

from careeros.core.config import Settings
from careeros.modules.bot.client import TelegramClient
from careeros.modules.bot.formatting import escape_md

log = structlog.get_logger(__name__)

HELP = (
    "*CareerOS*\n"
    "Forward me a job description and I will parse, dedupe and score it.\n\n"
    "/status — environment, database, webhook state\n"
    "/whoami — your chat id and whether you are the owner\n"
    "/help — this message"
)


class BotService:
    def __init__(self, settings: Settings, client: TelegramClient) -> None:
        self._settings = settings
        self._client = client

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
        else:
            # Capture (GH #3) lands here: non-command text is a job description.
            await self._client.send_message(
                chat_id,
                escape_md("Capture is not implemented yet (GH #3). Try /help."),
                parse_mode="MarkdownV2",
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

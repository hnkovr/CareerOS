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
from careeros.modules.bot.links import (
    build_profile_rows,
    build_search_rows,
    render_rows,
    resolve_open_target,
)
from careeros.modules.bot.platforms import (
    UnknownPlatforms,
    format_platform_set,
    known_platforms,
    parse_platform_set,
)
from careeros.modules.bot.preferences import PreferenceStore
from careeros.modules.opportunities.enums import Source

log = structlog.get_logger(__name__)

HELP = (
    "*CareerOS*\n"
    "Forward me a job description and I will parse, dedupe and score it.\n\n"
    "/services — show the platforms commands act on; /services set hh,upwork to change\n"
    "/open <service> — link to a platform\n"
    "/profiles [services] — your profile URL on each platform\n"
    '/urls "<query>" [services] — a job search URL per platform\n'
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
        elif command == "/services":
            await self._services(chat_id, text)
        elif command == "/open":
            await self._open(chat_id, text)
        elif command == "/profiles":
            await self._profiles(chat_id, text)
        elif command == "/urls":
            await self._urls(chat_id, text)
        elif looks_like_job_description(text):
            await self._capture(chat_id, text)
        else:
            await self._client.send_message(
                chat_id,
                escape_md("Forward me a job description, or try /help."),
                parse_mode="MarkdownV2",
            )

    async def _services(self, chat_id: int, text: str) -> None:
        """Show or replace the saved platform set."""
        parts = text.split(maxsplit=2)
        if len(parts) >= 2 and parts[1].lower() == "set":
            if len(parts) < 3:
                await self._say(chat_id, "usage: /services set hh,upwork")
                return
            try:
                wanted = parse_platform_set(parts[2])
            except UnknownPlatforms as exc:
                # Name what was wrong AND what exists: a rejection that does not
                # say what is valid just makes the user guess again.
                await self._say(chat_id, str(exc))
                return
            except ValueError as exc:
                await self._say(chat_id, str(exc))
                return
            saved = await self._with_store(lambda s: s.set_platforms(wanted))
            await self._say(chat_id, f"platforms: {format_platform_set(saved)}")
            return

        current = await self._with_store(lambda s: s.get_platforms())
        if current:
            await self._say(chat_id, f"platforms: {format_platform_set(current)}")
        else:
            # Unset is not "none": say what the commands will actually do.
            await self._say(
                chat_id,
                "no platform set saved — commands use all known platforms: "
                f"{', '.join(sorted(known_platforms()))}\n"
                "set one with: /services set hh,upwork",
            )

    # ── links (#26, #27) ──────────────────────────────────────────────────────

    async def _open(self, chat_id: int, text: str) -> None:
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await self._say(chat_id, "usage: /open hh")
            return
        try:
            target = resolve_open_target(parts[1])
        except ValueError as exc:
            await self._say(chat_id, str(exc))
            return
        await self._say_links(chat_id, f"{target.platform}: {target.url}")

    async def _profiles(self, chat_id: int, text: str) -> None:
        parts = text.split(maxsplit=1)
        platforms = await self._effective_platforms(parts[1] if len(parts) > 1 else None)
        if platforms is None:
            return await self._say(chat_id, "unknown platform in that list")
        rows = await self._with_platform_service(lambda svc: build_profile_rows(svc, platforms))
        await self._say_links(chat_id, render_rows(rows, empty="no platforms selected"))

    async def _urls(self, chat_id: int, text: str) -> None:
        """/urls "query" [services] — the quoted query is required, the set optional."""
        query, rest = _split_quoted(text)
        if not query:
            await self._say(chat_id, 'usage: /urls "senior data engineer" [hh,upwork]')
            return
        platforms = await self._effective_platforms(rest)
        if platforms is None:
            return await self._say(chat_id, "unknown platform in that list")
        try:
            rows = await self._with_platform_service(
                lambda svc: build_search_rows(svc, platforms, query)
            )
        except ValueError as exc:
            await self._say(chat_id, str(exc))
            return
        await self._say_links(
            chat_id, f"{query}\n" + render_rows(rows, empty="no platforms selected")
        )

    async def _effective_platforms(self, inline: str | None) -> list[str] | None:
        """Inline override, else the saved set, else every known platform.

        Unset is NOT "none": a user who never ran /services should still get answers.
        Returns None when an inline list names something that does not exist.
        """
        if inline and inline.strip():
            try:
                return parse_platform_set(inline)
            except ValueError:
                return None
        saved = await self._with_store(lambda s: s.get_platforms())
        return saved or sorted(known_platforms())

    async def _with_platform_service(self, fn):
        """Run one PlatformService operation in its own session (invariant 7)."""
        if self._sessionmaker is None:
            raise RuntimeError("no database configured")
        from careeros.modules.platform.service import PlatformService

        async with self._sessionmaker() as session:
            svc = PlatformService(self._settings, session=session, user_id=SINGLE_USER_ID)
            return await fn(svc)

    async def _with_store(self, fn):
        """Run one PreferenceStore operation in its own committed session."""
        if self._sessionmaker is None:
            raise RuntimeError("no database configured")
        async with self._sessionmaker() as session:
            result = await fn(PreferenceStore(session, SINGLE_USER_ID))
            await session.commit()
            return result

    async def _say(self, chat_id: int, text: str) -> None:
        await self._client.send_message(chat_id, escape_md(text), parse_mode="MarkdownV2")

    async def _say_links(self, chat_id: int, text: str) -> None:
        """Send link output as PLAIN text, with no parse_mode.

        MarkdownV2 requires escaping `.`, `-`, `_` and more, all of which occur in
        every URL. Escaping renders correctly but makes the message unreadable in
        any client that shows the raw text, and one missed character returns a 400
        naming a byte offset. Telegram auto-links bare URLs in plain text, so the
        formatting buys nothing here and costs a whole class of failure.
        """
        await self._client.send_message(chat_id, text)

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


def _split_quoted(text: str) -> tuple[str | None, str | None]:
    """Split `/urls "a b c" hh,upwork` into the quoted query and the remainder.

    Quoting is required rather than guessed: a bare `/urls senior data engineer hh`
    has no unambiguous boundary between the query and the platform list, and
    guessing wrong sends the user a search for the wrong words.
    """
    rest = text.split(maxsplit=1)
    if len(rest) < 2:
        return None, None
    body = rest[1].strip()
    for quote in ('"', "'"):
        if body.startswith(quote) and body.count(quote) >= 2:
            end = body.index(quote, 1)
            return body[1:end].strip() or None, body[end + 1 :].strip() or None
    return None, None

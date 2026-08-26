"""The bot's only cross-module entry point (invariant 7).

Handlers never touch another module's ORM models; everything they need arrives
through here. In P0 this is a thin command dispatcher — the capture and triage
flows (GH #3-#5, #7) attach to it without changing the router.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog

from careeros.core.auth import SINGLE_USER_ID
from careeros.core.config import Settings
from careeros.modules.bot.callbacks import BadCallback, TriageCallback, parse_callback, toast_for
from careeros.modules.bot.capture import looks_like_job_description
from careeros.modules.bot.client import TelegramClient
from careeros.modules.bot.cv import (
    artifact_card,
    diff_card,
    parse_cv_command,
    resolve_variant,
    variants_card,
)
from careeros.modules.bot.formatting import (
    analysis_card,
    chunk_message,
    escape_md,
    matches_short_id,
    ranked_list,
    score_card,
)
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
from careeros.modules.bot.queries import build_queries, render_queries, resolve_index
from careeros.modules.opportunities.enums import OpportunityStatus, Source

log = structlog.get_logger(__name__)

#: Plain text on purpose. MarkdownV2 requires escaping `[`, `]`, `>`, `-` and `.`,
#: all of which a usage line is made of, and one missed character turns /help — the
#: first command anyone types — into a 400. The title is the only formatted part.
HELP_BODY = (
    "Forward me a job description and I will parse, dedupe and score it.\n\n"
    "/services — show the platforms commands act on; /services set hh,upwork to change\n"
    "/open <service> — link to a platform\n"
    "/profiles [services] — your profile URL on each platform\n"
    '/urls "<query>" [services] — a job search URL per platform\n'
    "/urls <n> [services] — the same, for query n from /queries\n"
    "/queries — job-search query texts derived from your positionings\n"
    "/next — the next untriaged opportunity\n"
    "/top [n] — the highest-scoring ones (default 5)\n"
    "/opp <id> — one opportunity by the short handle /top prints\n"
    "/cv — list CV variants\n"
    "/cv update [meta|<variant>] — regenerate from the facts, no AI\n"
    "/cv improve [meta|<variant>] — let AI rewrite them, then show the diff\n"
    "/status — environment, database, webhook state\n"
    "/whoami — your chat id and whether you are the owner\n"
    "/help — this message"
)


#: Which stored status each triage button means. Skip is "ignored" rather than a
#: delete: the dedup key must keep matching, or the same posting comes back as new.
STATUS_BY_ACTION: dict[str, OpportunityStatus] = {
    "skip": OpportunityStatus.ignored,
    "save": OpportunityStatus.watching,
}

#: How many rows /top and /opp read before ranking or matching. The service orders
#: by arrival, so ranking by score has to look wider than the number it prints.
SCAN_LIMIT = 100

DEFAULT_TOP = 5


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
        # Generation takes tens of seconds and costs an AI call. Telegram delivers a
        # double-tap as two distinct updates, which the update_id gate cannot dedupe
        # because they genuinely are two — so the second one is refused here instead.
        self._inflight: set[tuple[int, str]] = set()

    async def handle(self, payload: dict) -> None:
        """Process one already-gated update. Never raises into the request path."""
        callback = payload.get("callback_query")
        if callback:
            try:
                await self._callback(callback)
            except Exception:
                log.exception("bot.callback_failed", data=callback.get("data"))
            return
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
        # `/opp_ab12cd34`: Telegram renders that as a tappable command inside a plain
        # listing, which is the only way to open an item on a phone without retyping a
        # uuid. The underscore form and the spaced form mean the same thing.
        if command and command.startswith("/opp_"):
            await self._opp(chat_id, command[len("/opp_") :])
            return
        if command in ("/start", "/help"):
            await self._client.send_message(
                chat_id, "*CareerOS*\n\n" + escape_md(HELP_BODY), parse_mode="MarkdownV2"
            )
        elif command == "/whoami":
            owner = self._settings.tg_owner_chat_id
            mine = "yes" if chat_id == owner else "no"
            await self._say(chat_id, f"chat id: {chat_id}\nowner: {mine}")
        elif command == "/status":
            await self._say(chat_id, self._status())
        elif command == "/services":
            await self._services(chat_id, text)
        elif command == "/open":
            await self._open(chat_id, text)
        elif command == "/profiles":
            await self._profiles(chat_id, text)
        elif command == "/urls":
            await self._urls(chat_id, text)
        elif command == "/queries":
            await self._queries(chat_id)
        elif command == "/cv":
            await self._cv(chat_id, text)
        elif command == "/next":
            await self._next(chat_id)
        elif command == "/top":
            await self._top(chat_id, text)
        elif command == "/opp":
            parts = text.split(maxsplit=1)
            await self._opp(chat_id, parts[1] if len(parts) > 1 else "")
        elif looks_like_job_description(text):
            await self._capture(chat_id, text)
        else:
            await self._say(chat_id, "Forward me a job description, or try /help.")

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
        await self._say_plain(chat_id, f"{target.platform}: {target.url}")

    async def _profiles(self, chat_id: int, text: str) -> None:
        parts = text.split(maxsplit=1)
        platforms = await self._effective_platforms(parts[1] if len(parts) > 1 else None)
        if platforms is None:
            return await self._say(chat_id, "unknown platform in that list")
        rows = await self._with_platform_service(lambda svc: build_profile_rows(svc, platforms))
        await self._say_plain(chat_id, render_rows(rows, empty="no platforms selected"))

    async def _urls(self, chat_id: int, text: str) -> None:
        """/urls "query" [services], or /urls <n> [services] for a saved query."""
        query, rest = _split_quoted(text)
        if query is None:
            query, rest = self._query_by_index(text)
            if query is None:
                await self._say(
                    chat_id,
                    'usage: /urls "senior data engineer" [hh,upwork]\n'
                    "   or: /urls 1 [hh,upwork] — see /queries" + (rest or ""),
                )
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
        await self._say_plain(
            chat_id, f"{query}\n" + render_rows(rows, empty="no platforms selected")
        )

    def _query_by_index(self, text: str) -> tuple[str | None, str | None]:
        """Resolve `/urls 2 hh` against the derived query list.

        Returns `(None, note)` when the token is not an index, so the caller can
        distinguish "you asked for query 9 and there are 6" from "you wrote no query
        at all" — the first needs the count, the second needs the usage line.
        """
        parts = text.split(maxsplit=2)
        token = parts[1] if len(parts) > 1 else ""
        if not token.isdigit():
            return None, None
        queries = build_queries(self._vault_data())
        picked = resolve_index(queries, token)
        if picked is None:
            return None, f"\n\nthere is no query {token} — /queries lists {len(queries)}"
        return picked.text, (parts[2] if len(parts) > 2 else None)

    async def _queries(self, chat_id: int) -> None:
        """Job-search query texts the vault's positionings imply (#28, read-only)."""
        try:
            queries = build_queries(self._vault_data())
        except Exception as exc:  # a broken vault must answer, not go silent
            log.warning("bot.queries_failed", error=str(exc))
            await self._say(chat_id, f"cannot read the vault: {exc}")
            return
        await self._say_plain(chat_id, render_queries(queries))

    # ── triage (#4) ───────────────────────────────────────────────────────────

    async def _callback(self, callback: dict) -> None:
        """One tapped inline button.

        Answer FIRST, then work. Telegram spins the button until answerCallbackQuery
        arrives and refuses the answer once the query ages out, so doing the AI work
        first hangs the button on exactly the slow actions.
        """
        callback_id = str(callback.get("id") or "")
        chat_id = ((callback.get("message") or {}).get("chat") or {}).get("id")
        try:
            action = parse_callback(callback.get("data"))
        except BadCallback as exc:
            # Rejected, not ignored (#4): a dropped tap is indistinguishable from a
            # dead bot, and the button keeps spinning until the query expires.
            log.warning("bot.callback_rejected", data=callback.get("data"))
            await self._client.answer_callback_query(callback_id, str(exc))
            return
        await self._client.answer_callback_query(callback_id, toast_for(action.action))
        if chat_id is None:
            return
        try:
            await self._triage(chat_id, action)
        except Exception as exc:
            log.exception("bot.triage_failed", action=action.action)
            await self._say(chat_id, f"{action.action} failed: {exc}")

    async def _triage(self, chat_id: int, action: TriageCallback) -> None:
        """Apply one button. Every transition goes through opportunities.service."""
        oid = action.opportunity_id
        status = STATUS_BY_ACTION.get(action.action)
        if status is not None:
            detail = await self._with_opportunities(lambda svc: svc.set_status(oid, status))
            await self._say(chat_id, f"{detail.title} → {detail.status}")
        elif action.action == "analyze":
            detail = await self._with_opportunities(lambda svc: svc.analyze(oid))
            await self._say_md(chat_id, analysis_card(detail))
        elif action.action == "prompt":
            # An external bundle is meant to be copied into another assistant, so it
            # goes out unformatted — escaping would have to be undone by hand.
            bundle = await self._with_opportunities(lambda svc: svc.external_prompt(oid, "generic"))
            await self._say_plain(chat_id, bundle.text)

    async def _next(self, chat_id: int) -> None:
        """The oldest-arrived opportunity still sitting at `new`."""
        items = await self._with_opportunities(
            lambda svc: svc.list(status=OpportunityStatus.new, limit=1)
        )
        if not items:
            await self._say(chat_id, "nothing untriaged — /top shows what is already scored")
            return
        await self._send_card(chat_id, items[0])

    async def _top(self, chat_id: int, text: str) -> None:
        """The highest-scoring opportunities, unscored ones counted rather than hidden."""
        parts = text.split()
        wanted = DEFAULT_TOP
        if len(parts) > 1:
            if not parts[1].isdigit() or int(parts[1]) < 1:
                await self._say(chat_id, f"usage: /top [n] — n is a number, default {DEFAULT_TOP}")
                return
            wanted = int(parts[1])
        items = await self._with_opportunities(lambda svc: svc.list(limit=SCAN_LIMIT))
        scored = [i for i in items if getattr(i.score, "overall", None) is not None]
        unscored = len(items) - len(scored)
        scored.sort(key=lambda i: i.score.overall, reverse=True)
        body = ranked_list(list(scored[:wanted]))
        if unscored:
            # Naming the omission: a ranked list that silently drops rows reads as
            # "this is everything", which is a different and wrong claim.
            body += f"\n\n{unscored} not ranked (no score yet)"
        await self._say_plain(chat_id, body)

    async def _opp(self, chat_id: int, wanted: str) -> None:
        """One opportunity by short handle or full uuid."""
        probe = wanted.strip()
        if not probe:
            await self._say(chat_id, "usage: /opp ab12cd34 — the handle /top prints")
            return
        items = await self._with_opportunities(lambda svc: svc.list(limit=SCAN_LIMIT))
        matches = [i for i in items if matches_short_id(i.id, probe)]
        if not matches:
            await self._say(chat_id, f"no opportunity starts with {probe!r}")
            return
        if len(matches) > 1:
            # Picking the first would act on an opportunity the owner did not choose.
            await self._say(
                chat_id, f"{len(matches)} opportunities start with {probe!r} — add more characters"
            )
            return
        detail = await self._with_opportunities(lambda svc: svc.get(matches[0].id))
        await self._send_card(chat_id, detail)
        if getattr(detail, "url", None):
            await self._say_plain(chat_id, str(detail.url))

    async def _send_card(self, chat_id: int, item: Any) -> None:
        """The triage card plus its buttons — the one place that pairs the two."""
        await self._client.send_message(
            chat_id,
            score_card(item),
            parse_mode="MarkdownV2",
            reply_markup=triage_keyboard(str(item.id)),
        )

    # ── CV (#29, #30) ─────────────────────────────────────────────────────────

    async def _cv(self, chat_id: int, text: str) -> None:
        try:
            cmd = parse_cv_command(text)
        except ValueError as exc:
            await self._say(chat_id, str(exc))
            return
        try:
            data = self._vault_data()
        except Exception as exc:
            log.warning("bot.cv_vault_failed", error=str(exc))
            await self._say(chat_id, f"cannot read the vault: {exc}")
            return

        if cmd.action == "show":
            core = str(getattr(data.meta, "default_cv_variant", "") or "") or None
            await self._say_md(chat_id, variants_card(list(data.cv_variants), core))
            return
        if self._sessionmaker is None:
            await self._say(chat_id, "CV generation is unavailable: no database configured.")
            return
        try:
            variant = resolve_variant(data, cmd.variant)
        except ValueError as exc:
            await self._say(chat_id, str(exc))
            return

        key = (chat_id, "cv")
        if key in self._inflight:
            await self._say(chat_id, "a CV run is already going for this chat — let it finish")
            return
        self._inflight.add(key)
        try:
            await self._say(chat_id, f"{cmd.action} {variant} — generating, this takes a moment…")
            if cmd.action == "update":
                await self._cv_update(chat_id, variant)
            else:
                await self._cv_improve(chat_id, variant)
        except Exception as exc:
            log.exception("bot.cv_failed", action=cmd.action, variant=variant)
            await self._say(chat_id, f"{cmd.action} failed: {exc}")
        finally:
            self._inflight.discard(key)

    async def _cv_update(self, chat_id: int, variant: str) -> None:
        """Deterministic regeneration: reruns selection and rendering, no AI (#29)."""
        from careeros.modules.cv.schemas import GenerateCVRequest

        out = await self._with_cv_service(
            lambda svc: svc.generate(GenerateCVRequest(variant_id=variant, use_ai=False))
        )
        await self._say_md(chat_id, artifact_card(out, header=f"CV updated — {variant}"))
        await self._send_artifact_file(chat_id, out, variant)

    async def _cv_improve(self, chat_id: int, variant: str) -> None:
        """AI rewrite of the SELECTED facts, shown as a diff against those facts (#30)."""
        result = await self._with_cv_service(lambda svc: svc.improve(variant))
        await self._say_md(
            chat_id, artifact_card(result.artifact, header=f"CV improved — {variant}")
        )
        if not result.artifact.ai_used:
            # An empty diff after a no-op AI pass reads as "AI had nothing to add",
            # which is a different claim from "no provider was configured".
            await self._say(chat_id, "no AI provider ran — the bullets are the facts as written")
        await self._say_md(chat_id, diff_card(result.comparison))
        await self._send_artifact_file(chat_id, result.artifact, variant)

    async def _send_artifact_file(self, chat_id: int, artifact: Any, variant: str) -> None:
        """Upload the rendered CV — the PDF if there is one, else the markdown.

        The read goes to a thread: this runs on the event loop that is also serving
        incoming webhook updates, and every one of those has ~60 seconds before
        Telegram retries it.
        """
        picked = await asyncio.to_thread(_first_rendered_file, artifact.files)
        if picked is None:
            await self._say(chat_id, "no document was rendered — see the warnings above")
            return
        suffix, content = picked
        await self._client.send_document(
            chat_id, f"{variant}.{suffix}", content, caption=f"{variant} · {suffix}"
        )

    # ── plumbing ──────────────────────────────────────────────────────────────

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

    def _vault_data(self):
        """Load the vault through its own module (invariant 7); read-only in P0."""
        from careeros.modules.vault.deps import get_vault

        return get_vault(self._settings).require()

    async def _with_platform_service(self, fn):
        """Run one PlatformService operation in its own session (invariant 7)."""
        if self._sessionmaker is None:
            raise RuntimeError("no database configured")
        from careeros.modules.platform.service import PlatformService

        async with self._sessionmaker() as session:
            svc = PlatformService(self._settings, session=session, user_id=SINGLE_USER_ID)
            return await fn(svc)

    async def _with_opportunities(self, fn):
        """Run one OpportunityService operation in its own committed session.

        Shared by capture and by every triage button, so the bot has exactly one way
        to reach the opportunities module (invariant 7) rather than one per handler.
        """
        if self._sessionmaker is None:
            raise RuntimeError("no database configured")
        from careeros.modules.ai.deps import build_ai_service
        from careeros.modules.opportunities.service import OpportunityService
        from careeros.modules.vault.deps import get_vault

        async with self._sessionmaker() as session:
            svc = OpportunityService(
                self._settings,
                get_vault(self._settings),
                build_ai_service(self._settings, session=session, user_id=SINGLE_USER_ID),
                session=session,
                user_id=SINGLE_USER_ID,
            )
            result = await fn(svc)
            await session.commit()
            return result

    async def _with_cv_service(self, fn):
        """Run one CVService operation in its own session (invariant 7)."""
        if self._sessionmaker is None:
            raise RuntimeError("no database configured")
        from careeros.modules.cv.deps import build_cv_service

        async with self._sessionmaker() as session:
            svc = build_cv_service(self._settings, session=session, user_id=SINGLE_USER_ID)
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
        await self._say_md(chat_id, escape_md(text))

    async def _say_md(self, chat_id: int, markdown: str) -> None:
        """Send already-escaped MarkdownV2, split at line boundaries if oversized.

        Cards keep each `*bold*` pair inside one line, so a newline split never lands
        between a marker and its closing partner.
        """
        for part in chunk_message(markdown):
            await self._client.send_message(chat_id, part, parse_mode="MarkdownV2")

    async def _say_plain(self, chat_id: int, text: str) -> None:
        """Send as PLAIN text, with no parse_mode.

        MarkdownV2 requires escaping `.`, `-`, `_` and more, all of which occur in
        every URL and in query text meant to be copied out. Escaping renders correctly
        but makes the message unreadable in any client that shows the raw text, and one
        missed character returns a 400 naming a byte offset. Telegram auto-links bare
        URLs in plain text, so the formatting buys nothing here and costs a whole class
        of failure.
        """
        for part in chunk_message(text):
            await self._client.send_message(chat_id, part)

    async def _capture(self, chat_id: int, text: str) -> None:
        """Ingest a forwarded job description and reply with its triage card."""
        if self._sessionmaker is None:
            await self._say(chat_id, "Capture is unavailable: no database configured.")
            return

        from careeros.modules.opportunities.schemas import IngestRequest

        url = text.strip() if text.strip().startswith("http") else None
        detail = await self._with_opportunities(
            lambda svc: svc.ingest(
                IngestRequest(text=None if url else text, url=url, source=Source.manual)
            )
        )
        await self._send_card(chat_id, detail)

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


def _first_rendered_file(files: Any) -> tuple[str, bytes] | None:
    """The best rendered file for chat: the PDF, else the markdown, else nothing.

    Blocking on purpose — the caller hands it to a thread. Returning the bytes
    rather than the path keeps the one filesystem touch in one place.
    """
    for attr, suffix in (("pdf", "pdf"), ("md", "md")):
        raw = getattr(files, attr, None)
        if not raw:
            continue
        path = Path(raw)
        if path.is_file():
            return suffix, path.read_bytes()
    return None

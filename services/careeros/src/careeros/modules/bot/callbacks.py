"""Inline-button callbacks (GH #4).

Telegram's contract here has one hard rule that shapes everything below: a
`callback_query` MUST be answered, and answered soon. Until `answerCallbackQuery`
arrives the client shows a spinner on the button, and once the query ages out the
answer is refused outright — so a handler that does its work first and answers
afterwards produces a hung button on exactly the slow actions (Analyze, Prompt)
where the user most needs to know something is happening.

Hence the split: parsing is a pure function that either yields an action or raises
with a sentence worth showing, and the caller answers before it works. Rejection is
visible on purpose — the issue asks for unknown callback data to be *rejected*
rather than ignored, and a silently dropped tap is indistinguishable from a dead bot.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from careeros.modules.bot.keyboards import TRIAGE_ACTIONS

#: Namespace byte on every triage payload. One character, because a uuid already
#: eats 36 of the 64 bytes Telegram allows.
PREFIX = "o"

#: What the user sees the instant they tap, before the work starts.
TOASTS: dict[str, str] = {
    "skip": "Skipped",
    "save": "Saved — watching",
    "analyze": "Analyzing…",
    "prompt": "Building the prompt…",
}


class BadCallback(ValueError):
    """Callback data this bot did not produce, or can no longer act on."""


@dataclass(frozen=True)
class TriageCallback:
    action: str
    opportunity_id: uuid.UUID


def parse_callback(data: str | None) -> TriageCallback:
    """`o:<action>:<uuid>` → an action, or raise with what was wrong.

    Every failure names both the offending payload and what is valid: a rejection
    that says only "unknown" leaves the owner unable to tell a stale button from a
    bug in the bot.
    """
    if not data:
        raise BadCallback("that button carried no data")
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise BadCallback(f"not a triage button: {data!r}")
    _, action, raw_id = parts
    if action not in TRIAGE_ACTIONS:
        raise BadCallback(f"unknown action {action!r}; known: {', '.join(TRIAGE_ACTIONS)}")
    try:
        opportunity_id = uuid.UUID(raw_id)
    except ValueError as exc:
        raise BadCallback(f"{raw_id!r} is not an opportunity id") from exc
    return TriageCallback(action=action, opportunity_id=opportunity_id)


def toast_for(action: str) -> str:
    return TOASTS.get(action, "Working…")

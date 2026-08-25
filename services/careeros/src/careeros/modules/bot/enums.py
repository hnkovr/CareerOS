"""Enums for the bot surface."""

from __future__ import annotations

from enum import StrEnum


class WebhookClaim(StrEnum):
    """Outcome of a startup attempt to own the webhook."""

    CLAIMED = "claimed"  # it was unset, and is now ours
    ALREADY_OURS = "already_ours"
    REFUSED_FOREIGN = "refused_foreign"  # someone else holds it; we did not take it
    FORCED = "forced"  # taken from a live owner, deliberately
    INELIGIBLE = "ineligible"  # no public URL configured — we never contact Telegram
    DISABLED = "disabled"


class UpdateVerdict(StrEnum):
    """Why an incoming update was accepted or dropped."""

    ACCEPTED = "accepted"
    BAD_SECRET = "bad_secret"  # -> 403
    NOT_OWNER = "not_owner"  # -> 200, no side effect
    DUPLICATE = "duplicate"  # -> 200, already processed

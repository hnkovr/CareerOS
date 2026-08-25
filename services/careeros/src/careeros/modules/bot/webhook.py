"""Webhook ownership: who Telegram currently delivers to, and when we may take it.

A webhook is exclusive per bot token. Registering one silently takes updates away
from whoever held it, which makes the naive "claim on startup" both tempting and
wrong: a demoted host coming back would re-take the bot and undo a failover with
no error anywhere. The rule here is therefore narrow, and the interesting path is
the refusal.
"""

from __future__ import annotations

from typing import Protocol

import structlog

from careeros.core.config import Settings
from careeros.modules.bot.enums import WebhookClaim

log = structlog.get_logger(__name__)


class TelegramWebhookAPI(Protocol):
    """Only the surface claiming needs — keeps this testable without HTTP."""

    async def get_webhook_info(self) -> dict: ...
    async def set_webhook(self, url: str, secret: str) -> None: ...
    async def delete_webhook(self) -> None: ...


def webhook_url(settings: Settings) -> str | None:
    """Our public webhook URL, or None when this process is not eligible to serve one."""
    if not settings.tg_public_url:
        return None
    return settings.tg_public_url.rstrip("/") + settings.tg_webhook_path


def _same_url(a: str, b: str) -> bool:
    """Telegram echoes back what it stored; a trailing slash is not a different owner."""
    return a.rstrip("/") == b.rstrip("/")


async def claim_webhook(settings: Settings, api: TelegramWebhookAPI) -> WebhookClaim:
    """Take ownership of the webhook when — and only when — it is safe to.

    Claim if it is unset or already ours. Refuse if a different live URL holds it,
    unless ``tg_webhook_force_claim`` says the takeover is deliberate.
    """
    if not settings.tg_enabled:
        return WebhookClaim.DISABLED

    ours = webhook_url(settings)
    if ours is None:
        # Eligibility key. A process without a public URL never contacts Telegram,
        # so a local run cannot claim the webhook away from the deployed bot.
        log.info("bot.webhook.ineligible", reason="tg_public_url unset")
        return WebhookClaim.INELIGIBLE

    if settings.tg_webhook_secret is None:
        raise ValueError(
            "tg_webhook_secret is required to claim a webhook — "
            "claiming without one leaves the endpoint unauthenticated"
        )
    secret = settings.tg_webhook_secret.get_secret_value()

    current = (await api.get_webhook_info()).get("url") or ""

    if current and _same_url(current, ours):
        log.info("bot.webhook.already_ours", url=ours)
        return WebhookClaim.ALREADY_OURS

    if current and not settings.tg_webhook_force_claim:
        log.warning("bot.webhook.refused_foreign", held_by=current, ours=ours)
        return WebhookClaim.REFUSED_FOREIGN

    forced = bool(current)
    await api.set_webhook(ours, secret)
    log.info("bot.webhook.claimed", url=ours, taken_from=current or None)
    return WebhookClaim.FORCED if forced else WebhookClaim.CLAIMED

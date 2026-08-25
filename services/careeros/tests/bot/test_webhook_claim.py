"""Startup webhook ownership (ADR-012 §B).

A webhook is exclusive: registering one takes updates away from whoever held it.
The rule is therefore narrow — claim only when it is unset or already ours — and
the test that matters most is the one asserting we DON'T claim.
"""

from __future__ import annotations

from typing import Any

import pytest

from careeros.core.config import Settings
from careeros.modules.bot.enums import WebhookClaim
from careeros.modules.bot.webhook import claim_webhook

OURS = "https://careeros.fly.dev/tg/webhook"
THEIRS = "https://staging.example.dev/tg/webhook"


class FakeTelegram:
    """Records calls so a test can assert that no write was attempted."""

    def __init__(self, current_url: str = "") -> None:
        self.current_url = current_url
        self.set_calls: list[tuple[str, str]] = []
        self.deleted = 0

    async def get_webhook_info(self) -> dict:
        return {"url": self.current_url}

    async def set_webhook(self, url: str, secret: str) -> None:
        self.set_calls.append((url, secret))
        self.current_url = url

    async def delete_webhook(self) -> None:
        self.deleted += 1
        self.current_url = ""


def settings(**kw: Any) -> Settings:
    # dict[str, Any]: Settings takes SecretStr/Literal fields that plain literals do not match
    base: dict[str, Any] = dict(
        env="test",
        tg_enabled=True,
        tg_bot_token="123:abc",
        tg_webhook_secret="s3cret",
        tg_owner_chat_id=1,
        tg_public_url="https://careeros.fly.dev",
        tg_webhook_path="/tg/webhook",
    )
    base.update(kw)
    return Settings(**base)


async def test_claims_when_the_webhook_is_unset() -> None:
    tg = FakeTelegram(current_url="")
    assert await claim_webhook(settings(), tg) is WebhookClaim.CLAIMED
    assert tg.set_calls == [(OURS, "s3cret")]


async def test_is_idempotent_when_the_webhook_is_already_ours() -> None:
    """A redeploy must not churn the webhook it already owns."""
    tg = FakeTelegram(current_url=OURS)
    assert await claim_webhook(settings(), tg) is WebhookClaim.ALREADY_OURS
    assert tg.set_calls == []


async def test_refuses_to_take_a_webhook_held_by_someone_else() -> None:
    """Restarting a demoted host must not silently undo a failover."""
    tg = FakeTelegram(current_url=THEIRS)
    assert await claim_webhook(settings(), tg) is WebhookClaim.REFUSED_FOREIGN
    assert tg.set_calls == [], "a refusal must make no write call at all"
    assert tg.current_url == THEIRS


async def test_force_claim_takes_it_deliberately() -> None:
    tg = FakeTelegram(current_url=THEIRS)
    result = await claim_webhook(settings(tg_webhook_force_claim=True), tg)
    assert result is WebhookClaim.FORCED
    assert tg.set_calls == [(OURS, "s3cret")]


async def test_without_a_public_url_we_never_contact_telegram() -> None:
    """The eligibility key: a local dev run cannot steal production's updates."""
    tg = FakeTelegram(current_url=OURS)
    assert await claim_webhook(settings(tg_public_url=None), tg) is WebhookClaim.INELIGIBLE
    assert tg.set_calls == []


async def test_disabled_bot_does_nothing() -> None:
    tg = FakeTelegram(current_url="")
    assert await claim_webhook(settings(tg_enabled=False), tg) is WebhookClaim.DISABLED
    assert tg.set_calls == []


async def test_claim_requires_a_secret() -> None:
    """Claiming without one would leave the endpoint unauthenticated."""
    tg = FakeTelegram(current_url="")
    with pytest.raises(ValueError, match="tg_webhook_secret"):
        await claim_webhook(settings(tg_webhook_secret=None), tg)
    assert tg.set_calls == []


async def test_trailing_slash_on_public_url_does_not_produce_a_double_slash() -> None:
    tg = FakeTelegram(current_url="")
    await claim_webhook(settings(tg_public_url="https://careeros.fly.dev/"), tg)
    assert tg.set_calls == [(OURS, "s3cret")]


async def test_a_url_differing_only_by_trailing_slash_is_still_ours() -> None:
    """Telegram echoes back what it stored; cosmetic drift must not force a rewrite."""
    tg = FakeTelegram(current_url=OURS + "/")
    assert await claim_webhook(settings(), tg) is WebhookClaim.ALREADY_OURS
    assert tg.set_calls == []

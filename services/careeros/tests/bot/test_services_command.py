"""The /services command (GH #25) — dispatch, not storage.

Storage is exercised against a real database elsewhere; what matters here is that
a typo is refused with a message the user can act on, and that an unset preference
says what the commands will actually do rather than printing an empty list.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from careeros.core.config import Settings
from careeros.modules.bot.service import BotService

OWNER = 4242


class SpyClient:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, chat_id: int, text: str, **kw) -> dict:
        self.sent.append(text)
        return {}


class FakeStore:
    def __init__(self, platforms: list[str] | None = None) -> None:
        self.platforms = platforms or []

    async def get_platforms(self) -> list[str]:
        return list(self.platforms)

    async def set_platforms(self, platforms: list[str]) -> list[str]:
        self.platforms = list(platforms)
        return list(self.platforms)


@pytest.fixture
def bot(monkeypatch):
    settings = Settings(
        env="test",
        tg_enabled=True,
        # SecretStr explicitly: pydantic coerces a str at runtime, but the
        # annotation is SecretStr | None and pyright checks direct kwargs.
        tg_bot_token=SecretStr("1:a"),
        tg_webhook_secret=SecretStr("s"),
        tg_owner_chat_id=OWNER,
    )
    client = SpyClient()
    service = BotService(settings, client)  # type: ignore[arg-type]
    store = FakeStore()

    async def _with_store(fn):
        return await fn(store)

    monkeypatch.setattr(service, "_with_store", _with_store)
    return service, client, store


def msg(text: str) -> dict:
    return {"update_id": 1, "message": {"chat": {"id": OWNER}, "text": text}}


async def test_unset_preference_explains_the_default(bot) -> None:
    """Printing an empty list would imply the commands do nothing."""
    service, client, _ = bot
    await service.handle(msg("/services"))
    said = client.sent[-1]
    assert "no platform set saved" in said
    assert "upwork" in said and "toptal" in said


async def test_shows_the_saved_set(bot) -> None:
    service, client, store = bot
    store.platforms = ["hh", "upwork"]
    await service.handle(msg("/services"))
    assert "hh, upwork" in client.sent[-1]


async def test_set_replaces_the_saved_set(bot) -> None:
    service, client, store = bot
    await service.handle(msg("/services set hh,toptal"))
    assert store.platforms == ["hh", "toptal"]
    assert "hh, toptal" in client.sent[-1]


async def test_set_preserves_the_order_typed(bot) -> None:
    service, _, store = bot
    await service.handle(msg("/services set toptal,hh"))
    assert store.platforms == ["toptal", "hh"]


async def test_a_typo_is_refused_and_names_both_the_error_and_the_options(bot) -> None:
    service, client, store = bot
    await service.handle(msg("/services set hh,upwrok"))
    said = client.sent[-1]
    assert "upwrok" in said, "the bad name must be quoted back"
    assert "upwork" in said, "the valid options must be listed"
    assert store.platforms == [], "nothing may be stored from a rejected command"


def _last(client):
    return client.sent[-1]


async def test_set_without_arguments_shows_usage(bot) -> None:
    service, client, store = bot
    await service.handle(msg("/services set"))
    assert "usage" in _last(client).lower()
    assert store.platforms == []


async def test_all_selects_everything(bot) -> None:
    service, _, store = bot
    await service.handle(msg("/services set all"))
    assert len(store.platforms) >= 7


async def test_a_rejected_set_leaves_a_previous_set_intact(bot) -> None:
    """A failed edit must not clear what was already saved."""
    service, _, store = bot
    await service.handle(msg("/services set hh"))
    await service.handle(msg("/services set nonsense"))
    assert store.platforms == ["hh"]

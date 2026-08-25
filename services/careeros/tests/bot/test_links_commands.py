"""/open, /profiles and /urls (GH #26, #27).

The property under test throughout: a platform that cannot answer must SAY SO.
Silently omitting it reads as "no results", which is a different and wrong claim —
so every command reports one line per requested platform, always.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from careeros.core.config import Settings
from careeros.modules.bot.links import (
    OpenTarget,
    build_search_rows,
    resolve_open_target,
)

OWNER = 4242


class FakeConnector:
    def __init__(self, search: str | None, profile: str | None) -> None:
        self._search, self._profile = search, profile

    def search_url(self, query) -> str | None:
        return self._search

    def profile_url(self, handle: str | None = None) -> str | None:
        return self._profile


class FakePlatformService:
    """Only the two methods the bot is allowed to reach (invariant 7)."""

    def __init__(self, connectors: dict[str, FakeConnector], own: dict[str, str | None]) -> None:
        self._connectors, self._own = connectors, own

    def connector(self, platform):
        return self._connectors[str(platform)]

    async def own_profile_url(self, platform) -> str | None:
        return self._own.get(str(platform))


def settings() -> Settings:
    return Settings(
        env="test",
        tg_enabled=True,
        tg_bot_token=SecretStr("1:a"),
        tg_webhook_secret=SecretStr("s"),
        tg_owner_chat_id=OWNER,
    )


# ── /urls ─────────────────────────────────────────────────────────────────────


async def test_a_row_is_produced_for_every_requested_platform() -> None:
    svc = FakePlatformService(
        {
            "hh": FakeConnector("https://hh.ru/search?q=de", None),
            "toptal": FakeConnector(None, None),
        },  # Toptal cannot express a search
        {},
    )
    rows = await build_search_rows(svc, ["hh", "toptal"], "data engineer")
    assert [r.platform for r in rows] == ["hh", "toptal"]


async def test_a_platform_that_cannot_search_is_reported_not_dropped() -> None:
    """An absent row would read as 'no results', which is a different claim."""
    svc = FakePlatformService({"toptal": FakeConnector(None, None)}, {})
    rows = await build_search_rows(svc, ["toptal"], "data engineer")
    assert len(rows) == 1
    assert rows[0].url is None
    assert rows[0].reason, "a missing URL must carry a stated reason"


async def test_a_platform_that_can_search_carries_its_url() -> None:
    svc = FakePlatformService({"hh": FakeConnector("https://hh.ru/search?q=de", None)}, {})
    rows = await build_search_rows(svc, ["hh"], "data engineer")
    assert rows[0].url == "https://hh.ru/search?q=de"
    assert rows[0].reason is None


async def test_a_connector_that_raises_does_not_lose_the_other_platforms() -> None:
    """One broken connector must not take the whole answer down with it."""

    class Exploding(FakeConnector):
        def search_url(self, query):
            raise RuntimeError("boom")

    svc = FakePlatformService(
        {"hh": FakeConnector("https://hh.ru/x", None), "upwork": Exploding(None, None)}, {}
    )
    rows = await build_search_rows(svc, ["hh", "upwork"], "q")
    assert len(rows) == 2
    assert rows[0].url == "https://hh.ru/x"
    assert rows[1].url is None and rows[1].reason


async def test_an_empty_query_is_refused() -> None:
    svc = FakePlatformService({"hh": FakeConnector("x", None)}, {})
    with pytest.raises(ValueError):
        await build_search_rows(svc, ["hh"], "   ")


# ── /open ─────────────────────────────────────────────────────────────────────


def test_a_known_service_resolves_to_its_home_url() -> None:
    target = resolve_open_target("hh")
    assert isinstance(target, OpenTarget)
    assert target.url.startswith("http")


def test_service_names_are_case_insensitive() -> None:
    assert resolve_open_target("HH").url == resolve_open_target("hh").url


def test_an_unknown_service_is_refused_by_name_and_lists_the_options() -> None:
    with pytest.raises(ValueError) as exc:
        resolve_open_target("nope")
    message = str(exc.value)
    assert "nope" in message
    assert "upwork" in message, "the rejection must say what does exist"


def test_every_known_platform_has_a_home_url() -> None:
    """A platform selectable in /services must be openable, or the two disagree."""
    from careeros.modules.bot.platforms import known_platforms

    for name in known_platforms():
        assert resolve_open_target(name).url.startswith("http"), name


# ── dispatch (#26, #27) ───────────────────────────────────────────────────────

from careeros.modules.bot.service import BotService, _split_quoted  # noqa: E402


class SpyClient:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, chat_id: int, text: str, **kw) -> dict:
        self.sent.append(text)
        return {}


@pytest.fixture
def bot(monkeypatch):
    service = BotService(settings(), SpyClient())  # type: ignore[arg-type]
    svc = FakePlatformService(
        {
            "hh": FakeConnector("https://hh.ru/search?q=de", "https://hh.ru/resume/1"),
            "toptal": FakeConnector(None, None),
        },
        {"hh": "https://hh.ru/resume/1", "toptal": None},
    )
    saved: list[str] = ["hh", "toptal"]

    async def _with_platform_service(fn):
        return await fn(svc)

    class Store:
        async def get_platforms(self):
            return list(saved)

    async def _with_store(fn):
        return await fn(Store())

    monkeypatch.setattr(service, "_with_platform_service", _with_platform_service)
    monkeypatch.setattr(service, "_with_store", _with_store)
    return service, service._client  # type: ignore[attr-defined]


def msg(text: str) -> dict:
    return {"update_id": 1, "message": {"chat": {"id": OWNER}, "text": text}}


async def test_open_sends_a_link(bot) -> None:
    service, client = bot
    await service.handle(msg("/open hh"))
    assert "hh.ru" in client.sent[-1]


async def test_open_without_an_argument_shows_usage(bot) -> None:
    service, client = bot
    await service.handle(msg("/open"))
    assert "usage" in client.sent[-1].lower()


async def test_profiles_lists_every_saved_platform_including_unknown_ones(bot) -> None:
    service, client = bot
    await service.handle(msg("/profiles"))
    said = client.sent[-1]
    assert "hh" in said and "toptal" in said, "both platforms must appear"
    assert "not known" in said, "the one without a URL must say why"


async def test_urls_requires_a_quoted_query(bot) -> None:
    service, client = bot
    await service.handle(msg("/urls senior data engineer"))
    assert "usage" in client.sent[-1].lower()


async def test_urls_returns_a_row_per_platform(bot) -> None:
    service, client = bot
    await service.handle(msg('/urls "data engineer"'))
    said = client.sent[-1]
    assert "hh.ru/search" in said
    assert "toptal" in said and "cannot express" in said


async def test_urls_accepts_an_inline_platform_override(bot) -> None:
    service, client = bot
    await service.handle(msg('/urls "data engineer" hh'))
    said = client.sent[-1]
    assert "hh.ru/search" in said
    assert "toptal" not in said


@pytest.mark.parametrize(
    ("raw", "query", "rest"),
    [
        ('/urls "a b" hh', "a b", "hh"),
        ("/urls 'a b' hh,upwork", "a b", "hh,upwork"),
        ('/urls "a b"', "a b", None),
        ("/urls nope", None, None),
        ("/urls", None, None),
    ],
)
def test_quoted_split(raw: str, query: str | None, rest: str | None) -> None:
    """Quoting is required, not guessed: a bare query has no unambiguous boundary."""
    assert _split_quoted(raw) == (query, rest)

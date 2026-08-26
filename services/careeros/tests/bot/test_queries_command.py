"""/queries and /urls <n> (GH #28).

The vault stores no queries, so these are projected from its positionings. The
property that matters is that the projection is an ADDRESSABLE list: a number
printed by /queries has to still mean the same query when /urls quotes it back,
or the command silently searches for the wrong thing.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from careeros.core.config import Settings
from careeros.modules.bot.queries import (
    build_queries,
    query_text,
    query_titles,
    render_queries,
    resolve_index,
)
from careeros.modules.bot.service import BotService

OWNER = 4242


class FakePositioning:
    def __init__(self, id: str, name: str, keywords_must: list[str] | None = None) -> None:
        self.id = id
        self.name = name
        self.keywords_must = keywords_must or []


class FakeMeta:
    def __init__(self, positioning: str = "", cv_variant: str = "core") -> None:
        self.default_positioning = positioning
        self.default_cv_variant = cv_variant


class FakeVaultData:
    def __init__(self, positioning: list[FakePositioning], default: str = "") -> None:
        self.positioning = positioning
        self.cv_variants: list[object] = []
        self.meta = FakeMeta(default)


def vault(*names: tuple[str, str]) -> FakeVaultData:
    return FakeVaultData([FakePositioning(i, n) for i, n in names])


# ── projection ────────────────────────────────────────────────────────────────


def test_a_parenthetical_qualifier_is_dropped_from_the_search_text() -> None:
    """`Analytics Engineer (dbt-centric)` searches badly; the note is for the owner."""
    assert query_text("Analytics Engineer (dbt-centric)") == "Analytics Engineer"


def test_whitespace_is_collapsed_after_stripping() -> None:
    assert query_text("Senior  Data   Engineer") == "Senior Data Engineer"


def test_a_name_without_a_qualifier_is_unchanged() -> None:
    assert query_text("Senior Data Engineer") == "Senior Data Engineer"


def test_a_slashed_name_searches_on_the_first_title() -> None:
    """A job board ANDs the words; `A / B` pasted whole matches nothing."""
    assert query_text("Senior Data Engineer / Analytics Engineer") == "Senior Data Engineer"


def test_the_other_titles_are_kept_rather_than_dropped() -> None:
    assert query_titles("A Engineer / B Engineer") == ["A Engineer", "B Engineer"]


def test_alternatives_are_shown_in_the_listing() -> None:
    data = FakeVaultData([FakePositioning("a", "Data Engineer / Analytics Engineer")])
    said = render_queries(build_queries(data))
    assert "1. Data Engineer" in said
    assert "Analytics Engineer" in said, "an alternative title must not vanish"


def test_every_positioning_yields_exactly_one_query() -> None:
    queries = build_queries(vault(("b", "B Engineer"), ("a", "A Engineer")))
    assert len(queries) == 2


def test_order_is_the_positioning_id_not_the_file_order() -> None:
    """Ids are stable across edits; any ranking would renumber the whole list."""
    queries = build_queries(vault(("zeta", "Z"), ("alpha", "A")))
    assert [q.positioning_id for q in queries] == ["alpha", "zeta"]


def test_indexes_start_at_one_and_are_contiguous() -> None:
    queries = build_queries(vault(("a", "A"), ("b", "B"), ("c", "C")))
    assert [q.index for q in queries] == [1, 2, 3]


def test_the_default_positioning_is_marked_not_reordered() -> None:
    data = FakeVaultData([FakePositioning("a", "A"), FakePositioning("z", "Z")], default="z")
    queries = build_queries(data)
    assert [q.index for q in queries] == [1, 2]
    assert queries[1].is_default and not queries[0].is_default


def test_keywords_ride_along_for_manual_refinement() -> None:
    data = FakeVaultData([FakePositioning("a", "A", ["dbt", "Dagster"])])
    assert build_queries(data)[0].keywords == ["dbt", "Dagster"]


def test_a_positioning_with_no_usable_name_is_skipped_rather_than_indexed_blank() -> None:
    data = FakeVaultData([FakePositioning("a", "(internal)"), FakePositioning("b", "B")])
    assert [q.text for q in build_queries(data)] == ["B"]


def test_an_empty_vault_says_where_queries_come_from() -> None:
    said = render_queries([])
    assert "positioning" in said


# ── addressing ────────────────────────────────────────────────────────────────


def test_resolve_index_finds_the_numbered_query() -> None:
    queries = build_queries(vault(("a", "A"), ("b", "B")))
    picked = resolve_index(queries, "2")
    assert picked is not None and picked.text == "B"


def test_resolve_index_returns_none_for_a_non_number() -> None:
    assert resolve_index(build_queries(vault(("a", "A"))), "hh") is None


def test_resolve_index_returns_none_past_the_end() -> None:
    assert resolve_index(build_queries(vault(("a", "A"))), "9") is None


def test_the_listing_names_both_ways_to_run_a_query() -> None:
    said = render_queries(build_queries(vault(("a", "Senior Data Engineer"))))
    assert "/urls 1" in said
    assert '/urls "' in said


# ── dispatch ──────────────────────────────────────────────────────────────────


class SpyClient:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, chat_id: int, text: str, **kw) -> dict:
        self.sent.append(text)
        return {}


@pytest.fixture
def bot(monkeypatch):
    settings = Settings(
        env="test",
        tg_enabled=True,
        tg_bot_token=SecretStr("1:a"),
        tg_webhook_secret=SecretStr("s"),
        tg_owner_chat_id=OWNER,
    )
    client = SpyClient()
    service = BotService(settings, client, sessionmaker=object())  # type: ignore[arg-type]
    data = FakeVaultData(
        [
            FakePositioning("analytics", "Analytics Engineer (dbt-centric)"),
            FakePositioning("senior_de", "Senior Data Engineer", ["dbt", "Dagster"]),
        ],
        default="senior_de",
    )
    monkeypatch.setattr(service, "_vault_data", lambda: data)

    async def _with_platform_service(fn):
        return await fn(object())  # the connectors are stubbed per test

    async def _effective_platforms(inline):
        return ["hh"]

    monkeypatch.setattr(service, "_with_platform_service", _with_platform_service)
    monkeypatch.setattr(service, "_effective_platforms", _effective_platforms)
    return service, client


def msg(text: str) -> dict:
    return {"update_id": 1, "message": {"chat": {"id": OWNER}, "text": text}}


async def test_queries_lists_them_in_plain_text_so_they_can_be_copied(bot) -> None:
    service, client = bot
    await service.handle(msg("/queries"))
    said = client.sent[-1]
    assert "1. Analytics Engineer" in said
    assert "\\" not in said, "escaped text is unreadable when copied out"


async def test_urls_by_index_searches_the_numbered_query(bot, monkeypatch) -> None:
    service, _client = bot
    seen: list[str] = []

    async def fake_rows(svc, platforms, query):
        seen.append(query)
        return []

    monkeypatch.setattr("careeros.modules.bot.service.build_search_rows", fake_rows)
    await service.handle(msg("/urls 2"))
    assert seen == ["Senior Data Engineer"]


async def test_urls_by_index_still_accepts_a_platform_list(bot, monkeypatch) -> None:
    service, _client = bot
    seen: list[tuple[str, object]] = []

    async def fake_rows(svc, platforms, query):
        seen.append((query, platforms))
        return []

    monkeypatch.setattr("careeros.modules.bot.service.build_search_rows", fake_rows)
    await service.handle(msg("/urls 1 hh,upwork"))
    assert seen and seen[0][0] == "Analytics Engineer"


async def test_an_out_of_range_index_says_how_many_there_are(bot) -> None:
    """`no such query` alone leaves the user guessing whether to try 3 or 30."""
    service, client = bot
    await service.handle(msg("/urls 9"))
    said = client.sent[-1]
    assert "9" in said and "2" in said


async def test_an_unquoted_word_still_gets_the_usage_line(bot) -> None:
    service, client = bot
    await service.handle(msg("/urls senior data engineer"))
    assert "usage" in client.sent[-1].lower()

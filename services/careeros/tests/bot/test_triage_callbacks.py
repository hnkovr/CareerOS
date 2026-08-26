"""Inline callbacks and the triage commands (GH #4).

Three properties are load-bearing:

* a tapped button is ANSWERED before the work starts — Telegram spins the button
  until `answerCallbackQuery` arrives and refuses the answer once the query ages
  out, so answering last hangs the UI on exactly the slow actions;
* callback data this bot did not produce is REJECTED, not ignored — the issue asks
  for it, and a dropped tap is indistinguishable from a dead bot;
* every transition goes through `opportunities.service`; the bot owns no state.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import SecretStr

from careeros.core.config import Settings
from careeros.modules.bot.callbacks import BadCallback, parse_callback
from careeros.modules.bot.formatting import ranked_list, short_id
from careeros.modules.bot.service import BotService

OWNER = 4242
OID = uuid.UUID("01a03aef-b36f-792d-b2d6-adf41a6963b6")
OTHER = uuid.UUID("01a03aef-0000-7000-8000-000000000001")  # same 8-char prefix


# ── parsing ───────────────────────────────────────────────────────────────────


def test_a_well_formed_payload_parses() -> None:
    action = parse_callback(f"o:analyze:{OID}")
    assert action.action == "analyze" and action.opportunity_id == OID


@pytest.mark.parametrize("verb", ["skip", "save", "analyze", "prompt"])
def test_every_button_the_keyboard_builds_parses_back(verb: str) -> None:
    assert parse_callback(f"o:{verb}:{OID}").action == verb


def test_empty_data_is_refused() -> None:
    with pytest.raises(BadCallback):
        parse_callback(None)


def test_a_foreign_namespace_is_refused() -> None:
    with pytest.raises(BadCallback, match="not a triage button"):
        parse_callback(f"x:skip:{OID}")


def test_an_unknown_action_names_the_known_ones() -> None:
    with pytest.raises(BadCallback) as exc:
        parse_callback(f"o:delete:{OID}")
    assert "analyze" in str(exc.value), "a rejection must say what is valid"


def test_a_malformed_id_is_refused_rather_than_passed_on() -> None:
    with pytest.raises(BadCallback, match="not an opportunity id"):
        parse_callback("o:skip:not-a-uuid")


def test_the_wrong_number_of_fields_is_refused() -> None:
    with pytest.raises(BadCallback):
        parse_callback("o:skip")


# ── fakes ─────────────────────────────────────────────────────────────────────


@dataclass
class FakeScore:
    overall: int = 80
    recommendation: Any = None
    dimensions: list[Any] = field(default_factory=list)


@dataclass
class FakeOpportunity:
    id: uuid.UUID = OID
    title: str = "Senior Data Engineer"
    company_name: str | None = "Northwind"
    score: FakeScore | None = field(default_factory=FakeScore)
    possible_duplicate_of: uuid.UUID | None = None
    status: str = "new"
    url: str | None = None
    analysis: Any = None


@dataclass
class FakeAnalysis:
    verdict: str = "apply"
    executive_summary: str = "Strong overlap with the target stack."
    strengths: list[str] = field(default_factory=lambda: ["dbt", "Dagster"])
    gaps: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    next_action: str = "tailor the core CV"


@dataclass
class FakeBundle:
    text: str = "PROMPT BODY — paste into any assistant"


class FakeOpportunityService:
    """Only the calls the bot is allowed to make (invariant 7)."""

    def __init__(self, log: list[str], items: list[FakeOpportunity] | None = None) -> None:
        self.log = log
        self.items = items if items is not None else [FakeOpportunity()]

    async def set_status(self, oid: uuid.UUID, status) -> FakeOpportunity:
        self.log.append(f"set_status:{status}")
        return FakeOpportunity(id=oid, status=str(status))

    async def analyze(self, oid: uuid.UUID) -> FakeOpportunity:
        self.log.append("analyze")
        return FakeOpportunity(id=oid, analysis=FakeAnalysis())

    async def external_prompt(self, oid: uuid.UUID, target: str) -> FakeBundle:
        self.log.append(f"external_prompt:{target}")
        return FakeBundle()

    async def list(self, *, status=None, min_score=None, limit=100) -> list[FakeOpportunity]:
        self.log.append(f"list:{status}")
        if status is not None:
            return [i for i in self.items if i.status == str(status)]
        return list(self.items)

    async def get(self, oid: uuid.UUID) -> FakeOpportunity:
        self.log.append("get")
        return next(i for i in self.items if i.id == oid)


class SpyClient:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.sent: list[str] = []
        self.answers: list[str] = []
        self.markups: list[Any] = []

    async def send_message(self, chat_id: int, text: str, **kw) -> dict:
        self.log.append("send_message")
        self.sent.append(text)
        self.markups.append(kw.get("reply_markup"))
        return {}

    async def answer_callback_query(self, callback_id: str, text: str = "") -> None:
        self.log.append("answer")
        self.answers.append(text)


@pytest.fixture
def bot(monkeypatch):
    log: list[str] = []
    settings = Settings(
        env="test",
        tg_enabled=True,
        tg_bot_token=SecretStr("1:a"),
        tg_webhook_secret=SecretStr("s"),
        tg_owner_chat_id=OWNER,
    )
    client = SpyClient(log)
    service = BotService(settings, client, sessionmaker=object())  # type: ignore[arg-type]
    opportunities = FakeOpportunityService(log)

    async def _with_opportunities(fn):
        return await fn(opportunities)

    monkeypatch.setattr(service, "_with_opportunities", _with_opportunities)
    return service, client, opportunities, log


def tap(action: str, oid: uuid.UUID = OID, data: str | None = None) -> dict:
    return {
        "update_id": 1,
        "callback_query": {
            "id": "cbq-1",
            "data": data if data is not None else f"o:{action}:{oid}",
            "message": {"chat": {"id": OWNER}},
        },
    }


def msg(text: str) -> dict:
    return {"update_id": 1, "message": {"chat": {"id": OWNER}, "text": text}}


# ── callbacks ─────────────────────────────────────────────────────────────────


async def test_the_button_is_answered_before_the_work_starts(bot) -> None:
    """Answering last leaves the button spinning through the whole AI call."""
    service, _client, _opps, log = bot
    await service.handle(tap("analyze"))
    assert log.index("answer") < log.index("analyze")


async def test_unknown_callback_data_is_answered_not_ignored(bot) -> None:
    service, client, _opps, log = bot
    await service.handle(tap("skip", data="o:delete:" + str(OID)))
    assert client.answers and "delete" in client.answers[0]
    assert "set_status" not in log and "analyze" not in log


async def test_a_foreign_payload_touches_no_service(bot) -> None:
    service, client, _opps, log = bot
    await service.handle(tap("skip", data="haxx"))
    assert client.answers, "the query must still be closed"
    assert not any(entry.startswith("set_status") for entry in log)


async def test_skip_sets_the_stored_status_to_ignored(bot) -> None:
    """Ignored rather than deleted: the dedup key must keep matching."""
    service, _client, _opps, log = bot
    await service.handle(tap("skip"))
    assert "set_status:ignored" in log


async def test_save_moves_it_to_watching(bot) -> None:
    service, _client, _opps, log = bot
    await service.handle(tap("save"))
    assert "set_status:watching" in log


async def test_analyze_sends_the_analysis_card(bot) -> None:
    service, client, _opps, _log = bot
    await service.handle(tap("analyze"))
    said = " ".join(client.sent)
    assert "apply" in said and "Strong overlap" in said


async def test_prompt_sends_the_bundle_unformatted(bot) -> None:
    """A bundle is meant to be copied into another assistant; escaping breaks that."""
    service, client, _opps, log = bot
    await service.handle(tap("prompt"))
    assert "external_prompt:generic" in log
    assert "PROMPT BODY — paste into any assistant" in client.sent[-1]
    assert "\\" not in client.sent[-1]


async def test_a_failing_transition_answers_the_chat(bot, monkeypatch) -> None:
    service, client, _opps, _log = bot

    async def _boom(fn):
        raise RuntimeError("database went away")

    monkeypatch.setattr(service, "_with_opportunities", _boom)
    await service.handle(tap("save"))
    assert client.answers, "the button is closed even when the work fails"
    assert "database went away" in client.sent[-1]


# ── commands ──────────────────────────────────────────────────────────────────


async def test_next_sends_a_card_with_its_buttons(bot) -> None:
    service, client, _opps, _log = bot
    await service.handle(msg("/next"))
    assert "Senior Data Engineer" in client.sent[-1]
    assert client.markups[-1] and "inline_keyboard" in client.markups[-1]


async def test_next_says_so_when_nothing_is_untriaged(bot) -> None:
    service, client, opps, _log = bot
    opps.items = [FakeOpportunity(status="watching")]
    await service.handle(msg("/next"))
    assert "nothing untriaged" in client.sent[-1]


async def test_top_ranks_by_score_not_by_arrival(bot) -> None:
    service, client, opps, _log = bot
    opps.items = [
        FakeOpportunity(id=uuid.uuid4(), title="low", score=FakeScore(40)),
        FakeOpportunity(id=uuid.uuid4(), title="high", score=FakeScore(95)),
    ]
    await service.handle(msg("/top"))
    said = client.sent[-1]
    assert said.index("high") < said.index("low")


async def test_top_honours_an_explicit_count(bot) -> None:
    service, client, opps, _log = bot
    opps.items = [
        FakeOpportunity(id=uuid.uuid4(), title=f"job {i}", score=FakeScore(90 - i))
        for i in range(6)
    ]
    await service.handle(msg("/top 2"))
    assert client.sent[-1].count("/opp_") == 2


async def test_top_defaults_to_five(bot) -> None:
    service, client, opps, _log = bot
    opps.items = [
        FakeOpportunity(id=uuid.uuid4(), title=f"job {i}", score=FakeScore(90 - i))
        for i in range(9)
    ]
    await service.handle(msg("/top"))
    assert client.sent[-1].count("/opp_") == 5


async def test_top_counts_the_unscored_rather_than_hiding_them(bot) -> None:
    """A list that silently drops rows reads as "this is everything"."""
    service, client, opps, _log = bot
    opps.items = [
        FakeOpportunity(id=uuid.uuid4(), title="scored"),
        FakeOpportunity(id=uuid.uuid4(), title="raw", score=None),
    ]
    await service.handle(msg("/top"))
    assert "1 not ranked" in client.sent[-1]


async def test_top_with_a_non_number_shows_usage(bot) -> None:
    service, client, _opps, _log = bot
    await service.handle(msg("/top lots"))
    assert "usage" in client.sent[-1].lower()


async def test_opp_resolves_the_short_handle(bot) -> None:
    service, client, _opps, _log = bot
    await service.handle(msg(f"/opp {short_id(OID)}"))
    assert "Senior Data Engineer" in client.sent[-1]


async def test_opp_resolves_the_full_uuid(bot) -> None:
    service, client, _opps, _log = bot
    await service.handle(msg(f"/opp {OID}"))
    assert "Senior Data Engineer" in client.sent[-1]


async def test_the_tappable_underscore_form_works(bot) -> None:
    """`/opp_ab12cd34` is what a phone user actually taps out of a /top listing."""
    service, client, _opps, _log = bot
    await service.handle(msg(f"/opp_{short_id(OID)}"))
    assert "Senior Data Engineer" in client.sent[-1]


async def test_an_ambiguous_handle_refuses_rather_than_guessing(bot) -> None:
    """Picking the first match would act on an opportunity nobody chose."""
    service, client, opps, _log = bot
    opps.items = [FakeOpportunity(id=OID), FakeOpportunity(id=OTHER, title="other")]
    await service.handle(msg(f"/opp {short_id(OID)}"))
    assert "add more characters" in client.sent[-1]


async def test_an_unknown_handle_says_so(bot) -> None:
    service, client, _opps, _log = bot
    await service.handle(msg("/opp ffffffff"))
    assert "no opportunity" in client.sent[-1]


async def test_opp_without_an_argument_shows_usage(bot) -> None:
    service, client, _opps, _log = bot
    await service.handle(msg("/opp"))
    assert "usage" in client.sent[-1].lower()


# ── listing ───────────────────────────────────────────────────────────────────


def test_the_listing_offers_a_tappable_command_per_row() -> None:
    rendered = ranked_list([FakeOpportunity()])
    assert f"/opp_{short_id(OID)}" in rendered


def test_an_empty_listing_says_what_to_do_next() -> None:
    assert "forward" in ranked_list([])

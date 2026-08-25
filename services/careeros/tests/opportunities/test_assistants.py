"""P3 assistants: interview frame + guard, negotiation frame + guard, comparison-ranking guard,
and the three endpoints with a fake provider."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient

from careeros.modules.opportunities.assistants import (
    guard_interview,
    guard_negotiation,
    interview_frame,
    negotiation_frame,
    ranking_problem,
)
from careeros.modules.opportunities.enums import (
    CompensationPeriod,
    ContractType,
    EmploymentType,
    OpportunityStatus,
    RemotePolicy,
    Seniority,
    Source,
)
from careeros.modules.opportunities.schemas import (
    CompareRankingOutput,
    Compensation,
    ExpectedQuestion,
    InterviewPrepOutput,
    LeveragePoint,
    NegotiationPlanOutput,
    OpportunityOut,
    RankedItem,
    Story,
)
from careeros.modules.vault.schema import CompensationTargets, VaultData
from careeros.modules.vault.service import Vault
from tests.conftest import DEMO_VAULT


@pytest.fixture(scope="module")
def data() -> VaultData:
    return Vault(DEMO_VAULT).require()


def _targets(data: VaultData) -> CompensationTargets:
    assert data.scoring is not None
    return data.scoring.compensation


def _evidenced_tech(data: VaultData) -> str:
    return next(a for a in data.achievements if a.technologies.all()).technologies.all()[0]


def _opp(**kw: Any) -> OpportunityOut:
    now = datetime.now(UTC)
    base: dict[str, Any] = dict(
        id=uuid.uuid4(),
        source=Source.direct,
        url=None,
        title="Senior Data Engineer",
        company_name="Orbital Analytics",
        contract_type=ContractType.b2b,
        employment_type=EmploymentType.full_time,
        location=None,
        remote_policy=RemotePolicy.remote_global,
        remote_regions=[],
        timezone_range=None,
        compensation=None,
        seniority=Seniority.senior,
        requirements=[],
        preferred=[],
        technologies=[],
        responsibilities=[],
        summary=None,
        red_flags=[],
        recruiter=None,
        received_at=now,
        deadline=None,
        status=OpportunityStatus.new,
        dedup_key="k",
        possible_duplicate_of=None,
        parse_confidence=0.9,
        parser="test",
        notes=None,
        created_at=now,
    )
    base.update(kw)
    return OpportunityOut(**base)


# ----------------------------------------------------------------------------- interview


def test_interview_frame_partitions_technologies(data: VaultData) -> None:
    tech = _evidenced_tech(data)
    frame = interview_frame(data, _opp(technologies=[tech, "COBOL"]), None)
    assert tech in frame.matched and "COBOL" in frame.missing
    assert not set(frame.matched) & set(frame.claimed_only)
    assert not set(frame.matched) & set(frame.missing)
    assert frame.materials and all(m.fact_id in data.fact_ids() for m in frame.materials)
    assert frame.materials[0].technologies  # best-evidenced first
    assert frame.track == "employment" and frame.stages[0] == "recruiter_screen"
    assert any("compensation range" in q for q in frame.questions_to_ask)
    assert any("COBOL" in q for q in frame.questions_to_ask)


def test_interview_frame_freelance_track(data: VaultData) -> None:
    frame = interview_frame(data, _opp(contract_type=ContractType.freelance), None)
    assert frame.track == "freelance" and "discovery" in frame.stages
    assert any("hourly rate" in q for q in frame.questions_to_ask)


def test_guard_interview_drops_unprovable_stories(data: VaultData) -> None:
    ach = next(a for a in data.achievements if a.facts)
    good = Story(
        title=ach.title,
        situation=ach.facts[0],
        action="Led the work.",
        result="Shipped.",
        derived_from=[ach.id],
    )
    unknown = Story(title="Ghost", situation="x", action="y", result="z", derived_from=["nope-1"])
    invented = Story(
        title="Big", situation="Cut cost by 9999%", action="y", result="z", derived_from=[ach.id]
    )
    plan = InterviewPrepOutput(
        stories=[good, unknown, invented],
        expected_questions=[
            ExpectedQuestion(question="Why us?", why="fit", answer_outline="Mission fit."),
            ExpectedQuestion(
                question="Scale?", why="probe", answer_outline="We moved 42 TB a day."
            ),
        ],
    )
    kept, rejected = guard_interview(plan, data)
    assert [s.title for s in kept.stories] == [ach.title]
    assert [q.question for q in kept.expected_questions] == ["Why us?"]
    assert len(rejected) == 3
    assert any("unknown fact ids" in r for r in rejected)
    assert any("9999%" in r for r in rejected)
    assert any("without citing" in r for r in rejected)


# ----------------------------------------------------------------------------- negotiation


def test_negotiation_frame_annual_below_target(data: VaultData) -> None:
    t = _targets(data)
    opp = _opp(
        compensation=Compensation(
            min=t.min_annual + 10000,
            max=t.target_annual - 20000,
            currency=t.currency,
            period=CompensationPeriod.year,
        )
    )
    frame = negotiation_frame(data, opp, [])
    assert frame.basis == "annual" and frame.position == "below_target"
    assert frame.target == t.target_annual and frame.floor == t.min_annual
    assert frame.anchor == t.target_annual  # nothing observed → the target anchors
    assert frame.gap_to_target_pct == round(20000 / t.target_annual * 100, 1)
    assert str(t.target_annual) in frame.allowed_numbers
    assert f"{t.target_annual / 1000:g}k" in frame.allowed_numbers
    assert frame.observed.n == 0 and "compensation not stated" not in frame.unknowns


def test_negotiation_frame_monthly_offer_and_stream_anchor(data: VaultData) -> None:
    t = _targets(data)
    opp = _opp(
        compensation=Compensation(
            max=(t.target_annual + 4000) / 12, currency=t.currency, period=CompensationPeriod.month
        )
    )
    hi = t.target_annual + 30000
    stream: list[dict[str, Any]] = [
        {
            "id": str(uuid.uuid4()),
            "compensation": {"max": v, "currency": t.currency, "period": "year"},
        }
        for v in (hi - 20000, hi - 10000, hi)
    ]
    stream.append(  # the opportunity itself is excluded from its own band
        {
            "id": str(opp.id),
            "compensation": {"max": 999999, "currency": t.currency, "period": "year"},
        }
    )
    stream.append(  # other basis → ignored
        {"id": "x", "compensation": {"max": 80, "currency": t.currency, "period": "hour"}}
    )
    stream.append({"id": "y", "compensation": None})
    frame = negotiation_frame(data, opp, stream)
    assert frame.basis == "annual" and frame.offered_max == t.target_annual + 4000
    assert frame.position == "at_target"
    assert frame.observed.n == 3 and frame.observed.p75 == hi - 5000
    assert frame.anchor == hi - 5000 and any("p75" in n for n in frame.notes)


def test_negotiation_frame_hourly_currency_and_missing(data: VaultData) -> None:
    t = _targets(data)
    frame = negotiation_frame(
        data,
        _opp(
            contract_type=ContractType.freelance,
            compensation=Compensation(
                min=t.min_hourly - 10,
                max=t.min_hourly - 5,
                currency=t.currency,
                period=CompensationPeriod.hour,
            ),
        ),
        [],
    )
    assert frame.basis == "hourly" and frame.position == "below_floor"
    assert frame.floor == t.min_hourly and frame.anchor == t.target_hourly

    foreign = _opp(
        compensation=Compensation(
            min=100000, max=120000, currency="XXX", period=CompensationPeriod.year
        )
    )
    frame = negotiation_frame(data, foreign, [])
    assert frame.position == "unknown" and any("no conversion" in u for u in frame.unknowns)

    frame = negotiation_frame(data, _opp(contract_type=ContractType.freelance), [])
    assert frame.basis == "hourly" and frame.position == "unknown"
    assert "compensation not stated" in frame.unknowns

    frame = negotiation_frame(
        data, _opp(compensation=Compensation(max=80, currency=t.currency)), []
    )
    assert frame.basis == "hourly" and any("no period" in n for n in frame.notes)


def test_guard_negotiation_rejects_foreign_numbers(data: VaultData) -> None:
    t = _targets(data)
    opp = _opp(
        technologies=[_evidenced_tech(data)],
        compensation=Compensation(
            min=100000, max=120000, currency=t.currency, period=CompensationPeriod.year
        ),
    )
    frame = negotiation_frame(data, opp, [])
    assert frame.leverage, "an evidenced technology must surface a leverage fact"
    lev = frame.leverage[0]
    target_k = f"{t.target_annual // 1000}k"
    plan = NegotiationPlanOutput(
        stance="counter",
        rationale=f"The offer tops out at 120k against a {t.target_annual} target.",
        counter_ask="Ask for 155000 base",
        leverage=[
            LeveragePoint(point=lev.title, derived_from=[lev.fact_id]),
            LeveragePoint(point="Saved 77 million last year", derived_from=[]),
        ],
        concessions=["Flexible on the start date"],
        script=[f"I am targeting {target_k}.", "Other offers are at 180k."],
        questions=["Is equity part of the package?"],
        risks=["Pushing 3 times may stall the process"],
    )
    kept, rejected = guard_negotiation(plan, frame, data)
    assert kept.rationale == plan.rationale and kept.counter_ask is None
    assert [lp.point for lp in kept.leverage] == [lev.title]
    assert kept.script == [f"I am targeting {target_k}."]
    assert kept.concessions == plan.concessions and kept.questions == plan.questions
    assert kept.risks == [] and len(rejected) == 4


# ----------------------------------------------------------------------------- compare


def test_ranking_problem() -> None:
    ids = [uuid.uuid4(), uuid.uuid4()]

    def out(pairs: list[tuple[uuid.UUID, int]]) -> CompareRankingOutput:
        return CompareRankingOutput(
            ranking=[RankedItem(opportunity_id=str(i), rank=r, rationale="r") for i, r in pairs],
            recommendation="x",
        )

    assert ranking_problem(out([(ids[1], 1), (ids[0], 2)]), ids) is None
    assert "exactly once" in (ranking_problem(out([(ids[0], 1), (ids[0], 2)]), ids) or "")
    assert "exactly once" in (ranking_problem(out([(ids[0], 1)]), ids) or "")
    assert "1..n" in (ranking_problem(out([(ids[0], 1), (ids[1], 3)]), ids) or "")


# ----------------------------------------------------------------------------- API

# distinct company/title from test_opportunities.py — the module-scoped DB is shared, and a
# near-duplicate here would flip that module's `possible_duplicate_of is None` assertion.
JD_A = """Head of Streaming Platform
Helios Grid — B2B contract, $120,000 - $140,000 per year. Fully remote, any timezone.
Requirements: Python, SQL, dbt, Dagster, ClickHouse, Docker.
"""
JD_B = """BI Developer
Nimbus Data — full-time employee, EUR 70,000 per year, remote within the EU.
Requirements: SQL, dbt, Airflow, Snowflake.
"""


@pytest.mark.db
async def test_assistant_endpoints_with_fake_provider(
    db_client: AsyncClient,
    settings,  # type: ignore[no-untyped-def]
    data: VaultData,
) -> None:
    from careeros.modules.ai.deps import get_provider_registry
    from careeros.modules.ai.providers.fake import FakeProvider

    r = await db_client.post("/api/opportunities/ingest", json={"source": "manual", "text": JD_A})
    assert r.status_code == 201, r.text
    a = r.json()
    r = await db_client.post("/api/opportunities/ingest", json={"source": "manual", "text": JD_B})
    assert r.status_code == 201, r.text
    b = r.json()
    ach = next(x for x in data.achievements if x.facts)
    ranking_ids: list[str] = [b["id"], a["id"]]

    def respond(req, schema):  # type: ignore[no-untyped-def]
        if schema is InterviewPrepOutput:
            return {
                "focus_areas": ["pipelines"],
                "stories": [
                    {
                        "title": ach.title,
                        "situation": ach.facts[0],
                        "action": "Led it.",
                        "result": "Done.",
                        "derived_from": [ach.id],
                    },
                    {
                        "title": "Ghost",
                        "situation": "s",
                        "action": "a",
                        "result": "r",
                        "derived_from": ["nope"],
                    },
                ],
                "plan": ["Re-read the stories"],
            }
        if schema is NegotiationPlanOutput:
            return {
                "stance": "counter",
                "rationale": "Below target.",
                "script": ["Ask for 140k.", "Mention 500k."],
            }
        if schema is CompareRankingOutput:
            return {
                "ranking": [
                    {"opportunity_id": i, "rank": n + 1, "rationale": "r"}
                    for n, i in enumerate(ranking_ids)
                ],
                "recommendation": "Go with the first.",
            }
        return {}

    get_provider_registry(settings).register(FakeProvider(respond), make_default=True)

    # deterministic frame only — needs no provider
    r = await db_client.post(f"/api/opportunities/{a['id']}/interview-prep", json={"use_ai": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan"] is None and body["ai_run_id"] is None
    assert body["frame"]["materials"] and set(body["frame"]["matched"]) <= set(a["technologies"])

    r = await db_client.post(f"/api/opportunities/{a['id']}/interview-prep", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [s["title"] for s in body["plan"]["stories"]] == [ach.title]
    assert (
        len(body["provenance_rejected"]) == 1
        and "unknown fact ids" in body["provenance_rejected"][0]
    )
    assert body["suggestion_id"] and body["ai_run_id"] and body["provider"] == "fake"

    r = await db_client.post(f"/api/opportunities/{a['id']}/negotiation", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["frame"]["basis"] == "annual" and body["frame"]["currency"] == "USD"
    assert body["frame"]["offered_max"] == 140000 and body["frame"]["position"] == "at_target"
    assert body["plan"]["script"] == ["Ask for 140k."] and len(body["provenance_rejected"]) == 1
    assert body["suggestion_id"]

    r = await db_client.post(
        "/api/opportunities/compare", json={"ids": [a["id"], b["id"]], "use_ai": True}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [x["opportunity_id"] for x in body["ranking"]] == ranking_ids
    assert body["recommendation"] and body["ai_run_id"] and body["ranking_note"] is None
    assert body["ranked"][0] == a["id"]  # deterministic order is never overwritten by the AI

    ranking_ids[:] = [a["id"], a["id"]]  # a broken ranking is dropped, not repaired
    r = await db_client.post(
        "/api/opportunities/compare", json={"ids": [a["id"], b["id"]], "use_ai": True}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ranking"] is None and "exactly once" in body["ranking_note"]

    r = await db_client.post(f"/api/opportunities/{uuid.uuid4()}/negotiation", json={})
    assert r.status_code == 404

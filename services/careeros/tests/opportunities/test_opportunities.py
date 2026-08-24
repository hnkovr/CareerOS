# ruff: noqa: E501
from __future__ import annotations

import pytest
from httpx import AsyncClient

from careeros.modules.cv.keywords import tech_vocabulary
from careeros.modules.opportunities.dedup import dedup_key, normalize_url, similarity
from careeros.modules.opportunities.enums import (
    ContractType,
    Recommendation,
    RemotePolicy,
    Seniority,
    Source,
)
from careeros.modules.opportunities.parser import merge_extractions, parse_compensation, parse_text
from careeros.modules.opportunities.schemas import OpportunityExtraction
from careeros.modules.opportunities.scoring import ScoringContext, score_opportunity
from careeros.modules.vault.enums import ScoreDimension
from careeros.modules.vault.schema import VaultData
from careeros.modules.vault.service import Vault
from tests.conftest import DEMO_VAULT

JD_STRONG = """Senior Data Engineer (Remote, worldwide)
Company: Orbital Analytics
We are a Series A startup building an AI-ready analytics platform. You will own our dbt + Dagster stack on BigQuery
and ClickHouse, and ship LLM-powered analytics features with the product team. Contractor (B2B) engagements welcome.

Requirements:
- 5+ years with Python and SQL
- dbt, Dagster, BigQuery
- ClickHouse or similar OLAP
- CI/CD for data (GitLab CI or GitHub Actions)

Nice to have:
- Terraform
- Kafka

Compensation: $110k - $150k per year. Fully remote, async, 3 hours overlap with CET.
"""

JD_WEAK = """Junior Data Analyst
Hybrid role in Austin, TX. US citizens only. Must be located in the US.
You will build Tableau dashboards and write SQL. Salesforce experience a plus. Competitive salary.
Take-home assignment and 5 rounds of interviews.
"""

JD_RECRUITER = """Hi Dana, I'm reaching out about a Lead Data Platform Engineer role at Meridian Payments (remote EU).
Stack: Snowflake, Airflow, Spark, AWS, Kafka, dbt. Rate up to €95/hour on a B2B contract. Interested?
Best, Jane Doe — jane.doe@example-recruiting.com
"""


@pytest.fixture(scope="module")
def data() -> VaultData:
    return Vault(DEMO_VAULT).require()


@pytest.fixture(scope="module")
def ctx(data: VaultData) -> ScoringContext:
    return ScoringContext.build(data)


# ----------------------------------------------------------------------------- parser


def test_parse_strong_jd(data: VaultData) -> None:
    ex = parse_text(JD_STRONG, tech_vocabulary(data)).extraction
    assert ex.title == "Senior Data Engineer (Remote, worldwide)"
    assert ex.company == "Orbital Analytics"
    assert ex.remote_policy == RemotePolicy.remote_global
    assert ex.contract_type == ContractType.b2b and ex.seniority == Seniority.senior
    assert {
        "Python",
        "SQL",
        "dbt",
        "Dagster",
        "BigQuery",
        "ClickHouse",
        "GitLab CI",
        "Terraform",
        "Kafka",
    } <= set(ex.technologies)
    assert "dbt, Dagster, BigQuery" in ex.requirements and "Terraform" in ex.preferred
    assert ex.compensation and ex.compensation.min == 110_000 and ex.compensation.max == 150_000
    assert ex.compensation.currency == "USD" and str(ex.compensation.period) == "year"
    assert "3h overlap" in (ex.timezone_range or "")
    assert "company not identified" not in ex.red_flags


def test_parse_weak_jd(data: VaultData) -> None:
    ex = parse_text(JD_WEAK, tech_vocabulary(data)).extraction
    assert ex.seniority == Seniority.junior
    assert ex.remote_policy == RemotePolicy.hybrid and "US" in ex.remote_regions
    assert ex.compensation is None
    assert (
        "compensation not disclosed" in ex.red_flags and "compensation not stated" in ex.red_flags
    )


def test_parse_recruiter_message(data: VaultData) -> None:
    ex = parse_text(JD_RECRUITER, tech_vocabulary(data)).extraction
    assert ex.recruiter and ex.recruiter.email == "jane.doe@example-recruiting.com"
    assert (
        ex.compensation
        and ex.compensation.currency == "EUR"
        and str(ex.compensation.period) == "hour"
        and ex.compensation.max == 95
    )
    assert ex.seniority == Seniority.lead and ex.contract_type == ContractType.b2b
    assert ex.remote_policy == RemotePolicy.remote_region and "EU" in ex.remote_regions


@pytest.mark.parametrize(
    ("text", "lo", "hi", "cur", "period"),
    [
        ("$120,000 - $150,000 / year", 120000, 150000, "USD", "year"),
        ("€80-100/h", 80, 100, "EUR", "hour"),
        ("up to 95 USD per hour", None, 95, "USD", "hour"),
        ("20k-25k PLN/month", 20000, 25000, "PLN", "month"),
        ("£75k", 75000, None, "GBP", "year"),
    ],
)
def test_parse_compensation(
    text: str, lo: float | None, hi: float | None, cur: str, period: str
) -> None:
    comp = parse_compensation(text)
    assert comp is not None, text
    assert comp.min == lo and comp.max == hi and comp.currency == cur and str(comp.period) == period


def test_merge_extractions_prefers_base_and_unions_lists() -> None:
    base = OpportunityExtraction(
        title="A", technologies=["dbt"], remote_policy=RemotePolicy.unknown
    )
    ai = OpportunityExtraction(
        title="B",
        company="Acme",
        technologies=["dbt", "Airflow"],
        remote_policy=RemotePolicy.hybrid,
    )
    merged = merge_extractions(base, ai)
    assert merged.title == "A" and merged.company == "Acme"
    assert merged.technologies == ["dbt", "Airflow"] and merged.remote_policy == RemotePolicy.hybrid


# ----------------------------------------------------------------------------- scoring


def _score(ctx: ScoringContext, data: VaultData, text: str, source: Source = Source.manual):
    ex = parse_text(text, tech_vocabulary(data)).extraction
    return score_opportunity(ctx, ex, source=source, text=text, vault_sha="sha")


def test_strong_jd_scores_high(ctx: ScoringContext, data: VaultData) -> None:
    s = _score(ctx, data, JD_STRONG)
    dims = {d.name: d for d in s.dimensions}
    assert s.overall >= 75, s
    assert dims[ScoreDimension.technical_fit].score >= 90
    assert dims[ScoreDimension.remote_us_fit].score == 100
    assert dims[ScoreDimension.compensation_fit].score >= 95
    assert dims[ScoreDimension.startup_fit].score >= 60
    assert s.recommendation in (Recommendation.apply, Recommendation.high_priority)
    assert abs(sum(d.weight for d in s.dimensions) - 1.0) < 0.01
    assert any("dbt" in sg for sg in dims[ScoreDimension.technical_fit].signals)


def test_weak_jd_scores_low_and_is_ignored(ctx: ScoringContext, data: VaultData) -> None:
    s = _score(ctx, data, JD_WEAK)
    dims = {d.name: d for d in s.dimensions}
    assert dims[ScoreDimension.remote_us_fit].score == 0
    assert dims[ScoreDimension.seniority_fit].score <= 20
    assert dims[ScoreDimension.application_effort].score <= 60
    assert s.recommendation in (Recommendation.ignore, Recommendation.watch)
    assert (
        any("not remote-compatible" in r for r in s.reasons)
        or s.recommendation == Recommendation.ignore
    )


def test_recruiter_inbound_becomes_reply_now(ctx: ScoringContext, data: VaultData) -> None:
    s = _score(ctx, data, JD_RECRUITER, source=Source.recruiter)
    assert s.overall >= 65
    assert s.recommendation == Recommendation.reply_now


def test_unknown_remote_triggers_ask_first(ctx: ScoringContext, data: VaultData) -> None:
    text = JD_STRONG.replace("(Remote, worldwide)", "").replace(
        "Fully remote, async, 3 hours overlap with CET.", ""
    )
    s = _score(ctx, data, text)
    if s.recommendation not in (Recommendation.ignore, Recommendation.watch):
        assert s.recommendation == Recommendation.ask_questions_first


def test_low_compensation_with_strong_fit_negotiates(ctx: ScoringContext, data: VaultData) -> None:
    text = JD_STRONG.replace("$110k - $150k per year", "$55k per year")
    s = _score(ctx, data, text)
    assert s.recommendation in (Recommendation.negotiate, Recommendation.watch)


# ----------------------------------------------------------------------------- dedup


def test_dedup_helpers() -> None:
    a = normalize_url("https://www.example.com/jobs/123/?utm_source=li&ref=x")
    b = normalize_url("https://example.com/jobs/123")
    assert a == b
    assert dedup_key(
        url="https://example.com/jobs/123?utm_campaign=z", title=None, company=None, raw_text=""
    ) == dedup_key(url=b, title=None, company=None, raw_text="")
    assert dedup_key(url=None, title="Senior DE", company="Acme", raw_text="x") == dedup_key(
        url=None, title="senior de", company="ACME", raw_text="y"
    )
    assert (
        similarity("Senior Data Engineer", "Acme", "Senior Data Engineer (Remote)", "Acme Inc") > 85
    )


# ----------------------------------------------------------------------------- service + API (db)


@pytest.mark.db
async def test_ingest_score_analyze_compare_api(db_client: AsyncClient) -> None:
    r1 = await db_client.post(
        "/api/opportunities/ingest", json={"source": "manual", "text": JD_STRONG}
    )
    assert r1.status_code == 201, r1.text
    o1 = r1.json()
    assert o1["title"].startswith("Senior Data Engineer") and o1["score"]["overall"] >= 75
    assert o1["score"]["recommendation"] in ("apply", "high_priority")
    assert o1["possible_duplicate_of"] is None and o1["raw_text"] == JD_STRONG

    r_dup = await db_client.post(
        "/api/opportunities/ingest",
        json={
            "source": "linkedin",
            "text": JD_STRONG,
            "url": "https://jobs.example.com/1?utm_source=x",
        },
    )
    assert r_dup.status_code == 201 and r_dup.json()["possible_duplicate_of"] == o1["id"]

    r2 = await db_client.post(
        "/api/opportunities/ingest", json={"source": "recruiter", "text": JD_RECRUITER}
    )
    o2 = r2.json()
    assert o2["score"]["recommendation"] == "reply_now" and o2["recruiter"]["email"].endswith(
        "example-recruiting.com"
    )

    r3 = await db_client.post(
        "/api/opportunities/ingest", json={"source": "manual", "text": JD_WEAK}
    )
    o3 = r3.json()
    assert o3["score"]["overall"] < o1["score"]["overall"]

    r = await db_client.get("/api/opportunities", params={"min_score": 60})
    assert (
        r.status_code == 200
        and {o["id"] for o in r.json()} >= {o1["id"], o2["id"]}
        and o3["id"] not in {o["id"] for o in r.json()}
    )

    r = await db_client.post(
        "/api/opportunities/compare", json={"ids": [o1["id"], o2["id"], o3["id"]]}
    )
    assert (
        r.status_code == 200
        and r.json()["ranked"][-1] == o3["id"]
        and "technical_fit" in r.json()["dimension_names"]
    )

    r = await db_client.post(
        f"/api/opportunities/{o1['id']}/external-prompt", json={"target": "chatgpt"}
    )
    assert (
        r.status_code == 200
        and "12. Apply / skip recommendation" in r.json()["text"]
        and JD_STRONG[:40] in r.json()["text"]
    )

    r = await db_client.patch(f"/api/opportunities/{o3['id']}/status", json={"status": "ignored"})
    assert r.status_code == 200 and r.json()["status"] == "ignored"

    r = await db_client.post(f"/api/opportunities/{o1['id']}/rescore")
    assert r.status_code == 200 and r.json()["score"]["overall"] == o1["score"]["overall"]

    r = await db_client.get("/api/opportunities/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


@pytest.mark.db
async def test_analyze_with_fake_provider(db_client: AsyncClient, settings) -> None:  # type: ignore[no-untyped-def]
    from careeros.modules.ai.deps import get_provider_registry
    from careeros.modules.ai.providers.fake import FakeProvider
    from careeros.modules.opportunities.schemas import OpportunityAnalysisOutput

    def respond(req, schema):  # type: ignore[no-untyped-def]
        if schema is OpportunityAnalysisOutput:
            return {
                "verdict": "apply",
                "executive_summary": "Strong fit.",
                "strengths": ["dbt", "Dagster"],
                "gaps": ["Kafka secondary"],
                "risks": [],
                "recommended_cv_variant": "does-not-exist",
                "next_action": "Apply with the startup CV.",
            }
        if schema is OpportunityExtraction:
            return {"title": "AI title", "company": "Orbital Analytics", "technologies": ["Looker"]}
        return {}

    get_provider_registry(settings).register(FakeProvider(respond), make_default=True)
    r = await db_client.post(
        "/api/opportunities/ingest", json={"source": "manual", "text": JD_STRONG, "use_ai": True}
    )
    assert r.status_code == 201
    o = r.json()
    assert (
        o["parser"].endswith("+ai") and "Looker" in o["technologies"] and o["title"] != "AI title"
    )  # heuristics win on title
    r = await db_client.post(f"/api/opportunities/{o['id']}/analyze", json={})
    assert r.status_code == 200, r.text
    a = r.json()["analysis"]
    assert (
        a["verdict"] == "apply" and a["recommended_cv_variant"] == "general-core"
    )  # unknown variant → default
    assert a["provider"] == "fake" and a["ai_run_id"]

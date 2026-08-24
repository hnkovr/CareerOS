# ruff: noqa: E501
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from careeros.modules.profiles.audit import audit_snapshot, category_scores, health_score
from careeros.modules.profiles.enums import AuditCategory, Severity
from careeros.modules.profiles.schemas import SnapshotExperienceItem, SnapshotIn
from careeros.modules.vault.enums import Platform
from careeros.modules.vault.schema import VaultData
from careeros.modules.vault.service import Vault
from tests.conftest import DEMO_VAULT

NOW = datetime(2026, 8, 24, tzinfo=UTC)

GOOD_LINKEDIN = SnapshotIn(
    platform=Platform.linkedin,
    headline="Senior Data Engineer / Analytics Engineer | GCP, BigQuery, Snowflake, dbt, Airflow, Dagster, Python, SQL, Databricks, Kafka, ClickHouse, LLMOps",
    about=(
        "Senior Data Engineer with 12+ years building analytics platforms. I design dbt + Dagster "
        "platforms on BigQuery and ClickHouse with CI/CD, cutting pipeline times from 34 to 9 minutes "
        "and infrastructure cost by 45%. Remote-friendly (B2B contractor, UTC+4, 3h US overlap). "
        "Based in Tbilisi. Featured: Agentic Market Intelligence Platform, dbt-cost-guard. "
        "Certified: Professional Data Engineer, dbt Analytics Engineering Certification. Message me to talk data platforms."
    ),
    experience=[
        SnapshotExperienceItem(
            company="Northwind Commerce",
            title="Senior Data Engineer",
            period="2023-now",
            description="dbt, Dagster, ClickHouse platform; GitLab CI; 40+ consumers; p95 600 ms",
        ),
        SnapshotExperienceItem(
            company="Lumen Analytics",
            title="Lead Analytics Engineer",
            period="2020-2023",
            description="BigQuery cost -38%, GA4 deliveries",
        ),
    ],
    skills=[
        "Python",
        "SQL",
        "dbt",
        "Dagster",
        "ClickHouse",
        "BigQuery",
        "GitLab CI",
        "Docker",
        "Airflow",
        "Snowflake",
        "Databricks",
        "Kafka",
        "Spark",
        "Terraform",
        "LLMOps",
        "Claude Code",
    ],
    projects=[{"name": "Agentic Market Intelligence Platform"}],
    portfolio=[{"name": "dbt-cost-guard"}],
    captured_at=NOW - timedelta(days=3),
)

STALE_UPWORK = SnapshotIn(
    platform=Platform.upwork,
    headline="Data engineer and analytics consultant helping companies with data things and dashboards for business",
    about="I am a passionate data engineer. I love working with data and building pipelines. I can help you with your data needs.",
    experience=[
        SnapshotExperienceItem(company="Orbit Fintech", title="Data Engineer", period="2017-2020")
    ],
    skills=["Python", "SQL", "Tableau"],
    captured_at=NOW - timedelta(days=200),
)


@pytest.fixture(scope="module")
def data() -> VaultData:
    return Vault(DEMO_VAULT).require()


def test_good_snapshot_scores_high(data: VaultData) -> None:
    findings = audit_snapshot(data, GOOD_LINKEDIN, now=NOW)
    health = health_score(category_scores(findings), findings)
    assert health >= 85, [f"{f.category}: {f.problem}" for f in findings]
    assert not any(f.severity in (Severity.critical, Severity.high) for f in findings), [
        f"{f.severity} {f.category}: {f.problem}"
        for f in findings
        if f.severity in (Severity.critical, Severity.high)
    ]


def test_stale_upwork_snapshot_finds_the_problems(data: VaultData) -> None:
    findings = audit_snapshot(data, STALE_UPWORK, now=NOW)
    cats = {f.category for f in findings}
    by_cat = {f.category: f for f in findings}
    assert AuditCategory.freshness in cats  # 200-day-old snapshot
    critical = [f for f in findings if f.severity == Severity.critical]
    assert any("Northwind Commerce" in f.problem for f in critical)  # current employer missing
    assert critical[0].source_fact_ids == ["exp_northwind"]
    assert AuditCategory.keyword_coverage in cats  # dbt/BigQuery/... absent
    assert AuditCategory.channel_fit in cats  # headline over upwork's 70-char limit
    assert AuditCategory.call_to_action in cats  # no CTA on upwork
    assert AuditCategory.consistency in cats  # Tableau not in vault
    assert "Tableau" in by_cat[AuditCategory.consistency].problem
    assert AuditCategory.proof_metrics in cats  # no numbers anywhere
    assert AuditCategory.compensation_positioning in cats  # no rate on upwork
    assert AuditCategory.positioning_fit in cats  # first-priority skills invisible
    health = health_score(category_scores(findings), findings)
    assert health < 60


def test_years_of_experience_contradiction(data: VaultData) -> None:
    about = GOOD_LINKEDIN.about
    assert about is not None
    snap = GOOD_LINKEDIN.model_copy(update={"about": about.replace("12+ years", "20 years")})
    findings = audit_snapshot(data, snap, now=NOW)
    consistency = [f for f in findings if f.category == AuditCategory.consistency]
    assert any("20 years" in f.problem and "~12" in f.problem for f in consistency)


def test_health_score_is_deterministic(data: VaultData) -> None:
    fa = audit_snapshot(data, STALE_UPWORK, now=NOW)
    fb = audit_snapshot(data, STALE_UPWORK, now=NOW)
    a = health_score(category_scores(fa), fa)
    b = health_score(category_scores(fb), fb)
    assert a == b


@pytest.mark.db
async def test_profiles_api_snapshot_audit_health(db_client: AsyncClient, settings) -> None:  # type: ignore[no-untyped-def]
    from careeros.modules.ai.deps import get_provider_registry
    from careeros.modules.ai.providers.fake import FakeProvider
    from careeros.modules.profiles.schemas import ProfileAuditOutput

    r = await db_client.post("/api/profiles/snapshots", json=GOOD_LINKEDIN.model_dump(mode="json"))
    assert r.status_code == 201, r.text
    snap_li = r.json()
    r = await db_client.post("/api/profiles/snapshots", json=STALE_UPWORK.model_dump(mode="json"))
    snap_uw = r.json()

    r = await db_client.post(
        f"/api/profiles/snapshots/{snap_uw['id']}/audit", json={"use_ai": False}
    )
    assert r.status_code == 200, r.text
    audit = r.json()
    assert audit["health_score"] < 60 and audit["engine_version"] == "audit-v1"
    assert audit["findings"][0]["severity"] == "critical"  # sorted most severe first
    assert all(f["origin"] == "deterministic" for f in audit["findings"])

    # AI audit merges validated findings; unknown fact ids are stripped and confidence capped
    def respond(req, schema):  # type: ignore[no-untyped-def]
        assert schema is ProfileAuditOutput
        return {
            "findings": [
                {
                    "category": "positioning_fit",
                    "severity": "high",
                    "problem": "Headline undersells agentic work",
                    "why_it_matters": "Strategic positioning invisible",
                    "suggested_change": "Mention AI-ready platforms",
                    "source_fact_ids": ["ach_northwind_ai_dev", "ach_made_up"],
                    "confidence": 0.9,
                },
            ],
            "headline_suggestion": "Senior Data Engineer — AI-ready analytics platforms (dbt, Dagster, ClickHouse)",
        }

    get_provider_registry(settings).register(FakeProvider(respond), make_default=True)
    r = await db_client.post(
        f"/api/profiles/snapshots/{snap_li['id']}/audit", json={"use_ai": True}
    )
    assert r.status_code == 200, r.text
    audit_ai = r.json()
    assert audit_ai["ai_used"] and audit_ai["headline_suggestion"].startswith(
        "Senior Data Engineer"
    )
    ai_findings = [f for f in audit_ai["findings"] if f["origin"] == "ai"]
    assert len(ai_findings) == 1
    assert ai_findings[0]["source_fact_ids"] == ["ach_northwind_ai_dev"]
    assert ai_findings[0]["confidence"] <= 0.4 and "ach_made_up" in ai_findings[0]["problem"]

    r = await db_client.get("/api/profiles/health")
    assert r.status_code == 200
    health = {h["platform"]: h for h in r.json()}
    assert health["upwork"]["health_score"] == audit["health_score"]
    assert health["linkedin"]["open_findings"] >= 1
    assert health["toptal"]["snapshot_id"] is None

    finding_id = audit["findings"][0]["id"]
    r = await db_client.patch(
        f"/api/profiles/findings/{finding_id}", json={"resolution": "applied"}
    )
    assert r.status_code == 200 and r.json()["resolution"] == "applied"

    r = await db_client.get("/api/profiles/snapshots", params={"platform": "upwork"})
    assert r.status_code == 200 and r.json()[0]["latest_health_score"] == audit["health_score"]

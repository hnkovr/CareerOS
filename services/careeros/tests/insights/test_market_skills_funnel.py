# ruff: noqa: E501
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from careeros.modules.insights.funnel import compute_funnel
from careeros.modules.insights.market import compute_market
from careeros.modules.insights.skills_gap import SkillStatus, compute_skills_gap
from careeros.modules.vault.schema import VaultData
from careeros.modules.vault.service import Vault
from tests.conftest import DEMO_VAULT

NOW = datetime(2026, 8, 26, tzinfo=UTC)


@pytest.fixture(scope="module")
def data() -> VaultData:
    return Vault(DEMO_VAULT).require()


def _row(techs: list[str], score: int | None = 70, **kw) -> dict:  # type: ignore[no-untyped-def]
    base = {
        "id": "x",
        "title": "t",
        "company": "c",
        "source": "manual",
        "status": "new",
        "received_at": NOW - timedelta(days=5),
        "technologies": techs,
        "remote_policy": "remote_global",
        "remote_regions": [],
        "contract_type": "b2b",
        "seniority": "senior",
        "compensation": None,
        "score": score,
        "recommendation": "apply",
    }
    base.update(kw)
    return base


STREAM = [
    _row(
        ["dbt", "BigQuery", "Python", "Airflow"],
        82,
        compensation={"min": 120000, "max": 150000, "currency": "USD", "period": "year"},
    ),
    _row(
        ["dbt", "Snowflake", "Airflow", "Kafka"],
        66,
        compensation={"min": 110000, "max": 130000, "currency": "USD", "period": "year"},
    ),
    _row(
        ["dbt", "Databricks", "Spark", "Kafka"],
        58,
        compensation={"max": 95, "currency": "EUR", "period": "hour"},
        contract_type="freelance",
    ),
    _row(
        ["Python", "Kafka", "Flink"],
        40,
        remote_policy="onsite",
        received_at=NOW - timedelta(days=200),
    ),
    _row(
        ["ClickHouse", "dbt", "Dagster", "Kafka"],
        90,
        compensation={"max": 85, "currency": "EUR", "period": "hour"},
    ),
]


def test_market_intelligence_windows_and_aggregates(data: VaultData) -> None:
    out = compute_market(data, STREAM, window_days=90, now=NOW)
    assert out.sample_size == 4  # the 200-day-old row is outside the window
    assert out.disclaimer.startswith("Based on your observed")
    top = {t.technology: t for t in out.technologies}
    assert top["dbt"].count == 4 and top["dbt"].share == 1.0
    assert top["Kafka"].count == 3 and "market_core" in top["Kafka"].market_groups
    assert any(set(c.technologies) == {"Kafka", "dbt"} for c in out.combos)
    assert out.remote_policy["remote_global"] == 4
    assert out.contract_type == {"b2b": 3, "freelance": 1}
    comp = {(c.kind, c.currency): c for c in out.compensation}
    assert comp[("annual", "USD")].n == 2 and comp[("annual", "USD")].median == 140000
    assert comp[("hourly", "EUR")].median == 90
    assert out.avg_score == 74.0
    everything = compute_market(data, STREAM, window_days=365, now=NOW)
    assert everything.sample_size == 5 and everything.remote_policy["onsite"] == 1


def test_skills_gap_statuses_and_portfolio(data: VaultData) -> None:
    out = compute_skills_gap(data, STREAM)
    by = {i.technology: i for i in out.items}
    assert (
        by["dbt"].status == SkillStatus.evidenced and by["dbt"].demand == 4 and by["dbt"].evidence
    )
    assert by["Kafka"].status == SkillStatus.evidenced  # backed by orbit achievements
    assert by["Flink"].status == SkillStatus.missing and by["Flink"].demand == 1
    assert by["Databricks"].status == SkillStatus.worth_learning  # target tier, working level
    assert by["Docker"].demand == 0 and by["Docker"].status in (
        SkillStatus.claimed,
        SkillStatus.evidenced,
    )
    assert out.counts["evidenced"] >= 5
    assert out.sample_size == 5
    techs = [p.technology for p in out.portfolio]
    assert "Databricks" in techs
    db = next(p for p in out.portfolio if p.technology == "Databricks")
    assert db.gap == SkillStatus.worth_learning and db.project_id == "proj_agentic_market_intel"
    assert "Databricks" in db.suggested_proof and db.estimated_roi in ("high", "medium", "low")
    assert out.portfolio == sorted(
        out.portfolio,
        key=lambda p: ({"high": 0, "medium": 1, "low": 2}[p.estimated_roi], -p.demand),
    )


def test_funnel_rates() -> None:
    t0 = NOW - timedelta(days=10)
    rows = [
        {
            "id": "a",
            "kind": "employment",
            "stage": "technical",
            "applied_at": t0,
            "closed_at": None,
            "created_at": t0,
            "events": [
                {"kind": "applied", "at": t0},
                {"kind": "message_received", "at": t0 + timedelta(days=3)},
            ],
            "interviews": [{"kind": "technical", "outcome": "pending"}],
        },
        {
            "id": "b",
            "kind": "employment",
            "stage": "applied",
            "applied_at": t0,
            "closed_at": None,
            "created_at": t0,
            "events": [],
            "interviews": [],
        },
        {
            "id": "c",
            "kind": "employment",
            "stage": "rejected",
            "applied_at": t0,
            "closed_at": NOW,
            "created_at": t0,
            "events": [{"kind": "message_received", "at": t0 + timedelta(days=1)}],
            "interviews": [],
        },
        {
            "id": "d",
            "kind": "freelance",
            "stage": "lead",
            "applied_at": None,
            "closed_at": None,
            "created_at": t0,
            "events": [],
            "interviews": [],
        },
        {
            "id": "e",
            "kind": "employment",
            "stage": "offer",
            "applied_at": t0,
            "closed_at": None,
            "created_at": t0,
            "events": [
                {"kind": "message_received", "at": t0 + timedelta(days=5)},
                {"kind": "offer", "at": NOW},
            ],
            "interviews": [{"kind": "final", "outcome": "passed"}],
        },
    ]
    out = compute_funnel(rows)
    assert out.applications_total == 5 and out.active == 4
    assert out.by_kind == {"employment": 4, "freelance": 1}
    assert out.applied == 4 and out.with_response == 3 and out.response_rate == 0.75
    assert out.interviews == 2 and out.interview_rate == 0.5
    assert out.offers == 1 and out.offer_rate == 0.25 and out.rejected == 1
    assert out.median_days_to_first_response == 3.0
    assert out.events_by_kind["message_received"] == 3


@pytest.mark.db
async def test_insights_api(db_client: AsyncClient) -> None:
    r = await db_client.post(
        "/api/opportunities/ingest",
        json={
            "source": "manual",
            "text": "Insights API Engineer at Prism Data (remote worldwide).\nRequirements:\n- dbt, BigQuery, DataHub\nB2B, $140k per year.",
        },
    )
    assert r.status_code == 201
    r = await db_client.get("/api/insights/market", params={"window_days": 30})
    assert r.status_code == 200 and r.json()["sample_size"] >= 1 and r.json()["window_days"] == 30
    assert any(t["technology"] == "dbt" for t in r.json()["technologies"])
    r = await db_client.get("/api/insights/skills-gap")
    assert r.status_code == 200
    body = r.json()
    # DataHub is in the scoring vocabulary but not a vault skill → a gap. Unknown words never
    # reach `technologies`: the parser extracts only vault-vocabulary technologies.
    assert any(
        i["technology"].lower() == "datahub" and i["status"] in ("missing", "worth_learning")
        for i in body["items"]
    )
    assert body["counts"]
    r = await db_client.get("/api/insights/funnel")
    assert r.status_code == 200 and "response_rate" in r.json()

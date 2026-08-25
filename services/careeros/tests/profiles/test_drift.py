# ruff: noqa: E501
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from careeros.modules.profiles.drift import Snap, detect_drift
from careeros.modules.vault.schema import VaultData
from careeros.modules.vault.service import Vault
from tests.conftest import DEMO_VAULT

NOW = datetime(2026, 8, 26, tzinfo=UTC)


@pytest.fixture(scope="module")
def data() -> VaultData:
    return Vault(DEMO_VAULT).require()


def _snap(
    platform: str,
    headline: str = "",
    about: str = "",
    text: str = "",
    skills: list[str] | None = None,
    rates: dict | None = None,
) -> Snap:
    return Snap(
        platform=platform,
        headline=headline,
        about=about,
        text=f"{headline} {about} {text}",
        skills=skills or [],
        rates=rates or {},
    )


def test_detects_years_headline_rate_employer_drift(data: VaultData) -> None:
    snaps = [
        _snap(
            "linkedin",
            headline="Senior Data Engineer | dbt, Dagster, ClickHouse, Snowflake",
            about="12+ years building data platforms. Based in Tbilisi.",
            text="Northwind Commerce Senior Data Engineer",
            skills=["dbt", "Dagster", "ClickHouse", "Python"],
        ),
        _snap(
            "wellfound",
            headline="Data engineer | dbt, BigQuery",
            about="10 years of experience",
            text="Lumen Analytics",
            skills=["dbt", "Python"],
        ),
        _snap(
            "upwork",
            headline="dbt consultant",
            about="Data platforms, BigQuery, ClickHouse",
            rates={"hourly": 60, "currency": "USD"},
        ),
        _snap(
            "toptal",
            headline="Senior Data Engineer",
            about="ClickHouse and Dagster",
            rates={"hourly": 95},
        ),
    ]
    drafts = detect_drift(data, snaps, now=NOW)
    fields = {d.field for d in drafts}
    assert {
        "years_experience",
        "headline_technology",
        "rate",
        "current_employer",
        "location",
        "skills",
    } <= fields
    years = [d for d in drafts if d.field == "years_experience"]
    assert any(
        d.platform_a == "linkedin" and d.platform_b == "wellfound" for d in years
    )  # 12 vs 10
    assert not any(
        d.platform_a == "linkedin" and d.platform_b == "vault" for d in years
    )  # 12 ≈ vault (2026-2014)
    assert any(d.platform_a == "wellfound" and d.platform_b == "vault" for d in years)
    # Snowflake is a vault skill (target tier) → not flagged as unknown; Dagster present on linkedin but absent on wellfound → flagged
    assert not any(d.field == "headline_technology" and d.value_a == "Snowflake" for d in drafts)
    assert any(
        d.field == "headline_technology" and d.platform_a == "wellfound" and "Dagster" in d.value_a
        for d in drafts
    )
    rate = [d for d in drafts if d.field == "rate"]
    assert any(
        (d.platform_a == "toptal"
        and d.platform_b == "upwork")
        or (d.platform_a == "upwork"
        and d.platform_b == "toptal")
        for d in rate
    )  # 60 vs 95
    assert any(
        d.platform_b == "vault" and d.platform_a == "upwork" for d in rate
    )  # 60 vs target 95
    employer = [d for d in drafts if d.field == "current_employer"]
    assert {d.platform_a for d in employer} >= {"wellfound", "upwork", "toptal"}
    assert not any(d.platform_a == "linkedin" for d in employer)
    keys = [d.key for d in drafts]
    assert len(keys) == len(set(keys))


def test_consistent_profiles_produce_no_drift(data: VaultData) -> None:
    about = "12+ years. Tbilisi. Currently Senior Data Engineer at Northwind Commerce."
    snaps = [
        _snap(
            "linkedin",
            headline="Senior Data Engineer | dbt, Dagster",
            about=about,
            skills=[
                "dbt",
                "Dagster",
                "ClickHouse",
                "GitLab CI",
                "Docker",
                "Claude Code",
                "Python",
                "SQL",
            ],
        ),
        _snap(
            "wellfound",
            headline="Senior Data Engineer | dbt, Dagster",
            about=about,
            skills=[
                "dbt",
                "Dagster",
                "ClickHouse",
                "GitLab CI",
                "Docker",
                "Claude Code",
                "Python",
                "SQL",
            ],
        ),
    ]
    assert detect_drift(data, snaps, now=NOW) == []


@pytest.mark.db
async def test_drift_api_recompute_persist_resolve(db_client: AsyncClient) -> None:
    for platform, headline, about in (
        (
            "linkedin",
            "Senior Data Engineer | dbt, Dagster, ClickHouse",
            "12+ years. Northwind Commerce. Tbilisi.",
        ),
        ("wellfound", "Data Engineer | dbt", "9 years of experience at Lumen Analytics"),
    ):
        r = await db_client.post(
            "/api/profiles/snapshots",
            json={
                "platform": platform,
                "capture_method": "paste",
                "headline": headline,
                "about": about,
                "captured_at": "2099-01-01T00:00:00Z",
            },
        )
        assert r.status_code == 201, r.text
    r = await db_client.post("/api/profiles/drift/recompute")
    assert r.status_code == 200, r.text
    summary = r.json()
    assert summary["open"] >= 3 and "years_experience" in summary["by_field"]
    first = summary["findings"][0]
    assert first["resolution"] == "open" and first["severity"] in ("high", "medium", "nice")

    # dismiss one; recompute keeps it dismissed and does not duplicate it
    r = await db_client.patch(
        f"/api/profiles/drift/{first['id']}", json={"resolution": "dismissed"}
    )
    assert r.status_code == 200 and r.json()["resolution"] == "dismissed"
    r = await db_client.post("/api/profiles/drift/recompute")
    after = r.json()
    assert after["open"] == summary["open"] - 1
    assert sum(1 for f in after["findings"] if f["key"] == first["key"]) == 1
    r = await db_client.get("/api/profiles/drift", params={"open_only": True})
    assert all(f["resolution"] == "open" for f in r.json()["findings"])

    # surfaces in the notification center and the brief
    r = await db_client.get("/api/notifications")
    assert any(n["kind"] == "profile_drift" for n in r.json()["items"])
    r = await db_client.get("/api/insights/brief")
    assert r.status_code == 200 and r.json()["stats"]["profiles_out_of_sync"] == after["open"]
    assert any(a["kind"] == "drift" for a in r.json()["actions"])

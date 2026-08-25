# ruff: noqa: E501
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from careeros.modules.profiles.checklist import _trim, compose_checklist
from careeros.modules.profiles.drift import DriftOut
from careeros.modules.profiles.enums import Severity
from careeros.modules.vault.enums import Platform
from careeros.modules.vault.schema import VaultData
from careeros.modules.vault.service import Vault
from tests.conftest import DEMO_VAULT

NOW = datetime(2026, 8, 26, tzinfo=UTC)


@pytest.fixture(scope="module")
def data() -> VaultData:
    return Vault(DEMO_VAULT).require()


def test_trim_respects_limits_on_word_boundaries() -> None:
    assert _trim("short", 70) == "short"
    out = _trim(
        "Senior Data Engineer / Analytics Engineer | GCP, BigQuery, Snowflake, dbt, Airflow, Dagster",
        40,
    )
    assert len(out) <= 40 and out.endswith("…") and not out.endswith(",…")


def test_compose_without_audit_falls_back_to_vault_copy(data: VaultData) -> None:
    import uuid

    drift = [
        DriftOut(
            id=uuid.uuid4(),
            key="k1",
            field="years_experience",
            platform_a="upwork",
            platform_b="vault",
            value_a="8 years",
            value_b="~12 years",
            severity=Severity.high,
            message="upwork claims 8 years; the vault implies ~12",
            resolution="open",
            created_at=NOW,
        ),
        DriftOut(
            id=uuid.uuid4(),
            key="k2",
            field="rate",
            platform_a="toptal",
            platform_b="upwork",
            value_a="95/h",
            value_b="60/h",
            severity=Severity.high,
            message="toptal rate 95/h vs upwork rate 60/h",
            resolution="open",
            created_at=NOW,
        ),
        DriftOut(
            id=uuid.uuid4(),
            key="k3",
            field="location",
            platform_a="linkedin",
            platform_b="wellfound",
            value_a="not stated",
            value_b="Tbilisi",
            severity=Severity.nice,
            message="…",
            resolution="dismissed",
            created_at=NOW,
        ),
    ]
    out = compose_checklist(
        data, Platform.upwork, audit=None, drift=drift, snapshot_id=None, now=NOW
    )
    assert out.health_score is None and out.audit_id is None
    assert any("no audit yet" in n for n in out.notes)
    assert [i.origin for i in out.items] == ["drift", "drift"]
    years = next(i for i in out.items if i.category == "drift:years_experience")
    assert (
        years.current == "8 years" and years.suggested == "~12 years"
    )  # vault side is the suggestion
    rate = next(i for i in out.items if i.category == "drift:rate")
    assert rate.current == "60/h" and rate.suggested is None and rate.why == "contradicts toptal"
    assert out.copy_ready.source == "vault"
    assert out.copy_ready.headline_limit == 70 and len(out.copy_ready.headline or "") <= 70
    assert out.copy_ready.about and "Message me" in out.copy_ready.about  # upwork CTA appended
    assert [i.order for i in out.items] == [1, 2]


@pytest.mark.db
async def test_checklist_api_merges_audit_and_drift(db_client: AsyncClient) -> None:
    r = await db_client.post(
        "/api/profiles/snapshots",
        json={
            "platform": "toptal",
            "capture_method": "paste",
            "headline": "Data engineer",
            "about": "I like data. 20 years of experience.",
            "skills": ["Python"],
            "captured_at": "2099-02-01T00:00:00Z",
        },
    )
    snap = r.json()
    r = await db_client.post(f"/api/profiles/snapshots/{snap['id']}/audit", json={"use_ai": False})
    assert r.status_code == 200
    audit = r.json()
    r = await db_client.post("/api/profiles/drift/recompute")
    assert r.status_code == 200
    r = await db_client.get("/api/profiles/checklist/toptal")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["audit_id"] == audit["id"] and out["health_score"] == audit["health_score"]
    origins = {i["origin"] for i in out["items"]}
    assert "audit" in origins
    assert out["items"] == sorted(out["items"], key=lambda i: i["order"])
    sev = [i["severity"] for i in out["items"]]
    rank = {"critical": 0, "high": 1, "medium": 2, "nice": 3}
    assert sev == sorted(sev, key=lambda x: rank[x])
    assert out["copy_ready"]["headline"] and out["copy_ready"]["headline_limit"] == 100
    # dismissing a finding drops it from the next checklist
    first = next(i for i in out["items"] if i["origin"] == "audit")
    r = await db_client.patch(
        f"/api/profiles/findings/{first['ref_id']}", json={"resolution": "dismissed"}
    )
    assert r.status_code == 200
    r = await db_client.get("/api/profiles/checklist/toptal")
    assert all(i["ref_id"] != first["ref_id"] for i in r.json()["items"])
    r = await db_client.get("/api/profiles/checklist/getmatch")
    assert r.status_code == 200 and r.json()["snapshot_id"] is None

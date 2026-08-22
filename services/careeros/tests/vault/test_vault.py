from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import AsyncClient

from careeros.modules.vault.enums import ItemStatus
from careeros.modules.vault.export import export_schemas
from careeros.modules.vault.loader import load_vault
from careeros.modules.vault.schema import ScoringModel, VaultData
from careeros.modules.vault.service import (
    ChangeRequest,
    Vault,
    VaultConflict,
    VaultError,
    VaultInvalid,
    search_facts,
)
from careeros.modules.vault.validator import validate_vault
from careeros.modules.vault.yamlio import load_yaml

# ----------------------------------------------------------------------------- load & validate


def test_demo_vault_loads_and_is_valid(demo_vault: Vault) -> None:
    result = demo_vault.load()
    assert result.ok, [str(i) for i in result.errors]
    data = result.data
    assert data is not None
    assert data.profile.name == "Dana Kovalenko"
    assert len(data.experience) == 4
    assert len(data.achievements) >= 9
    assert {v.id for v in data.cv_variants} >= {
        "general-core",
        "remote-us",
        "wellfound",
        "poland-eu",
    }
    assert data.scoring is not None and data.scoring.version == 1
    assert "ach_northwind_ci_cd" in data.fact_ids()


def test_missing_vault_dir_reports_error(tmp_path: Path) -> None:
    result = load_vault(tmp_path / "nope")
    assert result.data is None
    assert result.errors and "not found" in result.errors[0].message


def test_referential_integrity_detects_unknown_refs(demo_vault: Vault) -> None:
    data = demo_vault.require()
    broken = data.model_copy(deep=True)
    broken.experience[0].achievement_ids.append("ach_does_not_exist")
    broken.cv_variants[0].positioning_id = "nope"
    issues = validate_vault(broken)
    messages = [f"{i.location}: {i.message}" for i in issues if i.level == "error"]
    assert any("achievement_ids" in m and "ach_does_not_exist" in m for m in messages)
    assert any("positioning_id" in m and "nope" in m for m in messages)


def test_retired_reference_is_a_warning(demo_vault: Vault) -> None:
    data = demo_vault.require()
    broken = data.model_copy(deep=True)
    broken.experience[0].achievement_ids.append("ach_legacy_ssrs")
    issues = validate_vault(broken)
    assert any(i.level == "warning" and "retired" in i.message for i in issues)
    # cross-company achievement also flagged
    assert any("another company" in i.message for i in issues)


def test_scoring_weights_must_sum_to_one() -> None:
    with pytest.raises(ValueError, match=r"sum to 1\.0"):
        ScoringModel.model_validate(
            {
                "tech_groups": {"market_core": ["python"]},
                "dimensions": {"technical_fit": {"weight": 0.5}, "risk": {"weight": 0.1}},
                "eligibility": {"home_country": "GE", "home_timezone": "Asia/Tbilisi"},
                "compensation": {
                    "min_annual": 1,
                    "target_annual": 2,
                    "min_hourly": 1,
                    "target_hourly": 2,
                },
            }
        )


def test_schema_forbids_unknown_fields(demo_vault: Vault) -> None:
    data = demo_vault.require()
    with pytest.raises(ValueError):
        VaultData.model_validate(
            data.model_dump() | {"profile": data.profile.model_dump() | {"bogus": 1}}
        )


# ----------------------------------------------------------------------------- changes


def test_preview_change_produces_diff_without_writing(scratch_vault: Vault) -> None:
    head = scratch_vault.head_sha()
    item = scratch_vault.require().by_id(scratch_vault.require().achievements)[
        "ach_northwind_ci_cd"
    ]
    payload = item.model_dump(mode="json", exclude_unset=True)
    payload["facts"].append("Documented the pipeline in a runbook")
    preview = scratch_vault.preview_change(
        ChangeRequest(collection="achievements", item_id=item.id, data=payload)
    )
    assert preview.ok, preview.issues
    assert "+      - Documented the pipeline in a runbook" in preview.diff
    assert preview.message == "career(achievements): update ach_northwind_ci_cd"
    assert scratch_vault.head_sha() == head
    assert not scratch_vault.git.is_dirty()


def test_apply_change_commits_and_preserves_other_items(scratch_vault: Vault) -> None:
    before = scratch_vault.require()
    item = before.by_id(before.achievements)["ach_lumen_bq_cost"]
    payload = item.model_dump(mode="json", exclude_unset=True)
    payload["facts"].append("Presented results to client leadership")
    result = scratch_vault.apply_change(
        ChangeRequest(
            collection="achievements",
            item_id=item.id,
            data=payload,
            base_sha=scratch_vault.head_sha(),
        )
    )
    assert result.commit_sha == scratch_vault.head_sha()
    assert not scratch_vault.git.is_dirty()
    after = scratch_vault.require()
    assert (
        "Presented results to client leadership" in after.by_id(after.achievements)[item.id].facts
    )
    assert len(after.achievements) == len(before.achievements)
    # comments at the top of the file survive the round-trip
    text = (scratch_vault.root / "source/achievements.yaml").read_text()
    assert text.startswith("# yaml-language-server")
    log = scratch_vault.history(n=1)
    assert log[0].message == "career(achievements): update ach_lumen_bq_cost"


def test_apply_rejects_invalid_reference(scratch_vault: Vault) -> None:
    data = scratch_vault.require()
    payload = data.cv_variants[0].model_dump(mode="json", exclude_unset=True)
    payload["positioning_id"] = "ghost"
    with pytest.raises(VaultInvalid) as exc:
        scratch_vault.apply_change(
            ChangeRequest(collection="cv_variants", item_id=payload["id"], data=payload)
        )
    assert any("ghost" in i.message for i in exc.value.issues)
    assert not scratch_vault.git.is_dirty()


def test_apply_rejects_schema_violation(scratch_vault: Vault) -> None:
    preview = scratch_vault.preview_change(
        ChangeRequest(
            collection="skills", item_id="sk_python", data={"id": "sk_python", "name": "Python"}
        )
    )
    assert not preview.ok
    assert any(i.location.endswith("category") for i in preview.issues)


def test_apply_conflict_on_stale_base_sha(scratch_vault: Vault) -> None:
    data = scratch_vault.require()
    payload = data.skills[0].model_dump(mode="json", exclude_unset=True)
    with pytest.raises(VaultConflict):
        scratch_vault.apply_change(
            ChangeRequest(
                collection="skills", item_id=payload["id"], data=payload, base_sha="0" * 40
            )
        )


def test_add_and_delete_items(scratch_vault: Vault) -> None:
    new = {
        "id": "sk_bigquery_ml",
        "name": "BigQuery ML",
        "category": "ai",
        "tier": "target",
        "level": "learning",
        "status": "draft",
    }
    res = scratch_vault.apply_change(ChangeRequest(collection="skills", data=new))
    assert res.message == "career(skills): update sk_bigquery_ml"
    assert "sk_bigquery_ml" in scratch_vault.require().by_id(scratch_vault.require().skills)
    res = scratch_vault.apply_change(
        ChangeRequest(collection="skills", item_id="sk_bigquery_ml", op="delete")
    )
    assert res.message == "career(skills): remove sk_bigquery_ml"
    assert "sk_bigquery_ml" not in scratch_vault.require().by_id(scratch_vault.require().skills)


def test_per_file_collection_create_and_singleton_edit(scratch_vault: Vault) -> None:
    variant = {
        "id": "linkedin-featured",
        "name": "LinkedIn featured",
        "positioning_id": "senior_data_engineer",
        "channel_id": "linkedin",
        "sections": ["summary", "experience"],
    }
    res = scratch_vault.apply_change(ChangeRequest(collection="cv_variants", data=variant))
    assert res.file == "cv/variants/linkedin-featured.yaml"
    assert res.message == "career(cv_variants): add linkedin-featured"
    assert (scratch_vault.root / res.file).exists()

    profile = scratch_vault.require().profile.model_dump(mode="json", exclude_unset=True)
    profile["headline_baseline"] = "Senior Data Engineer | dbt, Dagster, ClickHouse"
    res = scratch_vault.apply_change(ChangeRequest(collection="profile", data=profile))
    assert scratch_vault.require().profile.headline_baseline.startswith("Senior Data Engineer |")
    raw = load_yaml(scratch_vault.root / "source/profile.yaml")
    assert list(raw.keys())[:3] == ["id", "status", "name"]  # key order preserved


def test_ids_are_immutable(scratch_vault: Vault) -> None:
    data = scratch_vault.require()
    payload = data.skills[0].model_dump(mode="json", exclude_unset=True)
    payload["id"] = "sk_renamed"
    with pytest.raises(VaultError, match="immutable"):
        scratch_vault.preview_change(
            ChangeRequest(collection="skills", item_id=data.skills[0].id, data=payload)
        )


def test_init_from_template(tmp_path: Path) -> None:
    from tests.conftest import REPO_ROOT

    vault = Vault(tmp_path / "new", git_user_name="T", git_user_email="t@example.com")
    vault.init_from_template(REPO_ROOT / "career" / "templates", owner="Test Owner")
    result = vault.load()
    assert result.ok, [str(i) for i in result.errors]
    assert result.data is not None and result.data.meta.owner == "Test Owner"
    assert vault.history(n=1)[0].message == "career(vault): initialise from template"


# ----------------------------------------------------------------------------- search & export


def test_search_facts(demo_vault: Vault) -> None:
    hits = search_facts(demo_vault.require(), "clickhouse")
    assert hits and hits[0].score == 100
    assert {h.collection for h in hits} >= {"achievements", "projects", "skills"}
    assert search_facts(demo_vault.require(), "") == []


def test_export_schemas_roundtrip(tmp_path: Path) -> None:
    written = export_schemas(tmp_path)
    names = {p.name for p in written}
    assert {
        "profile.schema.json",
        "achievements.schema.json",
        "scoring.schema.json",
        "vault-data.schema.json",
    } <= names
    doc = json.loads((tmp_path / "achievements.schema.json").read_text())
    assert doc["$schema"].startswith("https://json-schema.org")
    assert "items" in doc["properties"]


def test_committed_schemas_are_fresh(tmp_path: Path) -> None:
    from tests.conftest import REPO_ROOT

    export_schemas(tmp_path)
    committed = REPO_ROOT / "career" / "schemas"
    for fresh in tmp_path.glob("*.schema.json"):
        assert (committed / fresh.name).read_text() == fresh.read_text(), (
            f"{fresh.name} stale: run `just export-schemas`"
        )


# ----------------------------------------------------------------------------- API


async def test_vault_api(client: AsyncClient) -> None:
    r = await client.get("/api/vault/status")
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True and body["counts"]["achievements"] >= 9

    r = await client.get("/api/vault/achievements/ach_northwind_ci_cd")
    assert r.status_code == 200 and r.json()["status"] == ItemStatus.verified

    r = await client.get("/api/vault/nope")
    assert r.status_code == 404

    r = await client.get("/api/vault/facts/search", params={"q": "bigquery"})
    assert r.status_code == 200 and r.json()

    item = (await client.get("/api/vault/skills/sk_python")).json()
    item["years"] = 12
    r = await client.post(
        "/api/vault/changes/preview",
        json={"collection": "skills", "item_id": "sk_python", "data": item},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True and "+" in r.json()["diff"]

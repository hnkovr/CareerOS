from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.config import Settings
from careeros.modules.ai.prompts import PromptRegistry
from careeros.modules.ai.providers.fake import FakeProvider
from careeros.modules.ai.registry import ProviderRegistry
from careeros.modules.ai.service import AIService
from careeros.modules.cv.builder import build_document
from careeros.modules.cv.compare import compare_documents
from careeros.modules.cv.keywords import extract_known_tech, tech_vocabulary
from careeros.modules.cv.provenance import check_bullet, company_name_map, fact_sources
from careeros.modules.cv.rendercv_adapter import render, to_rendercv_dict
from careeros.modules.cv.schemas import CVBulletsOutput, CVSummaryOutput, GenerateCVRequest
from careeros.modules.cv.selection import select_facts
from careeros.modules.cv.service import CVService, VariantNotFound
from careeros.modules.vault.schema import VaultData
from careeros.modules.vault.service import Vault
from tests.conftest import CAREER_DIR, DEMO_VAULT

JD = """Senior Data Engineer (remote, EU). Must have: dbt, BigQuery, Airflow, Python, SQL.
Nice: ClickHouse, Dagster, Terraform. We use Google Cloud and Looker."""


@pytest.fixture(scope="module")
def data() -> VaultData:
    return Vault(DEMO_VAULT).require()


def _ai(
    settings: Settings,
    fake: FakeProvider,
    session: AsyncSession | None = None,
    user_id: uuid.UUID | None = None,
) -> AIService:
    return AIService(
        settings,
        ProviderRegistry({"fake": fake}, "fake", []),
        PromptRegistry(CAREER_DIR / "prompts"),
        session=session,
        user_id=user_id,
    )


# ----------------------------------------------------------------------------- keywords & selection


def test_jd_keyword_extraction_uses_vault_vocabulary(data: VaultData) -> None:
    found = extract_known_tech(JD, tech_vocabulary(data))
    assert {
        "dbt",
        "BigQuery",
        "Airflow",
        "Python",
        "SQL",
        "ClickHouse",
        "Dagster",
        "Terraform",
        "GCP",
    } <= set(found)
    assert "Looker" not in found  # unknown to the vault → not a keyword


def test_selection_respects_positioning_channel_and_jd(data: VaultData) -> None:
    variants = data.by_id(data.cv_variants)
    sel = select_facts(data, variants["general-core"], JD)
    assert [e.experience.id for e in sel.experiences][:2] == ["exp_northwind", "exp_lumen"]
    first = sel.experiences[0]
    assert first.achievements[0].achievement.id == "ach_northwind_ci_cd"  # emphasized + keywords
    assert all(a.achievement.status != "retired" for e in sel.experiences for a in e.achievements)
    assert "ach_legacy_ssrs" not in sel.fact_ids()
    assert "dbt" in sel.jd_keywords and "BigQuery" in sel.jd_keywords
    # startup variant: brightpath is de-emphasized and outside years_back=8
    wf = select_facts(data, variants["wellfound"])
    assert "exp_brightpath" not in {e.experience.id for e in wf.experiences}
    assert len(wf.projects) <= 3 and wf.projects[0].id == "proj_agentic_market_intel"
    assert len(wf.experiences[0].achievements) <= 3


def test_visibility_filters_per_platform(data: VaultData) -> None:
    variants = data.by_id(data.cv_variants)
    ids_wellfound = select_facts(data, variants["wellfound"]).fact_ids()
    ids_core = select_facts(data, variants["data-platform-engineer"]).fact_ids()
    assert "ach_brightpath_airflow" in ids_core and "ach_brightpath_airflow" not in ids_wellfound


# ----------------------------------------------------------------------------- provenance guard


def test_provenance_guard(data: VaultData) -> None:
    sources = fact_sources(data)
    names = company_name_map(data)
    ok = check_bullet(
        "Cut MR pipeline time from 34 to 9 minutes with dbt Slim CI",
        ["ach_northwind_ci_cd"],
        sources,
        names,
    )
    assert ok == []
    bad_number = check_bullet(
        "Cut pipeline time by 80% across 12 teams", ["ach_northwind_ci_cd"], sources, names
    )
    assert any("'80%'" in p for p in bad_number) and any("'12'" in p for p in bad_number)
    bad_id = check_bullet("Did things", ["ach_nope"], sources, names)
    assert any("unknown fact ids" in p for p in bad_id)
    bad_company = check_bullet(
        "Migrated Orbit Fintech reporting to Snowflake", ["ach_northwind_ci_cd"], sources, names
    )
    assert any("Orbit Fintech" in p for p in bad_company)
    ok_company = check_bullet(
        "Migrated Orbit Fintech reporting to Snowflake, cutting cost 28%",
        ["ach_orbit_snowflake"],
        sources,
        names,
    )
    assert ok_company == []


# ------------------------------------------------------------------------- builder, render, compare


def test_build_document_without_ai_uses_verbatim_facts(data: VaultData) -> None:
    variants = data.by_id(data.cv_variants)
    sel = select_facts(data, variants["general-core"], JD)
    doc = build_document(data, sel, vault_sha="abc")
    assert doc.header.name == "Dana Kovalenko" and doc.header.linkedin == "dana-kovalenko-demo"
    assert doc.summary and doc.summary.derived_from == ["profile"]
    bullets = [b for _, _, b in doc.all_bullets()]
    assert all(b.source == "fact" and b.derived_from for b in bullets)
    first_exp = doc.experience[0]
    assert first_exp.position == "Senior Data Engineer" and first_exp.end is None
    lumen = next(e for e in doc.experience if e.experience_id == "exp_lumen")
    assert lumen.position == "Analytics Engineer → Lead Analytics Engineer"
    assert "dbt" in doc.keywords and "BigQuery" in doc.keywords
    rc = to_rendercv_dict(doc)
    assert rc["cv"]["sections"]["Experience"][0]["end_date"] == "present"
    assert rc["design"]["theme"] == "classic"


def test_render_produces_pdf_md_json(data: VaultData, tmp_path: Path) -> None:
    variants = data.by_id(data.cv_variants)
    doc = build_document(data, select_facts(data, variants["wellfound"]), vault_sha=None)
    out = render(doc, tmp_path / "out", ["pdf", "md", "json"])
    assert out.files.pdf and Path(out.files.pdf).stat().st_size > 5_000
    assert out.files.md and "Northwind Commerce" in Path(out.files.md).read_text()
    assert out.files.json_ and (tmp_path / "out" / "input.yaml").exists()


def test_compare_documents(data: VaultData) -> None:
    variants = data.by_id(data.cv_variants)
    a = build_document(data, select_facts(data, variants["general-core"]), vault_sha=None)
    b = build_document(data, select_facts(data, variants["wellfound"], JD), vault_sha=None)
    cmp = compare_documents(a, b, label_a="core", label_b="wellfound")
    assert cmp.removed and cmp.unchanged > 0
    assert any("ach_orbit_snowflake" in d.derived_from for d in cmp.removed)
    assert cmp.sections_a != cmp.sections_b


# ------------------------------------------------------------------------- AI rewriting via service


def _responder(data: VaultData):
    def respond(req, schema):
        if schema is CVBulletsOutput:
            return {
                "groups": [
                    {
                        "company_id": "northwind_commerce",
                        "bullets": [
                            {
                                "text": (
                                    "Designed GitLab CI/CD for dbt + Dagster, "
                                    "cutting MR pipelines from 34 to 9 minutes"
                                ),
                                "derived_from": ["ach_northwind_ci_cd"],
                            },
                            {
                                "text": "Saved $2M by migrating to ClickHouse",
                                "derived_from": ["ach_northwind_clickhouse"],
                            },
                            {
                                "text": "Invented a thing nobody verified",
                                "derived_from": ["ach_ghost"],
                            },
                        ],
                    }
                ]
            }
        if schema is CVSummaryOutput:
            return {
                "text": (
                    "Senior Data Engineer with 11+ years building analytics platforms on dbt, "
                    "Dagster and ClickHouse for e-commerce and fintech teams."
                ),
                "derived_from": ["profile", "ach_northwind_ci_cd"],
            }
        return {}

    return respond


async def test_generate_with_ai_applies_guard_and_falls_back(
    settings: Settings, data: VaultData, tmp_path: Path
) -> None:
    fake = FakeProvider(_responder(data))
    svc = CVService(
        settings.model_copy(update={"generated_dir": tmp_path}),
        Vault(DEMO_VAULT),
        _ai(settings, fake),
    )
    out = await svc.generate(
        GenerateCVRequest(variant_id="general-core", jd_text=JD, formats=["json"])
    )
    assert out.status == "ready" and out.ai_used
    doc = out.document
    assert doc is not None
    nw = next(e for e in doc.experience if e.experience_id == "exp_northwind")
    assert [b.text for b in nw.bullets] == [
        "Designed GitLab CI/CD for dbt + Dagster, cutting MR pipelines from 34 to 9 minutes"
    ]
    assert nw.bullets[0].source == "ai"
    assert any("'2m'" in w for w in out.warnings) and any(
        "unknown fact ids" in w for w in out.warnings
    )
    lumen = next(e for e in doc.experience if e.experience_id == "exp_lumen")
    assert lumen.bullets and lumen.bullets[0].source == "fact"  # no AI group → verbatim facts
    assert doc.summary and doc.summary.source == "ai" and "11+" in doc.summary.text
    assert doc.generation.provider == "fake" and doc.generation.prompt_versions["cv_bullets"] == 1


async def test_generate_no_ai_and_unknown_variant(settings: Settings, tmp_path: Path) -> None:
    svc = CVService(
        settings.model_copy(update={"generated_dir": tmp_path}),
        Vault(DEMO_VAULT),
        _ai(settings, FakeProvider()),
    )
    out = await svc.generate(
        GenerateCVRequest(variant_id="remote-us", use_ai=False, formats=["json", "md"])
    )
    assert out.status == "ready" and not out.ai_used and out.files.md
    with pytest.raises(VariantNotFound):
        await svc.generate(GenerateCVRequest(variant_id="nope"))


@pytest.mark.db
async def test_cv_api_generate_get_compare(db_client: AsyncClient, settings: Settings) -> None:
    r = await db_client.get("/api/cv/variants")
    assert r.status_code == 200 and any(v["id"] == "general-core" for v in r.json())
    r1 = await db_client.post(
        "/api/cv/generate",
        json={"variant_id": "general-core", "use_ai": False, "formats": ["json"]},
    )
    assert r1.status_code == 200, r1.text
    r2 = await db_client.post(
        "/api/cv/generate",
        json={"variant_id": "wellfound", "use_ai": False, "formats": ["json"], "jd_text": JD},
    )
    assert r2.status_code == 200
    a, b = r1.json()["id"], r2.json()["id"]
    r = await db_client.get(f"/api/cv/artifacts/{a}")
    assert (
        r.status_code == 200
        and r.json()["bullet_count"] > 5
        and r.json()["document"]["variant_id"] == "general-core"
    )
    r = await db_client.get(f"/api/cv/artifacts/{a}/file/json")
    assert r.status_code == 200
    r = await db_client.get(f"/api/cv/artifacts/{a}/file/pdf")
    assert r.status_code == 404
    r = await db_client.post("/api/cv/compare", json={"a": a, "b": b})
    assert r.status_code == 200 and r.json()["removed"]
    r = await db_client.get("/api/cv/artifacts")
    assert r.status_code == 200 and len(r.json()) >= 2
    r = await db_client.post("/api/cv/generate", json={"variant_id": "nope"})
    assert r.status_code == 404


async def test_improve_diffs_the_ai_pass_against_the_verbatim_facts(
    settings: Settings, data: VaultData, tmp_path: Path
) -> None:
    """The baseline is regenerated, not looked up (#30).

    Comparing against "the last artifact that happened to exist" answers a different
    question every run, and if that one was itself an AI pass the diff shows AI-vs-AI
    drift rather than what AI changed about the facts.
    """
    svc = CVService(
        settings.model_copy(update={"generated_dir": tmp_path}),
        Vault(DEMO_VAULT),
        _ai(settings, FakeProvider(_responder(data))),
    )
    result = await svc.improve("general-core", jd_text=JD, formats=["json"])

    assert result.artifact.ai_used, "the AI pass must actually have run"
    assert not result.baseline.ai_used, "the baseline is the facts as written"
    assert result.comparison.a == "facts" and result.comparison.b == "ai"
    assert result.comparison.rewritten, "the fake provider rewrites at least one bullet"
    assert all(d.derived_from for d in result.comparison.rewritten), (
        "a bullet without provenance would violate invariant 2 before it ever reached chat"
    )

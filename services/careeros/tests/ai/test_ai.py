from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.config import Settings
from careeros.modules.ai.prompts import PromptNotFound, PromptRegistry, PromptRenderError
from careeros.modules.ai.provider import AIOutputInvalid, AIUnavailable, extract_json_object
from careeros.modules.ai.providers.fake import FakeProvider
from careeros.modules.ai.registry import ProviderRegistry
from careeros.modules.ai.schemas import BundleRequest, DevPacketRequest, GenerateRequest
from careeros.modules.ai.service import AIService
from tests.conftest import CAREER_DIR, DEMO_VAULT


class Verdict(BaseModel):
    verdict: str = Field(pattern="^(apply|skip)$")
    score: int = Field(ge=0, le=100)


@pytest.fixture
def prompts(tmp_path: Path) -> PromptRegistry:
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "verdict.yaml").write_text(
        "id: verdict\nversion: 2\narea: test\npurpose: t\ninputs: [jd]\noutput_schema: Verdict\n"
        "system: Be terse.\ntemplate: |\n  Judge: {{ jd }}\n"
    )
    return PromptRegistry(CAREER_DIR / "prompts", tmp_path / "p")


def _service(
    settings: Settings,
    prompts: PromptRegistry,
    fake: FakeProvider,
    session: AsyncSession | None = None,
    user_id: uuid.UUID | None = None,
) -> AIService:
    registry = ProviderRegistry({"fake": fake}, "fake", [])
    return AIService(settings, registry, prompts, session=session, user_id=user_id)


# ----------------------------------------------------------------------------- prompts


def test_prompt_registry_loads_library_and_vault_overlay(prompts: PromptRegistry) -> None:
    ids = set(prompts.all())
    assert {
        "cv_bullets",
        "opportunity_analysis",
        "external_opportunity_analysis",
        "dev_task_packet",
        "verdict",
    } <= ids
    assert prompts.get("verdict").source == "vault"
    rendered = prompts.render("verdict", jd="Senior DE")
    assert (
        rendered.user == "Judge: Senior DE"
        and rendered.system == "Be terse."
        and rendered.version == 2
    )


def test_prompt_missing_inputs_and_unknown(prompts: PromptRegistry) -> None:
    with pytest.raises(PromptRenderError, match="missing inputs: jd"):
        prompts.render("verdict")
    with pytest.raises(PromptNotFound):
        prompts.render("nope")


def test_library_prompts_render_with_demo_data(prompts: PromptRegistry) -> None:
    from careeros.modules.vault.service import Vault

    data = Vault(DEMO_VAULT).require()
    pos = data.positioning[0].model_dump()
    ch = data.channels[0].model_dump()
    facts = [
        {"id": a.id, "company": a.company_id, "title": a.title, "facts": a.facts, "metrics": []}
        for a in data.achievements[:2]
    ]
    out = prompts.render("cv_bullets", positioning=pos, channel=ch, facts=facts, context=None)
    assert "ach_northwind_ci_cd" in out.user and "derived_from" in out.user


# ----------------------------------------------------------------------------- provider port


def test_extract_json_object_tolerates_fences_and_prose() -> None:
    assert extract_json_object('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('prefix {"a": {"b": [1,2]}} suffix') == {"a": {"b": [1, 2]}}
    with pytest.raises(ValueError):
        extract_json_object("no json here")


async def test_structured_validates_and_retries_then_succeeds(
    settings: Settings, prompts: PromptRegistry
) -> None:
    answers = iter([{"verdict": "maybe", "score": 10}, {"verdict": "apply", "score": 88}])
    fake = FakeProvider(lambda req, schema: next(answers))
    svc = _service(settings, prompts, fake)
    result = await svc.structured("verdict", {"jd": "x"}, Verdict)
    assert result.data.verdict == "apply" and result.retries == 1
    assert "rejected by the validator" in fake.calls[-1][0].prompt


async def test_structured_gives_up_after_max_retries(
    settings: Settings, prompts: PromptRegistry
) -> None:
    fake = FakeProvider(lambda req, schema: {"verdict": "nope", "score": 1})
    svc = _service(settings, prompts, fake)
    with pytest.raises(AIOutputInvalid) as exc:
        await svc.structured("verdict", {"jd": "x"}, Verdict)
    assert len(exc.value.errors) == settings.ai_structured_max_retries + 1


async def test_fallback_chain_skips_unconfigured_provider(
    settings: Settings, prompts: PromptRegistry
) -> None:
    from careeros.modules.ai.providers.anthropic_provider import AnthropicProvider

    fake = FakeProvider(lambda req, schema: {"verdict": "skip", "score": 5})
    registry = ProviderRegistry(
        {"anthropic": AnthropicProvider(None, "m"), "fake": fake}, "anthropic", ["fake"]
    )
    svc = AIService(settings, registry, prompts)
    result = await svc.structured("verdict", {"jd": "x"}, Verdict)
    assert result.response.provider == "fake"
    registry_no_fb = ProviderRegistry({"anthropic": AnthropicProvider(None, "m")}, "anthropic", [])
    with pytest.raises(AIUnavailable):
        await AIService(settings, registry_no_fb, prompts).structured(
            "verdict", {"jd": "x"}, Verdict
        )


async def test_fake_provider_generate_and_stream() -> None:
    fake = FakeProvider()
    resp = await fake.generate(GenerateRequest(prompt="hello world"))
    assert resp.text.startswith("[fake]")
    chunks = [c async for c in fake.stream(GenerateRequest(prompt="a b"))]
    assert chunks[-1].done


# ----------------------------------------------------------------------------- modes B & C


async def test_bundle_renders_external_prompt_with_deep_link(
    settings: Settings, prompts: PromptRegistry
) -> None:
    from careeros.modules.vault.service import Vault

    data = Vault(DEMO_VAULT).require()
    svc = _service(settings, prompts, FakeProvider())
    out = await svc.bundle(
        BundleRequest(
            prompt_id="external_opportunity_analysis",
            target="claude",
            inputs={
                "positioning": data.positioning[0].model_dump(),
                "profile": data.profile.model_dump(),
                "facts": [
                    {"id": a.id, "title": a.title, "facts": a.facts} for a in data.achievements[:3]
                ],
                "opportunity": "Senior Data Engineer, remote EU, dbt + BigQuery",
                "constraints": ["contractor only"],
            },
        )
    )
    assert out.deep_link and out.deep_link.startswith("https://claude.ai/new?q=")
    assert "12. Apply / skip recommendation" in out.text
    assert "[ach_northwind_ci_cd]" in out.text
    gemini = await svc.bundle(
        BundleRequest(prompt_id="verdict", target="gemini", inputs={"jd": "x"})
    )
    assert gemini.deep_link is None and "Judge: x" in gemini.text


async def test_dev_packet_writes_markdown(
    settings: Settings, prompts: PromptRegistry, tmp_path: Path
) -> None:
    svc = _service(settings, prompts, FakeProvider())
    out = await svc.dev_packet(
        DevPacketRequest(
            slug="improve-linkedin-profile",
            context="LinkedIn headline lacks ClickHouse.",
            goal="Propose a new headline",
            relevant_files=["career/source/profile.yaml"],
            acceptance_criteria=["headline ≤ 220 chars"],
            suggested_commands=["just validate-career"],
        ),
        generated_dir=tmp_path,
    )
    path = Path(out.path)
    assert path.exists() and path.name == "improve-linkedin-profile.md"
    text = path.read_text()
    assert "# Task: improve-linkedin-profile" in text and "- [ ] headline ≤ 220 chars" in text
    assert "just validate-career" in text


# ----------------------------------------------------------------------------- ledger (db)


@pytest.mark.db
async def test_runs_are_recorded_and_feedback_works(
    settings: Settings, prompts: PromptRegistry, session: AsyncSession, user_id: uuid.UUID
) -> None:
    from careeros.modules.ai.schemas import FeedbackIn

    fake = FakeProvider(lambda req, schema: {"verdict": "apply", "score": 70})
    svc = _service(settings, prompts, fake, session=session, user_id=user_id)
    result = await svc.structured(
        "verdict", {"jd": "x"}, Verdict, entity_type="opportunity", entity_id="o1"
    )
    assert result.run_id is not None
    run = await svc.get_run(result.run_id)
    assert (
        run
        and run.valid
        and run.output == {"verdict": "apply", "score": 70}
        and run.prompt_version == 2
    )
    updated = await svc.feedback(result.run_id, FeedbackIn(feedback="down", note="too optimistic"))
    assert updated and updated.feedback == "down"
    runs = await svc.list_runs(entity_type="opportunity", entity_id="o1")
    assert runs and runs[0].id == result.run_id


@pytest.mark.db
async def test_ai_api(db_client: AsyncClient) -> None:
    r = await db_client.get("/api/ai/providers")
    assert r.status_code == 200 and {p["name"] for p in r.json()} >= {"anthropic", "openai", "fake"}
    r = await db_client.get("/api/ai/prompts")
    assert r.status_code == 200 and any(p["id"] == "cv_bullets" for p in r.json())
    r = await db_client.post(
        "/api/ai/bundles",
        json={
            "prompt_id": "dev_task_packet",
            "target": "chatgpt",
            "inputs": {
                "agent": "codex",
                "slug": "x",
                "context": "c",
                "goal": "g",
                "relevant_files": [],
                "constraints": [],
                "acceptance_criteria": ["done"],
                "suggested_commands": [],
                "expected_artifacts": [],
            },
        },
    )
    assert r.status_code == 200 and r.json()["deep_link"].startswith("https://chatgpt.com/?q=")
    r = await db_client.get("/api/ai/runs")
    assert r.status_code == 200 and r.json()[0]["mode"] == "external_bundle"
    r = await db_client.post("/api/ai/bundles", json={"prompt_id": "nope", "inputs": {}})
    assert r.status_code == 404

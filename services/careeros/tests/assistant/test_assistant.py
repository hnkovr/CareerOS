"""ADR-014 assistant: the read-only tool registry, the answer guard, and the API with a scripted
fake provider that behaves like a real tool-using model (reads tool results, cites ids)."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from careeros.core.config import Settings
from careeros.modules.ai.deps import build_ai_service
from careeros.modules.ai.schemas import ToolCall, ToolChatRequest
from careeros.modules.assistant.schemas import AssistantOutput
from careeros.modules.assistant.service import guard_answer
from careeros.modules.assistant.tools import TOOLS, ToolContext, default_registry
from careeros.modules.vault.schema import VaultData
from careeros.modules.vault.service import Vault
from tests.conftest import DEMO_VAULT


@pytest.fixture(scope="module")
def data() -> VaultData:
    return Vault(DEMO_VAULT).require()


def _ctx(settings: Settings) -> ToolContext:
    vault = Vault(DEMO_VAULT)
    return ToolContext(
        settings=settings,
        vault=vault,
        ai=build_ai_service(settings),
        session=None,  # type: ignore[arg-type]  # vault-only tools never touch it
        user_id=uuid.uuid4(),
    )


# ----------------------------------------------------------------------------- registry


def test_registry_specs_are_json_schemas_and_read_only() -> None:
    reg = default_registry()
    assert reg.names() == [t.name for t in TOOLS] and len(TOOLS) == 7
    for spec in reg.specs():
        assert spec.input_schema["type"] == "object"
    assert [t.name for t in reg.infos() if not t.read_only] == ["start_workflow"]


async def test_vault_tools_record_observations_and_ids(settings: Settings, data: VaultData) -> None:
    reg = default_registry()
    ctx = _ctx(settings)
    text = await reg.execute(
        ctx, ToolCall(id="1", name="get_career_facts", arguments={"section": "skills", "limit": 3})
    )
    facts = json.loads(text)
    assert len(facts["skills"]) == 3 and set(facts) == {"skills"}
    assert data.skills[0].id in ctx.seen_ids and ctx.observed == [text]

    tech = next(a for a in data.achievements if a.technologies.all()).technologies.all()[0]
    text = await reg.execute(ctx, ToolCall(id="2", name="search_facts", arguments={"query": tech}))
    hits = json.loads(text)
    assert hits["count"] >= 1 and hits["items"][0]["id"] in data.fact_ids()
    assert hits["items"][0]["matched_terms"] >= 1

    with pytest.raises(KeyError, match="unknown tool"):
        await reg.execute(ctx, ToolCall(id="3", name="apply_now", arguments={}))
    with pytest.raises(ValueError):  # pydantic validation of the arguments
        await reg.execute(ctx, ToolCall(id="4", name="search_facts", arguments={"query": "x"}))


# ----------------------------------------------------------------------------- guard


def test_guard_answer_rejects_unknown_ids_and_unseen_numbers(data: VaultData) -> None:
    ach = next(a for a in data.achievements if a.facts)
    observed = [json.dumps({"count": 3, "items": [{"id": "opp-1", "score": 82}]})]
    ok = AssistantOutput(
        answer="You have 3 open items; the best scores 82.", derived_from=["opp-1"]
    )
    assert guard_answer(ok, data, observed=observed, seen_ids={"opp-1"}) == []

    cited = AssistantOutput(answer=f"{ach.facts[0]}", derived_from=[ach.id])
    assert guard_answer(cited, data, observed=[], seen_ids=set()) == []

    bad_id = AssistantOutput(answer="fine", derived_from=["ghost-fact"])
    assert "unknown ids" in guard_answer(bad_id, data, observed=observed, seen_ids=set())[0]

    invented = AssistantOutput(answer="You beat 97% of applicants.", derived_from=[])
    problems = guard_answer(invented, data, observed=observed, seen_ids=set())
    assert len(problems) == 1 and "97%" in problems[0]


# ----------------------------------------------------------------------------- API

JD = """Principal Analytics Platform Engineer
Vega Metrics — B2B contract, $130,000 - $150,000 per year. Fully remote worldwide.
Requirements: Python, SQL, dbt, Dagster, ClickHouse.
"""


@pytest.mark.db
async def test_ask_api_runs_tools_cites_facts_and_guards(
    db_client: AsyncClient,
    settings: Settings,
    data: VaultData,
) -> None:
    from careeros.modules.ai.deps import get_provider_registry
    from careeros.modules.ai.providers.fake import FakeProvider

    r = await db_client.post("/api/opportunities/ingest", json={"source": "manual", "text": JD})
    assert r.status_code == 201, r.text
    opp = r.json()
    tech = next(a for a in data.achievements if a.technologies.all()).technologies.all()[0]
    mode = {"final": "cite"}

    def respond(req: ToolChatRequest) -> dict[str, Any]:
        # behaves like a model: search facts, read the opportunity, then answer from what it saw
        tool_results = [m for m in req.messages if m.role == "tool"]
        if not tool_results:
            return {"tool_calls": [{"name": "search_facts", "arguments": {"query": tech}}]}
        if len(tool_results) == 1:
            return {
                "tool_calls": [
                    {"name": "get_opportunity", "arguments": {"opportunity_id": opp["id"]}},
                    {"name": "no_such_tool", "arguments": {}},
                ]
            }
        hits = json.loads(tool_results[0].content or "{}")
        fact_id = hits["items"][0]["id"]
        if mode["final"] == "invent":
            return {
                "text": json.dumps(
                    {"answer": "Your offer beats 99.5% of the market.", "derived_from": [fact_id]}
                )
            }
        return {
            "text": "Answer: "
            + json.dumps(
                {
                    "answer": f"{tech} is evidenced by {fact_id}; the posting scores "
                    f"{hits['items'][0]['matched_terms']} matched term(s).",
                    "derived_from": [fact_id, opp["id"]],
                    "suggested_next_action": "Generate the tailored CV, then apply.",
                    "confidence": "high",
                }
            )
        }

    get_provider_registry(settings).register(
        FakeProvider(tool_responder=respond), make_default=True
    )

    r = await db_client.get("/api/assistant/tools")
    assert r.status_code == 200
    assert [t["name"] for t in r.json()][:2] == ["get_career_facts", "search_facts"]

    r = await db_client.post(
        "/api/assistant/ask",
        json={"question": f"Can I prove {tech} for this posting?", "opportunity_id": opp["id"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["guarded"] is False and body["provenance_problems"] == []
    expected_tools = ["search_facts", "get_opportunity", "no_such_tool"]
    assert [s["tool"] for s in body["tools_used"]] == expected_tools
    assert [s["ok"] for s in body["tools_used"]] == [True, True, False]
    assert body["turns"] == 3 and body["provider"] == "fake" and body["ai_run_id"]
    assert opp["id"] in body["derived_from"] and body["confidence"] == "high"

    r = await db_client.get(f"/api/ai/runs/{body['ai_run_id']}")
    assert r.status_code == 200
    run = r.json()
    assert run["prompt_id"] == "assistant_chat" and run["valid"] is True
    assert [s["tool"] for s in run["output"]["tool_trace"]] == [
        "search_facts",
        "get_opportunity",
        "no_such_tool",
    ]

    mode["final"] = "invent"
    r = await db_client.post("/api/assistant/ask", json={"question": "How good is the offer?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["guarded"] is True and body["answer"].startswith("Answer withheld")
    assert any("99.5%" in p for p in body["provenance_problems"])
    assert body["suggested_next_action"] is None and body["confidence"] == "low"

    r = await db_client.post("/api/assistant/ask", json={"question": "hi"})
    assert r.status_code == 422  # too short


@pytest.mark.db
async def test_ask_api_can_start_a_gated_workflow(
    db_client: AsyncClient, settings: Settings
) -> None:
    from careeros.modules.ai.deps import get_provider_registry
    from careeros.modules.ai.providers.fake import FakeProvider
    from careeros.modules.opportunities.schemas import OpportunityAnalysisOutput

    jd = """Staff Data Engineer, Streaming
Aurora Grid — B2B contract, $140,000 - $160,000 per year. Fully remote worldwide.
Requirements: Python, SQL, dbt, Dagster, ClickHouse.
"""
    r = await db_client.post("/api/opportunities/ingest", json={"source": "manual", "text": jd})
    assert r.status_code == 201, r.text
    opp = r.json()

    def respond(req: Any, schema: Any = None) -> dict[str, Any]:  # structured() calls
        if schema is OpportunityAnalysisOutput:
            return {
                "verdict": "apply",
                "executive_summary": "Fit.",
                "recommended_cv_variant": "general-core",
                "next_action": "Apply.",
            }
        return {}

    def respond_tools(req: ToolChatRequest) -> dict[str, Any]:
        tool_results = [m for m in req.messages if m.role == "tool"]
        if not tool_results:
            return {
                "tool_calls": [
                    {
                        "name": "start_workflow",
                        "arguments": {"kind": "apply", "target_id": opp["id"], "use_ai": True},
                    }
                ]
            }
        result = json.loads(tool_results[0].content or "{}")
        return {
            "text": json.dumps(
                {
                    "answer": f"Started the apply workflow {result['run_id']}; it is waiting for "
                    f"your approval on /workflows (step {result['waiting_on']}).",
                    "derived_from": [result["run_id"], opp["id"]],
                    "suggested_next_action": "Review the draft on /workflows and approve it.",
                    "confidence": "high",
                }
            )
        }

    get_provider_registry(settings).register(
        FakeProvider(respond, tool_responder=respond_tools), make_default=True
    )
    r = await db_client.get("/api/assistant/tools")
    tools = {t["name"]: t for t in r.json()}
    assert tools["start_workflow"]["read_only"] is False
    assert all(t["read_only"] for n, t in tools.items() if n != "start_workflow")

    r = await db_client.post(
        "/api/assistant/ask",
        json={"question": "Apply to this one, please.", "opportunity_id": opp["id"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["guarded"] is False, body["provenance_problems"]
    assert [s["tool"] for s in body["tools_used"]] == ["start_workflow"] and body["tools_used"][0][
        "ok"
    ]
    run_id = next(i for i in body["derived_from"] if i != opp["id"])

    # the run exists, stopped at its gate — nothing in the pipeline yet
    r = await db_client.get(f"/api/workflows/{run_id}")
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["state"] == "waiting_approval" and run["kind"] == "apply"
    assert run["steps"][3]["status"] == "waiting" and "application_id" not in run["context"]
    r = await db_client.get("/api/pipeline/board", params={"kind": "employment"})
    assert all(
        a["opportunity_id"] != opp["id"] for col in r.json()["columns"] for a in col["applications"]
    )

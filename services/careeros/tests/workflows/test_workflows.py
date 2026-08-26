"""ADR-017 workflows: definitions, the apply chain pausing at its gate, approve → application
created, reject → nothing created, follow-up chain with the guarded AI draft, failure paths."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from careeros.core.config import Settings
from careeros.modules.opportunities.schemas import OpportunityAnalysisOutput
from careeros.modules.workflows.engine import DEFINITIONS
from careeros.modules.workflows.enums import WorkflowKind
from careeros.modules.workflows.schemas import FollowUpDraft
from careeros.modules.workflows.service import WorkflowService

JD_A = """Lead Data Platform Engineer
Kestrel Analytics — B2B contract, $130,000 - $150,000 per year. Fully remote, any timezone.
Requirements: Python, SQL, dbt, Dagster, ClickHouse.
"""
JD_B = """Data Engineering Consultant
Pelican Labs — freelance, $90 - $110 per hour, remote. Requirements: SQL, dbt, Airflow.
"""
OPTIONS = {"use_ai": True, "formats": ["md", "json"]}


async def _board_opportunity_ids(client: AsyncClient, kind: str) -> set[str]:
    r = await client.get("/api/pipeline/board", params={"kind": kind})
    assert r.status_code == 200, r.text
    return {a["opportunity_id"] for col in r.json()["columns"] for a in col["applications"]}


def test_definitions_expose_gates() -> None:
    defs = {d.kind: d for d in WorkflowService.definitions()}
    assert set(defs) == {WorkflowKind.apply, WorkflowKind.follow_up}
    apply = defs[WorkflowKind.apply]
    assert [s.name for s in apply.steps] == [
        "analyze",
        "select_cv",
        "generate_cv",
        "draft_message",
        "create_application",
    ]
    assert [s.kind for s in apply.steps].count("approval") == 1
    assert apply.steps[3].kind == "approval" and apply.target_type == "opportunity"
    # every write step sits after a gate
    for d in DEFINITIONS.values():
        gate = next(i for i, s in enumerate(d.steps) if s.kind == "approval")
        assert all(s.kind == "auto" for s in d.steps[gate + 1 :])


def _register_fake(settings: Settings, *, follow_up_numbers: str = "") -> None:
    from careeros.modules.ai.deps import get_provider_registry
    from careeros.modules.ai.providers.fake import FakeProvider

    def respond(req: Any, schema: Any) -> dict[str, Any]:
        if schema is OpportunityAnalysisOutput:
            return {
                "verdict": "apply",
                "executive_summary": "Strong fit.",
                "recommended_cv_variant": "general-core",
                "suggested_response": "Hello — I'd like to apply; my CV is attached.",
                "next_action": "Apply.",
            }
        if schema is FollowUpDraft:
            return {
                "subject": "Checking in",
                "message": (
                    f"Hello, just following up on my application. {follow_up_numbers}"
                ).strip(),
            }
        return {}

    get_provider_registry(settings).register(FakeProvider(respond), make_default=True)


@pytest.mark.db
async def test_apply_workflow_pauses_then_creates_application_on_approval(
    db_client: AsyncClient, settings: Settings
) -> None:
    _register_fake(settings)
    r = await db_client.post("/api/opportunities/ingest", json={"source": "manual", "text": JD_A})
    assert r.status_code == 201, r.text
    opp = r.json()

    r = await db_client.post(
        "/api/workflows", json={"kind": "apply", "target_id": opp["id"], "options": OPTIONS}
    )
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["state"] == "waiting_approval" and run["current_step"] == 3
    statuses = {s["name"]: s["status"] for s in run["steps"]}
    assert statuses == {
        "analyze": "done",
        "select_cv": "done",
        "generate_cv": "done",
        "draft_message": "waiting",
        "create_application": "pending",
    }
    assert run["context"]["variant_id"] == "general-core"
    assert run["context"]["cv_artifact_id"] and run["context"]["message"].startswith("Hello")
    assert run["suggestion_id"] and run["steps"][3]["suggestion_id"] == run["suggestion_id"]
    r = await db_client.get(f"/api/workflows/{run['id']}")  # a fresh load must agree
    assert r.status_code == 200, r.text
    assert r.json()["steps"][3]["suggestion_id"] == run["suggestion_id"], r.json()["steps"]

    # nothing was written yet: no application, the CV artifact exists (it is a generated file)
    assert opp["id"] not in await _board_opportunity_ids(db_client, "employment")
    r = await db_client.get(f"/api/ai/suggestions/{run['suggestion_id']}")
    assert r.status_code == 200
    sug = r.json()
    assert sug["state"] == "suggested" and sug["target_type"] == "workflow"
    assert sug["payload"]["kind"] == "apply" and sug["payload"]["cv_artifact_id"]

    r = await db_client.post(
        f"/api/workflows/{run['id']}/decision", json={"decision": "approve", "note": "go"}
    )
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["state"] == "completed" and run["suggestion_id"] is None
    assert [s["status"] for s in run["steps"]] == ["done"] * 5
    assert run["steps"][3]["decided"] == "approved"
    app_id = run["context"]["application_id"]

    r = await db_client.get(f"/api/pipeline/applications/{app_id}")
    assert r.status_code == 200
    app = r.json()
    assert app["opportunity_id"] == opp["id"] and app["stage"] == "preparing"
    assert app["cv_artifact_id"] == run["context"]["cv_artifact_id"]
    assert any(
        "approved" in e["title"] and e["body"] == run["context"]["message"] for e in app["events"]
    )
    r = await db_client.get(f"/api/ai/suggestions/{sug['id']}")
    assert r.json()["state"] == "executed"

    # a second apply on the same opportunity fails at its first step, cleanly
    r = await db_client.post(
        "/api/workflows", json={"kind": "apply", "target_id": opp["id"], "options": OPTIONS}
    )
    assert r.status_code == 201
    assert r.json()["state"] == "failed" and "already in the pipeline" in r.json()["error"]

    # decisions are only accepted while waiting
    r = await db_client.post(f"/api/workflows/{run['id']}/decision", json={"decision": "approve"})
    assert r.status_code == 409


@pytest.mark.db
async def test_reject_creates_nothing_and_follow_up_guards_ai_numbers(
    db_client: AsyncClient, settings: Settings
) -> None:
    _register_fake(settings, follow_up_numbers="I have 12 years of experience.")
    r = await db_client.post("/api/opportunities/ingest", json={"source": "manual", "text": JD_B})
    assert r.status_code == 201, r.text
    opp = r.json()
    r = await db_client.post(
        "/api/workflows", json={"kind": "apply", "target_id": opp["id"], "options": OPTIONS}
    )
    assert r.status_code == 201 and r.json()["state"] == "waiting_approval"
    run = r.json()
    r = await db_client.post(
        f"/api/workflows/{run['id']}/decision", json={"decision": "reject", "note": "not now"}
    )
    assert r.status_code == 200
    run = r.json()
    assert run["state"] == "cancelled" and run["error"] == "not now"
    assert run["steps"][3]["status"] == "rejected" and run["steps"][4]["status"] == "pending"
    assert opp["id"] not in await _board_opportunity_ids(db_client, "freelance")
    r = await db_client.get(f"/api/ai/suggestions/{run['steps'][3]['suggestion_id']}")
    assert r.status_code == 200 and r.json()["state"] == "rejected", (
        r.text[:100],
        run["steps"][3].get("suggestion_id"),
        run["suggestion_id"],
        [s.get("suggestion_id") for s in run["steps"]],
    )

    # follow-up on a real application: the AI draft states "12 years" — not in its inputs → the
    # vault template is used instead, and the approval still records the follow-up
    r = await db_client.post("/api/pipeline/applications", json={"opportunity_id": opp["id"]})
    assert r.status_code == 201, r.text
    app = r.json()
    r = await db_client.post(
        "/api/workflows", json={"kind": "follow_up", "target_id": app["id"], "options": OPTIONS}
    )
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["state"] == "waiting_approval"
    assert "AI draft rejected" in run["steps"][1]["summary"] and "12" in run["steps"][1]["summary"]
    assert "12" not in run["context"]["message"] and run["context"]["subject"].startswith(
        "Following up"
    )

    r = await db_client.post(f"/api/workflows/{run['id']}/decision", json={"decision": "approve"})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["state"] == "completed" and run["steps"][2]["output"]["next_follow_up_at"]
    r = await db_client.get(f"/api/pipeline/applications/{app['id']}")
    app = r.json()
    assert app["next_follow_up_at"] and any(e["kind"] == "follow_up" for e in app["events"])

    # list + filters + 404
    r = await db_client.get("/api/workflows", params={"state": "completed"})
    assert r.status_code == 200 and all(x["state"] == "completed" for x in r.json())
    assert any(x["id"] == run["id"] for x in r.json())
    r = await db_client.get(f"/api/workflows/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.db
async def test_follow_up_on_closed_application_fails_and_cancel(
    db_client: AsyncClient, settings: Settings
) -> None:
    _register_fake(settings)
    r = await db_client.post(
        "/api/opportunities/ingest", json={"source": "manual", "text": JD_A + "\nRef: closed"}
    )
    assert r.status_code == 201, r.text
    opp = r.json()
    r = await db_client.post("/api/pipeline/applications", json={"opportunity_id": opp["id"]})
    assert r.status_code == 201, r.text
    app = r.json()
    r = await db_client.patch(
        f"/api/pipeline/applications/{app['id']}",
        json={"stage": "rejected", "clear_follow_up": False},
    )
    assert r.status_code == 200, r.text
    r = await db_client.post("/api/workflows", json={"kind": "follow_up", "target_id": app["id"]})
    assert r.status_code == 201
    assert r.json()["state"] == "failed" and "closed" in r.json()["error"]

    r = await db_client.get("/api/workflows/definitions")
    assert r.status_code == 200 and len(r.json()) == 2


@pytest.mark.db
async def test_sweep_starts_one_gated_run_per_due_follow_up(
    db_client: AsyncClient, settings: Settings
) -> None:
    from datetime import UTC, datetime, timedelta

    _register_fake(settings)
    apps: list[dict[str, Any]] = []
    for n, jd in enumerate((JD_A + "\nRef: sweep-a", JD_B + "\nRef: sweep-b")):
        r = await db_client.post("/api/opportunities/ingest", json={"source": "manual", "text": jd})
        assert r.status_code == 201, r.text
        r = await db_client.post(
            "/api/pipeline/applications", json={"opportunity_id": r.json()["id"]}
        )
        assert r.status_code == 201, r.text
        due = datetime.now(UTC) + timedelta(days=-1 if n == 0 else 3)
        r = await db_client.patch(
            f"/api/pipeline/applications/{r.json()['id']}",
            json={"next_follow_up_at": due.isoformat(), "clear_follow_up": False},
        )
        assert r.status_code == 200, r.text
        apps.append(r.json())

    r = await db_client.post("/api/workflows/sweep")
    assert r.status_code == 200, r.text
    runs = r.json()
    assert [x["target_ref"] for x in runs] == [apps[0]["id"]]  # only the overdue one
    assert runs[0]["state"] == "waiting_approval" and runs[0]["kind"] == "follow_up"

    r = await db_client.post("/api/workflows/sweep")
    assert r.status_code == 200 and r.json() == []  # an active run blocks a second one

    r = await db_client.post(
        f"/api/workflows/{runs[0]['id']}/decision", json={"decision": "approve"}
    )
    assert r.status_code == 200 and r.json()["state"] == "completed"
    r = await db_client.post("/api/workflows/sweep")
    assert r.json() == []  # the approved follow-up rescheduled itself 5 days out

    # the worker registers the same sweep as a task by importing the module (worker/main.py)
    import importlib

    from careeros.core.tasks import registry

    importlib.import_module("careeros.modules.workflows.tasks")
    assert "workflows.sweep_follow_ups" in registry.handlers

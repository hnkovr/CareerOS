# ruff: noqa: E501
from __future__ import annotations

import pytest
from httpx import AsyncClient

from careeros.modules.pipeline.enums import STAGES, PipelineKind, Stage

JD_EMPLOYMENT = """Senior Data Engineer at PipelineTest Inc (remote worldwide)
Requirements:
- dbt, BigQuery, Python
Full-time, $130k per year.
"""

JD_FREELANCE = """Freelance dbt consultant needed for a short-term project. Hourly, $90/h. Upwork-style gig, fixed-price possible."""


def test_stage_maps_are_disjoint_enough() -> None:
    emp, fre = set(STAGES[PipelineKind.employment]), set(STAGES[PipelineKind.freelance])
    assert Stage.applied in emp and Stage.applied not in fre
    assert Stage.proposal in fre and Stage.proposal not in emp
    assert Stage.archived in emp and Stage.archived in fre


@pytest.mark.db
async def test_application_lifecycle(db_client: AsyncClient) -> None:
    # ingest two opportunities
    r = await db_client.post(
        "/api/opportunities/ingest", json={"source": "manual", "text": JD_EMPLOYMENT}
    )
    opp_emp = r.json()
    r = await db_client.post(
        "/api/opportunities/ingest", json={"source": "upwork", "text": JD_FREELANCE}
    )
    opp_fre = r.json()

    # create: kind inferred from the opportunity
    r = await db_client.post("/api/pipeline/applications", json={"opportunity_id": opp_emp["id"]})
    assert r.status_code == 201, r.text
    app_emp = r.json()
    assert app_emp["kind"] == "employment" and app_emp["stage"] == "inbox"
    assert app_emp["opportunity_title"].startswith("Senior Data Engineer")
    assert app_emp["events"][0]["kind"] == "discovered"
    assert app_emp["score_overall"] is not None

    r = await db_client.post("/api/pipeline/applications", json={"opportunity_id": opp_fre["id"]})
    app_fre = r.json()
    assert app_fre["kind"] == "freelance" and app_fre["stage"] == "lead"

    # duplicate application → 409
    r = await db_client.post("/api/pipeline/applications", json={"opportunity_id": opp_emp["id"]})
    assert r.status_code == 409

    # invalid stage for kind → 422
    r = await db_client.patch(
        f"/api/pipeline/applications/{app_emp['id']}", json={"stage": "proposal"}
    )
    assert r.status_code == 422 and "employment" in r.json()["detail"]

    # stage → applied: applied_at set, follow-up auto-scheduled, opportunity synced
    r = await db_client.patch(
        f"/api/pipeline/applications/{app_emp['id']}", json={"stage": "applied"}
    )
    assert r.status_code == 200
    app_emp = r.json()
    assert app_emp["applied_at"] is not None and app_emp["next_follow_up_at"] is not None
    assert any(
        e["kind"] == "stage_change" and e["title"] == "inbox → applied" for e in app_emp["events"]
    )
    r = await db_client.get(f"/api/opportunities/{opp_emp['id']}")
    assert r.json()["status"] == "applied"

    # events + interviews
    r = await db_client.post(
        f"/api/pipeline/applications/{app_emp['id']}/events",
        json={
            "kind": "message_received",
            "title": "Recruiter replied",
            "body": "Wants a call Tuesday",
        },
    )
    assert r.status_code == 200
    r = await db_client.post(
        f"/api/pipeline/applications/{app_emp['id']}/interviews",
        json={"kind": "recruiter_screen", "scheduled_at": "2026-09-01T10:00:00Z"},
    )
    detail = r.json()
    assert detail["interviews"][0]["outcome"] == "pending"
    interview_id = detail["interviews"][0]["id"]
    r = await db_client.patch(
        f"/api/pipeline/applications/{app_emp['id']}/interviews/{interview_id}",
        json={"outcome": "passed"},
    )
    detail = r.json()
    assert detail["interviews"][0]["outcome"] == "passed"
    assert any(e["kind"] == "interview_done" for e in detail["events"])

    # board grouping
    r = await db_client.get("/api/pipeline/board", params={"kind": "employment"})
    board = r.json()
    assert board["stages"][0] == "inbox" and "proposal" not in board["stages"]
    applied_col = next(c for c in board["columns"] if c["stage"] == "applied")
    assert any(a["id"] == app_emp["id"] for a in applied_col["applications"])

    # follow-ups due within horizon
    r = await db_client.get("/api/pipeline/follow-ups", params={"within_days": 10})
    assert any(f["application"]["id"] == app_emp["id"] for f in r.json())

    # terminal stage clears follow-up, closes, archives the opportunity
    r = await db_client.patch(
        f"/api/pipeline/applications/{app_emp['id']}", json={"stage": "rejected"}
    )
    app_emp = r.json()
    assert app_emp["closed_at"] is not None and app_emp["next_follow_up_at"] is None
    r = await db_client.get(f"/api/opportunities/{opp_emp['id']}")
    assert r.json()["status"] == "archived"

    # 404s
    r = await db_client.get("/api/pipeline/applications/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


@pytest.mark.db
async def test_contacts_crud(db_client: AsyncClient) -> None:
    r = await db_client.post(
        "/api/contacts",
        json={
            "name": "Jane Recruiter",
            "company_name": "PipelineTest Inc",
            "email": "jane@pt.example",
            "relationship": "recruiter",
        },
    )
    assert r.status_code == 201, r.text
    contact = r.json()
    assert contact["company_name"] == "PipelineTest Inc"

    # second contact reuses the company (case-insensitive)
    r = await db_client.post(
        "/api/contacts",
        json={
            "name": "Bob HM",
            "company_name": "pipelinetest inc",
            "relationship": "hiring_manager",
        },
    )
    assert r.json()["company_id"] == contact["company_id"]

    r = await db_client.patch(
        f"/api/contacts/{contact['id']}",
        json={"next_action": "Reply about the call", "relationship": "recruiter"},
    )
    assert r.status_code == 200 and r.json()["next_action"] == "Reply about the call"

    r = await db_client.get("/api/contacts", params={"q": "jane"})
    assert r.status_code == 200 and any(c["id"] == contact["id"] for c in r.json())

    r = await db_client.get("/api/companies")
    assert any(c["name"] == "PipelineTest Inc" for c in r.json())

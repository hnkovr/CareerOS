# ruff: noqa: E501
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient


@pytest.mark.db
async def test_notification_center_aggregates_live_state(db_client: AsyncClient) -> None:
    # high-score new opportunity
    r = await db_client.post(
        "/api/opportunities/ingest",
        json={
            "source": "manual",
            "text": (
                "Notify Center Engineer at Beacon Signal Co (remote worldwide). Series A startup, AI-ready platform.\n"
                "Requirements:\n- dbt, BigQuery, Python, SQL, Dagster, ClickHouse\nB2B contract, $150k per year. Async, 3 hours overlap with CET."
            ),
        },
    )
    opp = r.json()
    assert opp["score"]["overall"] >= 80, opp["score"]

    # application with an overdue follow-up and an interview tomorrow
    r = await db_client.post("/api/pipeline/applications", json={"opportunity_id": opp["id"]})
    app = r.json()
    overdue = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    r = await db_client.patch(
        f"/api/pipeline/applications/{app['id']}", json={"next_follow_up_at": overdue}
    )
    assert r.status_code == 200
    tomorrow = (datetime.now(UTC) + timedelta(hours=20)).isoformat()
    r = await db_client.post(
        f"/api/pipeline/applications/{app['id']}/interviews",
        json={"kind": "technical", "scheduled_at": tomorrow},
    )
    assert r.status_code == 200

    # urgent unread email
    r = await db_client.post(
        "/api/inbox/ingest",
        json={
            "from_email": "urgent@beacon.example",
            "subject": "Interview confirmation needed today",
            "body_text": "Please book a time for the interview by tomorrow — calendly.com/beacon",
            "direction": "inbound",
            "provider": "manual",
        },
    )
    assert r.json()["urgency"] == "high"

    r = await db_client.get("/api/notifications")
    assert r.status_code == 200, r.text
    out = r.json()
    kinds = [n["kind"] for n in out["items"]]
    assert "follow_up_overdue" in kinds
    assert "interview_soon" in kinds
    assert "urgent_message" in kinds
    assert out["high"] >= 3 and out["count"] == len(out["items"])
    # high-severity items sort first
    assert out["items"][0]["severity"] == "high"
    follow = next(
        n
        for n in out["items"]
        if n["kind"] == "follow_up_overdue" and n["url_path"] == f"/pipeline/{app['id']}"
    )
    assert follow["detail"] == "overdue"
    # the opportunity is in the pipeline now (status applied? no — stage inbox), status new → high_score item may
    # be absent because creating an application does not change status; assert only when still new
    r2 = await db_client.get(f"/api/opportunities/{opp['id']}")
    if r2.json()["status"] == "new":
        assert any(
            n["kind"] == "high_score_opportunity" and opp["title"].split(" at ")[0] in n["title"]
            for n in out["items"]
        )

    # handling the state clears the notification: mark the message read
    msg_r = await db_client.get("/api/inbox/messages", params={"unread_only": True, "limit": 100})
    for m in msg_r.json():
        if m["from_email"] == "urgent@beacon.example":
            await db_client.patch(f"/api/inbox/messages/{m['id']}", json={"mark_read": True})
    r = await db_client.get("/api/notifications")
    assert not any(
        n["kind"] == "urgent_message" and n["detail"] == "urgent@beacon.example"
        for n in r.json()["items"]
    )

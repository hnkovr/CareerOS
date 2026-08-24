from __future__ import annotations

import pytest
from httpx import AsyncClient

from careeros.modules.ai.suggestions import LEGAL_TRANSITIONS, STATES


def test_transition_table_is_sound() -> None:
    assert set(LEGAL_TRANSITIONS) == set(STATES)
    assert LEGAL_TRANSITIONS["executed"] == set() and LEGAL_TRANSITIONS["rejected"] == set()
    assert "executed" not in LEGAL_TRANSITIONS["suggested"], "execution requires prior approval"
    assert "executed" in LEGAL_TRANSITIONS["approved"]


@pytest.mark.db
async def test_suggestion_approval_flow_and_reply_sent(db_client: AsyncClient, settings) -> None:  # type: ignore[no-untyped-def]
    from careeros.modules.ai.deps import get_provider_registry
    from careeros.modules.ai.providers.fake import FakeProvider
    from careeros.modules.inbox.schemas import ReplyDraftOutput

    def respond(req, schema):  # type: ignore[no-untyped-def]
        if schema is ReplyDraftOutput:
            return {
                "subject": "Re: Approval flow role",
                "body": "Thanks — happy to talk on Wednesday afternoon. Dana",
            }
        return {}

    get_provider_registry(settings).register(FakeProvider(respond), make_default=True)

    # opportunity + application + linked email
    r = await db_client.post(
        "/api/opportunities/ingest",
        json={
            "source": "manual",
            "text": (
                "Approval Flow Engineer at Gatekeeper Systems (remote worldwide).\n"
                "Requirements:\n- Python, dbt\nB2B, $135k/year."
            ),
        },
    )
    opp = r.json()
    r = await db_client.post("/api/pipeline/applications", json={"opportunity_id": opp["id"]})
    app = r.json()
    r = await db_client.post(
        "/api/inbox/ingest",
        json={
            "from_email": "gk@gatekeeper.example",
            "subject": "Approval Flow Engineer at Gatekeeper Systems",
            "body_text": (
                "Hi Dana, about the Approval Flow Engineer at Gatekeeper Systems — "
                "interested in a chat?"
            ),
            "direction": "inbound",
            "provider": "manual",
        },
    )
    msg = r.json()
    assert msg["links"]["application_id"] == app["id"]

    # draft a reply → suggestion in 'suggested'
    r = await db_client.post(
        f"/api/inbox/messages/{msg['id']}/suggest-reply", json={"intent": "follow_up"}
    )
    suggestion_id = r.json()["suggestion_id"]
    r = await db_client.get("/api/ai/suggestions", params={"state": "suggested"})
    assert any(s["id"] == suggestion_id for s in r.json())

    # illegal: suggested → executed
    r = await db_client.patch(f"/api/ai/suggestions/{suggestion_id}", json={"state": "executed"})
    assert (r.status_code == 422 and "approval" not in r.text) or r.status_code == 422

    # approve
    r = await db_client.patch(
        f"/api/ai/suggestions/{suggestion_id}", json={"state": "approved", "note": "looks good"}
    )
    assert r.status_code == 200 and r.json()["state"] == "approved" and r.json()["decided_at"]

    # mark reply as sent → suggestion executed + timeline event message_sent
    r = await db_client.post(
        f"/api/inbox/messages/{msg['id']}/reply-sent", json={"suggestion_id": suggestion_id}
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "executed"
    r = await db_client.get(f"/api/pipeline/applications/{app['id']}")
    kinds = [e["kind"] for e in r.json()["events"]]
    assert "message_sent" in kinds
    # follow-up auto-scheduled by the message_sent event
    assert r.json()["next_follow_up_at"] is not None

    # terminal: executed → rejected is illegal
    r = await db_client.patch(f"/api/ai/suggestions/{suggestion_id}", json={"state": "rejected"})
    assert r.status_code == 422

    # reject path on a fresh suggestion
    r = await db_client.post(
        f"/api/inbox/messages/{msg['id']}/suggest-reply", json={"intent": "decline"}
    )
    s2 = r.json()["suggestion_id"]
    r = await db_client.patch(
        f"/api/ai/suggestions/{s2}", json={"state": "rejected", "note": "tone off"}
    )
    assert r.json()["state"] == "rejected" and r.json()["decision_note"] == "tone off"

    r = await db_client.get("/api/ai/suggestions/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404

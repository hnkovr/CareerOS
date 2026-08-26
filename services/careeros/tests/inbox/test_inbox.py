# ruff: noqa: E501
from __future__ import annotations

import pytest
from httpx import AsyncClient

from careeros.modules.inbox.classify import classify_email, normalize_subject, parse_raw_email
from careeros.modules.inbox.enums import MessageClass, Urgency
from careeros.modules.inbox.schemas import EmailIn

REJECTION = EmailIn(
    from_email="talent@meridian.example",
    subject="Your application to Meridian",
    body_text="Thank you for your interest. Unfortunately we have decided to proceed with other candidates at this time.",
)
INTERVIEW = EmailIn(
    from_email="jane@acme.example",
    subject="Re: Senior Data Engineer",
    body_text="Great profile! Could we schedule a call this week? Here is my calendly.com/jane link. Please book a time by Friday.",
)
LINKEDIN_NOTIFICATION = EmailIn(
    from_email="jobs-noreply@linkedin.com",
    subject="You appeared in 12 searches",
    body_text="See who's viewed your profile. Unsubscribe from marketing emails here.",
)
RECRUITER_JD = EmailIn(
    from_email="maria@techrecruit.example",
    from_name="Maria Recruiter",
    subject="Exciting opportunity: Senior Data Engineer (remote EU)",
    body_text="""Hi Dana, I came across your profile and think you'd be perfect for this open role.

Senior Data Engineer at Quasar Labs (remote, EU only). B2B contract, €80-95/hour.

Requirements:
- dbt, BigQuery, Python, SQL
- Dagster or Airflow

Nice to have: ClickHouse, Terraform. Interested?""",
)


def test_rules_classification() -> None:
    assert classify_email(REJECTION).classification == MessageClass.rejection
    verdict = classify_email(INTERVIEW)
    assert verdict.classification == MessageClass.interview and verdict.urgency == Urgency.high
    assert verdict.deadline_hint and "friday" in verdict.deadline_hint.lower()
    ln = classify_email(LINKEDIN_NOTIFICATION)
    assert ln.classification == MessageClass.platform_notification
    assert any("linkedin" in s.lower() for s in ln.signals)
    rec = classify_email(RECRUITER_JD)
    assert rec.classification == MessageClass.recruiter_outreach


def test_parse_raw_email_and_subject_normalization() -> None:
    raw = """From: "Jane Doe" <jane.doe@example.com>
To: dana@example.com
Subject: Re: Re: Fwd: Lead Data Platform Engineer
Date: Mon, 25 Aug 2026

Hi Dana,

are you still interested?"""
    parsed = parse_raw_email(raw)
    assert parsed["from_email"] == "jane.doe@example.com"
    assert parsed["from_name"] == "Jane Doe"
    assert parsed["subject"] == "Re: Re: Fwd: Lead Data Platform Engineer"
    assert "are you still interested?" in str(parsed["body_text"])
    assert normalize_subject(str(parsed["subject"])) == "Lead Data Platform Engineer"
    assert normalize_subject(None) == "(no subject)"


@pytest.mark.db
async def test_ingest_classify_extract_link_flow(db_client: AsyncClient) -> None:
    # recruiter JD → classified, opportunity auto-extracted and scored
    r = await db_client.post("/api/inbox/ingest", json=RECRUITER_JD.model_dump(mode="json"))
    assert r.status_code == 201, r.text
    msg = r.json()
    assert msg["classification"] == "recruiter_outreach"
    assert msg["extracted_opportunity"] is True and msg["links"]["opportunity_id"]
    opp_id = msg["links"]["opportunity_id"]
    r = await db_client.get(f"/api/opportunities/{opp_id}")
    assert r.status_code == 200
    opp = r.json()
    assert opp["source"] == "recruiter" and "dbt" in opp["technologies"]
    assert opp["score"]["recommendation"] == "reply_now"

    # duplicate paste → same message, no second opportunity
    r = await db_client.post("/api/inbox/ingest", json=RECRUITER_JD.model_dump(mode="json"))
    assert r.status_code == 201 and r.json()["id"] == msg["id"]

    # follow-up in the same thread links to the same opportunity via title match, threads together
    followup = EmailIn(
        from_email="maria@techrecruit.example",
        subject="Re: Exciting opportunity: Senior Data Engineer (remote EU)",
        body_text="Hi Dana, following up on the Senior Data Engineer at Quasar Labs role — any update?",
    )
    r = await db_client.post("/api/inbox/ingest", json=followup.model_dump(mode="json"))
    msg2 = r.json()
    assert msg2["thread_id"] == msg["thread_id"]
    assert msg2["links"]["opportunity_id"] == opp_id
    assert msg2["extracted_opportunity"] is False

    r = await db_client.get(f"/api/inbox/threads/{msg['thread_id']}")
    assert r.status_code == 200 and r.json()["message_count"] == 2

    # rejection is classified but never extracts
    r = await db_client.post("/api/inbox/ingest", json=REJECTION.model_dump(mode="json"))
    rej = r.json()
    assert rej["classification"] == "rejection" and rej["extracted_opportunity"] is False

    # manual reclassification + mark read
    r = await db_client.patch(
        f"/api/inbox/messages/{rej['id']}",
        json={"classification": "application_update", "mark_read": True},
    )
    updated = r.json()
    assert updated["classification"] == "application_update" and updated["classified_by"] == "user"
    assert updated["read_at"] is not None

    # stats + filters
    r = await db_client.get("/api/inbox/stats")
    stats = r.json()
    assert stats["total"] >= 3 and stats["needs_attention"] >= 2
    r = await db_client.get("/api/inbox/messages", params={"needs_attention": True})
    assert all(
        m["classification"]
        in (
            "new_opportunity",
            "recruiter_outreach",
            "client_lead",
            "interview",
            "offer",
            "follow_up_required",
        )
        for m in r.json()
    )


@pytest.mark.db
async def test_contact_linking_and_application_timeline(db_client: AsyncClient) -> None:
    # known contact → linked; existing application → timeline event
    r = await db_client.post(
        "/api/contacts",
        json={
            "name": "Rita Recruiter",
            "email": "rita@quasar.example",
            "relationship": "recruiter",
        },
    )
    contact = r.json()
    r = await db_client.post(
        "/api/opportunities/ingest",
        json={
            "source": "manual",
            "text": "Staff Data Engineer at Nimbus Analytics (remote worldwide). Requirements:\n- Python, dbt\nB2B, $140k/year.",
        },
    )
    opp = r.json()
    r = await db_client.post("/api/pipeline/applications", json={"opportunity_id": opp["id"]})
    app = r.json()

    email = EmailIn(
        from_email="rita@quasar.example",
        subject="Staff Data Engineer at Nimbus Analytics — next steps",
        body_text="Hi Dana, about the Staff Data Engineer at Nimbus Analytics: the team would like to schedule a call.",
    )
    r = await db_client.post("/api/inbox/ingest", json=email.model_dump(mode="json"))
    msg = r.json()
    assert msg["links"]["contact_id"] == contact["id"]
    assert msg["links"]["opportunity_id"] == opp["id"]
    assert msg["links"]["application_id"] == app["id"]

    r = await db_client.get(f"/api/pipeline/applications/{app['id']}")
    events = [e["kind"] for e in r.json()["events"]]
    assert "message_received" in events


@pytest.mark.db
async def test_suggest_reply_records_suggestion(db_client: AsyncClient, settings) -> None:  # type: ignore[no-untyped-def]
    from careeros.modules.ai.deps import get_provider_registry
    from careeros.modules.ai.providers.fake import FakeProvider
    from careeros.modules.inbox.schemas import ReplyDraftOutput

    def respond(req, schema):  # type: ignore[no-untyped-def]
        assert schema is ReplyDraftOutput
        return {
            "subject": "Re: Senior Data Engineer",
            "body": "Thanks Maria — yes, interested. I'm available for a call Tuesday or Wednesday afternoon CET. Dana",
            "notes": "Verify the rate range before the call.",
        }

    get_provider_registry(settings).register(FakeProvider(respond), make_default=True)
    # ingest the message this test replies to: rows are truncated between tests (GH #24), so
    # reading "whatever recruiter message is already there" would depend on execution order
    ingested = await db_client.post("/api/inbox/ingest", json=RECRUITER_JD.model_dump(mode="json"))
    assert ingested.status_code == 201, ingested.text
    r = await db_client.get(
        "/api/inbox/messages", params={"classification": "recruiter_outreach", "limit": 1}
    )
    assert r.status_code == 200 and r.json(), r.text
    message_id = r.json()[0]["id"]
    r = await db_client.post(
        f"/api/inbox/messages/{message_id}/suggest-reply", json={"intent": "follow_up"}
    )
    assert r.status_code == 200, r.text
    reply = r.json()
    assert (
        reply["body"].startswith("Thanks Maria") and reply["suggestion_id"] and reply["ai_run_id"]
    )
    # the draft is a Suggestion — nothing was sent anywhere
    r = await db_client.get("/api/ai/runs", params={"entity_type": "message", "limit": 5})
    assert any(run["prompt_id"] == "inbox_reply" for run in r.json())

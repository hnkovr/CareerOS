# ruff: noqa: E501
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.db
async def test_daily_brief_ranks_actions_and_optionally_narrates(
    db_client: AsyncClient, settings
) -> None:  # type: ignore[no-untyped-def]
    from careeros.modules.ai.deps import get_provider_registry
    from careeros.modules.ai.providers.fake import FakeProvider
    from careeros.modules.insights.brief import DailyBriefOutput

    r = await db_client.post(
        "/api/opportunities/ingest",
        json={
            "source": "manual",
            "text": "Brief Test Engineer at Sunrise Data (remote worldwide). Series A startup, AI-ready.\nRequirements:\n- dbt, BigQuery, Python, SQL, Dagster, ClickHouse\nB2B contract, $150k per year. Async, 3 hours overlap with CET.",
        },
    )
    opp = r.json()
    assert opp["score"]["overall"] >= 65

    r = await db_client.get("/api/insights/brief")
    assert r.status_code == 200, r.text
    brief = r.json()
    assert brief["greeting"].startswith("Good") and brief["date"]
    assert brief["stats"]["new_opportunities"] >= 1
    assert brief["stats"]["best_opportunity"]["score"] >= opp["score"]["overall"] - 1
    kinds = [a["kind"] for a in brief["actions"]]
    assert "apply" in kinds
    assert brief["actions"] == sorted(brief["actions"], key=lambda a: a["priority"])
    assert brief["narrative"] is None

    def respond(req, schema):  # type: ignore[no-untyped-def]
        assert schema is DailyBriefOutput
        return {
            "narrative": "One strong new opportunity today; apply first, then clear the overdue follow-ups."
        }

    get_provider_registry(settings).register(FakeProvider(respond), make_default=True)
    r = await db_client.get("/api/insights/brief", params={"narrative": True})
    assert r.status_code == 200
    assert r.json()["narrative"].startswith("One strong") and r.json()["ai_run_id"]

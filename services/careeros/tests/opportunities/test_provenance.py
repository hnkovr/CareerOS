# ruff: noqa: E501
"""ADR-016: provenance rows, snapshots with fingerprints, diff, identity lookups."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.config import Settings
from careeros.modules.ai.deps import build_ai_service
from careeros.modules.opportunities.dedup import fingerprint, identity_candidates, normalize_url
from careeros.modules.opportunities.deps import (
    find_opportunity_id_by_external_id,
    find_opportunity_id_by_url,
)
from careeros.modules.opportunities.enums import AUTHORITY_ORDER, FieldSource, SourceRelation
from careeros.modules.opportunities.models import Opportunity, OpportunityRaw
from careeros.modules.opportunities.schemas import (
    IngestRequest,
    OpportunityExtraction,
    SnapshotIn,
    SourceIn,
)
from careeros.modules.opportunities.service import OpportunityNotFound, OpportunityService
from careeros.modules.vault.deps import get_vault

JD = """Senior Data Engineer (Remote, worldwide)
Company: Orbital Analytics
We are a Series A startup building an AI-ready analytics platform. You will own our dbt + Dagster stack on BigQuery
and ClickHouse. Contractor (B2B) engagements welcome.

Requirements:
- 5+ years with Python and SQL
- dbt, Dagster, BigQuery

Compensation: $110k - $150k per year. Fully remote, async, 3 hours overlap with CET.
"""
# The same page template on two loads: only counters, dates and tracking params differ.
NOISE_A = (
    "\n1234 views · posted 3 days ago · 26 August 2026 · https://hh.ru/vacancy/1?utm_source=a\n"
)
NOISE_B = "\n57 views · posted today · 27.08.2026 · https://hh.ru/vacancy/1?utm_source=b\n"
NOISE_RU_A = "\n12 просмотров · обновлено 3 дня назад · 26 августа 2026\n"
NOISE_RU_B = "\n480 просмотров · обновлено вчера · 27 августа 2026\n"
URL = "https://www.hh.ru/vacancy/123?utm_source=share&from=share_ios"


def _svc(settings: Settings, session: AsyncSession, user_id: uuid.UUID) -> OpportunityService:
    return OpportunityService(
        settings,
        get_vault(settings),
        build_ai_service(settings, session=session, user_id=user_id),
        session=session,
        user_id=user_id,
    )


def _ingest(**overrides: object) -> IngestRequest:
    base: dict[str, object] = {
        "source": "hh",
        "platform": "hh",
        "external_id": "123",
        "url": URL,
        "text": JD,
    }
    base.update(overrides)
    return IngestRequest.model_validate(base)


# ----------------------------------------------------------------------------- pure


def test_fingerprint_ignores_counters_dates_and_urls() -> None:
    a = fingerprint(JD + NOISE_A)
    assert a == fingerprint(JD + NOISE_B) and a.startswith("fp:") and len(a) == 23
    assert fingerprint(JD + NOISE_RU_A) == fingerprint(JD + NOISE_RU_B)
    assert fingerprint("  Senior   DE ") == fingerprint("senior de")


def test_fingerprint_changes_when_the_job_changes() -> None:
    assert fingerprint(JD) != fingerprint(JD.replace("$110k - $150k", "$120k - $160k"))
    assert fingerprint(JD) != fingerprint(JD.replace("(Remote, worldwide)", "(Hybrid, Berlin)"))


def test_identity_candidates_are_ordered_strongest_first() -> None:
    fp = fingerprint(JD)
    layers = identity_candidates(
        platform="hh",
        external_id="123",
        canonical_url=URL,
        company="Orbital Analytics",
        title="Senior Data Engineer",
        location=None,
        fingerprint=fp,
    )
    assert [layer for layer, _ in layers] == [
        "external_id",
        "canonical_url",
        "company_title_location",
        "fingerprint",
    ]
    assert layers[0][1] == "hh:123"
    assert layers[1][1] == normalize_url(URL) == "https://hh.ru/vacancy/123?from=share_ios"
    assert layers[3][1] == fp
    assert (
        identity_candidates(
            platform=None,
            external_id=None,
            canonical_url=None,
            company=None,
            title="x",
            location=None,
            fingerprint=None,
        )
        == []
    )


def test_authority_order_follows_enum_declaration() -> None:
    assert AUTHORITY_ORDER[0] == FieldSource.employer_api
    assert AUTHORITY_ORDER.index(FieldSource.board_api) < AUTHORITY_ORDER.index(
        FieldSource.board_page
    )
    assert AUTHORITY_ORDER.index(FieldSource.aggregator_estimate) < AUTHORITY_ORDER.index(
        FieldSource.archive
    )
    assert AUTHORITY_ORDER[-1] == FieldSource.manual
    assert set(SourceRelation) >= {
        SourceRelation.primary,
        SourceRelation.same_as,
        SourceRelation.possible_duplicate,
    }


# ----------------------------------------------------------------------------- service (db)


@pytest.mark.db
async def test_ingest_records_primary_source_and_links_raw(
    settings: Settings, session: AsyncSession, user_id: uuid.UUID
) -> None:
    svc = _svc(settings, session, user_id)
    detail = await svc.ingest(_ingest())
    assert detail.platform == "hh" and detail.external_id == "123"
    assert detail.canonical_url == normalize_url(URL) and "utm_source" not in detail.canonical_url

    sources = await svc.list_sources(detail.id)
    assert len(sources) == 1
    primary = sources[0]
    assert primary.relation == SourceRelation.primary and primary.platform == "hh"
    assert primary.external_id == "123" and primary.canonical_url == detail.canonical_url
    assert primary.authority == FieldSource.board_page  # pasted text, not an API read
    assert primary.strategy == "paste"

    row = await session.get(Opportunity, detail.id)
    assert row is not None
    raw = await session.get(OpportunityRaw, row.raw_id)
    assert raw is not None and raw.opportunity_id == detail.id and primary.raw_id == raw.id
    assert raw.fingerprint == fingerprint(JD) and raw.fetched_url == URL
    assert raw.extracted and raw.extracted["title"].startswith("Senior Data Engineer")
    assert row.field_evidence and row.field_evidence["compensation"][0]["source"] == "board_page"

    # identity lookups (ADR-016 §4)
    assert (
        await find_opportunity_id_by_external_id(session, "hh", "123", user_id=user_id) == detail.id
    )
    assert await find_opportunity_id_by_external_id(session, "hh", "999", user_id=user_id) is None
    assert (
        await find_opportunity_id_by_url(
            session, "https://hh.ru/vacancy/123/?from=share_ios&utm_medium=y", user_id=user_id
        )
        == detail.id
    )
    assert await find_opportunity_id_by_url(session, URL, user_id=user_id) == detail.id
    assert (
        await find_opportunity_id_by_url(session, "https://hh.ru/vacancy/124", user_id=user_id)
        is None
    )


@pytest.mark.db
async def test_structured_api_ingest_gets_board_api_authority_and_duplicate_flag(
    settings: Settings, session: AsyncSession, user_id: uuid.UUID
) -> None:
    svc = _svc(settings, session, user_id)
    first = await svc.ingest(_ingest())
    again = await svc.ingest(
        _ingest(
            text=None,
            url="https://hh.ru/vacancy/123?from=share_android",
            structured=OpportunityExtraction(
                title="Senior Data Engineer", company="Orbital Analytics"
            ),
        )
    )
    assert again.possible_duplicate_of == first.id  # provider id wins before any text similarity
    sources = await svc.list_sources(again.id)
    assert sources[0].authority == FieldSource.board_api and sources[0].strategy == "structured"
    manual = await svc.ingest(IngestRequest(source="manual", text="Some other posting, no salary"))  # type: ignore[arg-type]
    manual_sources = await svc.list_sources(manual.id)
    assert (
        manual_sources[0].platform == "other" and manual_sources[0].authority == FieldSource.manual
    )
    assert manual.platform is None


@pytest.mark.db
async def test_snapshot_unchanged_then_salary_change_creates_raw_and_diff(
    settings: Settings, session: AsyncSession, user_id: uuid.UUID
) -> None:
    svc = _svc(settings, session, user_id)
    detail = await svc.ingest(_ingest(text=JD + NOISE_A))
    first_raw_id = (await svc.list_snapshots(detail.id))[0].id

    same, created = await svc.record_snapshot(
        detail.id, SnapshotIn(raw_text=JD + NOISE_B, strategy="public_html", fetched_url=URL)
    )
    assert created is False and same.id == first_raw_id
    assert len(await svc.list_snapshots(detail.id)) == 1

    changed_text = JD.replace("$110k - $150k", "$120k - $160k") + NOISE_B
    snap, created = await svc.record_snapshot(
        detail.id,
        SnapshotIn(raw_text=changed_text, strategy="public_html", fetched_url=URL, quality=0.9),
    )
    assert created is True and snap.id != first_raw_id and snap.strategy == "public_html"
    snapshots = await svc.list_snapshots(detail.id)
    assert [s.id for s in snapshots] == [first_raw_id, snap.id]
    assert snapshots[1].fingerprint == fingerprint(changed_text) and snapshots[1].quality == 0.9

    after = await svc.get(detail.id)
    assert (
        after.compensation and after.compensation.max == 160_000 and after.raw_text == changed_text
    )
    row = await session.get(Opportunity, detail.id)
    assert row is not None and row.raw_id == snap.id
    assert len(row.field_evidence["compensation"]) == 2  # both claims kept

    diff = await svc.diff(detail.id)
    assert diff.from_raw_id == first_raw_id and diff.to_raw_id == snap.id
    changed_fields = {c.field: c for c in diff.changes}
    assert set(changed_fields) == {"compensation"}
    assert changed_fields["compensation"].before["max"] == 150_000
    assert changed_fields["compensation"].after["max"] == 160_000

    explicit = await svc.diff(detail.id, from_raw_id=snap.id, to_raw_id=first_raw_id)
    assert explicit.changes[0].after["max"] == 150_000
    with pytest.raises(OpportunityNotFound):
        await svc.diff(detail.id, to_raw_id=uuid.uuid4())


@pytest.mark.db
async def test_archive_and_weaker_sources_never_overwrite_the_live_view(
    settings: Settings, session: AsyncSession, user_id: uuid.UUID
) -> None:
    svc = _svc(settings, session, user_id)
    detail = await svc.ingest(
        _ingest(
            text=None,
            structured=OpportunityExtraction(
                title="Senior Data Engineer",
                company="Orbital Analytics",
                compensation={"min": 110000, "max": 150000, "currency": "USD", "period": "year"},  # type: ignore[arg-type]
            ),
        )
    )  # capture_method=structured → board_api evidence
    live_raw_id = (await svc.list_snapshots(detail.id))[0].id

    archived, created = await svc.record_snapshot(
        detail.id,
        SnapshotIn(
            raw_text="Senior Data Engineer\nOrbital Analytics\n$90k - $100k per year",
            is_archive=True,
            archive_ts=datetime(2026, 1, 1, tzinfo=UTC),
            strategy="wayback",
        ),
    )
    assert created and archived.is_archive
    row = await session.get(Opportunity, detail.id)
    assert row is not None and row.raw_id == live_raw_id  # history only
    assert row.compensation and row.compensation["max"] == 150000
    assert {e["source"] for e in row.field_evidence["compensation"]} == {"board_api", "archive"}

    estimate, created = await svc.record_snapshot(
        detail.id,
        SnapshotIn(
            raw_text="aggregator copy",
            authority=FieldSource.aggregator_estimate,
            extracted={
                "title": "Senior Data Engineer",
                "compensation": {"min": 80000, "max": 90000, "currency": "USD", "period": "year"},
            },
        ),
    )
    assert created
    await session.refresh(row)
    assert row.raw_id == estimate.id  # live snapshot: it is the current capture …
    assert row.compensation["max"] == 150000  # … but the board API claim outranks the estimate
    assert len(row.field_evidence["compensation"]) == 3
    assert (await svc.get(detail.id)).title == "Senior Data Engineer"


@pytest.mark.db
async def test_record_source_upserts_by_external_id_or_canonical_url(
    settings: Settings, session: AsyncSession, user_id: uuid.UUID
) -> None:
    svc = _svc(settings, session, user_id)
    detail = await svc.ingest(_ingest())
    rh = SourceIn(
        platform="rockethunt",
        external_id="rh-77",
        source_url="https://rockethunt.io/vacancy/rh-77?utm_source=tg",
        original_url="https://hh.ru/vacancy/123",
        relation=SourceRelation.aggregates,
        authority=FieldSource.aggregator,
        strategy="public_html",
    )
    one = await svc.record_source(detail.id, rh)
    two = await svc.record_source(
        detail.id, rh.model_copy(update={"confidence": 0.7, "meta": {"salary_estimate": True}})
    )
    assert one.id == two.id and two.confidence == 0.7 and two.meta == {"salary_estimate": True}
    assert (
        two.canonical_url == "https://rockethunt.io/vacancy/rh-77"
        and two.original_url == rh.original_url
    )

    site = SourceIn(
        platform="website",
        source_url="https://orbital.example/jobs/42/?ref=li",
        relation=SourceRelation.same_as,
        authority=FieldSource.employer_page,
    )
    a = await svc.record_source(detail.id, site)
    b = await svc.record_source(
        detail.id, site.model_copy(update={"source_url": "https://www.orbital.example/jobs/42"})
    )
    assert a.id == b.id and b.canonical_url == "https://orbital.example/jobs/42"

    sources = await svc.list_sources(detail.id)
    assert [s.relation for s in sources] == [
        SourceRelation.primary,
        SourceRelation.aggregates,
        SourceRelation.same_as,
    ]
    assert (
        await find_opportunity_id_by_external_id(session, "rockethunt", "rh-77", user_id=user_id)
        == detail.id
    )
    with pytest.raises(OpportunityNotFound):
        await svc.record_source(uuid.uuid4(), site)


@pytest.mark.db
async def test_merge_field_evidence_keeps_conflicts(
    settings: Settings, session: AsyncSession, user_id: uuid.UUID
) -> None:
    svc = _svc(settings, session, user_id)
    detail = await svc.ingest(_ingest())
    merged = await svc.merge_field_evidence(
        detail.id,
        [
            {
                "field": "location",
                "value": "Berlin",
                "source": "aggregator",
                "source_url": "https://agg/1",
            },
            {
                "field": "location",
                "value": "Berlin",
                "source": "aggregator",
                "source_url": "https://agg/1",
            },
            {
                "field": "location",
                "value": "Remote",
                "source": "employer_page",
                "source_url": "https://acme/1",
            },
        ],
    )
    assert [e["value"] for e in merged["location"]] == ["Berlin", "Remote"]
    assert "compensation" in merged  # ingest evidence survived the merge
    rows = (await session.scalars(select(Opportunity).where(Opportunity.id == detail.id))).all()
    assert rows[0].field_evidence == merged


# ----------------------------------------------------------------------------- API (db)


@pytest.mark.db
async def test_provenance_endpoints(db_client: AsyncClient) -> None:
    r = await db_client.post(
        "/api/opportunities/ingest",
        json={"source": "hh", "platform": "hh", "external_id": "555", "url": URL, "text": JD},
    )
    assert r.status_code == 201, r.text
    o = r.json()
    assert (
        o["platform"] == "hh"
        and o["external_id"] == "555"
        and o["canonical_url"] == normalize_url(URL)
    )

    r = await db_client.get(f"/api/opportunities/{o['id']}/sources")
    assert r.status_code == 200 and len(r.json()) == 1
    assert r.json()[0]["relation"] == "primary" and r.json()[0]["authority"] == "board_page"

    r = await db_client.get(f"/api/opportunities/{o['id']}/snapshots")
    assert r.status_code == 200 and len(r.json()) == 1
    snap = r.json()[0]
    assert snap["fingerprint"] == fingerprint(JD) and snap["opportunity_id"] == o["id"]

    r = await db_client.get(f"/api/opportunities/{o['id']}/diff")
    assert r.status_code == 200
    assert (
        r.json()["to_raw_id"] == snap["id"]
        and r.json()["from_raw_id"] is None
        and r.json()["changes"] == []
    )

    r = await db_client.get(
        f"/api/opportunities/{o['id']}/diff", params={"from": snap["id"], "to": str(uuid.uuid4())}
    )
    assert r.status_code == 404
    missing = "00000000-0000-0000-0000-000000000000"
    for path in ("sources", "snapshots", "diff"):
        assert (await db_client.get(f"/api/opportunities/{missing}/{path}")).status_code == 404

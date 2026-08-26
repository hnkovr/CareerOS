"""Cross-module entry points for the opportunities context (ADR-008: service-level access only)."""

from __future__ import annotations

import importlib
import uuid

from sqlalchemy.ext.asyncio import AsyncSession


async def opportunity_context_text(session: AsyncSession, opportunity_id: uuid.UUID) -> str | None:
    """Text a CV generation may tailor to: title, company, requirements, technologies, description.

    Resolved dynamically so the CV slice stays importable while the opportunities slice lands.
    """
    try:
        models = importlib.import_module("careeros.modules.opportunities.models")
    except ModuleNotFoundError:
        return None
    row = await session.get(models.Opportunity, opportunity_id)
    if row is None:
        return None
    parts = [
        f"{row.title} @ {row.company_name or ''}",
        "Requirements: " + "; ".join(row.requirements or []),
        "Preferred: " + "; ".join(row.preferred or []),
        "Technologies: " + ", ".join(row.technologies or []),
        row.description_md or "",
    ]
    return "\n".join(p for p in parts if p)


async def find_opportunity_id_by_url(
    session: AsyncSession, url: str, *, user_id: uuid.UUID | None = None
) -> uuid.UUID | None:
    """Oldest opportunity captured from ``url`` (used to link platform application observations).

    Matches the normalised URL against ``canonical_url`` first (tracking parameters, ``www.`` and
    trailing slashes do not matter — ADR-016 §4), then falls back to the exact ``url`` for rows
    that predate canonicalisation.
    """
    from sqlalchemy import select

    from careeros.modules.opportunities.dedup import normalize_url

    models = importlib.import_module("careeros.modules.opportunities.models")
    canonical = normalize_url(url)
    for condition in (
        models.Opportunity.canonical_url == canonical,
        models.Opportunity.url == url,
    ):
        stmt = select(models.Opportunity.id).where(condition)
        if user_id is not None:
            stmt = stmt.where(models.Opportunity.user_id == user_id)
        found = await session.scalar(stmt.order_by(models.Opportunity.created_at).limit(1))
        if found is not None:
            return found
    return None


async def find_opportunity_id_by_external_id(
    session: AsyncSession, platform: str, external_id: str, *, user_id: uuid.UUID | None = None
) -> uuid.UUID | None:
    """Oldest opportunity known under the platform's own id (ADR-016 identity layer 1).

    Checks the opportunity's own ``(platform, external_id)`` first, then ``opportunity_source``
    rows — a job first seen on an aggregator and later on the employer board is one job.
    """
    from sqlalchemy import select

    models = importlib.import_module("careeros.modules.opportunities.models")
    stmt = select(models.Opportunity.id).where(
        models.Opportunity.platform == platform, models.Opportunity.external_id == external_id
    )
    if user_id is not None:
        stmt = stmt.where(models.Opportunity.user_id == user_id)
    found = await session.scalar(stmt.order_by(models.Opportunity.created_at).limit(1))
    if found is not None:
        return found
    src = select(models.OpportunitySource.opportunity_id).where(
        models.OpportunitySource.platform == platform,
        models.OpportunitySource.external_id == external_id,
    )
    if user_id is not None:
        src = src.where(models.OpportunitySource.user_id == user_id)
    return await session.scalar(src.order_by(models.OpportunitySource.created_at).limit(1))

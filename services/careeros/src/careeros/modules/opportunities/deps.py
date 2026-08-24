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


async def find_opportunity_id_by_url(session: AsyncSession, url: str) -> uuid.UUID | None:
    """Oldest opportunity captured from ``url`` (used to link platform application observations)."""
    from sqlalchemy import select

    models = importlib.import_module("careeros.modules.opportunities.models")
    return await session.scalar(
        select(models.Opportunity.id)
        .where(models.Opportunity.url == url)
        .order_by(models.Opportunity.created_at)
        .limit(1)
    )

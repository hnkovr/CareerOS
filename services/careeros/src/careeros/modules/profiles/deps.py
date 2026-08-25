"""Cross-module entry points for the profiles context (ADR-008: service-level access only)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.modules.profiles.models import ProfileSnapshot
from careeros.modules.vault.enums import Platform


async def latest_snapshot_preferences(
    session: AsyncSession, user_id: uuid.UUID, platform: Platform | str
) -> dict[str, Any] | None:
    """``preferences`` of the newest snapshot for ``platform`` (None when there is none)."""
    snap = await session.scalar(
        select(ProfileSnapshot)
        .where(ProfileSnapshot.user_id == user_id, ProfileSnapshot.platform == str(platform))
        .order_by(ProfileSnapshot.captured_at.desc())
        .limit(1)
    )
    return dict(snap.preferences or {}) if snap is not None else None

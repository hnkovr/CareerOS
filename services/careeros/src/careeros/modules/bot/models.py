"""Bot-owned operational state.

Deliberately small. A preferred platform set is an operational PREFERENCE, not a
canonical career fact, so it lives in Postgres rather than the vault (invariant 1,
ADR 002). Nothing here is ever a source of truth for anything the CV or profiles
project from.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from careeros.core.db import Base, OwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class BotPreference(UUIDPrimaryKeyMixin, OwnedMixin, TimestampMixin, Base):
    """One row per user (P0 is single-user, but the schema does not assume it)."""

    __tablename__ = "bot_preference"

    #: Ordered platform slugs the commands act on by default. Order is the user's
    #: stated preference and is preserved, so a list rather than a set.
    platforms: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)

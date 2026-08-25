"""Async SQLAlchemy engine/session and the declarative base shared by all modules."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from careeros.core.config import Settings, get_settings
from careeros.core.ids import uuid7

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid7)


class OwnedMixin:
    """Every operational row belongs to a user (single user in P0, tenant key for SaaS)."""

    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)


_engine: AsyncEngine | None = None
_engine_url: str | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


#: Schemes that mean "PostgreSQL, driver unspecified" and so are ours to resolve.
#: An explicit driver (``postgresql+psycopg``) is a deliberate choice and is left alone.
_AMBIGUOUS_PG_SCHEMES = ("postgres://", "postgresql://")
_ASYNC_PG_SCHEME = "postgresql+asyncpg://"


def normalize_database_url(url: str) -> str:
    """Point a bare PostgreSQL URL at the async driver.

    ``fly mpg attach`` emits ``postgres://…``, and most documentation writes
    ``postgresql://…``. SQLAlchemy's async engine accepts neither, so without this
    the first deploy fails at connection time with an error naming SQLAlchemy
    rather than the platform that produced the URL.

    Only the two ambiguous PostgreSQL schemes are rewritten. Anything else — a
    sqlite URL used by tooling, or an explicitly chosen driver — is returned
    untouched, because overriding a stated driver would be a surprise, not a fix.
    """
    for scheme in _AMBIGUOUS_PG_SCHEMES:
        if url.startswith(scheme):
            return _ASYNC_PG_SCHEME + url[len(scheme) :]
    return url


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """Process-wide engine. Rebuilt when asked for a different URL (tests, multi-env tooling)."""
    global _engine, _engine_url, _sessionmaker
    url = normalize_database_url((settings or get_settings()).database_url)
    if _engine is None or url != _engine_url:
        _engine = create_async_engine(url, pool_pre_ping=True)
        _engine_url = url
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker(settings: Settings | None = None) -> async_sessionmaker[AsyncSession]:
    get_engine(settings)
    assert _sessionmaker is not None
    return _sessionmaker


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: sessions bound to the *app's* settings, never ambient env."""
    async with get_sessionmaker(request.app.state.settings)() as session:
        yield session


async def dispose_engine() -> None:
    global _engine, _engine_url, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _engine_url = None
    _sessionmaker = None

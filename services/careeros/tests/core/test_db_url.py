"""The database URL must survive whatever the host hands us.

Fly Managed Postgres emits `postgres://…` from `fly mpg attach`. SQLAlchemy's
async engine rejects that scheme outright, so without normalisation the first
deploy fails at connection time with an error that points at SQLAlchemy rather
than at the platform that produced the URL.
"""

from __future__ import annotations

import pytest

from careeros.core.db import normalize_database_url

ASYNC = "postgresql+asyncpg://u:p@h:5432/db"


@pytest.mark.parametrize(
    "raw",
    [
        "postgres://u:p@h:5432/db",  # what `fly mpg attach` writes
        "postgresql://u:p@h:5432/db",  # what most tools and docs write
        ASYNC,  # already correct — must not be touched twice
    ],
)
def test_every_accepted_scheme_becomes_asyncpg(raw: str) -> None:
    assert normalize_database_url(raw) == ASYNC


def test_query_parameters_and_credentials_are_preserved() -> None:
    raw = "postgres://u:p%40ss@h:5432/db?sslmode=require&application_name=careeros"
    out = normalize_database_url(raw)
    assert out.startswith("postgresql+asyncpg://")
    assert "p%40ss" in out  # an escaped password must not be mangled
    assert "sslmode=require" in out
    assert "application_name=careeros" in out


def test_a_non_postgres_url_is_left_alone() -> None:
    """Never rewrite a scheme we were not asked about — sqlite is used in tooling."""
    raw = "sqlite+aiosqlite:///./local.db"
    assert normalize_database_url(raw) == raw


def test_another_postgres_driver_is_not_hijacked() -> None:
    """An explicit driver choice is deliberate; only the bare scheme is ambiguous."""
    raw = "postgresql+psycopg://u:p@h:5432/db"
    assert normalize_database_url(raw) == raw


def test_empty_url_is_returned_unchanged() -> None:
    assert normalize_database_url("") == ""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from careeros.core.config import Settings
from careeros.core.ids import is_slug, slugify, uuid7
from careeros.core.tasks import InlineTaskRunner, TaskRegistry


async def test_health(client: AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.headers["x-request-id"]


def test_uuid7_is_time_ordered() -> None:
    a, b = uuid7(), uuid7()
    assert a.version == 7
    assert a.variant == uuid.RFC_4122
    assert a.int >> 80 <= b.int >> 80


def test_slugs() -> None:
    assert slugify("Achievement: Prodamus #001") == "achievement_prodamus_001"
    assert is_slug("achievement_prodamus_001")
    assert not is_slug("Bad-Slug")


def test_settings_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    s = Settings(
        anthropic_api_key="sk-secret",  # type: ignore[arg-type]
        database_url="postgresql+asyncpg://u:p@db:5432/x",
    )
    dumped = s.redacted_dump()
    assert dumped["anthropic_api_key"] == "***"
    assert dumped["database_url"] == "postgresql+asyncpg://***@db:5432/x"
    assert "sk-secret" not in str(dumped)


def test_blank_env_var_reads_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every optional value renders blank out of the templates until the owner fills it in.

    Regression: `CAREEROS_TG_OWNER_CHAT_ID=` made Settings() raise (int cannot parse ""), so a
    freshly rendered .env could not start the app at all — and a blank secret became
    SecretStr(""), which tests truthy while authenticating nothing.
    """
    for var in (
        "CAREEROS_TG_OWNER_CHAT_ID",
        "CAREEROS_ANTHROPIC_API_KEY",
        "CAREEROS_TG_PUBLIC_URL",
        "CAREEROS_HH_CLIENT_ID",
    ):
        monkeypatch.setenv(var, "")
    monkeypatch.setenv("CAREEROS_TG_NOTIFY_MIN_SCORE", "80")

    s = Settings()
    assert s.tg_owner_chat_id is None
    assert s.anthropic_api_key is None  # not SecretStr("")
    assert s.tg_public_url is None
    assert s.hh_client_id is None
    assert s.tg_notify_min_score == 80  # a value that IS set still parses
    assert s.tg_webhook_path == "/tg/webhook"  # a non-optional field keeps its default


async def test_inline_task_runner_executes_registered_handler() -> None:
    reg = TaskRegistry()
    seen: list[int] = []

    @reg.task("echo")
    async def echo(value: int) -> None:
        seen.append(value)

    runner = InlineTaskRunner(reg)
    ref = await runner.enqueue("echo", {"value": 42})
    assert seen == [42]
    assert ref.name == "echo"
    with pytest.raises(KeyError):
        await runner.enqueue("missing", {})

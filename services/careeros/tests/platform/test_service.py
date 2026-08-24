from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.config import Settings
from careeros.modules.platform.base import BaseConnector, ConnectorContext, PlatformError
from careeros.modules.platform.enums import (
    ApplicationStatus,
    AuthKind,
    ConnectionStatus,
    SyncKind,
    SyncMethod,
    SyncStatus,
)
from careeros.modules.platform.registry import PlatformRegistry
from careeros.modules.platform.schemas import (
    AccountInfo,
    ApplicationObservationIn,
    Capabilities,
    OAuthConfig,
)
from careeros.modules.platform.service import PlatformService
from careeros.modules.platform.tokens import MemoryTokenStore
from careeros.modules.vault.enums import Platform

pytestmark = pytest.mark.db


class FakeApi(BaseConnector):
    platform = Platform.hh
    capabilities = Capabilities(
        platform=Platform.hh,
        profile=[SyncMethod.api],
        jobs=[SyncMethod.api],
        applications=[SyncMethod.api],
        official_api=True,
        auth=AuthKind.oauth2,
    )

    def oauth_config(self, settings: Settings) -> OAuthConfig | None:
        return OAuthConfig(
            authorize_url="https://hh.example/oauth/authorize",
            token_url="https://hh.example/token",
            client_id="cid",
            client_secret="sec",  # type: ignore[arg-type]
            redirect_uri="http://localhost:8000/api/platform/oauth/hh/callback",
        )

    async def whoami(self, ctx: ConnectorContext) -> AccountInfo:
        assert ctx.tokens is not None
        return AccountInfo(account_id="u1", label="Dana Kovalenko", raw={"id": "u1"})


def _svc(settings: Settings, session: AsyncSession, user_id: uuid.UUID) -> PlatformService:
    return PlatformService(
        settings.model_copy(update={"hh_client_id": "cid", "hh_client_secret": SecretStr("sec")}),
        session=session,
        user_id=user_id,
        registry=PlatformRegistry([FakeApi()]),
        store=MemoryTokenStore(),
    )


async def test_connections_oauth_lifecycle(
    settings: Settings, session: AsyncSession, user_id: uuid.UUID
) -> None:
    svc = _svc(settings, session, user_id)
    conns = await svc.list_connections()
    assert [c.platform for c in conns] == [Platform.hh]
    assert conns[0].status == ConnectionStatus.disconnected and conns[0].has_tokens is False

    start = await svc.oauth_start(Platform.hh)
    assert start.authorize_url.startswith("https://hh.example/oauth/authorize?")
    assert f"state={start.state}" in start.authorize_url

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/token"
        return httpx.Response(
            200, json={"access_token": "acc", "refresh_token": "ref", "expires_in": 3600}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(PlatformError):
            await svc.oauth_callback(Platform.hh, "code", "wrong-state", http=http)
        conn = await svc.oauth_callback(Platform.hh, "code", start.state, http=http)
        assert conn.status == ConnectionStatus.connected and conn.has_tokens
        assert conn.account_label == "Dana Kovalenko" and conn.token_expires_at is not None

        checks = await svc.doctor(Platform.hh, http=http)
        assert all(c.ok for c in checks), [c.model_dump() for c in checks]

        refreshed = await svc.refresh(Platform.hh, http=http)
        assert refreshed.status == ConnectionStatus.connected

    gone = await svc.disconnect(Platform.hh)
    assert gone.status == ConnectionStatus.disconnected and gone.has_tokens is False


async def test_sync_runs_and_observation_upsert(
    settings: Settings, session: AsyncSession, user_id: uuid.UUID
) -> None:
    svc = _svc(settings, session, user_id)
    run = await svc.start_run(Platform.hh, SyncKind.applications, SyncMethod.api)
    first = ApplicationObservationIn(
        platform=Platform.hh,
        external_id="n-1",
        job_title="Data Engineer",
        company="Northwind Commerce",
        job_url="https://hh.ru/vacancy/1",
        status_raw="Отклик",
        status=ApplicationStatus.applied,
        applied_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    anon = ApplicationObservationIn(
        platform=Platform.hh, job_title="Analytics Engineer", company="Lumen Analytics"
    )

    async def resolver(url: str) -> uuid.UUID | None:
        return uuid.UUID(int=7) if url.endswith("/1") else None

    created, updated, skipped = await svc.upsert_observations(
        Platform.hh, [first, anon], run_id=run.id, resolve_opportunity=resolver
    )
    assert (created, updated, skipped) == (2, 0, 0)
    out = await svc.finish_run(run, status=SyncStatus.ok, seen=2, created=2)
    assert out.status == SyncStatus.ok and out.finished_at is not None

    # same state again → skipped; a status change → updated with history
    same = await svc.upsert_observations(Platform.hh, [first, anon])
    assert same == (0, 0, 2)
    invited = first.model_copy(
        update={"status_raw": "Приглашение", "status": ApplicationStatus.invited}
    )
    assert await svc.upsert_observations(Platform.hh, [invited]) == (0, 1, 0)

    rows = await svc.list_observations(platform=Platform.hh)
    by_title = {r.job_title: r for r in rows}
    de = by_title["Data Engineer"]
    assert de.status == ApplicationStatus.invited and de.history[0]["status"] == "applied"
    assert de.opportunity_id == uuid.UUID(int=7) and de.applied_at is not None
    assert by_title["Analytics Engineer"].external_id is None

    invited_only = await svc.list_observations(status=ApplicationStatus.invited)
    assert [r.job_title for r in invited_only] == ["Data Engineer"]
    runs = await svc.list_runs(platform=Platform.hh)
    assert runs[0].id == run.id and runs[0].items_created == 2

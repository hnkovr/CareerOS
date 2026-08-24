"""PlatformService: capabilities, connections, OAuth lifecycle, sync runs, observations.

No domain services are called here; ``sync.py`` composes this with profiles/opportunities.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.config import Settings
from careeros.core.logging import get_logger
from careeros.modules.platform import oauth
from careeros.modules.platform.base import (
    BaseConnector,
    ConnectorContext,
    NotConnected,
    PlatformError,
)
from careeros.modules.platform.enums import (
    ApplicationStatus,
    ConnectionStatus,
    SyncKind,
    SyncMethod,
    SyncStatus,
)
from careeros.modules.platform.models import (
    ApplicationObservation,
    PlatformConnection,
    PlatformSyncRun,
)
from careeros.modules.platform.registry import PlatformRegistry, get_registry
from careeros.modules.platform.schemas import (
    ApplicationObservationIn,
    ApplicationObservationOut,
    Capabilities,
    ConnectionOut,
    DoctorCheck,
    OAuthConfig,
    OAuthStartOut,
    SyncRunOut,
)
from careeros.modules.platform.tokens import (
    OAuthTokens,
    TokenStore,
    get_token_store,
    resolve_tokens,
)
from careeros.modules.vault.enums import Platform

log = get_logger(__name__)

OAUTH_STATE_TTL_S = 600
_oauth_states: dict[str, tuple[Platform, float]] = {}

OpportunityResolver = Callable[[str], Awaitable[uuid.UUID | None]]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PlatformService:
    def __init__(
        self,
        settings: Settings,
        *,
        session: AsyncSession,
        user_id: uuid.UUID,
        registry: PlatformRegistry | None = None,
        store: TokenStore | None = None,
    ) -> None:
        self.settings = settings
        self.session = session
        self.user_id = user_id
        self.registry = registry or get_registry()
        self.store: TokenStore = store or get_token_store(settings)

    # ------------------------------------------------------------------ capabilities
    def capabilities(self) -> list[Capabilities]:
        return self.registry.capabilities()

    def connector(self, platform: Platform | str) -> BaseConnector:
        return self.registry.get(platform)

    def tokens(self, platform: Platform) -> OAuthTokens | None:
        return resolve_tokens(self.settings, self.store, platform)

    def context(
        self, platform: Platform, http: httpx.AsyncClient, *, now: datetime | None = None
    ) -> ConnectorContext:
        return ConnectorContext(
            settings=self.settings,
            http=http,
            tokens=self.tokens(platform),
            now=now or _utcnow(),
        )

    # ------------------------------------------------------------------ connections
    async def list_connections(self) -> list[ConnectionOut]:
        rows = {
            r.platform: r
            for r in (
                await self.session.scalars(
                    select(PlatformConnection).where(PlatformConnection.user_id == self.user_id)
                )
            ).all()
        }
        return [self._connection_out(c, rows.get(str(c.platform))) for c in self.registry.all()]

    async def get_connection(self, platform: Platform) -> ConnectionOut:
        connector = self.connector(platform)
        return self._connection_out(connector, await self._connection_row(platform))

    async def _connection_row(self, platform: Platform) -> PlatformConnection | None:
        return await self.session.scalar(
            select(PlatformConnection).where(
                PlatformConnection.user_id == self.user_id,
                PlatformConnection.platform == str(platform),
            )
        )

    async def upsert_connection(self, platform: Platform, **fields: object) -> PlatformConnection:
        row = await self._connection_row(platform)
        if row is None:
            row = PlatformConnection(
                user_id=self.user_id,
                platform=str(platform),
                auth_kind=str(self.connector(platform).capabilities.auth),
            )
            self.session.add(row)
        for key, value in fields.items():
            setattr(row, key, value)
        await self.session.commit()
        return row

    async def touch_connection(
        self, platform: Platform, *, last_sync_at: datetime, error: str | None = None
    ) -> None:
        await self.upsert_connection(platform, last_sync_at=last_sync_at, last_error=error)

    def _connection_out(
        self, connector: BaseConnector, row: PlatformConnection | None
    ) -> ConnectionOut:
        tokens = self.tokens(connector.platform)
        status = ConnectionStatus(row.status) if row else ConnectionStatus.disconnected
        if tokens is not None and status == ConnectionStatus.disconnected:
            status = ConnectionStatus.connected  # env-pinned tokens without a callback
        if tokens is not None and tokens.is_expired() and tokens.refresh_token is None:
            status = ConnectionStatus.needs_reauth
        return ConnectionOut(
            platform=connector.platform,
            status=status,
            auth=connector.capabilities.auth,
            has_tokens=tokens is not None,
            account_id=row.account_id if row else None,
            account_label=row.account_label if row else None,
            scopes=list(row.scopes or []) if row else [],
            token_expires_at=tokens.expires_at if tokens else None,
            last_sync_at=row.last_sync_at if row else None,
            last_error=row.last_error if row else None,
            capabilities=connector.capabilities,
        )

    # ------------------------------------------------------------------ oauth
    def _oauth_config(self, platform: Platform) -> OAuthConfig:
        cfg = self.connector(platform).oauth_config(self.settings)
        if cfg is None:
            raise PlatformError(f"{platform}: does not use OAuth — nothing to connect")
        return cfg

    async def oauth_start(self, platform: Platform) -> OAuthStartOut:
        cfg = self._oauth_config(platform)
        state = oauth.new_state()
        now = time.monotonic()
        for key, (_, ts) in list(_oauth_states.items()):
            if now - ts > OAUTH_STATE_TTL_S:
                _oauth_states.pop(key, None)
        _oauth_states[state] = (platform, now)
        return OAuthStartOut(
            platform=platform,
            authorize_url=oauth.authorize_url(cfg, state),
            state=state,
            redirect_uri=cfg.redirect_uri,
        )

    async def oauth_callback(
        self, platform: Platform, code: str, state: str, *, http: httpx.AsyncClient
    ) -> ConnectionOut:
        entry = _oauth_states.pop(state, None)
        if entry is None or entry[0] != platform:
            raise PlatformError("unknown or expired OAuth state — start the connect flow again")
        cfg = self._oauth_config(platform)
        tokens = await oauth.exchange_code(http, platform, cfg, code)
        self.store.save(platform, tokens)
        return await self._after_tokens(platform, http)

    async def refresh(self, platform: Platform, *, http: httpx.AsyncClient) -> ConnectionOut:
        tokens = self.tokens(platform)
        if tokens is None:
            raise NotConnected(platform)
        cfg = self._oauth_config(platform)
        fresh = await oauth.refresh_tokens(http, platform, cfg, tokens)
        self.store.save(platform, fresh)
        return await self._after_tokens(platform, http)

    async def _after_tokens(self, platform: Platform, http: httpx.AsyncClient) -> ConnectionOut:
        connector = self.connector(platform)
        tokens = self.tokens(platform)
        fields: dict[str, object] = {
            "status": str(ConnectionStatus.connected),
            "token_expires_at": tokens.expires_at if tokens else None,
            "scopes": tokens.scope.split() if tokens and tokens.scope else [],
            "last_error": None,
        }
        try:
            info = await connector.whoami(self.context(platform, http))
            fields.update(
                account_id=info.account_id,
                account_label=info.label,
                meta={
                    "profile_url": info.profile_url,
                    **{k: v for k, v in info.raw.items() if k in ("id", "email")},
                },
            )
        except PlatformError as exc:  # tokens are saved; identity is best-effort
            log.warning("platform.whoami_failed", platform=str(platform), error=str(exc))
            fields["last_error"] = f"whoami: {exc}"
        row = await self.upsert_connection(platform, **fields)
        log.info("platform.connected", platform=str(platform), account=row.account_label)
        return self._connection_out(connector, row)

    async def disconnect(self, platform: Platform) -> ConnectionOut:
        self.store.delete(platform)
        row = await self.upsert_connection(
            platform,
            status=str(ConnectionStatus.disconnected),
            token_expires_at=None,
            scopes=[],
        )
        return self._connection_out(self.connector(platform), row)

    async def doctor(self, platform: Platform, *, http: httpx.AsyncClient) -> list[DoctorCheck]:
        connector = self.connector(platform)
        return await connector.doctor(self.context(platform, http))

    # ------------------------------------------------------------------ sync runs
    async def start_run(
        self, platform: Platform, kind: SyncKind, method: SyncMethod
    ) -> PlatformSyncRun:
        run = PlatformSyncRun(
            user_id=self.user_id,
            platform=str(platform),
            kind=str(kind),
            method=str(method),
            status=str(SyncStatus.failed),
            started_at=_utcnow(),
        )
        self.session.add(run)
        await self.session.flush()
        return run

    async def finish_run(
        self,
        run: PlatformSyncRun,
        *,
        status: SyncStatus,
        seen: int = 0,
        created: int = 0,
        updated: int = 0,
        skipped: int = 0,
        error: str | None = None,
        details: dict[str, object] | None = None,
    ) -> SyncRunOut:
        run.status = str(status)
        run.finished_at = _utcnow()
        run.items_seen = seen
        run.items_created = created
        run.items_updated = updated
        run.items_skipped = skipped
        run.error = error
        run.details = dict(details or {})
        await self.session.commit()
        return self._run_out(run)

    async def list_runs(
        self, *, platform: Platform | None = None, kind: SyncKind | None = None, limit: int = 50
    ) -> list[SyncRunOut]:
        stmt = (
            select(PlatformSyncRun)
            .where(PlatformSyncRun.user_id == self.user_id)
            .order_by(PlatformSyncRun.started_at.desc())
            .limit(limit)
        )
        if platform:
            stmt = stmt.where(PlatformSyncRun.platform == str(platform))
        if kind:
            stmt = stmt.where(PlatformSyncRun.kind == str(kind))
        return [self._run_out(r) for r in (await self.session.scalars(stmt)).all()]

    @staticmethod
    def _run_out(run: PlatformSyncRun) -> SyncRunOut:
        return SyncRunOut(
            id=run.id,
            platform=Platform(run.platform),
            kind=SyncKind(run.kind),
            method=SyncMethod(run.method),
            status=SyncStatus(run.status),
            started_at=run.started_at,
            finished_at=run.finished_at,
            items_seen=run.items_seen,
            items_created=run.items_created,
            items_updated=run.items_updated,
            items_skipped=run.items_skipped,
            error=run.error,
            details=dict(run.details or {}),
        )

    # ------------------------------------------------------------------ observations
    async def upsert_observations(
        self,
        platform: Platform,
        items: list[ApplicationObservationIn],
        *,
        run_id: uuid.UUID | None = None,
        resolve_opportunity: OpportunityResolver | None = None,
    ) -> tuple[int, int, int]:
        """Returns (created, updated, skipped). A status change keeps the previous state."""
        created = updated = skipped = 0
        now = _utcnow()
        for item in items:
            row = await self._find_observation(platform, item)
            if row is None:
                opportunity_id = None
                if resolve_opportunity and item.job_url:
                    opportunity_id = await resolve_opportunity(item.job_url)
                self.session.add(
                    ApplicationObservation(
                        user_id=self.user_id,
                        platform=str(platform),
                        external_id=item.external_id,
                        job_title=item.job_title[:300],
                        company=item.company,
                        job_url=item.job_url,
                        status_raw=item.status_raw[:200],
                        status=str(item.status),
                        applied_at=item.applied_at,
                        updated_at_platform=item.updated_at_platform,
                        observed_at=now,
                        opportunity_id=opportunity_id,
                        sync_run_id=run_id,
                        content_hash=item.content_hash(),
                        raw_payload=item.raw_payload,
                        history=[],
                    )
                )
                created += 1
                continue
            changed = (
                row.status != str(item.status)
                or row.status_raw != item.status_raw[:200]
                or (
                    item.updated_at_platform is not None
                    and row.updated_at_platform != item.updated_at_platform
                )
            )
            if not changed:
                skipped += 1
                continue
            history = list(row.history or [])
            history.append(
                {
                    "status": row.status,
                    "status_raw": row.status_raw,
                    "observed_at": row.observed_at.isoformat(),
                }
            )
            row.history = history
            row.status = str(item.status)
            row.status_raw = item.status_raw[:200]
            row.updated_at_platform = item.updated_at_platform or row.updated_at_platform
            row.applied_at = row.applied_at or item.applied_at
            row.observed_at = now
            row.sync_run_id = run_id
            row.raw_payload = item.raw_payload or row.raw_payload
            updated += 1
        await self.session.commit()
        log.info(
            "platform.observations_upserted",
            platform=str(platform),
            created=created,
            updated=updated,
            skipped=skipped,
        )
        return created, updated, skipped

    async def _find_observation(
        self, platform: Platform, item: ApplicationObservationIn
    ) -> ApplicationObservation | None:
        stmt = select(ApplicationObservation).where(
            ApplicationObservation.user_id == self.user_id,
            ApplicationObservation.platform == str(platform),
        )
        if item.external_id:
            stmt = stmt.where(ApplicationObservation.external_id == item.external_id)
        else:
            stmt = stmt.where(ApplicationObservation.content_hash == item.content_hash())
        return await self.session.scalar(stmt.order_by(ApplicationObservation.created_at).limit(1))

    async def list_observations(
        self,
        *,
        platform: Platform | None = None,
        status: ApplicationStatus | None = None,
        limit: int = 200,
    ) -> list[ApplicationObservationOut]:
        stmt = (
            select(ApplicationObservation)
            .where(ApplicationObservation.user_id == self.user_id)
            .order_by(ApplicationObservation.observed_at.desc())
            .limit(limit)
        )
        if platform:
            stmt = stmt.where(ApplicationObservation.platform == str(platform))
        if status:
            stmt = stmt.where(ApplicationObservation.status == str(status))
        return [self._observation_out(r) for r in (await self.session.scalars(stmt)).all()]

    @staticmethod
    def _observation_out(row: ApplicationObservation) -> ApplicationObservationOut:
        return ApplicationObservationOut(
            id=row.id,
            platform=Platform(row.platform),
            external_id=row.external_id,
            job_title=row.job_title,
            company=row.company,
            job_url=row.job_url,
            status_raw=row.status_raw,
            status=ApplicationStatus(row.status),
            applied_at=row.applied_at,
            updated_at_platform=row.updated_at_platform,
            raw_payload=row.raw_payload,
            observed_at=row.observed_at,
            opportunity_id=row.opportunity_id,
            sync_run_id=row.sync_run_id,
            history=list(row.history or []),
        )

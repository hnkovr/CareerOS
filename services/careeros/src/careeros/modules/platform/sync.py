"""PlatformSyncService: connector → domain services (profiles, opportunities) or observations.

This is the only place where the platform layer calls domain services (ADR-011). Connectors stay
pure; this service chooses the method (api > export > paste), runs it, persists, records the run.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.config import Settings
from careeros.core.logging import get_logger
from careeros.modules.ai.deps import build_ai_service
from careeros.modules.ai.service import AIService
from careeros.modules.opportunities.deps import find_opportunity_id_by_url
from careeros.modules.opportunities.service import OpportunityError, OpportunityService
from careeros.modules.platform.base import (
    BaseConnector,
    CapabilityUnavailable,
    NotConnected,
    ParseError,
    PlatformError,
)
from careeros.modules.platform.enums import SyncKind, SyncMethod, SyncStatus
from careeros.modules.platform.http import build_http
from careeros.modules.platform.registry import PlatformRegistry
from careeros.modules.platform.schemas import (
    ApplicationObservationIn,
    JobPosting,
    JobQuery,
    ParseResult,
    ProfileRead,
    SyncRequest,
    SyncResult,
)
from careeros.modules.platform.service import PlatformService
from careeros.modules.platform.tokens import TokenStore
from careeros.modules.profiles.service import ProfileService
from careeros.modules.vault.deps import get_vault
from careeros.modules.vault.enums import Platform
from careeros.modules.vault.service import Vault

log = get_logger(__name__)


class PlatformSyncService:
    def __init__(
        self,
        settings: Settings,
        *,
        session: AsyncSession,
        user_id: uuid.UUID,
        registry: PlatformRegistry | None = None,
        store: TokenStore | None = None,
        http_transport: httpx.AsyncBaseTransport | None = None,
        vault: Vault | None = None,
        ai: AIService | None = None,
    ) -> None:
        self.settings = settings
        self.session = session
        self.user_id = user_id
        self.platform = PlatformService(
            settings, session=session, user_id=user_id, registry=registry, store=store
        )
        self._transport = http_transport
        self._vault = vault
        self._ai = ai

    # ------------------------------------------------------------------ wiring
    @property
    def vault(self) -> Vault:
        if self._vault is None:
            self._vault = get_vault(self.settings)
        return self._vault

    @property
    def ai(self) -> AIService:
        if self._ai is None:
            self._ai = build_ai_service(self.settings, session=self.session, user_id=self.user_id)
        return self._ai

    def _profiles(self) -> ProfileService:
        return ProfileService(
            self.settings, self.vault, self.ai, session=self.session, user_id=self.user_id
        )

    def _opportunities(self) -> OpportunityService:
        return OpportunityService(
            self.settings, self.vault, self.ai, session=self.session, user_id=self.user_id
        )

    # ------------------------------------------------------------------ method selection
    def choose_method(self, platform: Platform, kind: SyncKind, req: SyncRequest) -> SyncMethod:
        connector = self.platform.connector(platform)
        available = connector.capabilities.methods(kind)
        if req.method is not None:
            if req.method not in available:
                raise CapabilityUnavailable(platform, kind, req.method, available)
            return req.method
        if req.text and SyncMethod.paste in available:
            return SyncMethod.paste
        if req.file_path and SyncMethod.export in available:
            return SyncMethod.export
        if SyncMethod.api in available and self.platform.tokens(platform) is not None:
            return SyncMethod.api
        if SyncMethod.api in available:
            raise NotConnected(platform)
        raise CapabilityUnavailable(platform, kind, None, available)

    # ------------------------------------------------------------------ fetch
    def run_offline(
        self,
        connector: BaseConnector,
        kind: SyncKind,
        method: SyncMethod,
        *,
        text: str | None,
        file_path: str | None,
    ) -> ProfileRead | list[JobPosting] | list[ApplicationObservationIn]:
        if method == SyncMethod.paste:
            if not text or not text.strip():
                raise ParseError("paste method needs non-empty text")
            if kind == SyncKind.profile:
                return connector.parse_profile_text(text)
            if kind == SyncKind.jobs:
                return connector.parse_jobs_text(text)
            return connector.parse_applications_text(text)
        if method == SyncMethod.export:
            if not file_path:
                raise ParseError("export method needs file_path")
            path = Path(file_path).expanduser()
            if not path.exists():
                raise ParseError(f"export path not found: {path}")
            if kind == SyncKind.profile:
                return connector.import_profile_export(path)
            if kind == SyncKind.jobs:
                return connector.import_jobs_export(path)
            return connector.import_applications_export(path)
        raise CapabilityUnavailable(
            connector.platform, kind, method, connector.capabilities.methods(kind)
        )

    async def run_api(
        self, connector: BaseConnector, kind: SyncKind, *, query: JobQuery | None
    ) -> ProfileRead | list[JobPosting] | list[ApplicationObservationIn]:
        async with build_http(self.settings, transport=self._transport) as http:
            ctx = self.platform.context(connector.platform, http)
            if ctx.tokens is None:
                raise NotConnected(connector.platform)
            if kind == SyncKind.profile:
                return await connector.read_profile(ctx)
            if kind == SyncKind.jobs:
                return await connector.search_jobs(ctx, query or JobQuery())
            return await connector.application_statuses(ctx)

    async def fetch(
        self, platform: Platform, kind: SyncKind, method: SyncMethod, req: SyncRequest
    ) -> ProfileRead | list[JobPosting] | list[ApplicationObservationIn]:
        connector = self.platform.connector(platform)
        if method == SyncMethod.api:
            return await self.run_api(connector, kind, query=req.query)
        return self.run_offline(connector, kind, method, text=req.text, file_path=req.file_path)

    async def parse(
        self,
        platform: Platform,
        kind: SyncKind,
        *,
        text: str | None = None,
        file_path: str | None = None,
    ) -> ParseResult:
        """Parse pasted text / an export without persisting anything (no DB access)."""
        method = SyncMethod.paste if text else SyncMethod.export
        connector = self.platform.connector(platform)
        if method not in connector.capabilities.methods(kind):
            raise CapabilityUnavailable(
                platform, kind, method, connector.capabilities.methods(kind)
            )
        items = self.run_offline(connector, kind, method, text=text, file_path=file_path)
        dumped = _dump(items)
        return ParseResult(
            platform=platform, kind=kind, method=method, items=dumped, count=len(dumped)
        )

    # ------------------------------------------------------------------ sync
    async def sync(self, platform: Platform, kind: SyncKind, req: SyncRequest) -> SyncResult:
        method = self.choose_method(platform, kind, req)
        items = await self.fetch(platform, kind, method, req)
        preview = _dump(items)
        if req.dry_run:
            return SyncResult(
                platform=platform,
                kind=kind,
                method=method,
                status=SyncStatus.ok,
                items_seen=len(preview),
                preview=preview,
                message="dry run — nothing persisted",
            )

        run = await self.platform.start_run(platform, kind, method)
        now = datetime.now(UTC)
        created_ids: list[uuid.UUID] = []
        duplicates: list[uuid.UUID] = []
        errors: list[str] = []
        created = updated = skipped = 0
        try:
            if kind == SyncKind.profile:
                assert isinstance(items, ProfileRead)
                snap = await self._profiles().create_snapshot(items.to_snapshot())
                created_ids.append(snap.id)
                created = 1
            elif kind == SyncKind.jobs:
                assert isinstance(items, list)
                opportunities = self._opportunities()
                for posting in items:
                    assert isinstance(posting, JobPosting)
                    if posting.url:
                        existing = await find_opportunity_id_by_url(self.session, posting.url)
                        if existing is not None:
                            duplicates.append(existing)
                            skipped += 1
                            continue
                    try:
                        detail = await opportunities.ingest(
                            posting.to_ingest(use_ai=req.use_ai, provider=req.provider)
                        )
                    except OpportunityError as exc:
                        errors.append(f"{posting.title}: {exc}")
                        skipped += 1
                        continue
                    if detail.possible_duplicate_of is not None:
                        duplicates.append(detail.id)
                    created_ids.append(detail.id)
                    created += 1
            else:
                assert isinstance(items, list)
                observations = [i for i in items if isinstance(i, ApplicationObservationIn)]

                async def resolve(url: str) -> uuid.UUID | None:
                    return await find_opportunity_id_by_url(self.session, url)

                created, updated, skipped = await self.platform.upsert_observations(
                    platform, observations, run_id=run.id, resolve_opportunity=resolve
                )
        except PlatformError as exc:
            await self.platform.finish_run(
                run, status=SyncStatus.failed, seen=len(preview), error=str(exc)
            )
            await self.platform.touch_connection(platform, last_sync_at=now, error=str(exc))
            raise

        status = SyncStatus.partial if errors else SyncStatus.ok
        run_out = await self.platform.finish_run(
            run,
            status=status,
            seen=len(preview),
            created=created,
            updated=updated,
            skipped=skipped,
            error="; ".join(errors[:5]) or None,
            details={
                "created_ids": [str(i) for i in created_ids],
                "duplicates": [str(i) for i in duplicates],
                "errors": errors[:20],
            },
        )
        await self.platform.touch_connection(platform, last_sync_at=now)
        log.info(
            "platform.synced",
            platform=str(platform),
            kind=str(kind),
            method=str(method),
            seen=len(preview),
            created=created,
            updated=updated,
            skipped=skipped,
        )
        return SyncResult(
            platform=platform,
            kind=kind,
            method=method,
            status=status,
            run=run_out,
            items_seen=len(preview),
            items_created=created,
            items_updated=updated,
            items_skipped=skipped,
            created_ids=created_ids,
            duplicates=duplicates,
            message="; ".join(errors[:5]) or None,
        )

    async def sync_all(
        self,
        platform: Platform | None = None,
        *,
        dry_run: bool = False,
        use_ai: bool = False,
    ) -> list[SyncResult]:
        """Best available method per capability; paste-only capabilities are reported as skipped."""
        results: list[SyncResult] = []
        platforms = [platform] if platform else self.platform.registry.platforms()
        for p in platforms:
            caps = self.platform.connector(p).capabilities
            for kind in SyncKind:
                available = caps.methods(kind)
                if SyncMethod.api in available and self.platform.tokens(p) is not None:
                    try:
                        results.append(
                            await self.sync(
                                p,
                                kind,
                                SyncRequest(method=SyncMethod.api, dry_run=dry_run, use_ai=use_ai),
                            )
                        )
                    except PlatformError as exc:
                        results.append(
                            SyncResult(
                                platform=p,
                                kind=kind,
                                method=SyncMethod.api,
                                status=SyncStatus.failed,
                                message=str(exc),
                            )
                        )
                    continue
                if SyncMethod.api in available:
                    message = f"not connected — careeros platform connect {p}"
                elif available:
                    message = "needs " + " or ".join(
                        "--text-file (paste)" if m == SyncMethod.paste else "--export PATH"
                        for m in available
                    )
                else:
                    message = "not supported"
                results.append(
                    SyncResult(
                        platform=p,
                        kind=kind,
                        method=None,
                        status=SyncStatus.skipped,
                        message=message,
                    )
                )
        return results


def _dump(items: BaseModel | list[Any]) -> list[dict[str, Any]]:
    seq = items if isinstance(items, list) else [items]
    return [i.model_dump(mode="json") for i in seq if isinstance(i, BaseModel)]

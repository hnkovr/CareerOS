"""PlatformSyncService: connector → domain services (profiles, opportunities) or observations.

This is the only place where the platform layer calls domain services (ADR-013). Connectors stay
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
from careeros.modules.opportunities.deps import (
    find_opportunity_id_by_external_id,
    find_opportunity_id_by_url,
)
from careeros.modules.opportunities.enums import FieldSource, OpportunityStatus
from careeros.modules.opportunities.enums import SourceRelation as OpportunityRelation
from careeros.modules.opportunities.schemas import SnapshotIn as OpportunitySnapshotIn
from careeros.modules.opportunities.schemas import SourceIn
from careeros.modules.opportunities.service import OpportunityError, OpportunityService
from careeros.modules.platform.base import (
    BaseConnector,
    CapabilityUnavailable,
    ConnectorContext,
    NotConnected,
    ParseError,
    PlatformError,
    ReadUnavailable,
)
from careeros.modules.platform.enums import (
    AccessMode,
    FetchStrategy,
    SyncKind,
    SyncMethod,
    SyncStatus,
)
from careeros.modules.platform.fetch.artifact import JobReadError
from careeros.modules.platform.fetch.budget import FetchBudget
from careeros.modules.platform.http import build_http
from careeros.modules.platform.registry import PlatformRegistry
from careeros.modules.platform.schemas import (
    ApplicationObservationIn,
    DetectionOut,
    FetchAttempt,
    JobPosting,
    JobQuery,
    ParseResult,
    ProfileRead,
    ReadOut,
    ReadRequest,
    SyncRequest,
    SyncResult,
)
from careeros.modules.platform.service import PlatformService
from careeros.modules.platform.sources import (
    CanonicalSource,
    DetectionResult,
    SourceKind,
    SourceRef,
)
from careeros.modules.platform.sources import detect as detect_source
from careeros.modules.platform.tokens import OAuthTokens, TokenStore
from careeros.modules.profiles.service import ProfileService
from careeros.modules.vault.deps import get_vault
from careeros.modules.vault.enums import Platform
from careeros.modules.vault.service import Vault

log = get_logger(__name__)

#: Failure reasons that mean "the posting is gone", not "we could not reach it" (ADR-015 §4).
CLOSED_REASONS: frozenset[str] = frozenset({"job_closed", "not_found", "gone"})
#: The quality flag a closed posting carries when the page still rendered.
CLOSED_FLAG = "job_closed"


def authority_for(
    platform: Platform, strategy: FetchStrategy | None, *, is_archive: bool = False
) -> FieldSource:
    """Field-evidence authority of a read (ADR-016 §3): who said it and how directly.

    The generic ``website`` provider reads the employer's own page, so it outranks a board;
    an archived copy is always ``archive``, whatever produced it.
    """
    if is_archive or strategy in (FetchStrategy.wayback, FetchStrategy.archive_today):
        return FieldSource.archive
    employer = platform == Platform.website
    if strategy == FetchStrategy.api:
        return FieldSource.employer_api if employer else FieldSource.board_api
    if strategy == FetchStrategy.search_recovery:
        return FieldSource.search_result
    return FieldSource.employer_page if employer else FieldSource.board_page


def closed_reason(exc: JobReadError) -> str | None:
    """Why this read means "the job is gone" — ``None`` when it only means "we failed"."""
    for attempt in exc.attempts:
        if attempt.error_type in CLOSED_REASONS or attempt.status_code in (404, 410):
            return attempt.error_type or f"http {attempt.status_code}"
    if exc.best_partial is not None and CLOSED_FLAG in exc.best_partial.flags:
        return CLOSED_FLAG
    return None


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
        if req.text:
            if SyncMethod.paste not in available:
                raise CapabilityUnavailable(platform, kind, SyncMethod.paste, available)
            return SyncMethod.paste
        if req.file_path:
            if SyncMethod.export not in available:
                raise CapabilityUnavailable(platform, kind, SyncMethod.export, available)
            return SyncMethod.export
        if SyncMethod.api in available and (
            self.platform.tokens(platform) is not None
            or (kind == SyncKind.jobs and connector.jobs_without_token)
        ):
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
    ) -> tuple[ProfileRead | list[JobPosting] | list[ApplicationObservationIn], list[str]]:
        """Run the API method; refresh an expired or rejected token once when refreshable."""
        platform = connector.platform
        async with build_http(self.settings, transport=self._transport) as http:
            ctx = self.platform.context(platform, http)
            public_search = kind == SyncKind.jobs and connector.jobs_without_token
            if ctx.tokens is None and not public_search:
                raise NotConnected(platform)
            refreshed = False
            if ctx.tokens is not None and self._refreshable(ctx.tokens) and ctx.tokens.is_expired():
                await self.platform.refresh(platform, http=http)
                ctx = self.platform.context(platform, http)
                refreshed = True
            try:
                items = await self._call_api(connector, kind, ctx, query)
            except NotConnected:
                if refreshed or ctx.tokens is None or not self._refreshable(ctx.tokens):
                    raise
                log.info("platform.token_rejected_refreshing", platform=str(platform))
                await self.platform.refresh(platform, http=http)
                ctx = self.platform.context(platform, http)
                items = await self._call_api(connector, kind, ctx, query)
            return items, list(ctx.warnings)

    @staticmethod
    def _refreshable(tokens: OAuthTokens) -> bool:
        return tokens.refresh_token is not None and not tokens.pinned

    @staticmethod
    async def _call_api(
        connector: BaseConnector, kind: SyncKind, ctx: ConnectorContext, query: JobQuery | None
    ) -> ProfileRead | list[JobPosting] | list[ApplicationObservationIn]:
        if kind == SyncKind.profile:
            return await connector.read_profile(ctx)
        if kind == SyncKind.jobs:
            return await connector.search_jobs(ctx, query or JobQuery())
        return await connector.application_statuses(ctx)

    async def fetch(
        self, platform: Platform, kind: SyncKind, method: SyncMethod, req: SyncRequest
    ) -> tuple[ProfileRead | list[JobPosting] | list[ApplicationObservationIn], list[str]]:
        connector = self.platform.connector(platform)
        if method == SyncMethod.api:
            return await self.run_api(connector, kind, query=req.query)
        items = self.run_offline(connector, kind, method, text=req.text, file_path=req.file_path)
        return items, []

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
        items, warnings = await self.fetch(platform, kind, method, req)
        preview = _dump(items)
        if req.dry_run:
            return SyncResult(
                platform=platform,
                kind=kind,
                method=method,
                status=SyncStatus.partial if warnings else SyncStatus.ok,
                items_seen=len(preview),
                preview=preview,
                warnings=warnings,
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
                        existing = await find_opportunity_id_by_url(
                            self.session, posting.url, user_id=self.user_id
                        )
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
                    return await find_opportunity_id_by_url(self.session, url, user_id=self.user_id)

                created, updated, skipped = await self.platform.upsert_observations(
                    platform, observations, run_id=run.id, resolve_opportunity=resolve
                )
        except Exception as exc:  # any failure is recorded on the run, then re-raised
            await self.session.rollback()
            await self.platform.finish_run(
                run,
                status=SyncStatus.failed,
                seen=len(preview),
                error=f"{type(exc).__name__}: {exc}",
            )
            await self.platform.touch_connection(platform, last_sync_at=now, error=str(exc))
            raise

        errors = errors + [f"warning: {w}" for w in warnings]
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
            warnings=warnings,
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
            for kind in (SyncKind.profile, SyncKind.jobs, SyncKind.applications):
                available = caps.methods(kind)
                connected = self.platform.tokens(p) is not None
                public_jobs = (
                    kind == SyncKind.jobs and self.platform.connector(p).jobs_without_token
                )
                if SyncMethod.api in available and (connected or public_jobs):
                    try:
                        results.append(
                            await self.sync(
                                p,
                                kind,
                                SyncRequest(method=SyncMethod.api, dry_run=dry_run, use_ai=use_ai),
                            )
                        )
                    except (NotConnected, CapabilityUnavailable) as exc:
                        # a sweep asks every platform: "connect first" / "needs a query" is
                        # something for the owner to do, not a failed sync (it must not make
                        # `careeros platform sync all` exit non-zero on a fresh install).
                        results.append(
                            SyncResult(
                                platform=p,
                                kind=kind,
                                method=None,
                                status=SyncStatus.skipped,
                                message=str(exc),
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

    # ------------------------------------------------------------------ read one job (ADR-015)
    def detect(
        self, url_or_ref: str | SourceRef, *, platform: Platform | None = None
    ) -> DetectionResult | None:
        """Which connector owns this URL. ``platform`` forces one instead of asking the registry.

        Pure: no network, no database. ``None`` = nothing recognised it (not even the generic
        provider, which answers for any http(s) URL — so ``None`` means "not a URL").
        """
        ref = url_or_ref if isinstance(url_or_ref, SourceRef) else SourceRef(value=str(url_or_ref))
        if platform is None:
            return detect_source(ref, self.platform.registry)
        connector = self.platform.connector(platform)
        try:
            canonical = connector.canonicalize(ref)
        except (ValueError, NotImplementedError):
            return None
        return DetectionResult(platform=platform, confidence=1.0, canonical=canonical)

    def detection_out(self, detection: DetectionResult) -> DetectionOut:
        c = detection.canonical
        return DetectionOut(
            platform=detection.platform,
            confidence=detection.confidence,
            canonical_url=c.canonical_url,
            external_id=c.external_id,
            host=c.host,
            locale=c.locale,
            private=c.private,
        )

    def _read_target(self, req: ReadRequest, source: SourceRef | None) -> DetectionResult:
        """Detect the provider, then enforce the access policy *before* any network call."""
        ref = source or SourceRef(kind=SourceKind.url, value=req.url, provider_hint=req.platform)
        if req.platform is not None and ref.provider_hint is None:
            ref = ref.model_copy(update={"provider_hint": req.platform})
        detection = self.detect(ref, platform=req.platform)
        if detection is None:
            raise ParseError(f"no connector recognises this source: {req.url!r}")
        caps = self.platform.connector(detection.platform).capabilities
        if caps.access == AccessMode.unsupported:
            raise CapabilityUnavailable(detection.platform, SyncKind.job, None, [])
        if not caps.read_job:
            raise ReadUnavailable(detection.platform, req.strategy)
        if req.strategy is not None and req.strategy not in caps.read_job:
            raise ReadUnavailable(detection.platform, req.strategy)
        return detection

    async def read_job(self, req: ReadRequest, *, source: SourceRef | None = None) -> ReadOut:
        """Read ONE job behind a user-supplied URL and file it (ADR-015 / ADR-016).

        detect → access policy → the connector's strategy chain → identity (provider id, then
        canonical URL) → a new opportunity **or** a snapshot of the one already known. Every run
        is recorded as a ``PlatformSyncRun(kind=job)`` carrying the attempts; a failure raises
        ``JobReadError`` with those attempts, never a bare "failed to fetch".
        """
        detection = self._read_target(req, source)
        platform = detection.platform
        canonical = detection.canonical
        connector = self.platform.connector(platform)
        budget = FetchBudget.from_settings(self.settings)

        run = None
        if not req.dry_run:
            # SyncMethod has no member per fetch strategy (and the column is 10 chars wide):
            # a job read is a machine read, and the strategy that produced it is in ``details``.
            run = await self.platform.start_run(platform, SyncKind.job, SyncMethod.api)
        try:
            async with build_http(self.settings, transport=self._transport) as http:
                ctx = self.platform.context(platform, http)
                read = await connector.fetch_job(
                    ctx,
                    canonical,
                    budget,
                    only=req.strategy,
                    use_cache=not req.no_cache,
                )
                warnings = list(ctx.warnings)
        except JobReadError as exc:
            if run is not None:
                await self.session.rollback()
                await self.platform.finish_run(
                    run,
                    status=SyncStatus.failed,
                    error=exc.diagnostics[:500],
                    details=_read_details(canonical, exc.attempts, exc.diagnostics, strategy=None),
                )
            raise

        posting = read.posting
        attempts = list(read.attempts)
        if posting is None:  # the chain only returns without a posting when no extractor was set
            raise JobReadError(platform, attempts, read.artifact, read.diagnostics)
        closed = bool(read.artifact and CLOSED_FLAG in read.artifact.flags)
        if req.dry_run:
            log.info(
                "platform.job_read_dry",
                platform=str(platform),
                strategy=str(posting.strategy),
                url=canonical.canonical_url,
            )
            return ReadOut(
                posting=posting,
                attempts=attempts,
                warnings=warnings,
                diagnostics=read.diagnostics,
                closed=closed,
            )

        out = await self._file_read(posting, req=req, canonical=canonical, closed=closed)
        out = out.model_copy(
            update={
                "posting": posting,
                "attempts": attempts,
                "warnings": warnings,
                "diagnostics": read.diagnostics,
                "closed": closed,
                "run_id": run.id if run is not None else None,
            }
        )
        if run is not None:
            run_out = await self.platform.finish_run(
                run,
                status=SyncStatus.partial if warnings else SyncStatus.ok,
                seen=1,
                created=1 if out.created else 0,
                updated=1 if out.snapshot_created and not out.created else 0,
                skipped=0 if (out.created or out.snapshot_created) else 1,
                error="; ".join(warnings[:5]) or None,
                details=_read_details(
                    canonical,
                    attempts,
                    read.diagnostics,
                    strategy=posting.strategy,
                    opportunity_id=out.opportunity_id,
                    created=out.created,
                    snapshot_created=out.snapshot_created,
                    closed=closed,
                ),
            )
            out = out.model_copy(update={"run_id": run_out.id})
        log.info(
            "platform.job_read",
            platform=str(platform),
            strategy=str(posting.strategy),
            host=canonical.host,
            created=out.created,
            snapshot=out.snapshot_created,
            closed=closed,
            opportunity=str(out.opportunity_id),
        )
        return out

    async def _file_read(
        self,
        posting: JobPosting,
        *,
        req: ReadRequest,
        canonical: CanonicalSource,
        closed: bool,
    ) -> ReadOut:
        """Identity → ingest or snapshot → provenance rows. The only DB-writing part of a read."""
        opportunities = self._opportunities()
        platform_value = str(posting.platform)
        canonical_url = posting.canonical_url or canonical.canonical_url
        external_id = posting.external_id or canonical.external_id
        source_url = posting.url or posting.resolved_url or canonical_url
        authority = authority_for(posting.platform, posting.strategy, is_archive=posting.is_archive)

        existing: uuid.UUID | None = None
        if external_id:
            existing = await find_opportunity_id_by_external_id(
                self.session, platform_value, external_id, user_id=self.user_id
            )
        if existing is None and canonical_url:
            existing = await find_opportunity_id_by_url(
                self.session, canonical_url, user_id=self.user_id
            )

        created = False
        snapshot_created = False
        raw_id: uuid.UUID | None = None
        if existing is None:
            ingest = posting.to_ingest(use_ai=req.use_ai, notes=req.notes).model_copy(
                update={
                    "platform": platform_value,
                    "canonical_url": canonical_url,
                    "external_id": external_id,
                }
            )
            detail = await opportunities.ingest(ingest)
            opportunity_id = detail.id
            created = True
            snapshot_created = True
        else:
            opportunity_id = existing
            snapshot, snapshot_created = await opportunities.record_snapshot(
                existing,
                OpportunitySnapshotIn(
                    raw_text=posting.raw_text or posting.title,
                    raw_payload={"provenance": posting.provenance()},
                    strategy=str(posting.strategy) if posting.strategy else None,
                    fetched_url=canonical_url,
                    resolved_url=posting.resolved_url,
                    is_archive=posting.is_archive,
                    archive_ts=posting.archive_ts,
                    quality=posting.quality,
                    extracted=posting.extraction.model_dump(mode="json")
                    if posting.extraction
                    else None,
                    content_hash=posting.content_hash,
                    captured_at=posting.fetched_at,
                    capture_method="read",
                    authority=authority,
                    source_url=source_url,
                ),
            )
            raw_id = snapshot.id
            log.info(
                "platform.duplicate_detected",
                platform=platform_value,
                opportunity=str(existing),
                snapshot=snapshot_created,
                external_id=external_id,
            )

        await opportunities.record_source(
            opportunity_id,
            SourceIn(
                platform=platform_value,
                external_id=external_id,
                source_url=source_url,
                canonical_url=canonical_url,
                original_url=posting.original_url,
                relation=OpportunityRelation(str(posting.relation)),
                authority=authority,
                strategy=str(posting.strategy) if posting.strategy else None,
                raw_id=raw_id,
                fetched_at=posting.fetched_at,
                published_at=posting.published_at or posting.posted_at,
                content_hash=posting.content_hash,
                is_archive=posting.is_archive,
                confidence=posting.quality,
            ),
        )
        if posting.original_url:
            # The employer's own posting, reached THROUGH this listing: recorded as a place the
            # job lives, never read here (that would be a second, unasked-for fetch).
            await opportunities.record_source(
                opportunity_id,
                SourceIn(
                    platform=str(Platform.website),
                    source_url=posting.original_url,
                    relation=OpportunityRelation.aggregates,
                    authority=FieldSource.employer_page,
                    confidence=None,
                ),
            )
        if posting.field_evidence:
            await opportunities.merge_field_evidence(
                opportunity_id,
                [e.model_dump(mode="json") for e in posting.field_evidence],
            )
        if closed:
            await self._mark_closed(
                opportunities,
                opportunity_id,
                authority=authority,
                source_url=source_url,
                observed_at=posting.fetched_at,
            )
        return ReadOut(
            posting=posting,
            opportunity_id=opportunity_id,
            created=created,
            duplicate_of=existing,
            snapshot_created=snapshot_created,
            closed=closed,
        )

    async def _mark_closed(
        self,
        opportunities: OpportunityService,
        opportunity_id: uuid.UUID,
        *,
        authority: FieldSource,
        source_url: str | None,
        observed_at: datetime | None,
    ) -> None:
        """Record "this posting is gone" as evidence; only *untriaged* jobs are also archived.

        A job the owner already applied to or explicitly filed keeps its status — the closure is
        a fact about the posting, not a decision about their pipeline.
        """
        await opportunities.merge_field_evidence(
            opportunity_id,
            [
                {
                    "field": "closed",
                    "value": True,
                    "source": str(authority),
                    "source_url": source_url,
                    "observed_at": (observed_at or datetime.now(UTC)).isoformat(),
                    "confidence": None,
                }
            ],
        )
        detail = await opportunities.get(opportunity_id)
        if detail.status in (OpportunityStatus.new, OpportunityStatus.watching):
            await opportunities.set_status(opportunity_id, OpportunityStatus.archived)
        log.info("platform.job_closed", opportunity=str(opportunity_id), status=str(detail.status))

    async def refresh_job(self, opportunity_id: uuid.UUID, *, no_cache: bool = True) -> ReadOut:
        """Re-read a stored job from its own URL and snapshot what changed (ADR-016 §4).

        A read that comes back 404/410 or "no longer available" is not an error: the closure is
        recorded as field evidence (and archives an untriaged job), and the attempts are returned.
        """
        opportunities = self._opportunities()
        detail = await opportunities.get(opportunity_id)
        url = detail.canonical_url or detail.url
        if not url:
            raise ParseError(f"opportunity {opportunity_id} has no URL to refresh")
        hint: Platform | None = None
        if detail.platform:
            try:
                hint = Platform(detail.platform)
            except ValueError:
                hint = None
        ref = SourceRef(
            kind=SourceKind.url,
            value=url,
            provider_hint=hint,
            metadata={
                "opportunity_id": str(opportunity_id),
                **({"external_id": detail.external_id} if detail.external_id else {}),
            },
        )
        request = ReadRequest(url=url, no_cache=no_cache, platform=hint)
        try:
            return await self.read_job(request, source=ref)
        except JobReadError as exc:
            reason = closed_reason(exc)
            if reason is None:
                raise
            await self._mark_closed(
                opportunities,
                opportunity_id,
                authority=authority_for(hint or Platform.website, None),
                source_url=url,
                observed_at=datetime.now(UTC),
            )
            return ReadOut(
                posting=None,
                opportunity_id=opportunity_id,
                duplicate_of=opportunity_id,
                closed=True,
                attempts=list(exc.attempts),
                warnings=[f"posting is gone ({reason})"],
                diagnostics=exc.diagnostics,
            )

    # kept as an alias so callers can say what they mean; ``refresh`` on this service is a job
    # refresh, while ``PlatformService.refresh`` is the OAuth token refresh.
    refresh = refresh_job


def _read_details(
    canonical: CanonicalSource,
    attempts: list[FetchAttempt],
    diagnostics: str,
    *,
    strategy: FetchStrategy | None,
    opportunity_id: uuid.UUID | None = None,
    created: bool = False,
    snapshot_created: bool = False,
    closed: bool = False,
) -> dict[str, Any]:
    """What a ``kind=job`` run stores: every attempt, in order, plus the outcome."""
    return {
        "url": canonical.canonical_url,
        "host": canonical.host,
        "external_id": canonical.external_id,
        "strategy": str(strategy) if strategy else None,
        "attempts": [a.model_dump(mode="json") for a in attempts],
        "diagnostics": diagnostics,
        "opportunity_id": str(opportunity_id) if opportunity_id else None,
        "created": created,
        "snapshot_created": snapshot_created,
        "closed": closed,
    }


def _dump(items: BaseModel | list[Any]) -> list[dict[str, Any]]:
    seq = items if isinstance(items, list) else [items]
    return [i.model_dump(mode="json") for i in seq if isinstance(i, BaseModel)]

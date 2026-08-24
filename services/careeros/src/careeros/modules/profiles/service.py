"""Profiles service: snapshots, audits (deterministic + optional AI), platform health."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from careeros.core.config import Settings
from careeros.core.logging import get_logger
from careeros.modules.ai.provider import AIError
from careeros.modules.ai.service import AIService
from careeros.modules.profiles.audit import (
    ENGINE_VERSION,
    audit_snapshot,
    category_scores,
    health_score,
)
from careeros.modules.profiles.enums import PROFILE_PLATFORMS, FindingResolution, Severity
from careeros.modules.profiles.models import AuditFinding, ProfileAudit, ProfileSnapshot
from careeros.modules.profiles.schemas import (
    AuditOut,
    FindingOut,
    PlatformHealth,
    ProfileAuditOutput,
    SnapshotIn,
    SnapshotOut,
)
from careeros.modules.vault.enums import Platform
from careeros.modules.vault.service import Vault

log = get_logger(__name__)


class ProfileError(Exception):
    pass


class SnapshotNotFound(ProfileError):
    pass


class ProfileService:
    def __init__(
        self,
        settings: Settings,
        vault: Vault,
        ai: AIService,
        *,
        session: AsyncSession,
        user_id: uuid.UUID,
    ) -> None:
        self.settings = settings
        self.vault = vault
        self.ai = ai
        self.session = session
        self.user_id = user_id

    # ------------------------------------------------------------------ snapshots
    async def create_snapshot(self, snap: SnapshotIn) -> SnapshotOut:
        payload = snap.model_dump(mode="json", exclude={"captured_at"})
        row = ProfileSnapshot(
            user_id=self.user_id,
            platform=str(snap.platform),
            capture_method=str(snap.capture_method),
            captured_at=snap.captured_at or datetime.now(UTC),
            headline=snap.headline,
            about=snap.about,
            experience=[e.model_dump(mode="json") for e in snap.experience],
            skills=list(snap.skills),
            projects=list(snap.projects),
            portfolio=list(snap.portfolio),
            rates=snap.rates,
            availability=snap.availability,
            preferences=dict(snap.preferences),
            raw_text=snap.raw_text,
            raw_payload=snap.raw_payload,
            content_hash=hashlib.sha256(str(sorted(payload.items())).encode()).hexdigest(),
        )
        self.session.add(row)
        await self.session.commit()
        log.info("profile.snapshot_created", platform=row.platform, id=str(row.id))
        return self._snapshot_out(row, audits_loaded=False)

    async def list_snapshots(
        self, *, platform: Platform | None = None, limit: int = 50
    ) -> list[SnapshotOut]:
        stmt = (
            select(ProfileSnapshot)
            .options(selectinload(ProfileSnapshot.audits).selectinload(ProfileAudit.findings))
            .order_by(ProfileSnapshot.captured_at.desc())
            .limit(limit)
        )
        if platform:
            stmt = stmt.where(ProfileSnapshot.platform == str(platform))
        rows = (await self.session.scalars(stmt)).all()
        return [self._snapshot_out(r) for r in rows]

    async def get_snapshot(self, snapshot_id: uuid.UUID) -> SnapshotOut:
        row = await self._snapshot_row(snapshot_id)
        return self._snapshot_out(row)

    # ------------------------------------------------------------------ audits
    async def audit(
        self, snapshot_id: uuid.UUID, *, use_ai: bool = False, provider: str | None = None
    ) -> AuditOut:
        row = await self._snapshot_row(snapshot_id)
        data = self.vault.require()
        snap = self._snapshot_in(row)
        findings = audit_snapshot(data, snap)
        headline_suggestion = None
        about_suggestion = None
        ai_used = False
        ai_run_id: uuid.UUID | None = None

        if use_ai:
            channel = next((c for c in data.channels if str(c.platform) == row.platform), None)
            positioning = data.by_id(data.positioning)[data.meta.default_positioning]
            fact_ids = data.fact_ids()
            facts_payload = [
                {"id": a.id, "title": a.title, "text": " ".join(a.facts)}
                for a in data.achievements[:12]
            ] + [{"id": p.id, "title": p.name, "text": p.summary} for p in data.projects[:6]]
            try:
                run = await self.ai.structured(
                    "profile_audit",
                    {
                        "platform": row.platform,
                        "channel": channel.model_dump(mode="json") if channel else {},
                        "positioning": positioning.model_dump(mode="json"),
                        "snapshot": snap.model_dump(mode="json"),
                        "facts": facts_payload,
                        "deterministic_findings": [f.model_dump(mode="json") for f in findings],
                    },
                    ProfileAuditOutput,
                    provider=provider,
                    entity_type="profile_snapshot",
                    entity_id=str(snapshot_id),
                )
                ai_used = True
                ai_run_id = run.run_id
                headline_suggestion = run.data.headline_suggestion
                about_suggestion = run.data.about_suggestion
                for af in run.data.findings:
                    unknown = [fid for fid in af.source_fact_ids if fid not in fact_ids]
                    finding = FindingOut(
                        category=af.category,
                        severity=af.severity,
                        problem=af.problem,
                        why_it_matters=af.why_it_matters,
                        suggested_change=af.suggested_change,
                        source_fact_ids=[fid for fid in af.source_fact_ids if fid in fact_ids],
                        confidence=min(af.confidence, 0.4) if unknown else af.confidence,
                        origin="ai",
                    )
                    if unknown:
                        finding.problem += f" [unverified fact refs dropped: {', '.join(unknown)}]"
                    findings.append(finding)
            except AIError as exc:
                log.warning("profile.ai_audit_failed", error=str(exc))

        scores = category_scores(findings)
        health = health_score(scores, findings)
        audit_row = ProfileAudit(
            user_id=self.user_id,
            snapshot_id=row.id,
            platform=row.platform,
            vault_sha=self.vault.head_sha(),
            engine_version=ENGINE_VERSION,
            health_score=health,
            category_scores=scores,
            headline_suggestion=headline_suggestion,
            about_suggestion=about_suggestion,
            ai_used=ai_used,
            ai_run_id=ai_run_id,
        )
        severity_rank = {
            Severity.critical: 0,
            Severity.high: 1,
            Severity.medium: 2,
            Severity.nice: 3,
        }
        findings.sort(key=lambda f: (severity_rank[f.severity], str(f.category)))
        for order, f in enumerate(findings):
            audit_row.findings.append(
                AuditFinding(
                    order=order,
                    category=str(f.category),
                    severity=str(f.severity),
                    problem=f.problem,
                    why_it_matters=f.why_it_matters,
                    suggested_change=f.suggested_change,
                    source_fact_ids=list(f.source_fact_ids),
                    confidence=f.confidence,
                    origin=f.origin,
                )
            )
        self.session.add(audit_row)
        await self.session.commit()
        log.info(
            "profile.audited",
            platform=row.platform,
            health=health,
            findings=len(findings),
            ai=ai_used,
        )
        return await self.get_audit(audit_row.id)

    async def get_audit(self, audit_id: uuid.UUID) -> AuditOut:
        row = await self.session.get(
            ProfileAudit,
            audit_id,
            options=[selectinload(ProfileAudit.findings)],
            populate_existing=True,
        )
        if row is None:
            raise SnapshotNotFound(str(audit_id))
        return self._audit_out(row)

    async def set_finding_resolution(
        self, finding_id: uuid.UUID, resolution: FindingResolution
    ) -> FindingOut:
        row = await self.session.get(AuditFinding, finding_id)
        if row is None:
            raise SnapshotNotFound(str(finding_id))
        row.resolution = str(resolution)
        await self.session.commit()
        return self._finding_out(row)

    async def platform_health(self) -> list[PlatformHealth]:
        """Latest audited health per platform — the dashboard's profile card."""
        out: list[PlatformHealth] = []
        for platform in PROFILE_PLATFORMS:
            snap = await self.session.scalar(
                select(ProfileSnapshot)
                .options(selectinload(ProfileSnapshot.audits).selectinload(ProfileAudit.findings))
                .where(ProfileSnapshot.platform == str(platform))
                .order_by(ProfileSnapshot.captured_at.desc())
                .limit(1)
            )
            if snap is None:
                out.append(
                    PlatformHealth(
                        platform=platform,
                        snapshot_id=None,
                        captured_at=None,
                        health_score=None,
                        open_findings=0,
                        top_severity=None,
                        audited_at=None,
                    )
                )
                continue
            audit = snap.audits[0] if snap.audits else None
            open_findings = [f for f in audit.findings if f.resolution == "open"] if audit else []
            top = min(
                (Severity(f.severity) for f in open_findings),
                default=None,
                key=lambda sv: {
                    Severity.critical: 0,
                    Severity.high: 1,
                    Severity.medium: 2,
                    Severity.nice: 3,
                }[sv],
            )
            out.append(
                PlatformHealth(
                    platform=platform,
                    snapshot_id=snap.id,
                    captured_at=snap.captured_at,
                    health_score=audit.health_score if audit else None,
                    open_findings=len(open_findings),
                    top_severity=top,
                    audited_at=audit.created_at if audit else None,
                )
            )
        return out

    # ------------------------------------------------------------------ internals
    async def _snapshot_row(self, snapshot_id: uuid.UUID) -> ProfileSnapshot:
        row = await self.session.get(
            ProfileSnapshot,
            snapshot_id,
            options=[selectinload(ProfileSnapshot.audits).selectinload(ProfileAudit.findings)],
            populate_existing=True,
        )
        if row is None:
            raise SnapshotNotFound(str(snapshot_id))
        return row

    @staticmethod
    def _snapshot_in(row: ProfileSnapshot) -> SnapshotIn:
        return SnapshotIn(
            platform=Platform(row.platform),
            capture_method=row.capture_method,  # type: ignore[arg-type]
            captured_at=row.captured_at,
            headline=row.headline,
            about=row.about,
            experience=row.experience,  # type: ignore[arg-type]
            skills=list(row.skills or []),
            projects=list(row.projects or []),
            portfolio=list(row.portfolio or []),
            rates=row.rates,
            availability=row.availability,
            preferences=dict(row.preferences or {}),
            raw_text=row.raw_text,
            raw_payload=row.raw_payload,
        )

    def _snapshot_out(self, row: ProfileSnapshot, *, audits_loaded: bool = True) -> SnapshotOut:
        latest = row.audits[0] if audits_loaded and row.audits else None
        return SnapshotOut(
            **self._snapshot_in(row).model_dump(exclude={"captured_at"}),
            captured_at=row.captured_at,
            id=row.id,
            content_hash=row.content_hash,
            created_at=row.created_at,
            latest_audit_id=latest.id if latest else None,
            latest_health_score=latest.health_score if latest else None,
        )

    def _finding_out(self, f: AuditFinding) -> FindingOut:
        return FindingOut(
            id=f.id,
            category=f.category,  # type: ignore[arg-type]
            severity=f.severity,  # type: ignore[arg-type]
            problem=f.problem,
            why_it_matters=f.why_it_matters,
            suggested_change=f.suggested_change,
            source_fact_ids=list(f.source_fact_ids or []),
            confidence=f.confidence,
            origin=f.origin,
            resolution=f.resolution,  # type: ignore[arg-type]
        )

    def _audit_out(self, row: ProfileAudit) -> AuditOut:
        return AuditOut(
            id=row.id,
            snapshot_id=row.snapshot_id,
            platform=Platform(row.platform),
            vault_sha=row.vault_sha,
            engine_version=row.engine_version,
            health_score=row.health_score,
            category_scores=dict(row.category_scores),
            findings=[self._finding_out(f) for f in row.findings],
            headline_suggestion=row.headline_suggestion,
            about_suggestion=row.about_suggestion,
            ai_used=row.ai_used,
            ai_run_id=row.ai_run_id,
            created_at=row.created_at,
        )

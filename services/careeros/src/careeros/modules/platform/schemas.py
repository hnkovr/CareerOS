"""Pydantic DTOs of the platform layer: capabilities, connector outputs, API payloads."""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, computed_field, field_validator

from careeros.modules.opportunities.schemas import IngestRequest, OpportunityExtraction
from careeros.modules.platform.enums import (
    LEVEL_BY_METHOD,
    METHOD_ORDER,
    SOURCE_BY_PLATFORM,
    AccessMode,
    ApplicationStatus,
    ApplyLevel,
    AuthKind,
    CapabilityLevel,
    ConnectionStatus,
    FetchStrategy,
    SourceRelation,
    SyncKind,
    SyncMethod,
    SyncStatus,
)
from careeros.modules.profiles.enums import CaptureMethod
from careeros.modules.profiles.schemas import SnapshotExperienceItem, SnapshotIn
from careeros.modules.vault.enums import Platform

# ------------------------------------------------------------------------------ capabilities


def _ordered_methods(methods: list[SyncMethod]) -> list[SyncMethod]:
    return [m for m in METHOD_ORDER if m in set(methods)]


class Capabilities(BaseModel):
    """Static declaration of what a connector can do and through which methods (ADR-004).

    ``profile`` / ``jobs`` / ``applications`` list the implemented methods, best first. The
    ADR-004 level names (``read_profile`` …) are derived so the matrix stays honest by construction.
    """

    platform: Platform
    profile: list[SyncMethod] = Field(default_factory=list)
    jobs: list[SyncMethod] = Field(default_factory=list)
    applications: list[SyncMethod] = Field(default_factory=list)
    write_profile: CapabilityLevel = CapabilityLevel.none
    read_messages: CapabilityLevel = CapabilityLevel.none
    apply: ApplyLevel = ApplyLevel.none
    official_api: bool = False
    email_fallback: bool = False
    auth: AuthKind = AuthKind.none
    #: True only for the provider that claims ANY url at low confidence (``website``).
    #: Consumers group it apart: a reader, not a service the user has an account on.
    fallback: bool = False
    #: Ordered strategies for reading one job behind a user-supplied URL (ADR-015), best first.
    read_job: list[FetchStrategy] = Field(default_factory=list)
    #: Access policy enforced before any network call; ``public`` is required by the
    #: ``public_html`` / ``jina`` / ``wayback`` strategies (``PlatformRegistry.verify``).
    access: AccessMode = AccessMode.manual_import
    notes: str = ""

    @field_validator("profile", "jobs", "applications", mode="after")
    @classmethod
    def _order(cls, v: list[SyncMethod]) -> list[SyncMethod]:
        return _ordered_methods(v)

    @field_validator("read_job", mode="after")
    @classmethod
    def _unique_strategies(cls, v: list[FetchStrategy]) -> list[FetchStrategy]:
        out: list[FetchStrategy] = []
        for x in v:
            if x not in out:
                out.append(x)
        return out

    def methods(self, kind: SyncKind) -> list[SyncMethod]:
        """Sync methods per kind; ``job`` (single URL read) has strategies, not methods."""
        return {
            SyncKind.profile: self.profile,
            SyncKind.jobs: self.jobs,
            SyncKind.applications: self.applications,
            SyncKind.job: [],
        }[kind]

    @computed_field
    @property
    def read_one(self) -> bool:
        """The connector can read a single job behind a URL (``read_job`` non-empty)."""
        return bool(self.read_job)

    def level(self, kind: SyncKind) -> CapabilityLevel:
        methods = self.methods(kind)
        return LEVEL_BY_METHOD[methods[0]] if methods else CapabilityLevel.none

    @computed_field
    @property
    def read_profile(self) -> CapabilityLevel:
        return self.level(SyncKind.profile)

    @computed_field
    @property
    def read_opportunities(self) -> CapabilityLevel:
        return self.level(SyncKind.jobs)

    @computed_field
    @property
    def read_applications(self) -> CapabilityLevel:
        return self.level(SyncKind.applications)

    @computed_field
    @property
    def export_import(self) -> CapabilityLevel:
        has_export = any(SyncMethod.export in self.methods(k) for k in SyncKind)
        return CapabilityLevel.export if has_export else CapabilityLevel.none

    @computed_field
    @property
    def manual_capture(self) -> bool:
        return any(SyncMethod.paste in self.methods(k) for k in SyncKind)


# ------------------------------------------------------------------------------ connector outputs


class ProfileRead(BaseModel):
    """Own profile as read from a platform. Maps 1:1 onto ``profiles.SnapshotIn``."""

    platform: Platform
    capture_method: CaptureMethod = CaptureMethod.paste
    external_id: str | None = None
    profile_url: str | None = None
    captured_at: datetime | None = None
    headline: str | None = None
    about: str | None = None
    experience: list[SnapshotExperienceItem] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    projects: list[dict[str, Any]] = Field(default_factory=list)
    portfolio: list[dict[str, Any]] = Field(default_factory=list)
    rates: dict[str, Any] | None = None
    availability: str | None = None
    preferences: dict[str, Any] = Field(default_factory=dict)
    raw_text: str | None = None
    raw_payload: dict[str, Any] | None = None

    @field_validator("skills", "experience", "projects", "portfolio", mode="before")
    @classmethod
    def _none_to_list(cls, v: object) -> object:
        return [] if v is None else v

    def to_snapshot(self) -> SnapshotIn:
        preferences = dict(self.preferences)
        if self.profile_url:
            preferences.setdefault("profile_url", self.profile_url)
        if self.external_id:
            preferences.setdefault("external_id", self.external_id)
        return SnapshotIn(
            platform=self.platform,
            capture_method=self.capture_method,
            captured_at=self.captured_at,
            headline=self.headline,
            about=self.about,
            experience=list(self.experience),
            skills=list(self.skills),
            projects=list(self.projects),
            portfolio=list(self.portfolio),
            rates=self.rates,
            availability=self.availability,
            preferences=preferences,
            raw_text=self.raw_text,
            raw_payload=self.raw_payload,
        )


class FieldEvidence(BaseModel):
    """Where one extracted field came from (ADR-016): kept per field, never last-write-wins.

    ``source`` names the extractor / authority (``jsonld``, ``embedded``, ``api``,
    ``text_heuristic``, ``aggregator_estimate``, ``llm`` …); ``value`` is JSON-shaped.
    """

    field: str
    value: Any = None
    source: str
    source_url: str | None = None
    observed_at: datetime | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class FetchAttempt(BaseModel):
    """One strategy attempt of a job read — the diagnostics unit stored in ``PlatformSyncRun``."""

    strategy: FetchStrategy
    url: str
    status_code: int | None = None
    ok: bool = False
    error_type: str | None = None
    error_message: str | None = None
    duration_ms: int = 0
    cache_status: Literal["hit", "miss", "negative", "bypass", "skip"] = "miss"


class JobPosting(BaseModel):
    """A job as seen on a platform. Maps onto ``opportunities.IngestRequest``.

    The provenance block (``canonical_url`` … ``field_evidence``) is filled by a job read
    (ADR-015/016); paste/search postings leave it at the defaults.
    """

    platform: Platform
    external_id: str | None = None
    url: str | None = None
    title: str
    company: str | None = None
    location: str | None = None
    posted_at: datetime | None = None
    raw_text: str = ""
    extraction: OpportunityExtraction | None = None
    raw_payload: dict[str, Any] | None = None
    # ---- provenance (job reads)
    canonical_url: str | None = None
    resolved_url: str | None = None
    original_url: str | None = Field(default=None, description="the employer's posting, if known")
    strategy: FetchStrategy | None = None
    fetched_at: datetime | None = None
    published_at: datetime | None = None
    expires_at: datetime | None = None
    content_hash: str | None = None
    fingerprint: str | None = None
    is_archive: bool = False
    archive_ts: datetime | None = Field(default=None, description="capture time, never a date")
    quality: float | None = None
    completeness: float | None = None
    relation: SourceRelation = SourceRelation.primary
    field_evidence: list[FieldEvidence] = Field(default_factory=list)

    def has_provenance(self) -> bool:
        return any(
            (self.canonical_url, self.strategy, self.fetched_at, self.content_hash, self.is_archive)
        )

    def provenance(self) -> dict[str, Any]:
        """JSON-shaped provenance the sync layer writes to ``opportunity_source`` / raw rows."""
        return self.model_dump(
            mode="json",
            include={
                "platform",
                "external_id",
                "canonical_url",
                "resolved_url",
                "original_url",
                "strategy",
                "fetched_at",
                "published_at",
                "expires_at",
                "content_hash",
                "fingerprint",
                "is_archive",
                "archive_ts",
                "quality",
                "completeness",
                "relation",
                "field_evidence",
            },
        )

    def to_ingest(
        self, *, use_ai: bool = False, provider: str | None = None, notes: str | None = None
    ) -> IngestRequest:
        structured = self.extraction or OpportunityExtraction()
        updates: dict[str, Any] = {}
        if structured.title is None:
            updates["title"] = self.title
        if structured.company is None and self.company:
            updates["company"] = self.company
        if structured.location is None and self.location:
            updates["location"] = self.location
        if updates:
            structured = structured.model_copy(update=updates)
        text = self.raw_text or None
        if text is None:
            text = "\n".join(p for p in (self.title, self.company, self.location) if p)
        raw_payload = self.raw_payload
        if self.has_provenance():
            # no IngestRequest field for it yet → travels inside the verbatim payload
            raw_payload = {**(raw_payload or {}), "provenance": self.provenance()}
        return IngestRequest(
            source=SOURCE_BY_PLATFORM[self.platform],
            url=self.url or self.canonical_url,
            text=text,
            structured=structured,
            use_ai=use_ai,
            provider=provider,
            received_at=self.published_at or self.posted_at,
            notes=notes,
            external_id=self.external_id,
            raw_payload=raw_payload,
        )


class ApplicationObservationIn(BaseModel):
    """One application/response as observed on a platform (read-only fact).

    Identity: ``external_id`` when the platform gives one, else ``content_hash()`` over
    title/company/url — the hash deliberately excludes ``external_id`` so a paste (no id) and a
    later API read (with id) of the same application merge into one row.
    """

    platform: Platform
    external_id: str | None = None
    job_title: str
    company: str | None = None
    job_url: str | None = None
    status_raw: str = ""
    status: ApplicationStatus = ApplicationStatus.unknown
    applied_at: datetime | None = None
    updated_at_platform: datetime | None = None
    raw_payload: dict[str, Any] | None = None

    def content_hash(self) -> str:
        key = "|".join(
            [
                str(self.platform),
                self.job_title.strip().lower(),
                (self.company or "").strip().lower(),
                (self.job_url or "").strip(),
            ]
        )
        return hashlib.sha256(key.encode()).hexdigest()


class JobQuery(BaseModel):
    text: str | None = None
    location: str | None = None
    remote: bool | None = None
    salary_min: float | None = None
    currency: str | None = None
    posted_since: date | None = None
    limit: int = Field(default=30, ge=1, le=100)
    extra: dict[str, Any] = Field(
        default_factory=dict, description="platform-specific knobs (hh area, upwork category …)"
    )


class AccountInfo(BaseModel):
    account_id: str | None = None
    label: str | None = None
    profile_url: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class DoctorCheck(BaseModel):
    name: str
    ok: bool
    detail: str = ""
    fix: str | None = None


class OAuthConfig(BaseModel):
    authorize_url: str
    token_url: str
    client_id: str
    client_secret: SecretStr
    scopes: list[str] = Field(default_factory=list)
    redirect_uri: str
    extra_authorize_params: dict[str, str] = Field(default_factory=dict)
    token_auth: Literal["body", "basic"] = "body"


# ------------------------------------------------------------------------------ API payloads


class ConnectionOut(BaseModel):
    platform: Platform
    status: ConnectionStatus
    auth: AuthKind
    has_tokens: bool
    pinned: bool = Field(default=False, description="tokens come from env, not the token file")
    account_id: str | None = None
    account_label: str | None = None
    scopes: list[str] = Field(default_factory=list)
    token_expires_at: datetime | None = None
    last_sync_at: datetime | None = None
    last_error: str | None = None
    capabilities: Capabilities


class PlatformUrls(BaseModel):
    platform: Platform
    search_url: str | None = Field(default=None, description="None = not expressible as a URL")
    profile_url: str | None = Field(default=None, description="None = the owner's URL is not known")


class OAuthStartOut(BaseModel):
    platform: Platform
    authorize_url: str
    state: str
    redirect_uri: str


class SyncRunOut(BaseModel):
    id: uuid.UUID
    platform: Platform
    kind: SyncKind
    method: SyncMethod
    status: SyncStatus
    started_at: datetime
    finished_at: datetime | None
    items_seen: int
    items_created: int
    items_updated: int
    items_skipped: int
    error: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ApplicationObservationOut(ApplicationObservationIn):
    id: uuid.UUID
    observed_at: datetime
    opportunity_id: uuid.UUID | None = None
    sync_run_id: uuid.UUID | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)


class SyncRequest(BaseModel):
    method: SyncMethod | None = Field(default=None, description="default: best available")
    text: str | None = Field(default=None, description="pasted page text (paste method)")
    file_path: str | None = Field(default=None, description="export file/dir/zip (export method)")
    query: JobQuery | None = None
    use_ai: bool = False
    provider: str | None = None
    dry_run: bool = Field(default=False, description="parse/fetch only; persist nothing")


class SyncResult(BaseModel):
    platform: Platform
    kind: SyncKind
    method: SyncMethod | None
    status: SyncStatus
    run: SyncRunOut | None = None
    items_seen: int = 0
    items_created: int = 0
    items_updated: int = 0
    items_skipped: int = 0
    created_ids: list[uuid.UUID] = Field(default_factory=list)
    duplicates: list[uuid.UUID] = Field(default_factory=list)
    preview: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    message: str | None = None


class ParseResult(BaseModel):
    platform: Platform
    kind: SyncKind
    method: SyncMethod
    items: list[dict[str, Any]]
    count: int


class ReadRequest(BaseModel):
    """Read one job behind a user-supplied URL (ADR-015)."""

    url: str
    dry_run: bool = Field(default=False, description="fetch/extract only; persist nothing")
    use_ai: bool = Field(default=False, description="AI extraction to fill deterministic gaps")
    no_cache: bool = Field(default=False, description="bypass the in-process fetch cache")
    strategy: FetchStrategy | None = Field(default=None, description="force one strategy")
    notes: str | None = None
    platform: Platform | None = Field(
        default=None, description="force a provider instead of detecting one from the URL"
    )


class DetectionOut(BaseModel):
    """Which provider owns a URL (``GET /api/platform/detect``) — no network, no persistence."""

    platform: Platform
    confidence: float = Field(ge=0.0, le=1.0)
    canonical_url: str
    external_id: str | None = None
    host: str
    locale: str | None = None
    private: bool = Field(
        default=False, description="came from a private message: no third-party strategies"
    )


class ReadOut(BaseModel):
    posting: JobPosting | None
    opportunity_id: uuid.UUID | None = None
    created: bool = False
    duplicate_of: uuid.UUID | None = None
    snapshot_created: bool = False
    closed: bool = Field(
        default=False, description="the posting reads as closed / gone (evidence recorded)"
    )
    run_id: uuid.UUID | None = Field(default=None, description="the PlatformSyncRun that read it")
    attempts: list[FetchAttempt] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    diagnostics: str = ""

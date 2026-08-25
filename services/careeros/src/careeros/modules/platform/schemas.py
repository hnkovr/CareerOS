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
    ApplicationStatus,
    ApplyLevel,
    AuthKind,
    CapabilityLevel,
    ConnectionStatus,
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
    notes: str = ""

    @field_validator("profile", "jobs", "applications", mode="after")
    @classmethod
    def _order(cls, v: list[SyncMethod]) -> list[SyncMethod]:
        return _ordered_methods(v)

    def methods(self, kind: SyncKind) -> list[SyncMethod]:
        return {
            SyncKind.profile: self.profile,
            SyncKind.jobs: self.jobs,
            SyncKind.applications: self.applications,
        }[kind]

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


class JobPosting(BaseModel):
    """A job as seen on a platform. Maps onto ``opportunities.IngestRequest``."""

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
        return IngestRequest(
            source=SOURCE_BY_PLATFORM[self.platform],
            url=self.url,
            text=text,
            structured=structured,
            use_ai=use_ai,
            provider=provider,
            received_at=self.posted_at,
            notes=notes,
            external_id=self.external_id,
            raw_payload=self.raw_payload,
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

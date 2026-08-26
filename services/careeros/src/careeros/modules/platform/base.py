"""Connector contract (ADR-004): pure I/O + mapping, no database, no domain services."""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import httpx

from careeros.core.config import Settings
from careeros.modules.platform.enums import FetchStrategy, SyncKind, SyncMethod
from careeros.modules.platform.schemas import (
    AccountInfo,
    ApplicationObservationIn,
    Capabilities,
    DoctorCheck,
    JobPosting,
    JobQuery,
    OAuthConfig,
    ProfileRead,
)
from careeros.modules.platform.sources import (
    CanonicalSource,
    DetectionResult,
    SourceRef,
    canonical_source,
    host_of,
    is_http_url,
)
from careeros.modules.platform.tokens import OAuthTokens, client_credentials
from careeros.modules.vault.enums import Platform

if TYPE_CHECKING:
    from careeros.modules.platform.fetch.artifact import FetchArtifact, JobRead
    from careeros.modules.platform.fetch.budget import FetchBudget
    from careeros.modules.platform.fetch.cache import FetchCache
    from careeros.modules.platform.fetch.robots import RobotsPolicy

# ------------------------------------------------------------------------------ errors


class PlatformError(Exception):
    """Base class of every platform-layer error."""


class CapabilityUnavailable(PlatformError):
    def __init__(
        self,
        platform: Platform,
        kind: SyncKind,
        method: SyncMethod | None,
        available: list[SyncMethod],
    ) -> None:
        self.platform = platform
        self.kind = kind
        self.method = method
        self.available = list(available)
        wanted = f"via {method}" if method else "at all"
        avail = ", ".join(str(m) for m in available) or "none"
        super().__init__(
            f"{platform}: {kind} is not available {wanted} (available methods: {avail})"
        )


class NotConnected(PlatformError):
    def __init__(self, platform: Platform, hint: str | None = None) -> None:
        self.platform = platform
        self.hint = hint or f"connect first: careeros platform connect {platform}"
        super().__init__(f"{platform}: not connected — {self.hint}")


class UpstreamError(PlatformError):
    def __init__(self, platform: Platform, status_code: int | None, detail: str) -> None:
        self.platform = platform
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{platform}: upstream error {status_code or ''}: {detail}".strip())


class ParseError(PlatformError):
    """Pasted text / export file is not in a shape the connector recognises."""


class ReadUnavailable(PlatformError):
    """The connector declares no way to read a single job (``capabilities.read_job`` empty)."""

    def __init__(self, platform: Platform, strategy: FetchStrategy | None = None) -> None:
        self.platform = platform
        self.strategy = strategy
        wanted = f"via {strategy}" if strategy else "at all"
        super().__init__(f"{platform}: reading one job by URL is not available {wanted}")


# ------------------------------------------------------------------------------ context


@dataclass(slots=True)
class ConnectorContext:
    """Everything a connector may touch: settings, an HTTP client, the user's tokens, the clock."""

    settings: Settings
    http: httpx.AsyncClient
    tokens: OAuthTokens | None = None
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    # Connectors append non-fatal problems here (e.g. one of several API pages failed); the sync
    # reports the run as ``partial`` and stores them — nothing is silently dropped.
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------------------ contract

# (kind, method) → method name a connector must override when it declares that capability.
METHOD_IMPL: dict[tuple[SyncKind, SyncMethod], str] = {
    (SyncKind.profile, SyncMethod.api): "read_profile",
    (SyncKind.profile, SyncMethod.export): "import_profile_export",
    (SyncKind.profile, SyncMethod.paste): "parse_profile_text",
    (SyncKind.jobs, SyncMethod.api): "search_jobs",
    (SyncKind.jobs, SyncMethod.export): "import_jobs_export",
    (SyncKind.jobs, SyncMethod.paste): "parse_jobs_text",
    (SyncKind.applications, SyncMethod.api): "application_statuses",
    (SyncKind.applications, SyncMethod.export): "import_applications_export",
    (SyncKind.applications, SyncMethod.paste): "parse_applications_text",
}

# Job reads (ADR-015): strategy → method a connector must override when it declares it.
READ_IMPL: dict[FetchStrategy, str] = {FetchStrategy.api: "fetch_job_api"}
# Any declared read strategy ⇒ these are overridden (``detect`` may instead rely on
# ``detect_hosts``); the registry enforces it.
READ_REQUIRED: tuple[str, ...] = ("detect", "extract_job")
DETECT_CONFIDENCE_HOST = 0.9


class BaseConnector(ABC):
    """Per-platform connector. Override only the methods you declare in ``capabilities``.

    Defaults raise ``CapabilityUnavailable`` so callers can branch on the declared matrix instead
    of catching ``NotImplementedError``. Connectors never persist anything and never call domain
    services (enforced by import-linter).
    """

    platform: ClassVar[Platform]
    capabilities: ClassVar[Capabilities]
    # True when the platform's job search is public (no user token needed, e.g. hh.ru).
    jobs_without_token: ClassVar[bool] = False
    # Hosts (without ``www.``) the default ``detect()`` recognises with high confidence;
    # subdomains match. Connectors with URL-shape rules override ``detect()`` instead.
    detect_hosts: ClassVar[tuple[str, ...]] = ()

    # ---- profile
    async def read_profile(self, ctx: ConnectorContext) -> ProfileRead:
        raise self._unavailable(SyncKind.profile, SyncMethod.api)

    def import_profile_export(self, path: Path) -> ProfileRead:
        raise self._unavailable(SyncKind.profile, SyncMethod.export)

    def parse_profile_text(self, text: str) -> ProfileRead:
        raise self._unavailable(SyncKind.profile, SyncMethod.paste)

    # ---- jobs
    async def search_jobs(self, ctx: ConnectorContext, query: JobQuery) -> list[JobPosting]:
        raise self._unavailable(SyncKind.jobs, SyncMethod.api)

    def import_jobs_export(self, path: Path) -> list[JobPosting]:
        raise self._unavailable(SyncKind.jobs, SyncMethod.export)

    def parse_jobs_text(self, text: str) -> list[JobPosting]:
        raise self._unavailable(SyncKind.jobs, SyncMethod.paste)

    # ---- applications
    async def application_statuses(self, ctx: ConnectorContext) -> list[ApplicationObservationIn]:
        raise self._unavailable(SyncKind.applications, SyncMethod.api)

    def import_applications_export(self, path: Path) -> list[ApplicationObservationIn]:
        raise self._unavailable(SyncKind.applications, SyncMethod.export)

    def parse_applications_text(self, text: str) -> list[ApplicationObservationIn]:
        raise self._unavailable(SyncKind.applications, SyncMethod.paste)

    # ---- one job behind a URL (ADR-015)
    def detect(self, url: str) -> DetectionResult | None:
        """Does this connector own ``url``? Default: ``detect_hosts`` match → 0.9, else ``None``."""
        if not self.detect_hosts or not is_http_url(url):
            return None
        host = host_of(url)
        if not any(host == h or host.endswith("." + h) for h in self.detect_hosts):
            return None
        return DetectionResult(
            platform=self.platform,
            confidence=DETECT_CONFIDENCE_HOST,
            canonical=self.canonicalize(url),
        )

    def canonicalize(self, source: SourceRef | str) -> CanonicalSource:
        """Normalised form of a URL / reference. Default: ``dedup.normalize_url``, no id.

        Raises ``ValueError`` when the reference carries no http(s) URL (a ``provider_id``
        reference needs a connector-specific override).
        """
        if isinstance(source, str):
            return canonical_source(self.platform, source)
        url = source.url()
        if url is None:
            raise ValueError(f"{self.platform}: {source.kind} reference without a URL")
        return canonical_source(
            self.platform,
            url,
            locale=source.metadata.get("locale"),
            private=source.is_private,
        )

    async def fetch_job(
        self,
        ctx: ConnectorContext,
        source: CanonicalSource,
        budget: FetchBudget,
        *,
        only: FetchStrategy | None = None,
        use_cache: bool = True,
        cache: FetchCache | None = None,
        policy: RobotsPolicy | None = None,
    ) -> JobRead:
        """Run the declared ``read_job`` strategies best-first; stop at the first usable artifact.

        ``extract_job`` turns that artifact into a ``JobPosting``. Raises ``JobReadError`` (with
        every attempt) when nothing usable came back, ``ReadUnavailable`` when nothing is declared.
        """
        from careeros.modules.platform.fetch.cache import default_cache
        from careeros.modules.platform.fetch.robots import RobotsPolicy as _RobotsPolicy
        from careeros.modules.platform.fetch.strategies import build_strategies, run_chain

        if not self.capabilities.read_job:
            raise ReadUnavailable(self.platform, only)
        strategies, notes = build_strategies(self, ctx.settings, only=only)
        if use_cache and cache is None:
            cache = default_cache(ctx.settings)
        if not use_cache:
            cache = None
        if policy is None:
            policy = _RobotsPolicy(ctx.http, user_agent=ctx.settings.platform_user_agent)
        return await run_chain(
            strategies, ctx, source, budget, cache, policy, extract=self.extract_job, notes=notes
        )

    def extract_job(self, artifact: FetchArtifact) -> JobPosting:
        """Deterministic extraction: JSON-LD ``JobPosting`` → embedded state hints → text.

        Connectors that declare ``read_job`` override this (the registry insists) and usually
        call it for the HTML case. Raises ``ValueError`` when no title can be found.
        """
        from careeros.modules.platform.fetch.extract.embedded import (
            find_next_data,
            find_nuxt,
            search_keys,
        )
        from careeros.modules.platform.fetch.extract.jsonld import (
            find_jobposting,
            jobposting_to_posting,
        )
        from careeros.modules.platform.fetch.extract.text import (
            html_meta,
            html_to_text,
            markdown_body,
            markdown_title,
            text_to_posting,
        )

        url = artifact.resolved_url or artifact.requested_url
        if artifact.raw_json is not None:
            payload = artifact.raw_json
            found = search_keys(payload, ("title", "name", "description", "company", "employer"))
            title = found.get("title") or found.get("name")
            if not isinstance(title, str) or not title.strip():
                raise ValueError("API payload without a title")
            text = found.get("description") if isinstance(found.get("description"), str) else ""
            company = found.get("company") or found.get("employer")
            posting = text_to_posting(
                text or title,
                self.platform,
                url,
                title=title,
                company=company if isinstance(company, str) else None,
                fetched_at=artifact.fetched_at,
                source="api",
                confidence=0.9,
            )
            return posting.model_copy(update={"raw_payload": {"api": payload}})
        raw = artifact.raw_text or ""
        if not raw.strip():
            raise ValueError("empty artifact")
        if artifact.is_markdown and not artifact.is_html:
            meta, body = markdown_body(raw)
            title = meta.get("title") or markdown_title(body)
            return text_to_posting(
                body,
                self.platform,
                url,
                title=title,
                fetched_at=artifact.fetched_at,
                source="jina_markdown",
            )
        jsonld = find_jobposting(raw)
        if jsonld is not None:
            return jobposting_to_posting(jsonld, self.platform, url, fetched_at=artifact.fetched_at)
        meta = html_meta(raw)
        embedded = find_next_data(raw) or find_nuxt(raw)
        hints = search_keys(embedded, ("title", "company", "companyName")) if embedded else {}
        title = (
            meta.get("h1")
            or meta.get("og:title")
            or (hints.get("title") if isinstance(hints.get("title"), str) else None)
            or meta.get("title")
        )
        company = hints.get("company") or hints.get("companyName")
        text = html_to_text(raw)
        if meta.get("og:description") and meta["og:description"] not in text:
            text = f"{meta['og:description']}\n\n{text}"
        return text_to_posting(
            text,
            self.platform,
            url,
            title=title,
            company=company if isinstance(company, str) else None,
            fetched_at=artifact.fetched_at,
        )

    async def fetch_job_api(self, ctx: ConnectorContext, source: CanonicalSource) -> FetchArtifact:
        """Native API read of one job (``FetchStrategy.api``); override when declared."""
        raise ReadUnavailable(self.platform, FetchStrategy.api)

    # ---- URLs the user can open (no fetching — these are for the user's own browser / the bot)
    def search_url(self, query: JobQuery) -> str | None:
        """The platform's own job-search page for ``query`` (``None`` = not expressible)."""
        return None

    def profile_url(self, handle: str | None = None) -> str | None:
        """Canonical public profile URL for ``handle`` on this platform; ``None`` when unknown."""
        return None

    # ---- auth / health
    def oauth_config(self, settings: Settings) -> OAuthConfig | None:
        """OAuth2 endpoints for API platforms; ``None`` when the platform needs no tokens.

        API connectors raise ``NotConnected`` with a hint when client credentials are missing.
        """
        return None

    async def whoami(self, ctx: ConnectorContext) -> AccountInfo:
        raise CapabilityUnavailable(self.platform, SyncKind.profile, SyncMethod.api, [])

    async def doctor(self, ctx: ConnectorContext) -> list[DoctorCheck]:
        """Configuration/token checks; API connectors extend with a cheap live probe."""
        return self.generic_checks(ctx)

    def auth_headers(self, ctx: ConnectorContext) -> dict[str, str]:
        if ctx.tokens is None:
            return {}
        return {"Authorization": f"Bearer {ctx.tokens.access_token.get_secret_value()}"}

    def require_tokens(self, ctx: ConnectorContext) -> OAuthTokens:
        if ctx.tokens is None:
            raise NotConnected(self.platform)
        return ctx.tokens

    # ---- helpers
    def generic_checks(self, ctx: ConnectorContext) -> list[DoctorCheck]:
        checks: list[DoctorCheck] = []
        caps = self.capabilities
        checks.append(
            DoctorCheck(
                name="capabilities",
                ok=True,
                detail=(
                    f"profile={caps.read_profile} jobs={caps.read_opportunities} "
                    f"applications={caps.read_applications}"
                ),
            )
        )
        if caps.auth == "oauth2":
            creds = client_credentials(ctx.settings, self.platform)
            checks.append(
                DoctorCheck(
                    name="client_credentials",
                    ok=creds is not None,
                    detail="configured" if creds else "missing",
                    fix=None
                    if creds
                    else (
                        f"set CAREEROS_{str(self.platform).upper()}_CLIENT_ID / "
                        f"CAREEROS_{str(self.platform).upper()}_CLIENT_SECRET"
                    ),
                )
            )
        if caps.auth != "none":
            tokens = ctx.tokens
            if tokens is None:
                checks.append(
                    DoctorCheck(
                        name="tokens",
                        ok=False,
                        detail="no tokens",
                        fix=f"careeros platform connect {self.platform}",
                    )
                )
            else:
                expired = tokens.is_expired(ctx.now)
                checks.append(
                    DoctorCheck(
                        name="tokens",
                        ok=not expired,
                        detail="expired" if expired else "present",
                        fix=f"careeros platform refresh {self.platform}" if expired else None,
                    )
                )
        return checks

    def _unavailable(self, kind: SyncKind, method: SyncMethod) -> CapabilityUnavailable:
        return CapabilityUnavailable(self.platform, kind, method, self.capabilities.methods(kind))

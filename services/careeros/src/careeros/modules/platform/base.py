"""Connector contract (ADR-004): pure I/O + mapping, no database, no domain services."""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import httpx

from careeros.core.config import Settings
from careeros.modules.platform.enums import SyncKind, SyncMethod
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
from careeros.modules.platform.tokens import OAuthTokens, client_credentials
from careeros.modules.vault.enums import Platform

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


# ------------------------------------------------------------------------------ context


@dataclass(slots=True)
class ConnectorContext:
    """Everything a connector may touch: settings, an HTTP client, the user's tokens, the clock."""

    settings: Settings
    http: httpx.AsyncClient
    tokens: OAuthTokens | None = None
    now: datetime = field(default_factory=lambda: datetime.now(UTC))


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


class BaseConnector(ABC):
    """Per-platform connector. Override only the methods you declare in ``capabilities``.

    Defaults raise ``CapabilityUnavailable`` so callers can branch on the declared matrix instead
    of catching ``NotImplementedError``. Connectors never persist anything and never call domain
    services (enforced by import-linter).
    """

    platform: ClassVar[Platform]
    capabilities: ClassVar[Capabilities]

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

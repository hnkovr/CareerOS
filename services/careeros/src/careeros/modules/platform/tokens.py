"""OAuth token storage: a git-ignored 0600 JSON file, overridable per platform by environment.

Tokens are user-granted, scoped and revocable — never passwords or session cookies (ADR-005/011).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, SecretStr

from careeros.core.config import Settings
from careeros.core.logging import get_logger
from careeros.modules.vault.enums import Platform

log = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class OAuthTokens(BaseModel):
    access_token: SecretStr
    refresh_token: SecretStr | None = None
    token_type: str = "bearer"
    expires_at: datetime | None = None
    scope: str | None = None
    obtained_at: datetime = Field(default_factory=_utcnow)
    source: Literal["store", "env"] = Field(
        default="store", description="env = pinned via CAREEROS_<PLATFORM>_ACCESS_TOKEN"
    )

    def is_expired(self, now: datetime | None = None, skew_s: int = 60) -> bool:
        if self.expires_at is None:
            return False
        now = now or _utcnow()
        return (self.expires_at - now).total_seconds() <= skew_s

    @property
    def pinned(self) -> bool:
        return self.source == "env"

    def redacted(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "access_token": "***",
            "refresh_token": "***" if self.refresh_token else None,
            "token_type": self.token_type,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "scope": self.scope,
            "obtained_at": self.obtained_at.isoformat(),
        }

    def dump_secret(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["access_token"] = self.access_token.get_secret_value()
        data["refresh_token"] = (
            self.refresh_token.get_secret_value() if self.refresh_token else None
        )
        return data


class TokenStore(Protocol):
    def load(self, platform: Platform) -> OAuthTokens | None: ...
    def save(self, platform: Platform, tokens: OAuthTokens) -> None: ...
    def delete(self, platform: Platform) -> None: ...
    def platforms(self) -> list[Platform]: ...


class FileTokenStore:
    """``{platform: tokens}`` JSON file created with mode 0600 (owner read/write only)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text() or "{}")
        except json.JSONDecodeError:
            log.warning("platform.token_file_corrupt", path=str(self.path))
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)

    def load(self, platform: Platform) -> OAuthTokens | None:
        raw = self._read().get(str(platform))
        return OAuthTokens.model_validate(raw) if raw else None

    def save(self, platform: Platform, tokens: OAuthTokens) -> None:
        data = self._read()
        data[str(platform)] = tokens.dump_secret()
        self._write(data)
        log.info("platform.tokens_saved", platform=str(platform), path=str(self.path))

    def delete(self, platform: Platform) -> None:
        data = self._read()
        if data.pop(str(platform), None) is not None:
            self._write(data)
            log.info("platform.tokens_deleted", platform=str(platform))

    def platforms(self) -> list[Platform]:
        out: list[Platform] = []
        for key in self._read():
            try:
                out.append(Platform(key))
            except ValueError:
                continue
        return out


class MemoryTokenStore:
    """In-process store for tests and dry runs."""

    def __init__(self) -> None:
        self._data: dict[Platform, OAuthTokens] = {}

    def load(self, platform: Platform) -> OAuthTokens | None:
        return self._data.get(platform)

    def save(self, platform: Platform, tokens: OAuthTokens) -> None:
        self._data[platform] = tokens

    def delete(self, platform: Platform) -> None:
        self._data.pop(platform, None)

    def platforms(self) -> list[Platform]:
        return list(self._data)


def env_tokens(settings: Settings, platform: Platform) -> OAuthTokens | None:
    """Tokens injected through ``CAREEROS_<PLATFORM>_ACCESS_TOKEN`` (container deployments)."""
    access: SecretStr | None = getattr(settings, f"{platform}_access_token", None)
    if access is None or not access.get_secret_value():
        return None
    refresh: SecretStr | None = getattr(settings, f"{platform}_refresh_token", None)
    return OAuthTokens(access_token=access, refresh_token=refresh or None, source="env")


def client_credentials(settings: Settings, platform: Platform) -> tuple[str, SecretStr] | None:
    client_id: str | None = getattr(settings, f"{platform}_client_id", None)
    secret: SecretStr | None = getattr(settings, f"{platform}_client_secret", None)
    if not client_id or secret is None or not secret.get_secret_value():
        return None
    return client_id, secret


def resolve_tokens(settings: Settings, store: TokenStore, platform: Platform) -> OAuthTokens | None:
    """Environment wins over the file store so a deployment can pin tokens explicitly."""
    return env_tokens(settings, platform) or store.load(platform)


def get_token_store(settings: Settings) -> FileTokenStore:
    return FileTokenStore(Path(settings.platform_token_file))

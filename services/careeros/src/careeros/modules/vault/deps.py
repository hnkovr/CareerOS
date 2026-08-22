"""Vault instance resolution shared by API, CLI and other modules' services."""

from __future__ import annotations

from pathlib import Path

from careeros.core.config import Settings, get_settings
from careeros.modules.vault.service import Vault

_instances: dict[str, Vault] = {}


def get_vault(settings: Settings | None = None, path: Path | None = None) -> Vault:
    settings = settings or get_settings()
    root = Path(path or settings.vault_path).resolve()
    key = str(root)
    if key not in _instances:
        _instances[key] = Vault(
            root,
            git_user_name=settings.vault_git_user_name,
            git_user_email=settings.vault_git_user_email,
            auto_push=settings.vault_auto_push,
        )
    return _instances[key]


def reset_vault_cache() -> None:
    _instances.clear()

"""Vault instance resolution shared by API, CLI and other modules' services."""

from __future__ import annotations

from pathlib import Path

from careeros.core.config import Settings, get_settings
from careeros.core.logging import get_logger
from careeros.modules.vault.layout import COLLECTIONS
from careeros.modules.vault.service import Vault

log = get_logger(__name__)

# a directory is a vault once it carries the meta file (`vault.yaml`)
META_FILE = COLLECTIONS["meta"].path

_instances: dict[str, Vault] = {}


def demo_vault_path(settings: Settings) -> Path:
    """The demo vault shipped with the repo (``career/examples/demo``)."""
    return settings.career_dir / "examples" / "demo"


def is_initialised(root: Path) -> bool:
    """True once ``careeros vault init`` (or a real private vault) has been laid down here."""
    return (Path(root) / META_FILE).is_file()


def resolve_vault_path(settings: Settings) -> tuple[Path, bool]:
    """``(root, read_only)`` for the default vault — the configured one, else the demo vault.

    A fresh checkout has ``CAREEROS_VAULT_PATH=career/private`` pointing at an empty scaffold,
    so reads fall back to the bundled demo vault (as README and the env templates promise).
    That fallback is read-only: demo facts must never be committed as if they were the owner's
    (invariant 1 — the vault is the only source of truth for a fact).
    """
    configured = Path(settings.vault_path)
    if is_initialised(configured):
        return configured, False
    demo = demo_vault_path(settings)
    if is_initialised(demo):
        return demo, True
    return configured, False  # no demo either: surface the configured vault's own errors


def get_vault(settings: Settings | None = None, path: Path | None = None) -> Vault:
    settings = settings or get_settings()
    if path is not None:
        root, read_only = Path(path), False
    else:
        root, read_only = resolve_vault_path(settings)
    root = root.resolve()
    key = f"{root}|{read_only:d}"
    if key not in _instances:
        if read_only:
            log.warning(
                "vault.demo_fallback",
                configured=str(settings.vault_path),
                using=str(root),
                hint="just vault-init <path> && set CAREEROS_VAULT_PATH",
            )
        _instances[key] = Vault(
            root,
            git_user_name=settings.vault_git_user_name,
            git_user_email=settings.vault_git_user_email,
            auto_push=settings.vault_auto_push,
            read_only=read_only,
        )
    return _instances[key]


def reset_vault_cache() -> None:
    _instances.clear()

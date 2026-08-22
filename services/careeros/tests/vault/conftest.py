from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from careeros.modules.vault.service import Vault
from tests.conftest import DEMO_VAULT


@pytest.fixture
def demo_vault() -> Vault:
    """Read-only view on the committed demo vault (never mutated by tests)."""
    return Vault(DEMO_VAULT)


@pytest.fixture
def scratch_vault(tmp_path: Path) -> Vault:
    """A git-initialised copy of the demo vault that tests may mutate."""
    root = tmp_path / "vault"
    shutil.copytree(DEMO_VAULT, root)
    vault = Vault(root, git_user_name="Test", git_user_email="test@example.com")
    vault.git.init()
    vault.git.commit_paths(["."], "initial")
    return vault

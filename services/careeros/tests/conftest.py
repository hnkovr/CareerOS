from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from careeros.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_VAULT = REPO_ROOT / "career" / "examples" / "demo"


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        env="test",
        task_runner="inline",
        vault_path=DEMO_VAULT,
        database_url=os.environ.get(
            "CAREEROS_TEST_DATABASE_URL",
            "postgresql+asyncpg://careeros:careeros@localhost:5432/careeros_test",
        ),
        api_token=None,
    )


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    from careeros.api.app import create_app

    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_VAULT = REPO_ROOT / "career" / "examples" / "demo"
CAREER_DIR = REPO_ROOT / "career"


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        env="test",
        task_runner="inline",
        vault_path=DEMO_VAULT,
        career_dir=CAREER_DIR,
        generated_dir=REPO_ROOT / "generated" / "test",
        database_url=os.environ.get(
            "CAREEROS_TEST_DATABASE_URL",
            "postgresql+asyncpg://careeros:careeros@localhost:5432/careeros_test",
        ),
        api_token=None,
        ai_default_provider="fake",
    )


@pytest.fixture(scope="session")
async def db(settings: Settings) -> AsyncIterator[bool]:
    """Create all tables on the test database; yields False (and db tests skip) if unreachable."""
    import contextlib
    import importlib

    from sqlalchemy.exc import OperationalError, SQLAlchemyError

    from careeros.core.db import Base, dispose_engine, get_engine

    for mod in (
        "careeros.core.models",
        "careeros.modules.ai.models",
        "careeros.modules.cv.models",
        "careeros.modules.opportunities.models",
        "careeros.modules.profiles.models",
        "careeros.modules.platform.models",
        "careeros.modules.pipeline.models",
        "careeros.modules.inbox.models",
        "careeros.modules.search.models",
    ):
        with contextlib.suppress(ModuleNotFoundError):
            importlib.import_module(mod)
    engine = get_engine(settings)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    except (OperationalError, SQLAlchemyError, OSError):
        await dispose_engine()
        yield False
        return
    from careeros.core.seed import seed_user

    await seed_user(settings.user_email)
    yield True
    await dispose_engine()


@pytest.fixture
async def session(db: bool, settings: Settings) -> AsyncIterator[AsyncSession]:
    if not db:
        pytest.skip("PostgreSQL not reachable (set CAREEROS_TEST_DATABASE_URL)")
    from careeros.core.db import get_sessionmaker

    async with get_sessionmaker(settings)() as s:
        yield s


@pytest.fixture
def user_id() -> uuid.UUID:
    from careeros.core.auth import SINGLE_USER_ID

    return SINGLE_USER_ID


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    from careeros.api.app import create_app

    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.fixture
async def db_client(db: bool, client: AsyncClient) -> AsyncClient:
    if not db:
        pytest.skip("PostgreSQL not reachable (set CAREEROS_TEST_DATABASE_URL)")
    return client

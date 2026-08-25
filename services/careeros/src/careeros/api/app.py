"""FastAPI application factory. Routers from each module are mounted here and nowhere else."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from careeros import __version__
from careeros.core.config import Settings, get_settings
from careeros.core.db import dispose_engine, get_sessionmaker
from careeros.core.logging import configure_logging, get_logger
from careeros.core.tasks import TaskRunner, build_runner

log = get_logger(__name__)


async def _start_bot(app: FastAPI, settings: Settings) -> None:
    """Wire the Telegram surface and claim its webhook, if this process may serve one.

    Failure here must never prevent the API from starting: the bot is one surface
    among several, and an unreachable Telegram at boot is a transient condition,
    not a reason to take the whole service down.
    """
    from careeros.modules.bot.client import TelegramClient
    from careeros.modules.bot.security import UpdateGate
    from careeros.modules.bot.service import BotService
    from careeros.modules.bot.webhook import claim_webhook

    app.state.bot_gate = UpdateGate(settings)
    app.state.bot_service = None
    app.state.bot_claim = None
    if not settings.tg_enabled or settings.tg_bot_token is None:
        return
    try:
        client = TelegramClient(settings)
        app.state.bot_service = BotService(settings, client, get_sessionmaker(settings))
        app.state.bot_claim = await claim_webhook(settings, client)
        log.info("bot.start", claim=str(app.state.bot_claim))
    except Exception:
        log.exception("bot.start_failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    app.state.task_runner = build_runner(settings.task_runner, settings.redis_url)
    await _start_bot(app, settings)
    log.info("api.start", version=__version__, env=settings.env, vault=str(settings.vault_path))
    try:
        yield
    finally:
        close = getattr(app.state.task_runner, "close", None)
        if close is not None:
            await close()
        await dispose_engine()
        log.info("api.stop")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title="CareerOS API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path)
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    _mount_routers(app)
    return app


def _mount_routers(app: FastAPI) -> None:
    from careeros.api.routers import ROUTERS

    for router in ROUTERS:
        app.include_router(router, prefix="/api")


def get_task_runner(request: Request) -> TaskRunner:
    return request.app.state.task_runner

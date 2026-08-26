"""``careeros-worker`` console entrypoint (ARQ). Task handlers register themselves on import."""

from __future__ import annotations

from typing import Any

from arq.connections import RedisSettings
from arq.cron import cron

from careeros.core.config import get_settings
from careeros.core.logging import configure_logging, get_logger
from careeros.core.tasks import run_task

log = get_logger(__name__)


def _import_task_modules() -> None:
    """Import every module that registers tasks so the registry is populated in the worker."""
    import importlib

    for mod in (
        "careeros.modules.cv.tasks",
        "careeros.modules.opportunities.tasks",
        "careeros.modules.profiles.tasks",
        "careeros.modules.workflows.tasks",
    ):
        try:
            importlib.import_module(mod)
        except ModuleNotFoundError:
            continue


async def startup(ctx: dict[str, Any]) -> None:
    _import_task_modules()
    log.info("worker.start")


async def daily_follow_up_sweep(ctx: dict[str, Any]) -> Any:
    """08:00 UTC: queue follow-up drafts for due applications (ADR-017); each waits for approval."""
    return await run_task(ctx, "workflows.sweep_follow_ups", {})


async def shutdown(ctx: dict[str, Any]) -> None:
    from careeros.core.db import dispose_engine

    await dispose_engine()
    log.info("worker.stop")


class WorkerSettings:
    functions = [run_task]
    cron_jobs = [cron(daily_follow_up_sweep, hour=8, minute=0)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 4
    job_timeout = 600


def run() -> None:
    from arq.worker import run_worker

    configure_logging(get_settings())
    run_worker(WorkerSettings)  # type: ignore[arg-type]


if __name__ == "__main__":
    run()

"""Task runner port (ADR-008).

Domain code calls ``runner.enqueue("name", payload)``; handlers are plain async functions registered
by name. ``InlineTaskRunner`` executes immediately (tests, CLI); ``ArqTaskRunner`` enqueues on Redis
and the worker executes via the same registry. Replacing the runner (e.g. with Temporal) does not
touch call sites.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from careeros.core.logging import get_logger

log = get_logger(__name__)

TaskHandler = Callable[..., Awaitable[Any]]


@dataclass
class TaskRegistry:
    handlers: dict[str, TaskHandler] = field(default_factory=dict)

    def task(self, name: str) -> Callable[[TaskHandler], TaskHandler]:
        def decorator(fn: TaskHandler) -> TaskHandler:
            if name in self.handlers:
                raise ValueError(f"task already registered: {name}")
            self.handlers[name] = fn
            return fn

        return decorator

    def get(self, name: str) -> TaskHandler:
        try:
            return self.handlers[name]
        except KeyError as exc:
            raise KeyError(f"unknown task: {name}") from exc


registry = TaskRegistry()


@dataclass(frozen=True)
class TaskRef:
    name: str
    id: str


class TaskRunner(Protocol):
    async def enqueue(
        self, name: str, payload: dict[str, Any], *, delay_s: float | None = None
    ) -> TaskRef: ...


class InlineTaskRunner:
    """Runs the handler immediately in-process. Deterministic; used by tests and the CLI."""

    def __init__(self, reg: TaskRegistry = registry) -> None:
        self._reg = reg
        self.executed: list[TaskRef] = []

    async def enqueue(
        self, name: str, payload: dict[str, Any], *, delay_s: float | None = None
    ) -> TaskRef:
        handler = self._reg.get(name)
        result = handler(**payload)
        if inspect.isawaitable(result):
            await result
        ref = TaskRef(name=name, id=f"inline:{len(self.executed) + 1}")
        self.executed.append(ref)
        return ref


class ArqTaskRunner:
    """Enqueues on Redis via ARQ; ``careeros.worker`` executes ``run_task`` with the registry."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._pool: Any = None

    async def _get_pool(self) -> Any:
        if self._pool is None:
            from arq import create_pool
            from arq.connections import RedisSettings

            self._pool = await create_pool(RedisSettings.from_dsn(self._redis_url))
        return self._pool

    async def enqueue(
        self, name: str, payload: dict[str, Any], *, delay_s: float | None = None
    ) -> TaskRef:
        registry.get(name)  # fail fast on unknown task names
        pool = await self._get_pool()
        job = await pool.enqueue_job("run_task", name, payload, _defer_by=delay_s)
        job_id = job.job_id if job is not None else "deduplicated"
        log.info("task.enqueued", task=name, job_id=job_id)
        return TaskRef(name=name, id=job_id)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None


async def run_task(ctx: dict[str, Any], name: str, payload: dict[str, Any]) -> Any:
    """ARQ-side dispatcher — the only function ARQ knows about."""
    handler = registry.get(name)
    log.info("task.start", task=name)
    try:
        return await handler(**payload)
    except Exception:
        log.exception("task.failed", task=name)
        raise
    finally:
        log.info("task.end", task=name)


def build_runner(kind: str, redis_url: str) -> TaskRunner:
    if kind == "inline":
        return InlineTaskRunner()
    if kind == "arq":
        return ArqTaskRunner(redis_url)
    raise ValueError(f"unknown task runner: {kind}")

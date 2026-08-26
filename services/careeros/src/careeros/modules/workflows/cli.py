"""``careeros workflows`` — start / inspect / decide workflows from the terminal (ADR-017)."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import typer

from careeros.core.auth import SINGLE_USER_ID
from careeros.core.config import get_settings
from careeros.core.db import get_sessionmaker
from careeros.modules.ai.deps import build_ai_service
from careeros.modules.vault.deps import get_vault
from careeros.modules.workflows.enums import WorkflowKind
from careeros.modules.workflows.schemas import DecisionRequest, StartRequest, WorkflowRunOut
from careeros.modules.workflows.service import WorkflowService

app = typer.Typer(help="Workflows with WAIT_FOR_APPROVAL gates (ADR-017).")


def _run(fn: Callable[[WorkflowService], Awaitable[Any]]) -> Any:
    settings = get_settings()

    async def go() -> Any:
        async with get_sessionmaker(settings)() as session:
            svc = WorkflowService(
                settings,
                get_vault(settings),
                build_ai_service(settings, session=session, user_id=SINGLE_USER_ID),
                session=session,
                user_id=SINGLE_USER_ID,
            )
            return await fn(svc)

    return asyncio.run(go())


def _print(run: WorkflowRunOut, *, as_json: bool = False) -> None:
    if as_json:
        typer.echo(json.dumps(run.model_dump(mode="json"), indent=2, ensure_ascii=False))
        return
    typer.echo(f"{run.id}  {run.kind}  {run.state}  target={run.target_ref}")
    for i, s in enumerate(run.steps):
        marker = "→" if i == run.current_step else " "
        line = f"  {marker} {s.name:20} {s.status:8} {s.summary or ''}"
        if s.error:
            line += f"  !! {s.error}"
        typer.echo(line)
    if run.suggestion_id:
        typer.echo(
            f"  waiting on suggestion {run.suggestion_id} — `careeros workflows approve {run.id}`"
        )
    if run.context.get("message"):
        typer.echo("\n--- message (send it yourself) ---\n" + str(run.context["message"]))


@app.command("definitions")
def definitions() -> None:
    for d in WorkflowService.definitions():
        typer.echo(f"{d.kind:10} {d.title} — target: {d.target_type}")
        for s in d.steps:
            typer.echo(f"    {'⏸' if s.kind == 'approval' else '·'} {s.name}: {s.description}")


@app.command("start")
def start(
    kind: WorkflowKind,
    target_id: uuid.UUID,
    no_ai: bool = typer.Option(False, "--no-ai", help="deterministic steps only"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    options: dict[str, Any] = {"use_ai": not no_ai}
    run = _run(lambda svc: svc.start(StartRequest(kind=kind, target_id=target_id, options=options)))
    _print(run, as_json=as_json)


@app.command("list")
def list_runs(limit: int = 20) -> None:
    for r in _run(lambda svc: svc.list(limit=limit)):
        typer.echo(
            f"{r.id}  {r.kind:10} {r.state:17} step {r.current_step}/{len(r.steps)}  {r.target_ref}"
        )


@app.command("show")
def show(run_id: uuid.UUID, as_json: bool = typer.Option(False, "--json")) -> None:
    _print(_run(lambda svc: svc.get(run_id)), as_json=as_json)


@app.command("approve")
def approve(run_id: uuid.UUID, note: str | None = None) -> None:
    _print(_run(lambda svc: svc.decide(run_id, DecisionRequest(decision="approve", note=note))))


@app.command("reject")
def reject(run_id: uuid.UUID, note: str | None = None) -> None:
    _print(_run(lambda svc: svc.decide(run_id, DecisionRequest(decision="reject", note=note))))

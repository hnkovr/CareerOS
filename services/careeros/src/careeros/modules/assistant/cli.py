"""``careeros assistant`` — ask the tool-using assistant from the terminal."""

from __future__ import annotations

import asyncio
import json
import uuid

import typer

from careeros.core.auth import SINGLE_USER_ID
from careeros.core.config import get_settings
from careeros.core.db import get_sessionmaker
from careeros.modules.ai.deps import build_ai_service
from careeros.modules.assistant.schemas import AskRequest
from careeros.modules.assistant.service import AssistantService
from careeros.modules.assistant.tools import default_registry
from careeros.modules.vault.deps import get_vault

app = typer.Typer(help="Tool-using career assistant (ADR-014).")


@app.command("tools")
def tools() -> None:
    """List the read-only tools the assistant may call."""
    for t in default_registry().infos():
        typer.echo(f"{t.name:22} {t.description}")


@app.command("ask")
def ask(
    question: str,
    opportunity: uuid.UUID | None = typer.Option(None, help="opportunity id as context"),
    provider: str | None = typer.Option(None, help="AI provider name"),
    max_steps: int = typer.Option(8, min=1, max=12),
    as_json: bool = typer.Option(False, "--json", help="print the full response as JSON"),
) -> None:
    """Ask one question; the answer cites fact ids and is withheld if it cannot be grounded."""
    settings = get_settings()

    async def run() -> None:
        async with get_sessionmaker(settings)() as session:
            svc = AssistantService(
                settings,
                get_vault(settings),
                build_ai_service(settings, session=session, user_id=SINGLE_USER_ID),
                session=session,
                user_id=SINGLE_USER_ID,
            )
            res = await svc.ask(
                AskRequest(
                    question=question,
                    opportunity_id=opportunity,
                    provider=provider,
                    max_steps=max_steps,
                )
            )
            if as_json:
                typer.echo(json.dumps(res.model_dump(mode="json"), indent=2, ensure_ascii=False))
                return
            typer.echo(res.answer)
            if res.suggested_next_action:
                typer.echo(f"\nNext: {res.suggested_next_action}")
            if res.derived_from:
                typer.echo(f"Sources: {', '.join(res.derived_from)}")
            for step in res.tools_used:
                typer.echo(f"  tool {step.step}: {step.tool}({json.dumps(step.arguments)})")
            typer.echo(f"({res.provider}/{res.model}, {res.turns} turns, run {res.ai_run_id})")

    asyncio.run(run())

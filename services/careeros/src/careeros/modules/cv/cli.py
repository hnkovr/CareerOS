"""``careeros cv ...`` commands (work without a database; artifacts land in generated/cv)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from careeros.core.config import get_settings
from careeros.modules.cv.compare import compare_documents
from careeros.modules.cv.deps import build_cv_service
from careeros.modules.cv.rendercv_adapter import document_from_json
from careeros.modules.cv.schemas import GenerateCVRequest
from careeros.modules.cv.service import CVError

app = typer.Typer(help="CV-as-Code: generate and compare variants")


@app.command()
def variants() -> None:
    """List CV variants defined in the vault."""
    for v in build_cv_service().variants():
        print(f"{v.id:26s} {v.name:34s} {v.positioning_id:32s} {v.channel_id:10s} {v.theme}")


@app.command()
def generate(
    variant: str = typer.Argument("general-core"),
    jd: Path | None = typer.Option(None, "--jd", help="file with a job description to tailor to"),
    no_ai: bool = typer.Option(
        False, "--no-ai", help="deterministic: verbatim facts, no provider call"
    ),
    provider: str | None = typer.Option(None, "--provider"),
    fmt: list[str] = typer.Option(["pdf", "md", "json"], "--format"),
) -> None:
    """Generate a CV variant → generated/cv/<variant>/<artifact-id>/cv.{pdf,md,json}."""
    settings = get_settings()
    svc = build_cv_service(settings)
    req = GenerateCVRequest(
        variant_id=variant,
        jd_text=jd.read_text(encoding="utf-8") if jd else None,
        use_ai=not no_ai,
        provider=provider,
        formats=fmt,  # type: ignore[arg-type]
    )
    try:
        out = asyncio.run(svc.generate(req))
    except CVError as exc:
        raise typer.BadParameter(str(exc)) from exc
    print(f"artifact {out.id} [{out.status}] ai={out.ai_used} bullets={out.bullet_count}")
    for k, v in out.files.model_dump(by_alias=True).items():
        if v:
            print(f"  {k:5s} {v}")
    for w in out.warnings:
        print(f"  ! {w}")


@app.command()
def compare(a: Path, b: Path) -> None:
    """Compare two generated cv.json documents."""
    result = compare_documents(
        document_from_json(a), document_from_json(b), label_a=str(a), label_b=str(b)
    )
    print(json.dumps(result.model_dump(), indent=2))

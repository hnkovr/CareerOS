"""``careeros opportunities ...`` — parse & score without a database (triage from the terminal)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from careeros.modules.cv.keywords import tech_vocabulary
from careeros.modules.opportunities.enums import Source
from careeros.modules.opportunities.parser import parse_text
from careeros.modules.opportunities.scoring import ScoringContext, score_opportunity
from careeros.modules.vault.deps import get_vault

app = typer.Typer(help="Opportunity triage")


@app.command()
def score(
    file: Path | None = typer.Argument(None, help="JD text file (default: stdin)"),
    source: Source = typer.Option(Source.manual, "--source"),
    url: str | None = typer.Option(None, "--url"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Parse and score a job description deterministically (no DB, no AI)."""
    text = file.read_text(encoding="utf-8") if file else sys.stdin.read()
    vault = get_vault()
    data = vault.require()
    parsed = parse_text(text, tech_vocabulary(data), url=url)
    result = score_opportunity(
        ScoringContext.build(data),
        parsed.extraction,
        source=source,
        text=text,
        vault_sha=vault.head_sha(),
    )
    if as_json:
        print(
            json.dumps(
                {
                    "extraction": parsed.extraction.model_dump(mode="json"),
                    "score": result.model_dump(mode="json"),
                },
                indent=2,
            )
        )
        return
    ex = parsed.extraction
    print(
        f"{ex.title or '?'} @ {ex.company or '?'}  "
        f"[{ex.remote_policy}; {ex.contract_type or '?'}; {ex.seniority or '?'}]"
    )
    print(f"technologies: {', '.join(ex.technologies) or '-'}")
    comp = ex.compensation.raw if ex.compensation else "-"
    print(f"compensation: {comp}   confidence={parsed.confidence}")
    print(f"\n{result.overall}/100 — {result.recommendation.upper()}")
    for r in result.reasons:
        print(f"  · {r}")
    print()
    for d in result.dimensions[1:]:
        print(f"  {d.name:22s} {d.score:3d}  w={d.weight:.2f}  {d.explanation}")

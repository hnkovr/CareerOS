"""``careeros profiles ...`` — audit a snapshot file without the API (no AI, no DB)."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from careeros.modules.profiles.audit import audit_snapshot, category_scores, health_score
from careeros.modules.profiles.schemas import SnapshotIn
from careeros.modules.vault.deps import get_vault
from careeros.modules.vault.yamlio import load_yaml

app = typer.Typer(help="Profile snapshots & audits")


@app.command("audit-file")
def audit_file(
    file: Path = typer.Argument(
        ..., help="YAML/JSON snapshot: {platform, headline, about, skills, ...}"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Audit a snapshot file against the vault (deterministic checks only)."""
    raw = load_yaml(file) if file.suffix in (".yaml", ".yml") else json.loads(file.read_text())
    snap = SnapshotIn.model_validate(raw)
    data = get_vault().require()
    findings = audit_snapshot(data, snap)
    scores = category_scores(findings)
    health = health_score(scores, findings)
    if as_json:
        print(
            json.dumps(
                {
                    "health_score": health,
                    "category_scores": scores,
                    "findings": [f.model_dump(mode="json") for f in findings],
                },
                indent=2,
            )
        )
        return
    print(f"Profile Health Score ({snap.platform}): {health}/100\n")
    for f in findings:
        print(f"[{f.severity.upper():8s}] {f.category}: {f.problem}")
        print(f"           why: {f.why_it_matters}")
        if f.suggested_change:
            print(f"           fix: {f.suggested_change}")
        if f.source_fact_ids:
            print(f"           facts: {', '.join(f.source_fact_ids)}")

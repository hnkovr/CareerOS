"""``careeros vault ...`` commands."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from careeros.core.config import get_settings
from careeros.modules.vault.deps import get_vault
from careeros.modules.vault.export import export_schemas
from careeros.modules.vault.service import VaultError

app = typer.Typer(help="Canonical career vault (Git + YAML)")


def _path_opt(path: str | None) -> Path | None:
    return Path(path) if path else None


@app.command()
def validate(
    path: str | None = typer.Option(None, "--path", help="vault dir (default: settings)"),
) -> None:
    """Validate schemas and referential integrity. Exit 1 on errors."""
    vault = get_vault(path=_path_opt(path))
    result = vault.load()
    for issue in result.issues:
        print(issue)
    errors = len(result.errors)
    warnings = len(result.issues) - errors
    print(
        f"{vault.root}: {'OK' if result.ok else 'INVALID'} — {errors} errors, {warnings} warnings"
    )
    if not result.ok:
        raise typer.Exit(1)


@app.command()
def status(path: str | None = typer.Option(None, "--path")) -> None:
    """Show vault status (repo, HEAD, counts)."""
    print(json.dumps(get_vault(path=_path_opt(path)).status().model_dump(), indent=2))


@app.command()
def show(
    collection: str,
    item_id: str | None = typer.Argument(None),
    path: str | None = typer.Option(None, "--path"),
) -> None:
    """Print a collection or a single item as JSON."""
    data = get_vault(path=_path_opt(path)).require()
    value = getattr(data, collection, None)
    if value is None:
        raise typer.BadParameter(f"unknown collection {collection}")
    if item_id and isinstance(value, list):
        value = next((v for v in value if v.id == item_id), None)
        if value is None:
            raise typer.BadParameter(f"{collection}/{item_id} not found")
    payload = (
        [v.model_dump(mode="json") for v in value]
        if isinstance(value, list)
        else value.model_dump(mode="json")
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))


@app.command()
def init(
    path: str = typer.Option("career/private", "--path"),
    owner: str | None = typer.Option(None, "--owner", help="your name (used in vault.yaml)"),
) -> None:
    """Create a new private vault from career/templates and git-init it."""
    settings = get_settings()
    template = settings.career_dir / "templates"
    vault = get_vault(path=Path(path))
    try:
        vault.init_from_template(template, owner=owner or settings.user_email)
    except VaultError as exc:
        raise typer.BadParameter(str(exc)) from exc
    print(f"initialised vault at {vault.root} (HEAD {vault.head_sha()})")
    print(f"set CAREEROS_VAULT_PATH={vault.root} to use it")


@app.command("export-schemas")
def export_schemas_cmd(out: str | None = typer.Option(None, "--out")) -> None:
    """Write JSON Schemas for all vault files (default: career/schemas)."""
    target = Path(out) if out else get_settings().career_dir / "schemas"
    for p in export_schemas(target):
        print(p)

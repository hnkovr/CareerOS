"""``careeros`` CLI. Sub-commands are added by module slices (vault, cv, opportunities, ...)."""

from __future__ import annotations

import json

import typer

from careeros import __version__
from careeros.core.config import get_settings

app = typer.Typer(
    name="careeros", help="CareerOS — personal agentic career data platform", no_args_is_help=True
)


@app.command()
def version() -> None:
    """Print the version."""
    print(__version__)


@app.command()
def settings() -> None:
    """Print effective settings (secrets redacted)."""
    print(json.dumps(get_settings().redacted_dump(), indent=2, default=str))


@app.command("export-openapi")
def export_openapi(out: str = "services/careeros/openapi.json") -> None:
    """Write the OpenAPI document (used to generate the TS client)."""
    from pathlib import Path

    from careeros.api.app import create_app

    spec = create_app().openapi()
    Path(out).write_text(json.dumps(spec, indent=2))
    print(f"wrote {out}")


def _register_subcommands() -> None:
    import importlib

    for mod, name in (
        ("careeros.modules.vault.cli", "vault"),
        ("careeros.modules.cv.cli", "cv"),
        ("careeros.modules.opportunities.cli", "opportunities"),
        ("careeros.modules.ai.cli", "ai"),
        ("careeros.modules.assistant.cli", "assistant"),
        ("careeros.modules.profiles.cli", "profiles"),
        ("careeros.modules.platform.cli", "platform"),
        ("careeros.modules.bot.cli", "bot"),
        ("careeros.core.seed", "seed"),
    ):
        try:
            module = importlib.import_module(mod)
        except ModuleNotFoundError:
            continue
        sub = getattr(module, "app", None)
        if sub is not None:
            app.add_typer(sub, name=name)


_register_subcommands()

if __name__ == "__main__":
    app()

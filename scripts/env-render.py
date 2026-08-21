#!/usr/bin/env python3
"""Render flat .env files from config/*.template files.

Templates use ``KEY=${VAR:-default}``; the value is taken from the current environment (``VAR``),
falling back to the default. Lines without the expression are copied verbatim.

Usage:
    scripts/env-render.py          # .env.config + .env.secrets(.demo) -> ./.env ;
                                   # .env.docker.template -> config/.env.docker
    scripts/env-render.py --check  # fail if a *.template carries a secret-looking literal
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
EXPR = re.compile(r"\$\{(?P<var>[A-Z0-9_]+)(?::-(?P<default>[^}]*))?\}")


def render_line(line: str) -> str:
    def sub(m: re.Match[str]) -> str:
        return os.environ.get(m.group("var"), m.group("default") or "")

    return EXPR.sub(sub, line)


def render(template: Path) -> str:
    return "".join(render_line(line) for line in template.read_text().splitlines(keepends=True))


def check_templates() -> int:
    bad = 0
    for tpl in CONFIG.glob(".env*.template"):
        for n, line in enumerate(tpl.read_text().splitlines(), 1):
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if (
                ("KEY" in key or "TOKEN" in key or "PASSWORD" in key or "SECRET" in key)
                and value
                and not EXPR.fullmatch(value)
            ):
                print(f"{tpl.name}:{n}: secret-looking literal for {key}", file=sys.stderr)
                bad += 1
    return bad


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        return 1 if check_templates() else 0

    secrets_tpl = CONFIG / ".env.secrets.template"
    if not secrets_tpl.exists():
        secrets_tpl = CONFIG / ".env.secrets.demo.template"
    out_env = ROOT / ".env"
    out_env.write_text(render(CONFIG / ".env.config.template") + "\n" + render(secrets_tpl))
    out_docker = CONFIG / ".env.docker"
    out_docker.write_text(render(CONFIG / ".env.docker.template"))
    print(f"rendered {out_env.relative_to(ROOT)} and {out_docker.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

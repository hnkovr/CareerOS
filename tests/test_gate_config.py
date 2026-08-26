"""`config/gate.yml` describes the `make all` pipeline; the Makefile executes it.

Two descriptions of the same order drift the moment someone adds a step to one of them, and
the drift is invisible — the pipeline keeps working while the doc that agents read goes stale.
So the order is asserted equal, not merely similar.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_YML = REPO_ROOT / "config" / "gate.yml"
MAKEFILE = REPO_ROOT / "Makefile"
JUSTFILE = REPO_ROOT / "Justfile"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def makefile_all_steps() -> list[str]:
    match = re.search(r"^all: (.+?)(?: ##|$)", MAKEFILE.read_text(encoding="utf-8"), re.M)
    assert match, "Makefile has no `all:` target"
    return match.group(1).split()


@pytest.fixture(scope="module")
def gate() -> dict:
    return yaml.safe_load(GATE_YML.read_text(encoding="utf-8"))["gate"]


def test_gate_order_matches_the_makefile(gate: dict) -> None:
    assert [s["step"] for s in gate["order"]] == makefile_all_steps()


def test_every_step_documents_what_it_proves_and_how_it_fails(gate: dict) -> None:
    for step in gate["order"]:
        assert step.get("proves"), f"{step['step']}: no `proves`"
        assert step.get("fails_when"), f"{step['step']}: no `fails_when`"


def test_named_make_targets_and_guards_exist(gate: dict) -> None:
    makefile = MAKEFILE.read_text(encoding="utf-8")
    for target in gate["make"].values():
        assert re.search(rf"^{re.escape(target)}:", makefile, re.M), f"no `{target}:` target"
    for path in (*gate["guards"].values(), gate["contracts"]["script"]):
        script = REPO_ROOT / path
        assert script.is_file(), f"missing {path}"


def test_only_unreachable_is_tolerated_by_the_pipeline(gate: dict) -> None:
    """Tolerating a *rejected* token would make bot-check decorative."""
    codes = gate["bot_exit_codes"]
    assert codes["tolerated_by_pipeline"] == [4]
    assert "unreachable" in codes[4]
    # and the Makefile really compares against that code, not some other one
    assert re.search(r"rc -eq 4", MAKEFILE.read_text(encoding="utf-8"))


def test_ci_and_the_justfile_run_the_same_gate_script() -> None:
    """One definition of the gate, or a check ends up running in one place and not the other.

    This is the shape of bug that already shipped twice: a CI step that looked like it covered
    the generated contracts but never ran their generator, and a lint block kept in step with
    the Justfile by hand (GH #23).
    """
    ci, justfile = CI.read_text(encoding="utf-8"), JUSTFILE.read_text(encoding="utf-8")
    for mode in ("lint", "test"):
        assert f"scripts/gate.sh {mode}" in ci, f"CI does not call the shared gate for {mode}"
        assert f"scripts/gate.sh {mode}" in justfile, f"Justfile does not delegate {mode}"
    assert (REPO_ROOT / "scripts" / "gate.sh").is_file()
    # CI must not go back to spelling the checks out itself
    assert "uv run ruff check ." not in ci
    assert "run: uv run pytest" not in ci


def test_the_gate_script_covers_every_check_the_pipeline_claims() -> None:
    gate = (REPO_ROOT / "scripts" / "gate.sh").read_text(encoding="utf-8")
    for tool in (
        "ruff check",
        "ruff format --check",
        "pyright",
        "lint-imports",
        "env-render.py --check",
        "pytest",
    ):
        assert tool in gate, f"gate.sh no longer runs {tool}"

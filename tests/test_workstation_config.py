"""`config/workstation.yml` describes the migration; the runbook is what a human follows.

The failure mode of a migration doc is not that it is wrong on the day it is written — it is
that a secret slot is added six weeks later and the doc never hears about it. The new machine
then bootstraps, passes every check, and is quietly missing one credential.

So the two descriptions are asserted equal rather than merely similar: every bootstrap command
in the data appears in the runbook, and the secret slots are the *same set* as the template
that renders `.env`. Sibling of `tests/test_gate_config.py`, which does this for `make all`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSTATION_YML = REPO_ROOT / "config" / "workstation.yml"
SECRETS_TEMPLATE = REPO_ROOT / "config" / ".env.secrets.demo.template"
MAKEFILE = REPO_ROOT / "Makefile"
JUSTFILE = REPO_ROOT / "Justfile"
CLAUDE_SETTINGS = REPO_ROOT / ".claude" / "settings.json"


@pytest.fixture(scope="module")
def workstation() -> dict:
    return yaml.safe_load(WORKSTATION_YML.read_text(encoding="utf-8"))["workstation"]


@pytest.fixture(scope="module")
def runbook(workstation: dict) -> str:
    return (REPO_ROOT / workstation["runbook"]).read_text(encoding="utf-8")


def _bare(command: str) -> str:
    """The command without its trailing comment, whitespace-normalised."""
    return " ".join(command.split("#")[0].split())


def test_every_bootstrap_command_appears_in_the_runbook(workstation: dict, runbook: str) -> None:
    normalised = " ".join(runbook.split())
    for step in workstation["bootstrap"]:
        run = _bare(step["run"])
        assert run in normalised, f"{step['step']}: `{run}` is not in the runbook"


def test_every_bootstrap_step_says_what_it_proves(workstation: dict) -> None:
    for step in workstation["bootstrap"]:
        assert step.get("proves"), f"{step['step']}: no `proves` — then why not skip it?"


def test_secret_slots_are_exactly_the_template_slots(workstation: dict) -> None:
    """A slot in one and not the other is a credential that silently misses the migration."""
    documented = {slot["name"] for slot in workstation["secrets"]["slots"]}
    template = SECRETS_TEMPLATE.read_text(encoding="utf-8")
    templated = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", template, re.M))
    assert documented == templated, (
        f"only in workstation.yml: {sorted(documented - templated)}; "
        f"only in the template: {sorted(templated - documented)}"
    )


def test_every_secret_slot_says_what_needs_it(workstation: dict) -> None:
    for slot in workstation["secrets"]["slots"]:
        assert slot.get("needed_for"), f"{slot['name']}: no `needed_for`"


def test_named_targets_and_scripts_exist(workstation: dict) -> None:
    makefile, justfile = MAKEFILE.read_text(encoding="utf-8"), JUSTFILE.read_text(encoding="utf-8")
    for target in workstation["make"].values():
        assert re.search(rf"^{re.escape(target)}:", makefile, re.M), f"no `{target}:` make target"
    for recipe in workstation["just"].values():
        found = re.search(rf"^{re.escape(recipe)}( |:)", justfile, re.M)
        assert found, f"no `{recipe}` just recipe"
    for key in ("preflight", "guard", "runbook"):
        assert (REPO_ROOT / workstation[key]).is_file(), f"missing {workstation[key]}"


def test_the_session_guard_is_registered_and_executable(workstation: dict) -> None:
    """An unregistered guard is worse than none — it looks like coverage and never runs."""
    guard = REPO_ROOT / workstation["guard"]
    assert guard.stat().st_mode & 0o111, f"{workstation['guard']} is not executable"
    assert workstation["guard"] in CLAUDE_SETTINGS.read_text(encoding="utf-8"), (
        "the guard is not wired into .claude/settings.json SessionStart"
    )


def test_nothing_is_listed_as_lost(workstation: dict) -> None:
    """Every row that does not migrate must name how it comes back, or it is a blocker."""
    for row in workstation["does_not_migrate"]:
        assert row.get("recreate"), f"{row['item']}: no `recreate` — data loss, not a footnote"


def test_every_hazard_is_explained_and_provable(workstation: dict, runbook: str) -> None:
    for hazard in workstation["hazards"]:
        assert hazard.get("why"), f"{hazard['id']}: no `why`"
        assert hazard.get("avoid"), f"{hazard['id']}: no `avoid`"
        proof = hazard["proof"]
        assert proof in runbook, f"{hazard['id']}: the runbook never names `{proof}`"


def test_the_preflight_refuses_to_bless_a_machine_it_did_not_check(workstation: dict) -> None:
    """Exit 0 must mean *checked and clean*, so the checks themselves are asserted present."""
    preflight = (REPO_ROOT / workstation["preflight"]).read_text(encoding="utf-8")
    for check in ("unpushed", "stash", "vault", "escrow", "state_dir"):
        assert check in preflight, f"preflight no longer checks {check}"


def test_the_handoff_state_dir_is_not_gitignored(workstation: dict) -> None:
    """It was, silently: `.AI/*` also matches `.ai/*` on a case-insensitive filesystem.

    An ignored state dir does not error — it just never reaches the other machine, which is
    precisely the failure this whole lane exists to prevent.
    """
    if shutil.which("git") is None:  # pragma: no cover - git is present in CI and locally
        pytest.skip("git unavailable")
    state = REPO_ROOT / workstation["state_dir"]
    probe = state / "probe.yml"
    ignored = subprocess.run(["git", "check-ignore", "-q", str(probe)], cwd=REPO_ROOT, check=False)
    assert ignored.returncode != 0, f"{workstation['state_dir']} is gitignored — it cannot migrate"

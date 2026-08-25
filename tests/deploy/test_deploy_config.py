"""Invariant tests for the Fly deploy configuration and its settings contract.

These encode decisions that are easy to undo by accident and expensive to debug in
production: the single-claimant rule, the no-auto-stop rule for ACK-then-background
handlers, and the env-push allow-list that keeps unrelated machine secrets at home.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
FLY_TOML = REPO_ROOT / "fly.toml"
DEPLOY_YML = REPO_ROOT / "config" / "deploy.yml"
SECRETS_TMPL = REPO_ROOT / "config" / ".env.secrets.demo.template"
TG_BOT_SH = REPO_ROOT / "scripts" / "prj-tools" / "tg-bot.sh"
BOT_GUARD_SH = REPO_ROOT / "scripts" / "hooks" / "bot-guard.sh"
SETTINGS = Path.home() / ".ai" / "skills" / "_settings" / "careeros.yml"
# The settings SSoT lives on the owner's workstation, not in the repo: on CI these checks are
# skipped rather than failed (the repo-only invariants below still run everywhere).
needs_ssot = pytest.mark.skipif(
    not SETTINGS.exists(), reason=f"workstation settings SSoT not present: {SETTINGS}"
)


@pytest.fixture(scope="module")
def fly() -> dict:
    return tomllib.loads(FLY_TOML.read_text())


@pytest.fixture(scope="module")
def overlay() -> dict:
    return yaml.safe_load(DEPLOY_YML.read_text())


# ── fly.toml ──────────────────────────────────────────────────────────────────


def test_auto_stop_is_off_because_handlers_work_after_the_ack(fly):
    """The webhook returns 200 then keeps working; an auto-stop kills it mid-flight."""
    assert fly["http_service"]["auto_stop_machines"] == "off"


def test_machines_start_on_demand_and_scale_to_zero(fly):
    svc = fly["http_service"]
    assert svc["auto_start_machines"] is True
    assert svc["min_machines_running"] == 0


def test_migrations_run_as_a_release_command(fly):
    """In the entrypoint they would race across restarts."""
    cmd = fly["deploy"]["release_command"]
    assert "alembic" in cmd and "upgrade head" in cmd


def test_release_command_points_at_a_real_alembic_config(fly):
    m = re.search(r"-c\s+(\S+)", fly["deploy"]["release_command"])
    assert m, "release_command must pass -c <alembic.ini>"
    assert (REPO_ROOT / m.group(1)).is_file()


def test_dockerfile_referenced_by_build_exists(fly):
    assert (REPO_ROOT / fly["build"]["dockerfile"]).is_file()


def test_internal_port_matches_the_configured_api_port(fly):
    assert str(fly["http_service"]["internal_port"]) == fly["env"]["CAREEROS_API_PORT"]


def test_health_check_targets_a_route_the_app_actually_serves(fly):
    path = fly["http_service"]["checks"][0]["path"]
    app_py = (REPO_ROOT / "services/careeros/src/careeros/api/app.py").read_text()
    assert f'"{path}"' in app_py


def test_no_secret_values_are_baked_into_fly_toml(fly):
    """fly.toml is committed; flyctl secrets are not."""
    suspicious = re.compile(r"(token|secret|password|api_key)", re.I)
    for key, value in fly.get("env", {}).items():
        if suspicious.search(key):
            assert not value or value.startswith("/"), f"{key} looks like a secret in fly.toml"


def test_task_runner_is_inline_so_no_redis_addon_is_required(fly):
    assert fly["env"]["CAREEROS_TASK_RUNNER"] == "inline"


@needs_ssot
def test_public_url_matches_the_settings_ssot(fly):
    settings = yaml.safe_load(SETTINGS.read_text())
    assert (
        fly["env"]["CAREEROS_TG_PUBLIC_URL"]
        == settings["careeros"]["tg_bot"]["deploy"]["fly"]["url"]
    )


# ── config/deploy.yml ─────────────────────────────────────────────────────────


def test_env_push_is_an_allow_list(overlay):
    """Without `include`, the driver ships every var it finds on this machine."""
    include = overlay["env_push"]["include"]
    assert include, "env_push.include must be a non-empty allow-list"
    assert all(p.startswith("CAREEROS") for p in include)


def test_database_url_is_never_pushed(overlay):
    """The platform injects it via `fly mpg attach`; a local DSN would overwrite it."""
    assert "CAREEROS_DATABASE_URL" in overlay["env_push"]["exclude"]


def test_local_only_vars_are_excluded(overlay):
    exclude = overlay["env_push"]["exclude"]
    assert "CAREEROS_REDIS_URL" in exclude
    assert "CAREEROS_TEST_DATABASE_URL" in exclude


def test_overlay_app_name_matches_fly_toml(overlay, fly):
    assert overlay["name"] == fly["app"]
    assert overlay["region"] == fly["primary_region"]


# ── secrets template ──────────────────────────────────────────────────────────


def test_secrets_template_contains_no_literal_values():
    """Blank and ${VAR:-} forms only — a non-empty literal here is a leak."""
    offenders = []
    for i, line in enumerate(SECRETS_TMPL.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        _, value = line.split("=", 1)
        if value and not value.startswith("${"):
            offenders.append(f"{i}: {line}")
    assert not offenders, f"literal values in secrets template: {offenders}"


def test_every_bot_secret_is_declared_in_the_template():
    text = SECRETS_TMPL.read_text()
    for key in ("CAREEROS_TG_BOT_TOKEN", "CAREEROS_TG_WEBHOOK_SECRET", "CAREEROS_TG_OWNER_CHAT_ID"):
        assert key in text


# ── script ↔ settings contract ────────────────────────────────────────────────


@needs_ssot
@pytest.mark.parametrize("script", [TG_BOT_SH, BOT_GUARD_SH], ids=lambda p: p.name)
def test_every_settings_key_a_script_reads_exists(script):
    """Settings drift must fail here, not at deploy time with a cryptic bash error.

    Matches any `.careeros.<path>` lookup regardless of how the script spells it —
    one yq call or many — so refactoring the read does not silently void this check.
    """
    root = yaml.safe_load(SETTINGS.read_text())["careeros"]
    paths = sorted(set(re.findall(r"\.careeros\.([a-zA-Z0-9_.]+)", script.read_text())))
    assert paths, f"no settings lookups found in {script.name} — did it change shape?"
    for dotted in paths:
        node = root
        for part in dotted.split("."):
            assert isinstance(node, dict) and part in node, (
                f"missing settings key: careeros.{dotted}"
            )
            node = node[part]
        assert node not in (None, ""), f"empty settings value: careeros.{dotted}"


@needs_ssot
def test_settings_handle_and_token_var_are_consistent():
    deploy = yaml.safe_load(SETTINGS.read_text())["careeros"]["tg_bot"]
    assert deploy["handle"].startswith("@")
    assert deploy["deploy"]["targets"]["fly"]["bot"] == deploy["handle"]
    assert deploy["deploy"]["targets"]["fly"]["token_var"] == deploy["deploy"]["token_secret"]


def test_platform_credentials_are_not_pushed_to_the_host(overlay):
    """Platform sync is local-only, so the deployed app cannot use these.

    The `CAREEROS_*` allow-list admits any new CAREEROS_-prefixed variable
    automatically — including OAuth client secrets and refresh tokens. Shipping
    credentials to a host that never uses them is exposure for nothing.
    Re-admit only when platform sync actually runs there (GH #21).
    """
    exclude = overlay["env_push"]["exclude"]
    for pattern in ("CAREEROS_HH_*", "CAREEROS_UPWORK_*", "CAREEROS_PLATFORM_*"):
        assert pattern in exclude, f"{pattern} must not reach the platform"


def test_exclude_patterns_actually_cover_the_declared_credential_settings(overlay):
    """Guards against a settings rename silently escaping the exclusion."""
    import fnmatch

    config_py = (REPO_ROOT / "services/careeros/src/careeros/core/config.py").read_text()
    creds = re.findall(r"^\s+((?:hh|upwork)_\w*(?:secret|token|client_id))\s*:", config_py, re.M)
    assert creds, "no platform credential settings found — did config.py change shape?"
    exclude = overlay["env_push"]["exclude"]
    for field in creds:
        var = f"CAREEROS_{field.upper()}"
        assert any(fnmatch.fnmatch(var, pat) for pat in exclude), f"{var} would be pushed"

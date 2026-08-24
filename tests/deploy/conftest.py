"""Fixtures that let us exercise the real bash ops scripts, not a reimplementation.

The scripts resolve BOTH their settings file and the secret resolver under $HOME, so
pointing HOME at a temp tree is enough to isolate them completely — no edits, no
injection seams, no mocking framework. `curl` is stubbed on PATH and answers from
canned fixtures keyed by Bot API method.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TG_BOT = REPO_ROOT / "scripts" / "prj-tools" / "tg-bot.sh"
BOT_GUARD = REPO_ROOT / "scripts" / "hooks" / "bot-guard.sh"

FAKE_TOKEN = "123456:FAKE-TOKEN-VALUE-must-never-be-printed"
HANDLE = "careeros_hnkovr_bot"
OUR_URL = "https://careeros.fly.dev/tg/webhook"


@pytest.fixture
def env(tmp_path: Path):
    """A self-contained fake HOME + stubbed curl. Returns a small control surface."""
    home = tmp_path / "home"
    settings_dir = home / ".ai" / "skills" / "_settings"
    secrets_dir = home / ".ai" / "skills" / "_scripts" / "secrets"
    mock = tmp_path / "mock"
    binroot = tmp_path / "bin"
    for d in (settings_dir, secrets_dir, mock, binroot):
        d.mkdir(parents=True)

    (settings_dir / "careeros.yml").write_text(
        "careeros:\n"
        f"  tg_bot:\n"
        f'    handle: "@{HANDLE}"\n'
        "    deploy:\n"
        "      token_secret: CAREEROS_TG_BOT_TOKEN\n"
        "      webhook_secret: CAREEROS_TG_WEBHOOK_SECRET\n"
        "      webhook_path: /tg/webhook\n"
        "      fly:\n"
        "        url: https://careeros.fly.dev\n"
        "  api:\n"
        "    telegram_bot_api: https://api.telegram.org\n"
    )

    # Fake resolver: prints a value per var name, or nothing (absent).
    (secrets_dir / "find-secret.sh").write_text(
        "#!/usr/bin/env bash\n"
        'case "$1" in\n'
        f'  CAREEROS_TG_BOT_TOKEN) [ -f "$MOCK/no_token" ] || printf %s "{FAKE_TOKEN}" ;;\n'
        '  CAREEROS_TG_WEBHOOK_SECRET) [ -f "$MOCK/no_secret" ] || printf %s "s3cret" ;;\n'
        "esac\n"
    )
    (secrets_dir / "find-secret.sh").chmod(0o755)

    # Stubbed curl: answers by Bot API method found in the URL, logs every call.
    (binroot / "curl").write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$MOCK/calls.log"\n'
        'url=""; for a in "$@"; do case "$a" in https://*) url="$a";; esac; done\n'
        'out=""; for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done\n'
        'method="${url##*/}"; method="${method%%\\?*}"\n'
        'f="$MOCK/$method.json"\n'
        '[ -f "$MOCK/unreachable" ] && exit 7\n'
        '[ -f "$f" ] || f="$MOCK/default.json"\n'
        '[ -f "$f" ] || { echo "{}"; exit 0; }\n'
        'if [ -n "$out" ]; then cat "$f" > "$out"; else cat "$f"; fi\n'
    )
    (binroot / "curl").chmod(0o755)

    for tool in (
        "jq",
        "yq",
        "bash",
        "date",
        "stat",
        "mktemp",
        "openssl",
        "git",
        "grep",
        "sed",
        "awk",
        "cat",
        "chmod",
        "mkdir",
        "printf",
        "seq",
        "sleep",
        "head",
        "tail",
    ):
        src = shutil.which(tool)
        if src and not (binroot / tool).exists():
            (binroot / tool).symlink_to(src)

    class Env:
        def __init__(self) -> None:
            self.mock = mock
            self.home = home
            self.set_getme(HANDLE)
            self.set_webhook(url="")

        def set_getme(self, username: str | None, ok: bool = True) -> None:
            body = (
                {"ok": ok, "result": {"username": username}}
                if ok
                else {"ok": False, "description": "Unauthorized"}
            )
            (mock / "getMe.json").write_text(json.dumps(body))

        def set_webhook(self, url: str, pending: int = 0, error: str = "") -> None:
            res: dict = {
                "url": url,
                "pending_update_count": pending,
                "has_custom_certificate": False,
            }
            if error:
                res["last_error_message"] = error
            (mock / "getWebhookInfo.json").write_text(json.dumps({"ok": True, "result": res}))

        def set_write_ok(self, ok: bool = True) -> None:
            body = (
                {"ok": True, "result": True} if ok else {"ok": False, "description": "Bad Request"}
            )
            for m in ("setWebhook.json", "deleteWebhook.json"):
                (mock / m).write_text(json.dumps(body))

        def set_api_error(self, description: str = "Unauthorized") -> None:
            """A rejected token fails EVERY endpoint, not just getMe."""
            body = json.dumps({"ok": False, "description": description})
            for m in ("getMe", "getWebhookInfo", "setWebhook", "deleteWebhook", "default"):
                (mock / f"{m}.json").write_text(body)

        def no_token(self) -> None:
            (mock / "no_token").touch()

        def no_secret(self) -> None:
            (mock / "no_secret").touch()

        def unreachable(self) -> None:
            (mock / "unreachable").touch()

        def calls(self) -> str:
            p = mock / "calls.log"
            return p.read_text() if p.exists() else ""

        def run(self, script: Path, *args: str) -> subprocess.CompletedProcess:
            environ = {
                "HOME": str(home),
                "MOCK": str(mock),
                "PATH": f"{binroot}:{os.environ['PATH']}",
                "TMPDIR": str(tmp_path / "tmp"),
            }
            (tmp_path / "tmp").mkdir(exist_ok=True)
            return subprocess.run(
                ["bash", str(script), *args],
                capture_output=True,
                text=True,
                env=environ,
                cwd=str(REPO_ROOT),
                timeout=60,
            )

    e = Env()
    e.set_write_ok(True)
    return e

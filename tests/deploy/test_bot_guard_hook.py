"""Tests for scripts/hooks/bot-guard.sh.

A SessionStart hook has one hard requirement beyond being useful: it must never
break or stall a session. So every failure path is asserted to exit 0, and the
Telegram call is asserted to be cached.
"""

from __future__ import annotations

from conftest import BOT_GUARD, FAKE_TOKEN, HANDLE, OUR_URL

FOREIGN = "https://someone-else.fly.dev/tg/webhook"


def test_reports_ownership_when_webhook_is_ours(env):
    env.set_webhook(url=OUR_URL)
    r = env.run(BOT_GUARD)
    assert r.returncode == 0
    assert "owns" in r.stdout
    assert HANDLE in r.stdout


def test_reports_standby_when_someone_else_owns_it(env):
    env.set_webhook(url=FOREIGN)
    r = env.run(BOT_GUARD)
    assert r.returncode == 0
    assert "standby" in r.stdout
    assert FOREIGN in r.stdout


def test_reports_off_and_names_the_fix(env):
    env.set_webhook(url="")
    r = env.run(BOT_GUARD)
    assert r.returncode == 0
    assert "off" in r.stdout
    assert "bot-webhook-set" in r.stdout


def test_surfaces_last_error_and_pending_count(env):
    env.set_webhook(url=OUR_URL, pending=42, error="403 Forbidden")
    r = env.run(BOT_GUARD)
    assert "42" in r.stdout
    assert "403" in r.stdout


# ── never break the session ───────────────────────────────────────────────────


def test_missing_token_exits_zero_and_names_the_fix(env):
    env.no_token()
    r = env.run(BOT_GUARD)
    assert r.returncode == 0
    assert "ensure-tg-bot.sh" in r.stdout


def test_unreachable_telegram_exits_zero(env):
    env.unreachable()
    r = env.run(BOT_GUARD)
    assert r.returncode == 0
    assert "unreachable" in r.stdout.lower()


def test_rejected_token_exits_zero(env):
    env.set_api_error()
    r = env.run(BOT_GUARD)
    assert r.returncode == 0
    assert "re-mint" in r.stdout.lower()


def test_missing_settings_file_is_silent(env):
    (env.home / ".ai" / "skills" / "_settings" / "careeros.yml").unlink()
    r = env.run(BOT_GUARD)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_token_value_never_printed(env):
    env.set_webhook(url=OUR_URL)
    r = env.run(BOT_GUARD)
    assert FAKE_TOKEN not in r.stdout
    assert FAKE_TOKEN not in r.stderr


# ── caching ───────────────────────────────────────────────────────────────────


def test_second_run_within_ttl_does_not_call_telegram_again(env):
    """A session-start guard must not hammer the Bot API on every new session."""
    env.set_webhook(url=OUR_URL)
    env.run(BOT_GUARD)
    first = env.calls().count("getWebhookInfo")
    env.run(BOT_GUARD)
    assert env.calls().count("getWebhookInfo") == first


def test_cached_response_file_is_not_world_readable(env):
    """The cached payload is derived from a request authenticated by the token."""
    env.set_webhook(url=OUR_URL)
    env.run(BOT_GUARD)
    cache = next((env.home.parent / "tmp").glob("careeros-bot-guard/webhook.json"), None)
    assert cache is not None, "cache file was not written"
    assert cache.stat().st_mode & 0o077 == 0

"""Behavioural tests for scripts/prj-tools/tg-bot.sh.

The property under test is not "does it call the API" but "does it refuse the
dangerous things": a token belonging to the wrong bot, and taking a webhook away
from a live owner. Both have burned this fleet before.
"""

from __future__ import annotations

from conftest import FAKE_TOKEN, OUR_URL, TG_BOT

FOREIGN = "https://someone-else.fly.dev/tg/webhook"


# ── token identity ────────────────────────────────────────────────────────────


def test_missing_token_names_the_variable_and_the_fix(env):
    env.no_token()
    r = env.run(TG_BOT, "check")
    assert r.returncode == 1
    assert "CAREEROS_TG_BOT_TOKEN" in r.stderr
    assert "ensure-tg-bot.sh" in r.stderr


def test_token_for_the_wrong_bot_is_refused(env):
    """The expensive failure is a token that WORKS but is someone else's bot."""
    env.set_getme("some_other_bot")
    r = env.run(TG_BOT, "check")
    assert r.returncode == 2
    assert "some_other_bot" in r.stderr
    assert "refusing" in r.stderr.lower()


def test_token_rejected_by_telegram_is_reported_as_such(env):
    env.set_getme(None, ok=False)
    r = env.run(TG_BOT, "check")
    assert r.returncode == 2
    assert "rejected" in r.stderr.lower()


def test_unreachable_telegram_is_not_reported_as_a_bad_token(env):
    """A timeout must not send the human back to BotFather for a fine credential.

    Being offline says nothing about the token, so it gets its own exit code (4) — that is
    what lets `make all` carry on offline while still failing on a token Telegram rejected (2).
    """
    env.unreachable()
    r = env.run(TG_BOT, "check")
    assert r.returncode == 4  # not 2 — see test_token_rejected_by_telegram_is_reported_as_such
    assert "cannot reach" in r.stderr.lower()


def test_token_value_never_appears_in_output(env):
    env.set_webhook(url=OUR_URL)
    for action in ("check", "info"):
        r = env.run(TG_BOT, action)
        assert FAKE_TOKEN not in r.stdout
        assert FAKE_TOKEN not in r.stderr


# ── webhook ownership ─────────────────────────────────────────────────────────


def test_set_claims_when_webhook_is_unset(env):
    env.set_webhook(url="")
    r = env.run(TG_BOT, "set")
    assert r.returncode == 0
    assert "setWebhook" in env.calls()


def test_set_is_idempotent_when_webhook_is_already_ours(env):
    env.set_webhook(url=OUR_URL)
    r = env.run(TG_BOT, "set")
    assert r.returncode == 0
    assert "setWebhook" in env.calls()


def test_set_refuses_a_foreign_owner_and_makes_no_write_call(env):
    """Restarting a demoted host must not silently undo a failover."""
    env.set_webhook(url=FOREIGN)
    r = env.run(TG_BOT, "set")
    assert r.returncode == 3
    assert FOREIGN in r.stderr
    assert "--force" in r.stderr
    assert "setWebhook" not in env.calls()


def test_force_takes_a_foreign_webhook_deliberately(env):
    env.set_webhook(url=FOREIGN)
    r = env.run(TG_BOT, "set", "--force")
    assert r.returncode == 0
    assert "setWebhook" in env.calls()


def test_set_requires_the_shared_secret(env):
    """Claiming without a secret would leave the endpoint unauthenticated."""
    env.no_secret()
    env.set_webhook(url="")
    r = env.run(TG_BOT, "set")
    assert r.returncode == 1
    assert "CAREEROS_TG_WEBHOOK_SECRET" in r.stderr
    assert "setWebhook" not in env.calls()


def test_set_sends_the_secret_token_to_telegram(env):
    env.set_webhook(url="")
    env.run(TG_BOT, "set")
    assert "secret_token=" in env.calls()


def test_set_reports_a_telegram_side_failure(env):
    env.set_webhook(url="")
    env.set_write_ok(False)
    r = env.run(TG_BOT, "set")
    assert r.returncode == 2
    assert "setWebhook failed" in r.stderr


# ── reporting ─────────────────────────────────────────────────────────────────


def test_check_reports_off_owns_and_standby(env):
    env.set_webhook(url="")
    assert "off" in env.run(TG_BOT, "check").stderr

    env.set_webhook(url=OUR_URL)
    assert "owns" in env.run(TG_BOT, "check").stderr

    env.set_webhook(url=FOREIGN)
    out = env.run(TG_BOT, "check").stderr
    assert "standby" in out and FOREIGN in out


def test_info_surfaces_pending_and_last_error(env):
    env.set_webhook(url=OUR_URL, pending=17, error="wrong response from the webhook: 403")
    r = env.run(TG_BOT, "info")
    assert "17" in r.stderr
    assert "403" in r.stderr
    assert OUR_URL in r.stderr


def test_delete_calls_delete_webhook(env):
    env.set_webhook(url=OUR_URL)
    r = env.run(TG_BOT, "delete")
    assert r.returncode == 0
    assert "deleteWebhook" in env.calls()


def test_unknown_action_exits_with_usage(env):
    r = env.run(TG_BOT, "frobnicate")
    assert r.returncode == 1
    assert "usage" in r.stderr.lower()


def test_no_action_exits_with_usage(env):
    r = env.run(TG_BOT)
    assert r.returncode == 1
    assert "usage" in r.stderr.lower()

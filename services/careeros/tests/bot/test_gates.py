"""The three gates between the public internet and the ingest path (ADR-012 §B).

These are the only things protecting a single-user bot whose webhook URL is
guessable, so each gate is tested for what it REFUSES, not just what it allows.
"""

from __future__ import annotations

from typing import Any

import pytest

from careeros.core.config import Settings
from careeros.modules.bot.enums import UpdateVerdict
from careeros.modules.bot.security import UpdateGate

OWNER = 4242
OTHER = 9999


def settings(**kw: Any) -> Settings:
    # dict[str, Any]: Settings takes SecretStr/Literal fields that plain literals do not match
    base: dict[str, Any] = dict(
        env="test",
        tg_enabled=True,
        tg_bot_token="123:abc",
        tg_webhook_secret="s3cret",
        tg_owner_chat_id=OWNER,
    )
    base.update(kw)
    return Settings(**base)


def update(chat_id: int = OWNER, update_id: int = 1) -> dict:
    return {"update_id": update_id, "message": {"chat": {"id": chat_id}, "text": "hi"}}


@pytest.fixture
def gate() -> UpdateGate:
    return UpdateGate(settings())


# ── gate 1: shared secret ─────────────────────────────────────────────────────


def test_correct_secret_passes(gate: UpdateGate) -> None:
    assert gate.check_secret("s3cret") is UpdateVerdict.ACCEPTED


@pytest.mark.parametrize("supplied", ["", "wrong", None, "s3cre", "s3secret"])
def test_wrong_or_absent_secret_is_refused(gate: UpdateGate, supplied) -> None:
    assert gate.check_secret(supplied) is UpdateVerdict.BAD_SECRET


def test_secret_check_is_constant_time() -> None:
    """Uses hmac.compare_digest — a plain == leaks length/prefix by timing."""
    import inspect

    from careeros.modules.bot import security

    assert "compare_digest" in inspect.getsource(security.UpdateGate.check_secret)


def test_missing_configured_secret_refuses_everything() -> None:
    """An unset secret must fail closed, never open."""
    g = UpdateGate(settings(tg_webhook_secret=None))
    assert g.check_secret("anything") is UpdateVerdict.BAD_SECRET
    assert g.check_secret(None) is UpdateVerdict.BAD_SECRET


# ── gate 2: owner ─────────────────────────────────────────────────────────────


def test_owner_chat_passes(gate: UpdateGate) -> None:
    assert gate.check_owner(update(chat_id=OWNER)) is UpdateVerdict.ACCEPTED


def test_other_chat_is_dropped_not_rejected(gate: UpdateGate) -> None:
    """200 + no side effect: a stranger must not learn whether the bot is live."""
    assert gate.check_owner(update(chat_id=OTHER)) is UpdateVerdict.NOT_OWNER


def test_owner_is_read_from_callback_queries_too(gate: UpdateGate) -> None:
    cb = {"update_id": 2, "callback_query": {"message": {"chat": {"id": OWNER}}, "data": "skip"}}
    assert gate.check_owner(cb) is UpdateVerdict.ACCEPTED


def test_update_without_any_chat_is_not_owner(gate: UpdateGate) -> None:
    assert gate.check_owner({"update_id": 3}) is UpdateVerdict.NOT_OWNER


def test_unset_owner_refuses_everything() -> None:
    """Fail closed: an unconfigured owner must not mean 'anyone'."""
    g = UpdateGate(settings(tg_owner_chat_id=None))
    assert g.check_owner(update(chat_id=OWNER)) is UpdateVerdict.NOT_OWNER


# ── gate 3: idempotency ───────────────────────────────────────────────────────


def test_first_sighting_of_an_update_is_accepted(gate: UpdateGate) -> None:
    assert gate.check_duplicate(1) is UpdateVerdict.ACCEPTED


def test_repeat_of_the_same_update_id_is_dropped(gate: UpdateGate) -> None:
    """Telegram retries anything unacknowledged within ~60s; retries must be free."""
    assert gate.check_duplicate(7) is UpdateVerdict.ACCEPTED
    assert gate.check_duplicate(7) is UpdateVerdict.DUPLICATE
    assert gate.check_duplicate(7) is UpdateVerdict.DUPLICATE


def test_distinct_updates_do_not_collide(gate: UpdateGate) -> None:
    assert gate.check_duplicate(1) is UpdateVerdict.ACCEPTED
    assert gate.check_duplicate(2) is UpdateVerdict.ACCEPTED


def test_seen_set_is_bounded(gate: UpdateGate) -> None:
    """An unbounded set is a slow memory leak on a long-lived single machine."""
    for i in range(gate.SEEN_CAPACITY * 2):
        gate.check_duplicate(i)
    assert len(gate._seen) <= gate.SEEN_CAPACITY


def test_oldest_ids_are_evicted_first(gate: UpdateGate) -> None:
    for i in range(gate.SEEN_CAPACITY + 5):
        gate.check_duplicate(i)
    # id 0 fell out of the window, so it is no longer recognised as a duplicate
    assert gate.check_duplicate(0) is UpdateVerdict.ACCEPTED
    # a recent one is still remembered
    assert gate.check_duplicate(gate.SEEN_CAPACITY + 4) is UpdateVerdict.DUPLICATE


# ── ordering ──────────────────────────────────────────────────────────────────


def test_evaluate_stops_at_the_first_failing_gate(gate: UpdateGate) -> None:
    """A bad secret must be refused before the body is trusted for anything."""
    verdict = gate.evaluate(secret="wrong", payload=update(chat_id=OTHER, update_id=5))
    assert verdict is UpdateVerdict.BAD_SECRET
    # and the update_id was never recorded, since we never got that far
    assert gate.check_duplicate(5) is UpdateVerdict.ACCEPTED


def test_evaluate_accepts_a_well_formed_owner_update(gate: UpdateGate) -> None:
    assert gate.evaluate(secret="s3cret", payload=update()) is UpdateVerdict.ACCEPTED


def test_evaluate_does_not_burn_the_update_id_for_a_non_owner(gate: UpdateGate) -> None:
    """A stranger must not be able to make us forget a real update id."""
    assert (
        gate.evaluate(secret="s3cret", payload=update(chat_id=OTHER, update_id=11))
        is UpdateVerdict.NOT_OWNER
    )
    assert gate.check_duplicate(11) is UpdateVerdict.ACCEPTED

"""The three gates every incoming update must pass (ADR-012 §B).

Ordering is part of the contract: the shared secret is checked before the body is
trusted for anything, and a request that fails an earlier gate never reaches a
later one. In particular a rejected update must not consume its ``update_id``,
or a stranger could make us forget a real update.

Every gate FAILS CLOSED. An unset secret or an unconfigured owner refuses
everything rather than admitting everyone — the opposite default is how a
single-user bot silently becomes public.
"""

from __future__ import annotations

import hmac
from collections import OrderedDict
from typing import Any

from careeros.core.config import Settings
from careeros.modules.bot.enums import UpdateVerdict


class UpdateGate:
    """Stateful across a process: remembers recently seen update ids."""

    #: Bounded so a long-lived single machine cannot leak memory. Telegram retries
    #: within ~60s, so a window of this size covers retries many times over.
    SEEN_CAPACITY = 2048

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._seen: OrderedDict[int, None] = OrderedDict()

    # ── gate 1 ────────────────────────────────────────────────────────────────
    def check_secret(self, supplied: str | None) -> UpdateVerdict:
        """Compare the X-Telegram-Bot-Api-Secret-Token header.

        ``compare_digest`` rather than ``==``: the latter short-circuits on the
        first differing byte and so leaks the secret's length and prefix by timing.
        """
        expected = self._settings.tg_webhook_secret
        if expected is None or supplied is None:
            return UpdateVerdict.BAD_SECRET
        if hmac.compare_digest(supplied, expected.get_secret_value()):
            return UpdateVerdict.ACCEPTED
        return UpdateVerdict.BAD_SECRET

    # ── gate 2 ────────────────────────────────────────────────────────────────
    def check_owner(self, payload: dict[str, Any]) -> UpdateVerdict:
        """Accept only the configured owner's chat.

        Anything else is dropped with 200 rather than 403: a stranger probing the
        endpoint must not be able to tell a live bot from a dead one.
        """
        owner = self._settings.tg_owner_chat_id
        if owner is None:
            return UpdateVerdict.NOT_OWNER
        chat_id = self._chat_id(payload)
        return UpdateVerdict.ACCEPTED if chat_id == owner else UpdateVerdict.NOT_OWNER

    @staticmethod
    def _chat_id(payload: dict[str, Any]) -> int | None:
        """Chat id from whichever update kind this is; None when there is no chat."""
        for key in ("message", "edited_message", "channel_post"):
            chat = (payload.get(key) or {}).get("chat") or {}
            if "id" in chat:
                return chat["id"]
        callback = payload.get("callback_query") or {}
        chat = (callback.get("message") or {}).get("chat") or {}
        return chat.get("id")

    # ── gate 3 ────────────────────────────────────────────────────────────────
    def check_duplicate(self, update_id: int) -> UpdateVerdict:
        """Record an update id, reporting whether it had already been seen.

        The webhook acknowledges before the work finishes, so Telegram's retries
        are expected rather than exceptional; this is what makes them free.
        """
        if update_id in self._seen:
            return UpdateVerdict.DUPLICATE
        self._seen[update_id] = None
        while len(self._seen) > self.SEEN_CAPACITY:
            self._seen.popitem(last=False)
        return UpdateVerdict.ACCEPTED

    # ── all three, in order ───────────────────────────────────────────────────
    def evaluate(self, *, secret: str | None, payload: dict[str, Any]) -> UpdateVerdict:
        """Run the gates in order, stopping at the first refusal."""
        verdict = self.check_secret(secret)
        if verdict is not UpdateVerdict.ACCEPTED:
            return verdict
        verdict = self.check_owner(payload)
        if verdict is not UpdateVerdict.ACCEPTED:
            return verdict
        update_id = payload.get("update_id")
        if update_id is None:
            return UpdateVerdict.ACCEPTED
        return self.check_duplicate(update_id)

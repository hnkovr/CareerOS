"""Inline keyboards.

Callback data is capped at 64 bytes by Telegram, and a longer payload is dropped
without an error anyone sees. A uuid is already 36 of those bytes, so the prefixes
here stay deliberately terse.
"""

from __future__ import annotations

from typing import Any

#: action -> button label. Order is the order shown.
TRIAGE_ACTIONS: dict[str, str] = {
    "skip": "Skip",
    "save": "Save",
    "analyze": "Analyze",
    "prompt": "Prompt",
}

CALLBACK_LIMIT = 64


def triage_keyboard(opportunity_id: str) -> dict[str, Any]:
    """Skip / Save / Analyze / Prompt for one opportunity."""
    buttons = [
        {"text": label, "callback_data": f"o:{action}:{opportunity_id}"}
        for action, label in TRIAGE_ACTIONS.items()
    ]
    for button in buttons:
        if len(button["callback_data"].encode()) > CALLBACK_LIMIT:
            raise ValueError(
                f"callback_data exceeds Telegram's {CALLBACK_LIMIT}-byte limit: "
                f"{button['callback_data']!r}"
            )
    # Two rows of two: four buttons on one row are unreadably narrow on a phone.
    return {"inline_keyboard": [buttons[:2], buttons[2:]]}

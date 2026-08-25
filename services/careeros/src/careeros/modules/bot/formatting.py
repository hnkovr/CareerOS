"""MarkdownV2 helpers.

Telegram rejects an unescaped reserved character with a 400 that names a byte
offset, not the character — so escaping is done in one place and tested.
"""

from __future__ import annotations

#: Every character Telegram requires escaping in MarkdownV2.
_RESERVED = r"_*[]()~`>#+-=|{}.!"


def escape_md(text: str) -> str:
    """Escape all MarkdownV2 reserved characters."""
    return "".join("\\" + ch if ch in _RESERVED else ch for ch in text)

"""Identifier helpers. Operational rows use UUIDv7 (time-ordered); vault items use stable slugs."""

from __future__ import annotations

import os
import re
import time
import uuid

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_]{1,79}$")


def uuid7() -> uuid.UUID:
    """RFC 9562 UUIDv7: 48-bit unix ms timestamp + 74 random bits."""
    ts_ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")
    value = (
        (ts_ms << 80)
        | (0x7 << 76)
        | ((rand >> 64) & 0x0FFF) << 64
        | (0b10 << 62)
        | (rand & ((1 << 62) - 1))
    )
    return uuid.UUID(int=value)


def is_slug(value: str) -> bool:
    return bool(_SLUG_RE.match(value))


def slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return value[:80] or "item"

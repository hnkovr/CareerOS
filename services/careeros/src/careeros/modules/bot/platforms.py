"""Parsing a platform selection typed into a chat (GH #25).

The expensive failure is a silently accepted typo: `/services set hh,upwrok`
would store a set that makes every later command return nothing, with no error at
the point the mistake was made. So an unknown name is rejected loudly, by name,
alongside the list of what does exist — and a partly-valid selection is rejected
whole, because storing the good half of a typo'd command is a surprise.
"""

from __future__ import annotations

import re

#: Space, comma and semicolon all separate. People type what they type, and a
#: separator guess must not silently become a "platform" nobody asked for.
_SEPARATORS = re.compile(r"[,;\s]+")

#: Selects every connector that exists.
ALL = "all"


class UnknownPlatforms(ValueError):
    """One or more names in a selection have no connector."""

    def __init__(self, unknown: list[str], known: set[str]) -> None:
        self.unknown = unknown
        super().__init__(
            f"unknown platform(s): {', '.join(unknown)}. known: {', '.join(sorted(known))}"
        )


def known_platforms() -> set[str]:
    """Every platform with a registered connector.

    Read from the registry rather than hardcoded, so a connector added by the
    platform lane becomes selectable here without touching the bot.
    """
    from careeros.modules.platform.registry import PlatformRegistry

    return {p.value for p in PlatformRegistry.default()._by_platform}


def parse_platform_set(raw: str) -> list[str]:
    """Parse a typed selection into a validated, de-duplicated platform list.

    Order is preserved: it is the user's stated preference, not an accident.
    """
    known = known_platforms()
    names = [n.strip().lower() for n in _SEPARATORS.split(raw.strip()) if n.strip()]
    if not names:
        raise ValueError("no platforms given — an empty set would act on nothing")

    if names == [ALL]:
        return sorted(known)

    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)

    unknown = [n for n in seen if n not in known]
    if unknown:
        raise UnknownPlatforms(unknown, known)
    return seen


def format_platform_set(platforms: list[str]) -> str:
    """Render a stored set for display; never render an empty set as blank."""
    return ", ".join(platforms) if platforms else "(none configured)"

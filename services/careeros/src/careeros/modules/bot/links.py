"""Link building for /open, /profiles and /urls (GH #26, #27).

One rule governs all three: a platform that cannot answer is REPORTED, never
dropped. An absent row reads as "no results", which is a materially different
claim from "this platform cannot express that query" — and the user acts on the
difference. So every requested platform produces exactly one row, carrying either
a URL or a stated reason.

URL construction itself belongs to the connectors, which already know how to
express a query for their own platform. This module only orchestrates and renders,
and reaches the platform layer through PlatformService (invariant 7).
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

log = structlog.get_logger(__name__)

#: Home page per platform. Kept here rather than in the connectors because it is a
#: bot-presentation concern: the connectors deal in search and profile URLs, not in
#: "where do I send someone who just wants to look at this site".
HOME_URLS: dict[str, str] = {
    "hh": "https://hh.ru/",
    "upwork": "https://www.upwork.com/",
    "linkedin": "https://www.linkedin.com/jobs/",
    "wellfound": "https://wellfound.com/jobs",
    "indeed": "https://www.indeed.com/",
    "getmatch": "https://getmatch.ru/",
    "toptal": "https://www.toptal.com/",
    "rockethunt": "https://rockethunt.ai/",
    "justjoin": "https://justjoin.it/",
}


@dataclass(frozen=True)
class OpenTarget:
    platform: str
    url: str


@dataclass(frozen=True)
class LinkRow:
    """One platform's answer. Exactly one of url / reason is set."""

    platform: str
    url: str | None
    reason: str | None = None


def resolve_open_target(name: str) -> OpenTarget:
    """Home URL for a named service."""
    key = name.strip().lower()
    if key not in HOME_URLS:
        raise ValueError(f"unknown service: {name}. known: {', '.join(sorted(HOME_URLS))}")
    return OpenTarget(platform=key, url=HOME_URLS[key])


async def build_search_rows(
    platform_service: object, platforms: list[str], query: str
) -> list[LinkRow]:
    """One row per requested platform, with its search URL or why there isn't one."""
    if not query.strip():
        raise ValueError("empty search query — nothing to search for")

    from careeros.modules.platform.schemas import JobQuery

    job_query = JobQuery(text=query.strip())
    rows: list[LinkRow] = []
    for name in platforms:
        rows.append(
            _row(
                lambda c: c.search_url(job_query),
                platform_service,
                name,
                absent="cannot express this search as a URL",
            )
        )
    return rows


async def build_profile_rows(platform_service: object, platforms: list[str]) -> list[LinkRow]:
    """One row per requested platform, with the owner's profile URL or why not."""
    rows: list[LinkRow] = []
    for name in platforms:
        try:
            url = await platform_service.own_profile_url(name)  # type: ignore[attr-defined]
        except Exception as exc:
            log.warning("bot.profile_url_failed", platform=name, error=str(exc))
            rows.append(LinkRow(name, None, "lookup failed"))
            continue
        rows.append(
            LinkRow(name, url, None if url else "profile URL not known — connect or snapshot first")
        )
    return rows


def _row(fn, platform_service: object, name: str, *, absent: str) -> LinkRow:
    """Call one connector, converting both None and a raised error into a stated reason."""
    try:
        connector = platform_service.connector(name)  # type: ignore[attr-defined]
        url = fn(connector)
    except Exception as exc:
        log.warning("bot.search_url_failed", platform=name, error=str(exc))
        return LinkRow(name, None, "lookup failed")
    return LinkRow(name, url, None if url else absent)


def render_rows(rows: list[LinkRow], *, empty: str) -> str:
    """Plain text (not MarkdownV2): callers escape, and URLs must not be mangled."""
    if not rows:
        return empty
    lines = []
    for row in rows:
        lines.append(f"{row.platform}: {row.url}" if row.url else f"{row.platform}: — {row.reason}")
    return "\n".join(lines)

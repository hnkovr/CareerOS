"""``robots.txt`` before any public page read (ADR-015 §1), cached per host.

RFC 9309 semantics: 2xx → parse; 4xx → unrestricted; 5xx / unreachable → fail-open (we cannot
know), but an explicit ``Disallow`` for our product token (or ``*``) is honoured (fail-closed).

The matcher is our own, not ``urllib.robotparser``: that one applies the *first* matching rule
and knows no ``*`` / ``$`` in paths, so a file that says ``Allow: /`` before ``Disallow: /api/``
(RocketHunt's does) would read as "everything allowed". RFC 9309 wants the *longest* matching
rule, ``Allow`` winning ties, and wildcard paths — that is what ``parse_robots`` implements.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from careeros.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RobotsDecision:
    allowed: bool
    #: allow | disallow | no_robots | unavailable | not_http
    reason: str


@dataclass(slots=True)
class _Rule:
    allow: bool
    pattern: str
    regex: re.Pattern[str]


@dataclass(slots=True)
class _Group:
    agents: list[str] = field(default_factory=list)
    rules: list[_Rule] = field(default_factory=list)


@dataclass(slots=True)
class _Entry:
    groups: list[_Group] | None
    status: str
    expires_at: float


_SHARED: dict[str, _Entry] = {}


def _compile(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    core = pattern[:-1] if anchored else pattern
    regex = "^" + ".*".join(re.escape(part) for part in core.split("*"))
    return re.compile(regex + ("$" if anchored else ""))


def parse_robots(text: str) -> list[_Group]:
    """Groups of (user-agents, allow/disallow rules); rules before a ``User-agent`` are dropped."""
    groups: list[_Group] = []
    current: _Group | None = None
    collecting_agents = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if current is None or not collecting_agents:
                current = _Group()
                groups.append(current)
            current.agents.append(value.lower())
            collecting_agents = True
            continue
        collecting_agents = False
        if key in ("allow", "disallow") and current is not None and value:
            current.rules.append(_Rule(key == "allow", value, _compile(value)))
    return groups


def product_token(user_agent: str) -> str:
    return user_agent.split("/", 1)[0].split(" ", 1)[0].strip().lower()


def select_group(groups: list[_Group], user_agent: str) -> _Group | None:
    """The most specific group: one naming our product token, else ``*``, else none."""
    token = product_token(user_agent)
    for group in groups:
        if any(agent != "*" and (agent == token or agent in token) for agent in group.agents):
            return group
    for group in groups:
        if "*" in group.agents:
            return group
    return None


def path_allowed(group: _Group, path: str) -> bool:
    matches = [rule for rule in group.rules if rule.regex.match(path)]
    if not matches:
        return True
    longest = max(len(rule.pattern) for rule in matches)
    return any(rule.allow for rule in matches if len(rule.pattern) == longest)


class RobotsPolicy:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        user_agent: str,
        ttl_s: float = 3600.0,
        cache: dict[str, _Entry] | None = None,
        clock: Callable[[], float] = time.monotonic,
        timeout_s: float = 10.0,
        max_bytes: int = 512 * 1024,
    ) -> None:
        self._client = client
        self.user_agent = user_agent
        self.ttl_s = ttl_s
        self._cache = _SHARED if cache is None else cache
        self._clock = clock
        self._timeout_s = timeout_s
        self._max_bytes = max_bytes

    async def allowed(self, url: str) -> RobotsDecision:
        parts = urlsplit(url)
        if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
            return RobotsDecision(False, "not_http")
        entry = await self._entry(parts.scheme.lower(), parts.netloc.lower())
        if entry.groups is None:
            return RobotsDecision(True, entry.status)
        group = select_group(entry.groups, self.user_agent)
        if group is None:
            return RobotsDecision(True, "allow")
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        if path_allowed(group, path):
            return RobotsDecision(True, "allow")
        return RobotsDecision(False, "disallow")

    async def _entry(self, scheme: str, netloc: str) -> _Entry:
        key = f"{scheme}://{netloc}"
        cached = self._cache.get(key)
        now = self._clock()
        if cached is not None and cached.expires_at > now:
            return cached
        entry = await self._fetch(key)
        entry.expires_at = now + self.ttl_s
        self._cache[key] = entry
        return entry

    async def _fetch(self, origin: str) -> _Entry:
        url = f"{origin}/robots.txt"
        try:
            resp = await self._client.get(
                url,
                headers={"Accept": "text/plain, */*;q=0.5", "User-Agent": self.user_agent},
                timeout=self._timeout_s,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            log.info("platform.robots_unavailable", origin=origin, error=type(exc).__name__)
            return _Entry(None, "unavailable", 0.0)
        if 200 <= resp.status_code < 300:
            return _Entry(parse_robots(resp.text[: self._max_bytes]), "ok", 0.0)
        if 400 <= resp.status_code < 500:
            return _Entry(None, "no_robots", 0.0)
        log.info("platform.robots_unavailable", origin=origin, status=resp.status_code)
        return _Entry(None, "unavailable", 0.0)


def reset_robots_cache() -> None:
    _SHARED.clear()

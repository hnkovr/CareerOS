"""HTTP client for connectors: identified User-Agent, timeouts, bounded retries, typed errors."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from careeros.core.config import Settings
from careeros.core.logging import get_logger
from careeros.modules.platform.base import NotConnected, UpstreamError
from careeros.modules.vault.enums import Platform

log = get_logger(__name__)

RETRY_STATUSES = (429, 502, 503, 504)


def build_http(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.AsyncClient:
    """One client per sync; ``transport`` lets tests inject ``httpx.MockTransport``."""
    base = {"User-Agent": settings.platform_user_agent, "Accept": "application/json"}
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.platform_http_timeout_s),
        headers={**base, **(headers or {})},
        transport=transport,
        follow_redirects=False,
    )


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    platform: Platform,
    ok: tuple[int, ...] = (200,),
    retries: int = 2,
    backoff_s: float = 1.0,
    **kw: Any,
) -> Any:
    """Perform a request and decode JSON. Retries 429/502/503/504 with capped backoff.

    401 → ``NotConnected`` (token missing/expired/revoked); any other non-ok → ``UpstreamError``.
    """
    attempt = 0
    while True:
        try:
            resp = await client.request(method, url, **kw)
        except httpx.HTTPError as exc:
            if attempt < retries:
                attempt += 1
                await asyncio.sleep(min(backoff_s * (2 ** (attempt - 1)), 5.0))
                continue
            raise UpstreamError(platform, None, f"{type(exc).__name__}: {exc}") from exc
        if resp.status_code in RETRY_STATUSES and attempt < retries:
            attempt += 1
            retry_after = resp.headers.get("retry-after")
            try:
                wait = float(retry_after) if retry_after else backoff_s * (2 ** (attempt - 1))
            except ValueError:
                wait = backoff_s
            log.info(
                "platform.http_retry", platform=str(platform), status=resp.status_code, wait=wait
            )
            await asyncio.sleep(min(wait, 5.0))
            continue
        if resp.status_code == 401:
            raise NotConnected(platform, "token rejected (401) — reconnect or refresh")
        if resp.status_code not in ok:
            raise UpstreamError(platform, resp.status_code, resp.text[:300])
        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except ValueError as exc:
            raise UpstreamError(platform, resp.status_code, "non-JSON response") from exc

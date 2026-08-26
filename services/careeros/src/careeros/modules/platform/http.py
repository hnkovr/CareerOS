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


HTML_ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"
REDIRECT_STATUSES = (301, 302, 303, 307, 308)


def build_http(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    headers: dict[str, str] | None = None,
    timeout_s: float | None = None,
) -> httpx.AsyncClient:
    """One client per sync; ``transport`` lets tests inject ``httpx.MockTransport``.

    The default ``Accept`` is JSON (API connectors); pass ``headers={"Accept": …}`` for a
    page-reading client, or let ``request_text`` set it per request. Redirects are never
    followed by the client itself — ``request_text`` follows same-host ones explicitly.
    """
    base = {"User-Agent": settings.platform_user_agent, "Accept": "application/json"}
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_s or settings.platform_http_timeout_s),
        headers={**base, **(headers or {})},
        transport=transport,
        follow_redirects=False,
    )


async def _send(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    platform: Platform,
    retries: int,
    backoff_s: float,
    **kw: Any,
) -> httpx.Response:
    """One request with the shared retry policy: 429/502/503/504 (Retry-After honoured, capped
    at 5 s) and transport errors are retried ``retries`` times; the final response is returned
    whatever its status."""
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
        return resp


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
    resp = await _send(
        client, method, url, platform=platform, retries=retries, backoff_s=backoff_s, **kw
    )
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


def _same_host(a: str, b: str) -> bool:
    ha = (httpx.URL(a).host or "").lower().removeprefix("www.")
    hb = (httpx.URL(b).host or "").lower().removeprefix("www.")
    return bool(ha) and ha == hb


async def request_text(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    platform: Platform,
    ok: tuple[int, ...] | None = (200,),
    retries: int = 2,
    backoff_s: float = 1.0,
    accept: str = HTML_ACCEPT,
    headers: dict[str, str] | None = None,
    max_redirects: int = 3,
    **kw: Any,
) -> tuple[int, str, str | None, str]:
    """Fetch a page as text → ``(status, text, content_type, final_url)``.

    Same retry policy as ``request_json``. Redirects are followed only to the **same host**
    (``www.`` ignored), at most ``max_redirects`` times; a cross-host redirect is returned as the
    3xx it is (``final_url`` = the request URL, not the foreign target) so the caller can decide.
    ``ok=None`` never raises on status — the caller reads the code (job reads keep 404/403/… as
    diagnostics); otherwise a status outside ``ok`` raises ``UpstreamError``. No cookies are ever
    sent or kept: the client stores none and none are added here.
    """
    req_headers = {"Accept": accept, **(headers or {})}
    current = url
    hops = 0
    while True:
        resp = await _send(
            client,
            method,
            current,
            platform=platform,
            retries=retries,
            backoff_s=backoff_s,
            headers=req_headers,
            **kw,
        )
        location = resp.headers.get("location")
        if resp.status_code in REDIRECT_STATUSES and location and hops < max_redirects:
            target = str(httpx.URL(current).join(location))
            if not _same_host(current, target):
                log.info(
                    "platform.http_redirect_foreign",
                    platform=str(platform),
                    status=resp.status_code,
                    host=httpx.URL(target).host,
                )
                break
            hops += 1
            current = target
            continue
        break
    if ok is not None and resp.status_code not in ok:
        raise UpstreamError(platform, resp.status_code, resp.text[:300])
    content_type = resp.headers.get("content-type")
    return resp.status_code, resp.text, content_type, current

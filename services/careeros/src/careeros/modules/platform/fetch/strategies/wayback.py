"""Wayback Machine (CDX lookup → latest 200 snapshot) as the archive fallback (ADR-015 §3).

The artifact is ``is_archive=True`` with the capture time in ``archive_ts``; the capture time
is never a publication date — extraction reads the page's own dates.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from urllib.parse import urlencode

from careeros.modules.platform.base import ConnectorContext, UpstreamError
from careeros.modules.platform.enums import FetchStrategy
from careeros.modules.platform.fetch.artifact import FetchArtifact
from careeros.modules.platform.fetch.budget import FetchBudget
from careeros.modules.platform.http import HTML_ACCEPT, request_text
from careeros.modules.platform.sources import CanonicalSource


def parse_cdx(text: str) -> tuple[str, str] | None:
    """``(timestamp, original_url)`` of the latest row of a CDX ``output=json`` response."""
    try:
        rows: Any = json.loads(text)
    except ValueError:
        return None
    if not isinstance(rows, list) or len(rows) < 2 or not isinstance(rows[0], list):
        return None
    header = [str(h) for h in rows[0]]
    ts_idx = header.index("timestamp") if "timestamp" in header else 1
    url_idx = header.index("original") if "original" in header else 2
    best: tuple[str, str] | None = None
    for row in rows[1:]:
        if not isinstance(row, list) or len(row) <= max(ts_idx, url_idx):
            continue
        ts, original = str(row[ts_idx]), str(row[url_idx])
        if not ts.isdigit():
            continue
        if best is None or ts > best[0]:
            best = (ts, original)
    return best


def archive_timestamp(ts: str) -> datetime | None:
    try:
        return datetime.strptime(ts[:14].ljust(14, "0"), "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None


class WaybackStrategy:
    name = FetchStrategy.wayback

    def __init__(self, *, retries: int = 1, backoff_s: float = 0.5) -> None:
        self.retries = retries
        self.backoff_s = backoff_s

    def cdx_url(self, ctx: ConnectorContext, source: CanonicalSource) -> str:
        base = ctx.settings.wayback_cdx_base.rstrip("/")
        query = urlencode(
            {
                "url": source.canonical_url,
                "output": "json",
                "filter": "statuscode:200",
                "limit": "-3",
            }
        )
        return f"{base}/cdx/search/cdx?{query}"

    def snapshot_url(self, ctx: ConnectorContext, ts: str, original: str) -> str:
        base = ctx.settings.wayback_cdx_base.rstrip("/")
        return f"{base}/web/{ts}id_/{original}"

    async def run(
        self, ctx: ConnectorContext, source: CanonicalSource, budget: FetchBudget
    ) -> FetchArtifact:
        started = perf_counter()

        def fail(error_type: str, message: str, status: int | None = None) -> FetchArtifact:
            return FetchArtifact(
                provider=source.platform,
                strategy=self.name,
                requested_url=source.canonical_url,
                external_id=source.external_id,
                fetched_at=ctx.now,
                status_code=status,
                is_archive=True,
                error_type=error_type,
                error_message=message[:300],
                duration_ms=int((perf_counter() - started) * 1000),
                flags=["archive"],
            )

        try:
            status, text, _, _ = await request_text(
                ctx.http,
                "GET",
                self.cdx_url(ctx, source),
                platform=source.platform,
                ok=None,
                retries=self.retries,
                backoff_s=self.backoff_s,
                accept="application/json, */*;q=0.5",
            )
        except UpstreamError as exc:
            return fail("network", exc.detail, exc.status_code)
        if status != 200:
            return fail("archive_lookup_failed", f"CDX answered {status}", status)
        latest = parse_cdx(text)
        if latest is None:
            return fail("archive_not_found", "no 200 snapshot in the CDX index")
        ts, original = latest
        try:
            status, html, content_type, _ = await request_text(
                ctx.http,
                "GET",
                self.snapshot_url(ctx, ts, original),
                platform=source.platform,
                ok=None,
                retries=self.retries,
                backoff_s=self.backoff_s,
                accept=HTML_ACCEPT,
            )
        except UpstreamError as exc:
            return fail("network", exc.detail, exc.status_code)
        return FetchArtifact(
            provider=source.platform,
            strategy=self.name,
            requested_url=source.canonical_url,
            resolved_url=original,
            external_id=source.external_id,
            fetched_at=ctx.now,
            status_code=status,
            content_type=content_type,
            raw_text=html,
            is_archive=True,
            archive_ts=archive_timestamp(ts),
            duration_ms=int((perf_counter() - started) * 1000),
            flags=["archive"],
        )

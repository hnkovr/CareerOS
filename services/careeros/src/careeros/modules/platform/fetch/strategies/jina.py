"""Jina Reader (``r.jina.ai``) as a transformed-representation fallback (ADR-015 §3).

Public URLs only — the chain never routes a URL from a private message here. Output is
markdown, flagged ``transformed``: never authoritative over native structured fields.
"""

from __future__ import annotations

from time import perf_counter

from careeros.modules.platform.base import ConnectorContext, UpstreamError
from careeros.modules.platform.enums import FetchStrategy
from careeros.modules.platform.fetch.artifact import FetchArtifact
from careeros.modules.platform.fetch.budget import FetchBudget
from careeros.modules.platform.fetch.extract.text import markdown_body
from careeros.modules.platform.http import request_text
from careeros.modules.platform.sources import CanonicalSource

ACCEPT = "text/markdown, text/plain;q=0.9, */*;q=0.5"


class JinaStrategy:
    name = FetchStrategy.jina

    def __init__(self, *, retries: int = 1, backoff_s: float = 0.5) -> None:
        self.retries = retries
        self.backoff_s = backoff_s

    def reader_url(self, ctx: ConnectorContext, source: CanonicalSource) -> str:
        return f"{ctx.settings.jina_reader_base.rstrip('/')}/{source.canonical_url}"

    async def run(
        self, ctx: ConnectorContext, source: CanonicalSource, budget: FetchBudget
    ) -> FetchArtifact:
        started = perf_counter()
        headers = {"X-Return-Format": "markdown"}
        key = ctx.settings.jina_api_key
        if key is not None and key.get_secret_value():
            headers["Authorization"] = f"Bearer {key.get_secret_value()}"
        try:
            status, text, content_type, _ = await request_text(
                ctx.http,
                "GET",
                self.reader_url(ctx, source),
                platform=source.platform,
                ok=None,
                retries=self.retries,
                backoff_s=self.backoff_s,
                accept=ACCEPT,
                headers=headers,
            )
        except UpstreamError as exc:
            return FetchArtifact(
                provider=source.platform,
                strategy=self.name,
                requested_url=source.canonical_url,
                external_id=source.external_id,
                fetched_at=ctx.now,
                status_code=exc.status_code,
                error_type="network",
                error_message=exc.detail[:300],
                duration_ms=int((perf_counter() - started) * 1000),
                flags=["transformed"],
            )
        meta, _ = markdown_body(text)
        return FetchArtifact(
            provider=source.platform,
            strategy=self.name,
            requested_url=source.canonical_url,
            resolved_url=meta.get("url") or source.canonical_url,
            external_id=source.external_id,
            fetched_at=ctx.now,
            status_code=status,
            content_type=content_type or "text/markdown",
            raw_text=text,
            duration_ms=int((perf_counter() - started) * 1000),
            flags=["transformed"],
        )

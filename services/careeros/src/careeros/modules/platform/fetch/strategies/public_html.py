"""One GET of the public page, as a browser would (ADR-015 §1): ``Accept: text/html``, the
identifying User-Agent, same-host redirects only, no cookies. ``robots.txt`` is checked by the
chain before this runs."""

from __future__ import annotations

from time import perf_counter

from careeros.modules.platform.base import ConnectorContext, UpstreamError
from careeros.modules.platform.enums import FetchStrategy
from careeros.modules.platform.fetch.artifact import FetchArtifact
from careeros.modules.platform.fetch.budget import FetchBudget
from careeros.modules.platform.http import HTML_ACCEPT, request_text
from careeros.modules.platform.sources import CanonicalSource


class PublicHtmlStrategy:
    name = FetchStrategy.public_html

    def __init__(self, *, retries: int = 1, backoff_s: float = 0.5) -> None:
        self.retries = retries
        self.backoff_s = backoff_s

    async def run(
        self, ctx: ConnectorContext, source: CanonicalSource, budget: FetchBudget
    ) -> FetchArtifact:
        started = perf_counter()
        headers = {"Accept-Language": source.locale} if source.locale else None
        try:
            status, text, content_type, final_url = await request_text(
                ctx.http,
                "GET",
                source.canonical_url,
                platform=source.platform,
                ok=None,
                retries=self.retries,
                backoff_s=self.backoff_s,
                accept=HTML_ACCEPT,
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
            )
        return FetchArtifact(
            provider=source.platform,
            strategy=self.name,
            requested_url=source.canonical_url,
            resolved_url=final_url,
            external_id=source.external_id,
            fetched_at=ctx.now,
            status_code=status,
            content_type=content_type,
            raw_text=text,
            duration_ms=int((perf_counter() - started) * 1000),
        )

"""RocketHunt (rockethunt.ai): read one public vacancy page by URL (ADR-015).

RocketHunt aggregates tech vacancies re-posted from Telegram channels. It has no public API
(``/api/`` is disallowed by robots) and its terms forbid mass collection, so this connector
does exactly two things: it recognises a vacancy URL the user gives us and reads **that one
page** (``public_html`` → ``jina`` → ``wayback``), and it parses a pasted job list. Listings,
sitemaps and search endpoints are never touched — discovery is a ``search_url`` deep link the
owner opens in their own browser.

Two RocketHunt specifics drive the extractor (see :mod:`.extract`): the salary is often the
aggregator's own estimate, so it carries ``aggregator_estimate`` evidence unless the vacancy
text states the figures itself; and contacts sit behind a paid gate that is never unlocked,
never parsed and never inferred.
"""

from __future__ import annotations

from urllib.parse import urlencode

from careeros.modules.platform import parsers
from careeros.modules.platform.base import BaseConnector, ConnectorContext
from careeros.modules.platform.connectors.rockethunt import extract, urls
from careeros.modules.platform.enums import AccessMode, AuthKind, FetchStrategy, SyncMethod
from careeros.modules.platform.fetch.artifact import FetchArtifact
from careeros.modules.platform.fetch.extract.jsonld import find_jobposting
from careeros.modules.platform.fetch.robots import RobotsPolicy
from careeros.modules.platform.schemas import (
    Capabilities,
    DoctorCheck,
    JobPosting,
    JobQuery,
)
from careeros.modules.platform.sources import (
    CanonicalSource,
    DetectionResult,
    SourceRef,
    canonical_source,
)
from careeros.modules.vault.enums import Platform

#: A vacancy URL is an unmistakable shape (uuid v4 under ``/{en,ru}/vacancies/``) — no other
#: connector can claim it by accident, so detection is near-certain.
DETECT_CONFIDENCE = 0.95

#: Search is client-side; the deep link comes from the JSON-LD ``potentialAction.urlTemplate``
#: ``https://rockethunt.ai/en?text={search_term_string}`` (verified live 2026-08-26).
SEARCH_URL = f"{urls.BASE}/{urls.CANONICAL_LOCALE}"

#: Shape-only sample used by ``doctor`` — a syntactically valid uuid that belongs to no vacancy,
#: so the probe proves reachability without pinning someone's live posting into the repo.
SAMPLE_URL = urls.vacancy_url("00000000-0000-4000-8000-000000000000")


class Connector(BaseConnector):
    platform = Platform.rockethunt
    detect_hosts = urls.HOSTS

    capabilities = Capabilities(
        platform=Platform.rockethunt,
        jobs=[SyncMethod.paste],
        read_job=[FetchStrategy.public_html, FetchStrategy.jina, FetchStrategy.wayback],
        access=AccessMode.public,
        auth=AuthKind.none,
        official_api=False,
        email_fallback=False,
        notes=(
            "Aggregator of Telegram job posts; public vacancy pages only (read-one, ADR-015); "
            "contacts gated and never fetched; salary is an aggregator estimate"
        ),
    )

    # ---- read one job -----------------------------------------------------------------
    def detect(self, url: str) -> DetectionResult | None:
        """Only ``/{en,ru}/vacancies/<uuid>`` is ours; every other RocketHunt URL is not a job."""
        parsed = urls.parse_vacancy(url)
        if parsed is None:
            return None
        uuid, locale = parsed
        return DetectionResult(
            platform=self.platform,
            confidence=DETECT_CONFIDENCE,
            canonical=self._canonical(uuid, locale),
        )

    def canonicalize(self, source: SourceRef | str) -> CanonicalSource:
        """The ``en`` page of the same uuid; the locale asked for is kept on the source."""
        url = source if isinstance(source, str) else source.url()
        if url is None:
            raise ValueError(f"{self.platform}: reference without a URL")
        parsed = urls.parse_vacancy(url)
        if parsed is None:
            raise ValueError(f"{self.platform}: not a vacancy URL: {url!r}")
        uuid, locale = parsed
        private = not isinstance(source, str) and source.is_private
        return self._canonical(uuid, locale, private=private)

    def _canonical(self, uuid: str, locale: str, *, private: bool = False) -> CanonicalSource:
        return canonical_source(
            self.platform,
            urls.vacancy_url(uuid),
            external_id=uuid,
            locale=locale,
            private=private,
        )

    def extract_job(self, artifact: FetchArtifact) -> JobPosting:
        """JSON-LD ``JobPosting`` + the embedded vacancy record; the contact gate always applies."""
        raw = artifact.raw_text or ""
        if artifact.raw_json is None and artifact.is_html and raw.strip():
            url = artifact.resolved_url or artifact.requested_url
            posting = extract.extract_page(
                raw,
                url,
                platform=self.platform,
                external_id=artifact.external_id,
                locale=_locale_of(url),
                fetched_at=artifact.fetched_at,
            )
            if posting is not None:
                return posting
        # Jina markdown, an archived copy without JSON-LD, or a shape we do not know yet.
        return extract.gate_contacts(super().extract_job(artifact))

    # ---- paste ------------------------------------------------------------------------
    def parse_jobs_text(self, text: str) -> list[JobPosting]:
        return parsers.generic_jobs(text, self.platform)

    # ---- URLs the owner opens themselves ----------------------------------------------
    def search_url(self, query: JobQuery) -> str | None:
        """RocketHunt's own search page; ``None`` when the query has no text to search for."""
        if not query.text:
            return None
        return f"{SEARCH_URL}?{urlencode({'text': query.text})}"

    # ---- health -----------------------------------------------------------------------
    async def doctor(self, ctx: ConnectorContext) -> list[DoctorCheck]:
        checks = self.generic_checks(ctx)
        checks.append(
            DoctorCheck(
                name="detection",
                ok=self.detect(SAMPLE_URL) is not None,
                detail=f"/{{en,ru}}/vacancies/<uuid> on {urls.HOST}",
            )
        )
        robots = await self._robots_check(ctx)
        checks.append(robots)
        if robots.ok:
            check, body = await self._page(ctx)
            checks.append(check)
            if body is not None:
                checks += _content_checks(body)
        checks.append(
            DoctorCheck(
                name="contacts",
                ok=True,
                detail="gated (never fetched): contact keys and 'Show contacts' are ignored",
            )
        )
        return checks

    async def _robots_check(self, ctx: ConnectorContext) -> DoctorCheck:
        policy = RobotsPolicy(ctx.http, user_agent=ctx.settings.platform_user_agent)
        decision = await policy.allowed(SAMPLE_URL)
        return DoctorCheck(
            name="robots",
            ok=decision.allowed,
            detail=f"{urls.HOST}: {decision.reason} for /{urls.CANONICAL_LOCALE}/vacancies/",
            fix=None if decision.allowed else "robots.txt forbids it — use the paste path",
        )

    async def _page(self, ctx: ConnectorContext) -> tuple[DoctorCheck, str | None]:
        """One GET of the shape-only sample page: is ``public_html`` usable from here at all?"""
        if not ctx.settings.job_fetch_enable_public_html:
            return (
                DoctorCheck(
                    name="public_html",
                    ok=False,
                    detail="disabled (CAREEROS_JOB_FETCH_ENABLE_PUBLIC_HTML=false)",
                    fix="set CAREEROS_JOB_FETCH_ENABLE_PUBLIC_HTML=true",
                ),
                None,
            )
        try:
            response = await ctx.http.get(
                SAMPLE_URL, headers={"Accept": "text/html"}, follow_redirects=False
            )
        except Exception as exc:  # network shape, not a bug: doctor reports, never raises
            return (
                DoctorCheck(
                    name="public_html",
                    ok=False,
                    detail=f"{type(exc).__name__}: {exc}"[:200],
                    fix=f"check network access to {urls.BASE}",
                ),
                None,
            )
        status = response.status_code
        body = response.text if 200 <= status < 300 else None
        detail = f"GET {SAMPLE_URL} → {status}"
        if body is None:
            detail += " (the sample uuid belongs to no vacancy — the host answered)"
        return (
            DoctorCheck(name="public_html", ok=status < 500, detail=detail),
            body,
        )


def _content_checks(html: str) -> list[DoctorCheck]:
    jsonld = find_jobposting(html)
    record = extract.find_vacancy_record(html, None)
    embedded = extract.read_embedded(record) if record else {}
    original = extract.original_url(embedded)
    return [
        DoctorCheck(
            name="structured_data",
            ok=jsonld is not None,
            detail="jsonld: JobPosting present" if jsonld is not None else "jsonld: absent",
            fix=None
            if jsonld is not None
            else "page shape changed — re-check docs/platform/rockethunt.md",
        ),
        DoctorCheck(
            name="original_source",
            ok=True,
            detail=f"detected: {original}" if original else "absent (aggregated post only)",
        ),
    ]


def _locale_of(url: str | None) -> str:
    parsed = urls.parse_vacancy(url or "")
    return parsed[1] if parsed else urls.CANONICAL_LOCALE

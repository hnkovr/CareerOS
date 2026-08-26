"""JustJoin.it — read one offer behind a user-supplied URL (ADR-015 §6, plan phase C).

Chain: the board's own candidate API (``api``) → the offer page's JSON-LD ``JobPosting``
(``public_html``) → a Wayback capture (``wayback``). There is **no listing search**: ``robots.txt``
disallows ``/api/`` and the user terms (14.07.2026 §11.3) forbid automated downloading of the
service's data, so CareerOS only ever reads the single offer whose URL the user gave it — and
``client.py`` deliberately contains no listing function at all. Job discovery on JustJoin happens
in the user's own browser through ``search_url``, or by pasting a list (``parse_jobs_text``).

Everything the board keeps open (category, currency, contract type, experience level) is mapped
tolerantly in ``mapping.py``; the payload's key set is fingerprinted so schema drift shows up in
the artifact, in ``ctx.warnings`` and in the logs instead of breaking a read.
"""

from __future__ import annotations

import re
from time import perf_counter
from urllib.parse import urlencode, urlsplit

from careeros.core.logging import get_logger
from careeros.modules.opportunities.enums import FieldSource
from careeros.modules.platform import parsers
from careeros.modules.platform.base import (
    BaseConnector,
    ConnectorContext,
    NotConnected,
    PlatformError,
    UpstreamError,
)
from careeros.modules.platform.connectors.justjoin import client, mapping
from careeros.modules.platform.enums import AccessMode, AuthKind, FetchStrategy, SyncMethod
from careeros.modules.platform.fetch.artifact import FetchArtifact
from careeros.modules.platform.fetch.extract.jsonld import find_jobposting, jobposting_to_posting
from careeros.modules.platform.schemas import (
    Capabilities,
    DoctorCheck,
    FieldEvidence,
    JobPosting,
    JobQuery,
)
from careeros.modules.platform.sources import (
    CanonicalSource,
    DetectionResult,
    SourceKind,
    SourceRef,
    canonical_source,
    host_of,
    is_http_url,
)
from careeros.modules.vault.enums import Platform

log = get_logger(__name__)

HOSTS: tuple[str, ...] = ("justjoin.it", "www.justjoin.it")
#: ``/job-offer/<slug>`` is the current form; ``/offers/<slug>`` is the legacy one still in the
#: wild (same slug, same offer).
OFFER_PATH = re.compile(r"^/(?:job-offer|offers)/(?P<slug>[^/?#]+)/?$")
DETECT_CONFIDENCE = 0.95
#: Listing deep link the user opens in their own browser (path verified 2026-08-26 from the live
#: offer page's own links; the keyword parameter name is not verified — see the docs).
SEARCH_URL = "https://justjoin.it/job-offers/all-locations"
#: A slug that cannot exist: ``doctor`` classifies the endpoint's answer without reading an offer.
PROBE_SLUG = "careeros-doctor-probe-not-a-real-offer"


def offer_slug(url: str) -> str | None:
    """The offer slug of a JustJoin offer URL; ``None`` for any other URL."""
    if not is_http_url(url):
        return None
    if host_of(url) not in ("justjoin.it",):
        return None
    match = OFFER_PATH.match(urlsplit(url.strip()).path)
    return match.group("slug") if match else None


def classify_error(exc: UpstreamError) -> tuple[str, str]:
    """``UpstreamError`` → ``(error_type, detail)`` in the vocabulary of ``fetch.quality``."""
    detail = exc.detail
    low = detail.lower()
    if "non-json" in low or "expected a json object" in low:
        return "malformed", detail
    status = exc.status_code
    if status is None:
        return ("timeout" if "timeout" in low else "network"), detail
    if status in (404, 410):
        return "not_found", detail
    if status in (401, 403):
        return "forbidden", detail
    if status == 429:
        return "rate_limited", detail
    if status >= 500:
        return "upstream", detail
    return "http_error", detail


class Connector(BaseConnector):
    platform = Platform.justjoin
    detect_hosts = HOSTS

    capabilities = Capabilities(
        platform=Platform.justjoin,
        jobs=[SyncMethod.paste],
        read_job=[FetchStrategy.api, FetchStrategy.public_html, FetchStrategy.wayback],
        access=AccessMode.public,
        official_api=False,
        auth=AuthKind.none,
        notes=(
            "Read-one of a single offer by URL (candidate API detail → page JSON-LD); "
            "no listing search (robots + terms §11.3)"
        ),
    )

    # ---- detection / canonical form
    def detect(self, url: str) -> DetectionResult | None:
        slug = offer_slug(url)
        if slug is None:
            return None
        return DetectionResult(
            platform=self.platform,
            confidence=DETECT_CONFIDENCE,
            canonical=self._canonical(slug),
        )

    def canonicalize(self, source: SourceRef | str) -> CanonicalSource:
        if isinstance(source, str):
            slug = offer_slug(source)
            return self._canonical(slug) if slug else canonical_source(self.platform, source)
        locale = source.metadata.get("locale")
        locale = locale if isinstance(locale, str) else None
        if source.kind == SourceKind.provider_id:
            return self._canonical(source.value.strip(), locale=locale, private=source.is_private)
        url = source.url()
        if url is None:
            raise ValueError("justjoin: reference without a URL")
        slug = offer_slug(url)
        if slug is None:
            return canonical_source(self.platform, url, locale=locale, private=source.is_private)
        return self._canonical(slug, locale=locale, private=source.is_private)

    def _canonical(
        self, slug: str, *, locale: str | None = None, private: bool = False
    ) -> CanonicalSource:
        return CanonicalSource(
            platform=self.platform,
            external_id=slug,
            canonical_url=mapping.canonical_offer_url(slug),
            locale=locale,
            host="justjoin.it",
            private=private,
        )

    # ---- read one offer
    async def fetch_job_api(self, ctx: ConnectorContext, source: CanonicalSource) -> FetchArtifact:
        """``GET /api/candidate-api/offers/<slug>`` — one request, for one user-supplied URL."""
        started = perf_counter()
        slug = source.external_id or offer_slug(source.canonical_url)

        def artifact(**kw: object) -> FetchArtifact:
            return FetchArtifact(
                provider=self.platform,
                strategy=FetchStrategy.api,
                requested_url=client.offer_detail_url(slug) if slug else source.canonical_url,
                external_id=slug,
                fetched_at=ctx.now,
                duration_ms=int((perf_counter() - started) * 1000),
                **kw,  # type: ignore[arg-type]
            )

        if not ctx.settings.justjoin_enable_public_api:
            return artifact(
                error_type="disabled",
                error_message="CAREEROS_JUSTJOIN_ENABLE_PUBLIC_API=false",
            )
        if slug is None:
            return artifact(
                error_type="unsupported_url",
                error_message=f"not a JustJoin offer URL: {source.canonical_url}",
            )
        try:
            payload = await client.offer_detail(ctx.http, slug)
        except NotConnected as exc:
            return artifact(status_code=401, error_type="forbidden", error_message=str(exc))
        except UpstreamError as exc:
            error_type, detail = classify_error(exc)
            return artifact(
                status_code=exc.status_code, error_type=error_type, error_message=detail
            )
        self._report_drift(ctx, payload, slug)
        return artifact(
            status_code=200,
            content_type="application/json",
            resolved_url=payload.get("url") if isinstance(payload.get("url"), str) else None,
            raw_json=payload,
        )

    def _report_drift(self, ctx: ConnectorContext, payload: dict[str, object], slug: str) -> None:
        """Fingerprint the key set; a missing required key is a warning, never a failure."""
        fingerprint = mapping.schema_fingerprint(payload)
        missing = mapping.missing_required(payload)
        if not missing:
            return
        message = (
            f"justjoin: schema drift — missing keys: {', '.join(missing)} "
            f"(fingerprint {fingerprint}, baseline {mapping.BASELINE_FINGERPRINT})"
        )
        ctx.warnings.append(message)
        log.warning(
            "platform.schema_drift",
            provider=str(self.platform),
            slug=slug,
            missing=missing,
            fingerprint=fingerprint,
            baseline=mapping.BASELINE_FINGERPRINT,
        )

    def extract_job(self, artifact: FetchArtifact) -> JobPosting:
        """API payload → ``mapping``; page / archive HTML → JSON-LD, else the shared fallback."""
        url = artifact.resolved_url or artifact.requested_url
        if artifact.raw_json is not None:
            if not isinstance(artifact.raw_json, dict):
                raise ValueError("justjoin: API payload is not an object")
            return mapping.offer_to_posting(
                artifact.raw_json, url=url, fetched_at=artifact.fetched_at
            )
        raw = artifact.raw_text or ""
        if raw.strip() and not (artifact.is_markdown and not artifact.is_html):
            node = find_jobposting(raw)
            if node is not None:
                posting = jobposting_to_posting(
                    node, self.platform, url, fetched_at=artifact.fetched_at
                )
                return self._restamp(posting, artifact)
        return self._restamp(super().extract_job(artifact), artifact)

    def _restamp(self, posting: JobPosting, artifact: FetchArtifact) -> JobPosting:
        """Page reads speak with the board page's authority (an archived copy with the archive's).

        The extractor that produced them stays visible in ``raw_payload["extractor"]``.
        """
        source = str(FieldSource.archive if artifact.is_archive else FieldSource.board_page)
        evidence: list[FieldEvidence] = [
            e.model_copy(update={"source": source}) for e in posting.field_evidence
        ]
        extractor = {str(e.source) for e in posting.field_evidence} or {"text"}
        slug = offer_slug(artifact.requested_url) or offer_slug(artifact.resolved_url or "")
        raw_payload = {**(posting.raw_payload or {}), "extractor": sorted(extractor)}
        return posting.model_copy(
            update={
                "field_evidence": evidence,
                "raw_payload": raw_payload,
                "external_id": posting.external_id or slug,
            }
        )

    # ---- paste / deep link
    def parse_jobs_text(self, text: str) -> list[JobPosting]:
        return parsers.generic_jobs(text, self.platform)

    def search_url(self, query: JobQuery) -> str | None:
        text = (query.text or "").strip()
        if not text:
            return None
        return f"{SEARCH_URL}?{urlencode({'keyword': text})}"

    # ---- health
    async def doctor(self, ctx: ConnectorContext) -> list[DoctorCheck]:
        checks = self.generic_checks(ctx)
        sample = mapping.canonical_offer_url(PROBE_SLUG)
        hit = self.detect(sample)
        checks.append(
            DoctorCheck(
                name="detection",
                ok=hit is not None and hit.canonical.external_id == PROBE_SLUG,
                detail=f"{sample} → {hit.canonical.canonical_url if hit else 'not detected'}",
            )
        )
        checks.append(await self._api_check(ctx))
        checks.append(
            DoctorCheck(
                name="listing_search",
                ok=True,
                detail=(
                    "not implemented by policy — read-one only (robots.txt Disallow: /api/, "
                    "user terms §11.3, ADR-015)"
                ),
            )
        )
        checks.append(
            DoctorCheck(
                name="schema_fingerprint",
                ok=True,
                detail=(
                    f"baseline {mapping.BASELINE_FINGERPRINT} "
                    f"({len(mapping.BASELINE_KEYS)} top-level keys, fixture 2026-08-26)"
                ),
            )
        )
        return checks

    async def _api_check(self, ctx: ConnectorContext) -> DoctorCheck:
        name = "api_reachable"
        endpoint = "GET /api/candidate-api/offers/<slug>"
        if not ctx.settings.justjoin_enable_public_api:
            return DoctorCheck(
                name=name,
                ok=False,
                detail="disabled by settings",
                fix="set CAREEROS_JUSTJOIN_ENABLE_PUBLIC_API=true",
            )
        try:
            await client.offer_detail(ctx.http, PROBE_SLUG, retries=0)
        except UpstreamError as exc:
            error_type, detail = classify_error(exc)
            if error_type == "not_found":
                return DoctorCheck(
                    name=name,
                    ok=True,
                    detail=f"{endpoint} answers ({exc.status_code} for a probe slug)",
                )
            return DoctorCheck(
                name=name,
                ok=False,
                detail=f"{endpoint} → {error_type} ({exc.status_code or 'no response'})",
                fix=(
                    "check network access to https://justjoin.it and CAREEROS_PLATFORM_USER_AGENT"
                    if error_type in ("network", "timeout")
                    else f"the endpoint answered {error_type}: {detail[:120]}"
                ),
            )
        except PlatformError as exc:
            return DoctorCheck(name=name, ok=False, detail=str(exc))
        return DoctorCheck(name=name, ok=True, detail=f"{endpoint} answers (200)")

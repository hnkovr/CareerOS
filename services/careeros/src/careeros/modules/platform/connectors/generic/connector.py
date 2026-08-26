"""Generic career page / JSON-LD connector — the fallback provider for any http(s) URL.

``detect()`` answers with low confidence for every http(s) URL, so a specific connector always
wins when it recognises the host. Reads are ``public_html → jina → wayback`` (ADR-015); the
extractor is JSON-LD ``JobPosting`` first, then ``og:title`` / ``og:description``, then the
readable text through the opportunities heuristics. Nothing is searched or crawled — the paste
path is the only "list" this connector knows.
"""

from __future__ import annotations

from careeros.modules.platform import parsers
from careeros.modules.platform.base import BaseConnector
from careeros.modules.platform.enums import AccessMode, AuthKind, FetchStrategy, SyncMethod
from careeros.modules.platform.fetch.artifact import FetchArtifact
from careeros.modules.platform.fetch.extract.jsonld import find_jobposting, jobposting_to_posting
from careeros.modules.platform.fetch.extract.text import (
    html_meta,
    html_to_text,
    markdown_body,
    markdown_title,
    text_to_posting,
)
from careeros.modules.platform.schemas import Capabilities, JobPosting
from careeros.modules.platform.sources import (
    CanonicalSource,
    DetectionResult,
    SourceRef,
    canonical_source,
    is_http_url,
)
from careeros.modules.vault.enums import Platform

GENERIC_CONFIDENCE = 0.1


class Connector(BaseConnector):
    platform = Platform.website

    capabilities = Capabilities(
        platform=Platform.website,
        jobs=[SyncMethod.paste],
        read_job=[FetchStrategy.public_html, FetchStrategy.jina, FetchStrategy.wayback],
        access=AccessMode.public,
        official_api=False,
        auth=AuthKind.none,
        notes=(
            "Fallback provider for any employer/career page: one public read of the URL you "
            "give (JSON-LD JobPosting → og:* → text), Jina Reader / Wayback as fallbacks. No "
            "search, no crawling; paste a job list to import several."
        ),
    )

    # ---- read one job
    def detect(self, url: str) -> DetectionResult | None:
        if not is_http_url(url):
            return None
        try:
            canonical = self.canonicalize(url)
        except ValueError:
            return None
        return DetectionResult(
            platform=self.platform, confidence=GENERIC_CONFIDENCE, canonical=canonical
        )

    def canonicalize(self, source: SourceRef | str) -> CanonicalSource:
        if isinstance(source, str):
            return canonical_source(self.platform, source)
        url = source.url()
        if url is None:
            raise ValueError("generic: reference without a URL")
        return canonical_source(
            self.platform, url, locale=source.metadata.get("locale"), private=source.is_private
        )

    def extract_job(self, artifact: FetchArtifact) -> JobPosting:
        url = artifact.resolved_url or artifact.requested_url
        raw = artifact.raw_text or ""
        if artifact.raw_json is not None or not raw.strip():
            return super().extract_job(artifact)
        if artifact.is_markdown and not artifact.is_html:
            meta, body = markdown_body(raw)
            return text_to_posting(
                body,
                self.platform,
                url,
                title=meta.get("title") or markdown_title(body),
                fetched_at=artifact.fetched_at,
                source="jina_markdown",
            )
        jsonld = find_jobposting(raw)
        if jsonld is not None:
            return jobposting_to_posting(jsonld, self.platform, url, fetched_at=artifact.fetched_at)
        meta = html_meta(raw)
        title = meta.get("og:title") or meta.get("h1") or meta.get("title")
        text = html_to_text(raw)
        description = meta.get("og:description")
        if description and description not in text:
            text = f"{description}\n\n{text}"
        posting = text_to_posting(
            text,
            self.platform,
            url,
            title=title,
            company=meta.get("og:site_name"),
            fetched_at=artifact.fetched_at,
            source="og_meta" if title else "text_heuristic",
        )
        return posting.model_copy(update={"raw_payload": {"meta": meta}})

    # ---- paste
    def parse_jobs_text(self, text: str) -> list[JobPosting]:
        return parsers.generic_jobs(text, self.platform)

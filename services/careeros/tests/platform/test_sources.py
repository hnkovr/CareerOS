"""Source references, canonicalisation and connector-driven detection (ADR-015 §5.1)."""

from __future__ import annotations

import pytest

from careeros.modules.platform.base import BaseConnector
from careeros.modules.platform.enums import AccessMode, FetchStrategy
from careeros.modules.platform.registry import PlatformRegistry, get_registry
from careeros.modules.platform.schemas import Capabilities
from careeros.modules.platform.sources import (
    CanonicalSource,
    DetectionResult,
    SourceKind,
    SourceRef,
    canonical_source,
    detect,
    find_first_url,
    host_of,
    is_http_url,
)
from careeros.modules.vault.enums import Platform

URL = "https://www.careers.northwind.example/jobs/4711?utm_source=tg&ref=abc&lang=en"


def test_is_http_url_and_host_of() -> None:
    assert is_http_url("https://example.com/x") and is_http_url(" http://a.b/ ")
    assert not is_http_url("ftp://example.com/x")
    assert not is_http_url("example.com/x")
    assert not is_http_url("javascript:alert(1)")
    assert host_of("https://User:pw@WWW.Example.com:8443/p") == "example.com"
    assert host_of("not a url") == ""


def test_canonical_source_normalises_tracking_params_and_host() -> None:
    src = canonical_source(Platform.website, URL, locale="en")
    assert src.canonical_url == "https://careers.northwind.example/jobs/4711?lang=en"
    assert src.host == "careers.northwind.example" and src.locale == "en"
    assert src.external_id is None and src.private is False
    with pytest.raises(ValueError):
        canonical_source(Platform.website, "mailto:x@y")


def test_source_ref_privacy_and_url_lookup() -> None:
    plain = SourceRef(value=URL)
    assert plain.kind == SourceKind.url and plain.is_private is False and plain.url() == URL
    tg = SourceRef(kind=SourceKind.telegram_message, value=f"fyi {URL} looks good")
    assert tg.is_private and tg.url() == URL
    email = SourceRef(kind=SourceKind.email, value="no link here")
    assert email.is_private and email.url() is None
    child = SourceRef(kind=SourceKind.repost, value=URL, parent=email)
    assert child.is_private  # privacy is inherited through the chain
    meta = SourceRef(kind=SourceKind.text, value="pasted body", metadata={"url": URL})
    assert meta.url() == URL
    pid = SourceRef(kind=SourceKind.provider_id, value="4711", provider_hint=Platform.hh)
    assert pid.url() is None
    assert find_first_url("see https://a.example/x, then b.") == "https://a.example/x"


class Northwind(BaseConnector):
    platform = Platform.toptal  # any platform not otherwise registered in the test registry
    detect_hosts = ("northwind.example",)
    capabilities = Capabilities(
        platform=Platform.toptal, read_job=[FetchStrategy.public_html], access=AccessMode.public
    )

    def extract_job(self, artifact):  # type: ignore[override]
        raise NotImplementedError


class ById(BaseConnector):
    platform = Platform.hh
    capabilities = Capabilities(platform=Platform.hh)

    def detect(self, url: str) -> DetectionResult | None:
        if "hh.example/vacancy/" not in url:
            return None
        return DetectionResult(
            platform=self.platform, confidence=0.95, canonical=self.canonicalize(url)
        )

    def canonicalize(self, source: SourceRef | str) -> CanonicalSource:
        if isinstance(source, SourceRef) and source.kind == SourceKind.provider_id:
            return CanonicalSource(
                platform=self.platform,
                external_id=source.value,
                canonical_url=f"https://hh.example/vacancy/{source.value}",
                host="hh.example",
            )
        return super().canonicalize(source)


class Broken(BaseConnector):
    platform = Platform.indeed
    capabilities = Capabilities(platform=Platform.indeed)

    def detect(self, url: str) -> DetectionResult | None:
        raise RuntimeError("boom")


def _registry() -> PlatformRegistry:
    generic = get_registry().get(Platform.website)
    return PlatformRegistry([Broken(), ById(), Northwind(), generic])


def test_detect_prefers_the_most_confident_connector_and_falls_back_to_generic() -> None:
    reg = _registry()
    hit = detect(URL, reg)
    assert hit is not None and hit.platform == Platform.toptal and hit.confidence == 0.9
    assert hit.canonical.canonical_url == "https://careers.northwind.example/jobs/4711?lang=en"

    other = detect("https://unknown.example/careers/42?utm_medium=x", reg)
    assert other is not None and other.platform == Platform.website and other.confidence == 0.1
    assert other.canonical.canonical_url == "https://unknown.example/careers/42"

    assert detect("not a url at all", reg) is None
    assert detect(SourceRef(kind=SourceKind.email, value="no link"), reg) is None


def test_detect_marks_private_sources_and_honours_provider_hint() -> None:
    reg = _registry()
    private = detect(SourceRef(kind=SourceKind.telegram_message, value=f"look: {URL}"), reg)
    assert private is not None and private.canonical.private is True

    by_id = detect(
        SourceRef(kind=SourceKind.provider_id, value="4711", provider_hint=Platform.hh), reg
    )
    assert by_id is not None and by_id.confidence == 1.0
    assert by_id.canonical.external_id == "4711"
    assert by_id.canonical.canonical_url == "https://hh.example/vacancy/4711"

    # a hint for a connector that does not recognise the URL is ignored, not trusted
    hinted = detect(SourceRef(value=URL, provider_hint=Platform.hh), reg)
    assert hinted is not None and hinted.platform == Platform.toptal

    # a broken detector must not hide the others
    assert detect("https://hh.example/vacancy/1", reg) is not None


def test_default_registry_detects_any_http_url_through_the_generic_connector() -> None:
    hit = detect("https://careers.example.org/positions/12?fbclid=zzz", get_registry())
    assert hit is not None and hit.platform == Platform.website
    assert hit.canonical.canonical_url == "https://careers.example.org/positions/12"

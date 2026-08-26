"""Fetch layer without network: artifacts, quality, budget, robots, cache, strategies, chain."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from careeros.core.config import Settings
from careeros.modules.platform.base import ConnectorContext, UpstreamError
from careeros.modules.platform.enums import FetchStrategy, SourceRelation
from careeros.modules.platform.fetch.artifact import (
    FetchArtifact,
    JobRead,
    JobReadError,
    fingerprint_text,
)
from careeros.modules.platform.fetch.budget import FetchBudget
from careeros.modules.platform.fetch.cache import FetchCache
from careeros.modules.platform.fetch.quality import assess
from careeros.modules.platform.fetch.robots import RobotsPolicy
from careeros.modules.platform.fetch.strategies import (
    Strategy,
    build_strategies,
    run_chain,
    strategy_enabled,
)
from careeros.modules.platform.fetch.strategies.jina import JinaStrategy
from careeros.modules.platform.fetch.strategies.public_html import PublicHtmlStrategy
from careeros.modules.platform.fetch.strategies.wayback import WaybackStrategy, parse_cdx
from careeros.modules.platform.http import request_text
from careeros.modules.platform.registry import get_registry
from careeros.modules.platform.schemas import JobPosting
from careeros.modules.platform.sources import CanonicalSource, canonical_source
from careeros.modules.vault.enums import Platform

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
JOB_URL = "https://careers.northwind.example/jobs/senior-data-engineer-4711"
JOB_HOST = "careers.northwind.example"


def _fx(*parts: str) -> str:
    return (FIXTURES / Path(*parts)).read_text()


def _artifact(
    text: str | None = None,
    *,
    status: int | None = 200,
    strategy: FetchStrategy = FetchStrategy.public_html,
    content_type: str | None = "text/html; charset=utf-8",
    raw_json: Any = None,
    error_type: str | None = None,
    **kw: Any,
) -> FetchArtifact:
    return FetchArtifact(
        provider=Platform.website,
        strategy=strategy,
        requested_url=JOB_URL,
        fetched_at=NOW,
        status_code=status,
        content_type=content_type,
        raw_text=text,
        raw_json=raw_json,
        error_type=error_type,
        **kw,
    )


class Site:
    """``httpx.MockTransport`` handler for the job site, Jina and Wayback; records requests."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.page: str = _fx("generic", "jobposting.html")
        self.page_status = 200
        self.robots: str | None = _fx("generic", "robots_allow.txt")
        self.robots_status = 200
        self.jina: str = _fx("jina", "reader.md")
        self.jina_status = 200
        self.cdx: str = _fx("wayback", "cdx.json")
        self.cdx_status = 200
        self.snapshot: str = _fx("wayback", "snapshot.html")
        self.redirect_to: str | None = None
        self.raise_network = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host == "r.jina.example":
            return httpx.Response(
                self.jina_status, text=self.jina, headers={"content-type": "text/markdown"}
            )
        if host == "wayback.example":
            if path.startswith("/cdx/"):
                return httpx.Response(
                    self.cdx_status, text=self.cdx, headers={"content-type": "application/json"}
                )
            return httpx.Response(200, text=self.snapshot, headers={"content-type": "text/html"})
        if path == "/robots.txt":
            if self.robots is None:
                return httpx.Response(self.robots_status)
            return httpx.Response(self.robots_status, text=self.robots)
        if self.raise_network:
            raise httpx.ConnectError("connection refused", request=request)
        if self.redirect_to and path == "/old":
            return httpx.Response(301, headers={"location": self.redirect_to})
        return httpx.Response(
            self.page_status, text=self.page, headers={"content-type": "text/html"}
        )


def _settings(settings: Settings, **updates: Any) -> Settings:
    base = {
        "jina_reader_base": "https://r.jina.example",
        "wayback_cdx_base": "https://wayback.example",
    }
    return settings.model_copy(update={**base, **updates})


def _ctx(settings: Settings, site: Site) -> ConnectorContext:
    from careeros.modules.platform.http import build_http

    return ConnectorContext(
        settings=settings, http=build_http(settings, transport=httpx.MockTransport(site)), now=NOW
    )


def _source(url: str = JOB_URL, **kw: Any) -> CanonicalSource:
    return canonical_source(Platform.website, url, **kw)


# --------------------------------------------------------------------------- artifact


def test_artifact_forbids_headers_and_hashes_content() -> None:
    with pytest.raises(ValidationError):
        FetchArtifact(
            provider=Platform.website,
            strategy=FetchStrategy.public_html,
            requested_url=JOB_URL,
            fetched_at=NOW,
            headers={"cookie": "x"},  # type: ignore[call-arg]
        )
    a = _artifact("<html>one</html>")
    assert (
        a.ok
        and a.is_html
        and not a.is_markdown
        and a.content_hash() == _artifact("<html>one</html>").content_hash()
    )
    assert (
        _artifact(None, raw_json={"b": 1, "a": 2}).content_hash()
        == _artifact(None, raw_json={"a": 2, "b": 1}).content_hash()
    )
    assert _artifact(None, status=None).content_hash() is None
    assert not _artifact("x", status=503).ok and not _artifact("x", error_type="network").ok
    attempt = _artifact("x", status=404).to_attempt()
    assert (
        attempt.strategy == FetchStrategy.public_html
        and attempt.status_code == 404
        and not attempt.ok
    )


def test_fingerprint_ignores_volatile_numbers_and_punctuation() -> None:
    a = fingerprint_text(
        "Senior Data Engineer — 12 applicants, posted 3 days ago. Salary 25 000 PLN"
    )
    b = fingerprint_text(
        "senior data engineer — 48 applicants, posted 5 days ago; salary 27 000 PLN!"
    )
    assert a == b and a is not None and len(a) == 32
    assert fingerprint_text("") is None and fingerprint_text("12345") is None
    assert fingerprint_text("different words entirely") != a


# --------------------------------------------------------------------------- quality


@pytest.mark.parametrize(
    ("fixture", "reason"),
    [
        ("captcha.html", "captcha"),
        ("login.html", "login_wall"),
        ("not_found.html", "not_found"),
        ("shell.html", "js_shell"),
        ("closed.html", "job_closed"),
        ("search.html", "search_results"),
    ],
)
def test_quality_rejects_pages_that_only_look_like_a_job(fixture: str, reason: str) -> None:
    q = assess(_artifact(_fx("generic", fixture)))
    assert q.usable is False and q.reason == reason


def test_quality_accepts_real_postings_and_scores_completeness() -> None:
    q = assess(_artifact(_fx("generic", "jobposting.html")))
    assert q.usable and q.reason is None and q.completeness == 1.0 and q.quality == 1.0
    og = assess(_artifact(_fx("generic", "og_only.html")))
    assert og.usable and og.completeness >= 0.8 and og.quality < q.quality
    md = assess(
        _artifact(
            _fx("jina", "reader.md"), strategy=FetchStrategy.jina, content_type="text/markdown"
        )
    )
    assert md.usable and "transformed" in md.flags
    api = assess(
        _artifact(
            None,
            raw_json={"title": "DE", "description": "x" * 50, "salary": {"from": 1}},
            strategy=FetchStrategy.api,
        )
    )
    assert api.usable and api.completeness == 0.5


def test_quality_status_and_error_short_circuits() -> None:
    assert assess(_artifact("<html>x</html>", status=404)).reason == "not_found"
    assert assess(_artifact("<html>x</html>", status=500)).reason == "http_error"
    assert assess(_artifact("<html>x</html>", status=302)).reason == "http_error"
    assert assess(_artifact("", status=200)).reason == "empty"
    assert assess(_artifact("<html>x</html>", error_type="network")).reason == "network"
    assert assess(_artifact(None, raw_json=[])).reason == "empty"
    closed_json = _artifact(
        None,
        raw_json={"title": "DE", "description": "y" * 50, "isActive": False},
        strategy=FetchStrategy.api,
    )
    assert "job_closed" in assess(closed_json).flags


# --------------------------------------------------------------------------- budget


def test_budget_counts_attempts_time_and_archive_calls() -> None:
    clock = [100.0]
    b = FetchBudget(max_attempts=2, max_total_s=10.0, max_archive_calls=1, clock=lambda: clock[0])
    assert b.allows(FetchStrategy.public_html) is None and b.remaining().attempts == 2
    b.consume(FetchStrategy.wayback)
    assert b.remaining().archive_calls == 0 and b.allows(FetchStrategy.wayback) is not None
    assert b.allows(FetchStrategy.archive_today) is not None  # archive budget is shared
    assert b.allows(FetchStrategy.search_recovery) is not None  # max_search_calls=0
    b.consume(FetchStrategy.public_html)
    assert b.exhausted() and "attempts exhausted" in (b.allows(FetchStrategy.jina) or "")
    clock[0] += 11
    assert b.remaining().seconds == 0.0
    fresh = FetchBudget(max_total_s=5.0, clock=lambda: clock[0])
    fresh.start()
    clock[0] += 6
    assert "time budget" in (fresh.allows(FetchStrategy.api) or "")


def test_budget_from_settings(settings: Settings) -> None:
    b = FetchBudget.from_settings(settings.model_copy(update={"job_fetch_max_attempts": 7}))
    assert b.max_attempts == 7 and b.max_total_s == 30.0 and b.max_archive_calls == 2


# --------------------------------------------------------------------------- robots


async def test_robots_disallow_is_honoured_and_cached_per_host(settings: Settings) -> None:
    site = Site()
    site.robots = _fx("generic", "robots_disallow.txt")
    ctx = _ctx(settings, site)
    policy = RobotsPolicy(ctx.http, user_agent=settings.platform_user_agent, cache={})
    jobs = await policy.allowed(JOB_URL)
    assert jobs.allowed is False and jobs.reason == "disallow"  # CareerOS: Disallow /jobs/
    about = await policy.allowed(f"https://{JOB_HOST}/about")
    assert about.allowed is True and about.reason == "allow"
    # the CareerOS group is the most specific match, so the `*` rules do not apply to it
    assert (await policy.allowed(f"https://{JOB_HOST}/api/x")).allowed is True
    assert len([r for r in site.requests if r.url.path == "/robots.txt"]) == 1  # cached
    assert (await policy.allowed(f"https://{JOB_HOST}/jobs/public")).allowed is True  # $ anchor
    assert (await policy.allowed(f"https://{JOB_HOST}/jobs/public2")).allowed is False
    other = RobotsPolicy(ctx.http, user_agent="OtherBot/1.0 (+https://example.org)", cache={})
    # longest match wins (RFC 9309), so `Allow: /` first does not neutralise the Disallows
    assert (await other.allowed(f"https://{JOB_HOST}/api/x")).allowed is False
    assert (await other.allowed(f"https://{JOB_HOST}/en/payment/42")).allowed is False  # wildcard
    assert (await other.allowed(JOB_URL)).allowed is True
    assert (await policy.allowed("ftp://x/y")).allowed is False


async def test_robots_fails_open_without_a_file_or_on_server_errors(settings: Settings) -> None:
    site = Site()
    site.robots = None
    site.robots_status = 404
    policy = RobotsPolicy(_ctx(settings, site).http, user_agent="CareerOS/0.1", cache={})
    assert (await policy.allowed(JOB_URL)).reason == "no_robots"
    site2 = Site()
    site2.robots_status = 503
    policy2 = RobotsPolicy(_ctx(settings, site2).http, user_agent="CareerOS/0.1", cache={})
    assert (await policy2.allowed(JOB_URL)) == (await policy2.allowed(JOB_URL))
    assert (await policy2.allowed(JOB_URL)).reason == "unavailable"


async def test_robots_ttl_expires(settings: Settings) -> None:
    clock = [0.0]
    site = Site()
    policy = RobotsPolicy(
        _ctx(settings, site).http,
        user_agent="CareerOS/0.1",
        cache={},
        ttl_s=10,
        clock=lambda: clock[0],
    )
    await policy.allowed(JOB_URL)
    clock[0] = 11
    await policy.allowed(JOB_URL)
    assert len([r for r in site.requests if r.url.path == "/robots.txt"]) == 2


# --------------------------------------------------------------------------- cache


def test_cache_positive_negative_and_ttl() -> None:
    clock = [0.0]
    cache = FetchCache(ttl_s=100, negative_ttl_s=10, clock=lambda: clock[0])
    good = _artifact(_fx("generic", "jobposting.html"), usable=True, quality=0.9)
    assert cache.put(good, canonical_url=JOB_URL) is True
    hit = cache.get(Platform.website, FetchStrategy.public_html, JOB_URL)
    assert hit is not None and hit.artifact is good and not hit.negative
    assert (
        cache.get(Platform.website, FetchStrategy.jina, JOB_URL) is None
    )  # strategy is part of the key
    assert cache.get(Platform.website, FetchStrategy.public_html, JOB_URL, "ru") is None

    captcha = _artifact(_fx("generic", "captcha.html"), error_type="captcha")
    assert cache.put(captcha, canonical_url=JOB_URL + "?x=1") is False
    neg = cache.get(Platform.website, FetchStrategy.public_html, JOB_URL + "?x=1")
    assert neg is not None and neg.negative and neg.reason == "captcha" and neg.artifact is None
    clock[0] = 11
    assert cache.get(Platform.website, FetchStrategy.public_html, JOB_URL + "?x=1") is None
    assert cache.get(Platform.website, FetchStrategy.public_html, JOB_URL) is not None
    clock[0] = 101
    assert cache.get(Platform.website, FetchStrategy.public_html, JOB_URL) is None


def test_cache_never_stores_transient_failures_and_evicts() -> None:
    cache = FetchCache(max_entries=2)
    assert (
        cache.put(_artifact("x", status=503, error_type="http_error"), canonical_url="u1") is False
    )
    assert len(cache) == 0
    assert cache.put(_artifact("x", error_type="network"), canonical_url="u1") is False
    assert (
        cache.put(_artifact("x", status=404, error_type="not_found"), canonical_url="u1") is False
    )
    assert len(cache) == 1  # 404 is stable → negative entry
    cache.put(_artifact("y", usable=True), canonical_url="u2")
    cache.put(_artifact("z", usable=True), canonical_url="u3")
    assert len(cache) == 2 and cache.get(Platform.website, FetchStrategy.public_html, "u1") is None
    assert cache.invalidate("u3") == 1 and len(cache) == 1
    cache.clear()
    assert len(cache) == 0


# --------------------------------------------------------------------------- http.request_text


async def test_request_text_follows_same_host_redirects_only(settings: Settings) -> None:
    site = Site()
    site.redirect_to = f"https://{JOB_HOST}/jobs/new"
    ctx = _ctx(settings, site)
    status, _text, ctype, final = await request_text(
        ctx.http, "GET", f"https://{JOB_HOST}/old", platform=Platform.website, retries=0
    )
    assert status == 200 and final == f"https://{JOB_HOST}/jobs/new" and "html" in (ctype or "")
    assert site.requests[0].headers["accept"].startswith("text/html")
    assert "cookie" not in site.requests[-1].headers

    site.redirect_to = "https://evil.example/steal"
    status, _, _, final = await request_text(
        ctx.http, "GET", f"https://{JOB_HOST}/old", platform=Platform.website, ok=None, retries=0
    )
    assert status == 301 and final == f"https://{JOB_HOST}/old"
    with pytest.raises(UpstreamError):
        await request_text(
            ctx.http, "GET", f"https://{JOB_HOST}/old", platform=Platform.website, retries=0
        )


# --------------------------------------------------------------------------- strategies


async def test_public_html_strategy_reads_the_page(settings: Settings) -> None:
    site = Site()
    art = await PublicHtmlStrategy(retries=0).run(
        _ctx(settings, site), _source(locale="en"), FetchBudget()
    )
    assert art.status_code == 200 and art.is_html and art.strategy == FetchStrategy.public_html
    assert art.resolved_url == JOB_URL and art.fetched_at == NOW and not art.is_archive
    assert site.requests[-1].headers["accept-language"] == "en"
    site.page_status = 404
    art = await PublicHtmlStrategy(retries=0).run(_ctx(settings, site), _source(), FetchBudget())
    assert art.status_code == 404 and art.error_type is None  # the verdict is quality's job
    site.raise_network = True
    art = await PublicHtmlStrategy(retries=0).run(_ctx(settings, site), _source(), FetchBudget())
    assert art.error_type == "network" and "ConnectError" in (art.error_message or "")


async def test_jina_strategy_builds_reader_url_and_headers(settings: Settings) -> None:
    site = Site()
    s = _settings(settings, jina_api_key=SecretStr("jina-key"))
    art = await JinaStrategy(retries=0).run(_ctx(s, site), _source(), FetchBudget())
    req = site.requests[-1]
    assert str(req.url) == f"https://r.jina.example/{JOB_URL}"
    assert (
        req.headers["x-return-format"] == "markdown"
        and req.headers["authorization"] == "Bearer jina-key"
    )
    assert art.strategy == FetchStrategy.jina and art.is_markdown and "transformed" in art.flags
    assert (
        art.resolved_url == JOB_URL
        and art.raw_text is not None
        and art.raw_text.startswith("Title:")
    )
    site2 = Site()
    await JinaStrategy(retries=0).run(_ctx(_settings(settings), site2), _source(), FetchBudget())
    assert "authorization" not in site2.requests[-1].headers


async def test_wayback_strategy_uses_latest_cdx_snapshot(settings: Settings) -> None:
    site = Site()
    art = await WaybackStrategy(retries=0).run(
        _ctx(_settings(settings), site), _source(), FetchBudget()
    )
    cdx, snap = site.requests[-2], site.requests[-1]
    assert cdx.url.host == "wayback.example" and cdx.url.path == "/cdx/search/cdx"
    assert cdx.url.params["url"] == JOB_URL and cdx.url.params["filter"] == "statuscode:200"
    assert snap.url.path == f"/web/20260815093000id_/{JOB_URL}"
    assert art.is_archive and art.archive_ts == datetime(2026, 8, 15, 9, 30, tzinfo=UTC)
    assert art.resolved_url == JOB_URL and art.status_code == 200 and "archive" in art.flags
    assert parse_cdx("[]") is None and parse_cdx("not json") is None
    assert parse_cdx(json.dumps([["timestamp", "original"], ["20200101000000", "u"]])) == (
        "20200101000000",
        "u",
    )

    site.cdx = "[]"
    art = await WaybackStrategy(retries=0).run(
        _ctx(_settings(settings), site), _source(), FetchBudget()
    )
    assert art.error_type == "archive_not_found" and art.is_archive
    site.cdx_status = 500
    art = await WaybackStrategy(retries=0).run(
        _ctx(_settings(settings), site), _source(), FetchBudget()
    )
    assert art.error_type == "archive_lookup_failed" and art.status_code == 500


def test_strategy_enabled_kill_switches(settings: Settings) -> None:
    assert strategy_enabled(FetchStrategy.public_html, settings, Platform.website) == (True, None)
    off = settings.model_copy(update={"job_fetch_enable_jina": False})
    assert strategy_enabled(FetchStrategy.jina, off, Platform.website)[0] is False
    rh = settings.model_copy(update={"rockethunt_enable_public_html": False})
    assert strategy_enabled(FetchStrategy.public_html, rh, Platform.rockethunt)[0] is False
    assert strategy_enabled(FetchStrategy.public_html, rh, Platform.website)[0] is True
    jj = settings.model_copy(update={"justjoin_enable_public_api": False})
    assert strategy_enabled(FetchStrategy.api, jj, Platform.justjoin)[0] is False
    assert strategy_enabled(FetchStrategy.archive_today, settings, Platform.website)[0] is False

    generic = get_registry().get(Platform.website)
    strategies, notes = build_strategies(generic, off)
    assert [s.name for s in strategies] == [FetchStrategy.public_html, FetchStrategy.wayback]
    assert notes and "jina" in notes[0]
    only, notes = build_strategies(generic, settings, only=FetchStrategy.wayback)
    assert [s.name for s in only] == [FetchStrategy.wayback] and notes == []
    none, notes = build_strategies(generic, settings, only=FetchStrategy.api)
    assert none == [] and "not declared" in notes[0]


# --------------------------------------------------------------------------- the chain


class Canned:
    """A strategy that returns scripted artifacts and counts its calls."""

    def __init__(self, name: FetchStrategy, artifact: FetchArtifact | Exception) -> None:
        self.name = name
        self.artifact = artifact
        self.calls = 0

    async def run(
        self, ctx: ConnectorContext, source: CanonicalSource, budget: FetchBudget
    ) -> FetchArtifact:
        self.calls += 1
        if isinstance(self.artifact, Exception):
            raise self.artifact
        return self.artifact


def _extract(artifact: FetchArtifact) -> JobPosting:
    return get_registry().get(Platform.website).extract_job(artifact)


def _chain(*strategies: Strategy) -> list[Strategy]:
    return list(strategies)


async def test_chain_stops_at_the_first_usable_artifact(settings: Settings) -> None:
    first = Canned(FetchStrategy.public_html, _artifact(_fx("generic", "jobposting.html")))
    second = Canned(
        FetchStrategy.jina, _artifact(_fx("jina", "reader.md"), strategy=FetchStrategy.jina)
    )
    budget = FetchBudget()
    read = await run_chain(
        _chain(first, second), _ctx(settings, Site()), _source(), budget, extract=_extract
    )
    assert isinstance(read, JobRead) and second.calls == 0 and first.calls == 1
    assert read.posting is not None and read.posting.title == "Senior Data Engineer"
    assert (
        read.posting.strategy == FetchStrategy.public_html and read.posting.canonical_url == JOB_URL
    )
    assert read.posting.content_hash and read.posting.fingerprint and read.posting.quality == 1.0
    assert read.posting.relation == SourceRelation.primary and read.posting.fetched_at == NOW
    assert [a.strategy for a in read.attempts] == [FetchStrategy.public_html] and read.attempts[
        0
    ].ok
    assert budget.attempts_used == 1 and "public_html: 200 ok" in read.diagnostics


async def test_chain_falls_back_after_captcha_and_never_caches_it(settings: Settings) -> None:
    captcha = Canned(FetchStrategy.public_html, _artifact(_fx("generic", "captcha.html")))
    jina = Canned(
        FetchStrategy.jina,
        _artifact(
            _fx("jina", "reader.md"), strategy=FetchStrategy.jina, content_type="text/markdown"
        ),
    )
    cache = FetchCache()
    read = await run_chain(
        _chain(captcha, jina),
        _ctx(settings, Site()),
        _source(),
        FetchBudget(),
        cache,
        extract=_extract,
    )
    assert read.posting is not None and read.posting.strategy == FetchStrategy.jina
    assert [(a.strategy, a.ok, a.error_type) for a in read.attempts] == [
        (FetchStrategy.public_html, False, "captcha"),
        (FetchStrategy.jina, True, None),
    ]
    neg = cache.get(Platform.website, FetchStrategy.public_html, JOB_URL)
    assert neg is not None and neg.negative and neg.reason == "captcha"
    pos = cache.get(Platform.website, FetchStrategy.jina, JOB_URL)
    assert pos is not None and pos.artifact is not None and pos.artifact.usable
    # second read: the captcha is a negative hit, jina a positive hit — no strategy runs
    read2 = await run_chain(
        _chain(captcha, jina),
        _ctx(settings, Site()),
        _source(),
        FetchBudget(),
        cache,
        extract=_extract,
    )
    assert captcha.calls == 1 and jina.calls == 1
    assert [a.cache_status for a in read2.attempts] == ["negative", "hit"]


async def test_chain_skips_third_parties_for_private_sources(settings: Settings) -> None:
    html = Canned(FetchStrategy.public_html, _artifact(_fx("generic", "captcha.html")))
    jina = Canned(
        FetchStrategy.jina, _artifact(_fx("jina", "reader.md"), strategy=FetchStrategy.jina)
    )
    wayback = Canned(
        FetchStrategy.wayback,
        _artifact(_fx("wayback", "snapshot.html"), strategy=FetchStrategy.wayback, is_archive=True),
    )
    with pytest.raises(JobReadError) as exc:
        await run_chain(
            _chain(html, jina, wayback),
            _ctx(settings, Site()),
            _source(private=True),
            FetchBudget(),
            extract=_extract,
        )
    assert jina.calls == 0 and wayback.calls == 0
    assert [(a.strategy, a.error_type) for a in exc.value.attempts] == [
        (FetchStrategy.public_html, "captcha"),
        (FetchStrategy.jina, "private_source"),
        (FetchStrategy.wayback, "private_source"),
    ]
    assert exc.value.best_partial is not None and exc.value.best_partial.error_type == "captcha"
    assert "private" in str(exc.value) and "captcha" in exc.value.diagnostics


async def test_chain_honours_robots_budget_and_reports_diagnostics(settings: Settings) -> None:
    site = Site()
    site.robots = _fx("generic", "robots_disallow.txt")
    ctx = _ctx(settings, site)
    policy = RobotsPolicy(ctx.http, user_agent=settings.platform_user_agent, cache={})
    html = Canned(FetchStrategy.public_html, _artifact(_fx("generic", "jobposting.html")))
    boom = Canned(FetchStrategy.jina, RuntimeError("exploded"))
    wayback = Canned(
        FetchStrategy.wayback,
        _artifact(
            _fx("generic", "not_found.html"),
            status=404,
            strategy=FetchStrategy.wayback,
            is_archive=True,
        ),
    )
    budget = FetchBudget(max_attempts=1)
    with pytest.raises(JobReadError) as exc:
        await run_chain(
            _chain(html, boom, wayback), ctx, _source(), budget, None, policy, extract=_extract
        )
    assert html.calls == 0  # robots said no — never fetched
    attempts = exc.value.attempts
    assert attempts[0].error_type == "robots_disallow" and attempts[0].cache_status == "skip"
    assert attempts[1].error_type == "RuntimeError" and "exploded" in (
        attempts[1].error_message or ""
    )
    assert attempts[2].error_type == "budget" and wayback.calls == 0
    assert "robots" in exc.value.diagnostics and "attempts exhausted" in exc.value.diagnostics


async def test_chain_archive_artifact_is_marked_historical(settings: Settings) -> None:
    wayback = Canned(
        FetchStrategy.wayback,
        _artifact(
            _fx("wayback", "snapshot.html"),
            strategy=FetchStrategy.wayback,
            is_archive=True,
            archive_ts=datetime(2026, 8, 15, 9, 30, tzinfo=UTC),
        ),
    )
    read = await run_chain(
        _chain(wayback), _ctx(settings, Site()), _source(), FetchBudget(), extract=_extract
    )
    posting = read.posting
    assert (
        posting is not None
        and posting.is_archive
        and posting.relation == SourceRelation.historical_version_of
    )
    assert posting.archive_ts == datetime(2026, 8, 15, 9, 30, tzinfo=UTC)
    assert posting.published_at == datetime(
        2026, 6, 28, tzinfo=UTC
    )  # the page's own date, not the capture
    assert posting.to_ingest().raw_payload is not None
    assert posting.to_ingest().raw_payload["provenance"]["is_archive"] is True  # type: ignore[index]


async def test_chain_continues_when_extraction_fails(settings: Settings) -> None:
    good = _artifact(_fx("generic", "jobposting.html"))
    first = Canned(FetchStrategy.public_html, good)
    second = Canned(
        FetchStrategy.jina,
        _artifact(
            _fx("jina", "reader.md"), strategy=FetchStrategy.jina, content_type="text/markdown"
        ),
    )
    calls: list[FetchStrategy] = []

    def flaky(artifact: FetchArtifact) -> JobPosting:
        calls.append(artifact.strategy)
        if artifact.strategy == FetchStrategy.public_html:
            raise ValueError("no title")
        return _extract(artifact)

    read = await run_chain(
        _chain(first, second), _ctx(settings, Site()), _source(), FetchBudget(), extract=flaky
    )
    assert calls == [FetchStrategy.public_html, FetchStrategy.jina]
    assert read.attempts[0].error_type == "extract_failed" and read.attempts[1].ok
    assert "extract_failed (ValueError: no title)" in read.diagnostics

    with pytest.raises(JobReadError) as exc:
        await run_chain(
            [],
            _ctx(settings, Site()),
            _source(),
            FetchBudget(),
            extract=_extract,
            notes=["jina: disabled"],
        )
    assert (
        "no strategy available" in exc.value.diagnostics
        and "jina: disabled" in exc.value.diagnostics
    )


async def test_chain_without_extractor_returns_the_artifact(settings: Settings) -> None:
    first = Canned(FetchStrategy.public_html, _artifact(_fx("generic", "og_only.html")))
    read = await run_chain(_chain(first), _ctx(settings, Site()), _source(), FetchBudget())
    assert read.posting is None and read.artifact is not None and read.artifact.usable

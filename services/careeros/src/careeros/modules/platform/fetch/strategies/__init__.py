"""Acquisition strategies (ADR-015 §2): one interface, an ordered chain, a bounded run.

``run_chain`` walks the connector's declared strategies best-first and stops at the first
artifact ``quality.assess`` calls usable. It records every attempt (skips included), honours
the budget, the L1 cache and ``robots.txt`` before a public page read, and never hands a URL
from a private message to a third party. Nothing usable → ``JobReadError`` with diagnostics.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from time import perf_counter
from typing import TYPE_CHECKING, Any, Protocol

from careeros.core.config import Settings
from careeros.core.logging import get_logger
from careeros.modules.platform.base import ConnectorContext, PlatformError, UpstreamError
from careeros.modules.platform.enums import (
    ARCHIVE_STRATEGIES,
    THIRD_PARTY_STRATEGIES,
    FetchStrategy,
    SourceRelation,
)
from careeros.modules.platform.fetch.artifact import (
    FetchArtifact,
    JobRead,
    JobReadError,
    fingerprint_text,
    summarize_attempts,
)
from careeros.modules.platform.fetch.budget import FetchBudget
from careeros.modules.platform.fetch.cache import FetchCache
from careeros.modules.platform.fetch.quality import assess
from careeros.modules.platform.fetch.robots import RobotsPolicy
from careeros.modules.platform.schemas import FetchAttempt, JobPosting
from careeros.modules.platform.sources import CanonicalSource
from careeros.modules.vault.enums import Platform

if TYPE_CHECKING:
    from careeros.modules.platform.base import BaseConnector

log = get_logger(__name__)

Extractor = Callable[[FetchArtifact], JobPosting]


class Strategy(Protocol):
    name: FetchStrategy

    async def run(
        self, ctx: ConnectorContext, source: CanonicalSource, budget: FetchBudget
    ) -> FetchArtifact: ...


# ------------------------------------------------------------------------------ enablement


def strategy_enabled(
    name: FetchStrategy, settings: Settings, platform: Platform
) -> tuple[bool, str | None]:
    """Global and per-provider kill switches (ADR-015 §5); reserved strategies are off."""
    if name in (FetchStrategy.archive_today, FetchStrategy.search_recovery):
        return False, f"{name}: reserved, not implemented in this slice"
    global_flag = {
        FetchStrategy.public_html: settings.job_fetch_enable_public_html,
        FetchStrategy.jina: settings.job_fetch_enable_jina,
        FetchStrategy.wayback: settings.job_fetch_enable_wayback,
        FetchStrategy.api: True,
    }[name]
    if not global_flag:
        return False, f"{name}: disabled (CAREEROS_JOB_FETCH_ENABLE_{name.upper()}=false)"
    if name == FetchStrategy.public_html:
        flag = getattr(settings, f"{platform}_enable_public_html", True)
        if not flag:
            return (
                False,
                f"{name}: disabled (CAREEROS_{str(platform).upper()}_ENABLE_PUBLIC_HTML=false)",
            )
    if name == FetchStrategy.api:
        flag = getattr(settings, f"{platform}_enable_public_api", True)
        if not flag:
            return (
                False,
                f"{name}: disabled (CAREEROS_{str(platform).upper()}_ENABLE_PUBLIC_API=false)",
            )
    return True, None


def build_strategies(
    connector: BaseConnector,
    settings: Settings,
    *,
    only: FetchStrategy | None = None,
) -> tuple[list[Strategy], list[str]]:
    """Strategy objects for ``connector.capabilities.read_job`` (in order) + notes on skipped ones.

    ``only`` restricts the chain to one declared strategy (``careeros platform read --strategy``).
    """
    from careeros.modules.platform.fetch.strategies.api import ApiStrategy
    from careeros.modules.platform.fetch.strategies.jina import JinaStrategy
    from careeros.modules.platform.fetch.strategies.public_html import PublicHtmlStrategy
    from careeros.modules.platform.fetch.strategies.wayback import WaybackStrategy

    declared = list(connector.capabilities.read_job)
    notes: list[str] = []
    if only is not None:
        if only not in declared:
            notes.append(f"{only}: not declared by {connector.platform}")
            return [], notes
        declared = [only]
    out: list[Strategy] = []
    for name in declared:
        enabled, why = strategy_enabled(name, settings, connector.platform)
        if not enabled:
            notes.append(why or f"{name}: disabled")
            continue
        if name == FetchStrategy.api:
            out.append(ApiStrategy(connector))
        elif name == FetchStrategy.public_html:
            out.append(PublicHtmlStrategy())
        elif name == FetchStrategy.jina:
            out.append(JinaStrategy())
        elif name == FetchStrategy.wayback:
            out.append(WaybackStrategy())
    return out, notes


# ------------------------------------------------------------------------------ the chain


def _skip(strategy: FetchStrategy, url: str, reason: str, why: str) -> FetchAttempt:
    return FetchAttempt(
        strategy=strategy,
        url=url,
        ok=False,
        error_type=reason,
        error_message=why,
        duration_ms=0,
        cache_status="skip",
    )


def _error_artifact(
    strategy: FetchStrategy,
    source: CanonicalSource,
    ctx: ConnectorContext,
    error_type: str,
    message: str,
    *,
    status_code: int | None = None,
    duration_ms: int = 0,
) -> FetchArtifact:
    return FetchArtifact(
        provider=source.platform,
        strategy=strategy,
        requested_url=source.canonical_url,
        external_id=source.external_id,
        fetched_at=ctx.now,
        status_code=status_code,
        error_type=error_type,
        error_message=message[:300],
        duration_ms=duration_ms,
        is_archive=strategy in ARCHIVE_STRATEGIES,
    )


def stamp_provenance(
    posting: JobPosting, artifact: FetchArtifact, source: CanonicalSource
) -> JobPosting:
    """Copy what the artifact knows about itself onto the posting (ADR-016 provenance)."""
    updates: dict[str, Any] = {
        "canonical_url": source.canonical_url,
        "resolved_url": artifact.resolved_url or artifact.requested_url,
        "strategy": artifact.strategy,
        "fetched_at": artifact.fetched_at,
        "is_archive": artifact.is_archive,
        "archive_ts": artifact.archive_ts,
        "quality": artifact.quality,
        "completeness": artifact.completeness,
        "content_hash": artifact.content_hash(),
        "fingerprint": fingerprint_text(posting.raw_text or artifact.raw_text),
        "external_id": posting.external_id or source.external_id or artifact.external_id,
        "url": posting.url or source.canonical_url,
    }
    if artifact.is_archive:
        updates["relation"] = SourceRelation.historical_version_of
    return posting.model_copy(update=updates)


def _better(a: FetchArtifact | None, b: FetchArtifact) -> FetchArtifact:
    if a is None:
        return b
    return b if (b.quality or 0.0) > (a.quality or 0.0) else a


async def run_chain(
    strategies: list[Strategy],
    ctx: ConnectorContext,
    source: CanonicalSource,
    budget: FetchBudget,
    cache: FetchCache | None = None,
    policy: RobotsPolicy | None = None,
    *,
    extract: Extractor | None = None,
    notes: list[str] | None = None,
) -> JobRead:
    """Run ``strategies`` best-first; stop at the first usable artifact.

    "Usable" is ``quality.assess(artifact).usable`` — a 2xx artifact whose content passes the
    interstitial / error / shell / listing detectors and shows a title plus at least two of
    (company, description, location, salary, skills); a closed job counts only when it still
    carries most of its content. When ``extract`` is given it must also yield a ``JobPosting``;
    an extraction failure is recorded and the next strategy is tried.
    """
    attempts: list[FetchAttempt] = []
    diagnostics: list[str] = list(notes or [])
    best: FetchArtifact | None = None
    url = source.canonical_url
    budget.start()
    if not strategies:
        diagnostics.append("no strategy available")

    for strategy in strategies:
        name = strategy.name
        if source.private and name in THIRD_PARTY_STRATEGIES:
            attempts.append(_skip(name, url, "private_source", "URL came from a private message"))
            continue
        blocked = budget.allows(name)
        if blocked:
            attempts.append(_skip(name, url, "budget", blocked))
            continue

        entry = cache.get(source.platform, name, url, source.locale) if cache else None
        if entry is not None:
            if entry.negative or entry.artifact is None:
                attempts.append(
                    FetchAttempt(
                        strategy=name,
                        url=url,
                        ok=False,
                        error_type=entry.reason or "negative_cache",
                        error_message="recently failed the same way (negative cache)",
                        cache_status="negative",
                    )
                )
                log.info(
                    "platform.fetch_cache_hit",
                    provider=str(source.platform),
                    strategy=str(name),
                    negative=True,
                )
                continue
            artifact = entry.artifact.model_copy(update={"cache_status": "hit"})
            log.info(
                "platform.fetch_cache_hit",
                provider=str(source.platform),
                strategy=str(name),
                negative=False,
            )
        else:
            if name == FetchStrategy.public_html and policy is not None:
                decision = await policy.allowed(url)
                if not decision.allowed:
                    attempts.append(
                        _skip(name, url, "robots_disallow", f"robots.txt: {decision.reason}")
                    )
                    log.info(
                        "platform.fetch_robots_disallow",
                        provider=str(source.platform),
                        host=source.host,
                    )
                    continue
            budget.consume(name)
            artifact = await _run_one(strategy, ctx, source, budget)
            artifact = _assessed(artifact)
            if cache is not None:
                cache.put(artifact, canonical_url=url, locale=source.locale)

        attempts.append(artifact.to_attempt())
        log.info(
            "platform.provider_attempt",
            provider=str(source.platform),
            strategy=str(name),
            host=source.host,
            status_code=artifact.status_code,
            duration_ms=artifact.duration_ms,
            quality=artifact.quality,
            cache=artifact.cache_status,
            failure_type=artifact.error_type,
            attempt=len(attempts),
        )
        if not artifact.usable:
            best = _better(best, artifact)
            continue

        if extract is None:
            return JobRead(None, artifact, attempts, summarize_attempts(attempts))
        try:
            posting = extract(artifact)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"[:300]
            attempts[-1] = attempts[-1].model_copy(
                update={"ok": False, "error_type": "extract_failed", "error_message": message}
            )
            log.warning(
                "platform.provider_failure",
                provider=str(source.platform),
                strategy=str(name),
                error=message,
            )
            best = _better(best, artifact.model_copy(update={"usable": False}))
            continue
        posting = stamp_provenance(posting, artifact, source)
        if len(attempts) > 1:
            log.info(
                "platform.fallback_selected",
                provider=str(source.platform),
                strategy=str(name),
                attempt=len(attempts),
            )
        log.info(
            "platform.provider_success",
            provider=str(source.platform),
            strategy=str(name),
            host=source.host,
        )
        return JobRead(
            posting, artifact, attempts, "; ".join([*diagnostics, summarize_attempts(attempts)])
        )

    summary = "; ".join([*diagnostics, summarize_attempts(attempts)])
    log.info(
        "platform.provider_failure",
        provider=str(source.platform),
        host=source.host,
        diagnostics=summary,
    )
    raise JobReadError(source.platform, attempts, best, summary)


async def _run_one(
    strategy: Strategy, ctx: ConnectorContext, source: CanonicalSource, budget: FetchBudget
) -> FetchArtifact:
    started = perf_counter()
    timeout = max(0.1, budget.remaining().seconds)
    try:
        artifact = await asyncio.wait_for(strategy.run(ctx, source, budget), timeout=timeout)
    except TimeoutError:
        return _error_artifact(
            strategy.name,
            source,
            ctx,
            "timeout",
            f"exceeded {timeout:.1f}s",
            duration_ms=int((perf_counter() - started) * 1000),
        )
    except UpstreamError as exc:
        return _error_artifact(
            strategy.name,
            source,
            ctx,
            "upstream" if exc.status_code else "network",
            exc.detail,
            status_code=exc.status_code,
            duration_ms=int((perf_counter() - started) * 1000),
        )
    except PlatformError as exc:
        return _error_artifact(
            strategy.name,
            source,
            ctx,
            type(exc).__name__,
            str(exc),
            duration_ms=int((perf_counter() - started) * 1000),
        )
    except Exception as exc:
        log.warning(
            "platform.strategy_crashed",
            strategy=str(strategy.name),
            error=f"{type(exc).__name__}: {exc}",
        )
        return _error_artifact(
            strategy.name,
            source,
            ctx,
            type(exc).__name__,
            str(exc),
            duration_ms=int((perf_counter() - started) * 1000),
        )
    if artifact.duration_ms == 0:
        artifact = artifact.model_copy(
            update={"duration_ms": int((perf_counter() - started) * 1000)}
        )
    return artifact


def _assessed(artifact: FetchArtifact) -> FetchArtifact:
    q = assess(artifact)
    flags = list(dict.fromkeys([*artifact.flags, *q.flags]))
    return artifact.model_copy(
        update={
            "quality": q.quality,
            "completeness": q.completeness,
            "usable": q.usable,
            "flags": flags,
            "error_type": artifact.error_type or (None if q.usable else q.reason),
        }
    )

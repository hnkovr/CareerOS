"""``careeros platform …`` — capabilities, connect/doctor, sync profile/jobs/applications.

``--dry-run`` parses or fetches and prints without touching the database.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
import webbrowser
from collections import Counter
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import typer

from careeros.core.auth import SINGLE_USER_ID
from careeros.core.config import get_settings
from careeros.core.db import get_sessionmaker
from careeros.modules.opportunities.service import OpportunityError
from careeros.modules.platform.base import PlatformError
from careeros.modules.platform.enums import FetchStrategy, SyncKind, SyncMethod, SyncStatus
from careeros.modules.platform.fetch.artifact import JobReadError
from careeros.modules.platform.http import build_http
from careeros.modules.platform.registry import get_registry
from careeros.modules.platform.schemas import (
    FetchAttempt,
    JobQuery,
    ReadOut,
    ReadRequest,
    SyncRequest,
    SyncResult,
)
from careeros.modules.platform.sources import detect as detect_source
from careeros.modules.platform.sync import PlatformSyncService
from careeros.modules.vault.enums import Platform

app = typer.Typer(help="Platform connectors: own profile, job search, application statuses")


def _run(fn: Callable[[PlatformSyncService], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        settings = get_settings()
        async with get_sessionmaker(settings)() as session:
            svc = PlatformSyncService(settings, session=session, user_id=SINGLE_USER_ID)
            return await fn(svc)

    try:
        return asyncio.run(go())
    except JobReadError as exc:
        # A read that failed still owes the owner the diagnostics: which strategy was tried,
        # what came back, and why it was rejected (ADR-015 §4).
        print(f"error: {exc.platform}: could not read the job", file=sys.stderr)
        for attempt in exc.attempts:
            print("  " + _attempt_line(attempt), file=sys.stderr)
        if not exc.attempts:
            print(f"  {exc.diagnostics}", file=sys.stderr)
        raise typer.Exit(1) from exc
    except PlatformError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise typer.Exit(1) from exc
    except OpportunityError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise typer.Exit(1) from exc


def _print_result(res: SyncResult, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(res.model_dump(mode="json"), indent=2, ensure_ascii=False, default=str))
        return
    print(
        f"{res.platform}/{res.kind} via {res.method or '-'}: {res.status.upper()} — "
        f"seen={res.items_seen} created={res.items_created} updated={res.items_updated} "
        f"skipped={res.items_skipped}"
    )
    if res.message:
        print(f"  {res.message}")
    for item in res.preview[:50]:
        title = item.get("title") or item.get("job_title") or item.get("headline") or "?"
        extra = item.get("company") or ", ".join(item.get("skills", [])[:6]) or ""
        status_txt = item.get("status_raw") or item.get("status") or ""
        print(f"  · {title}  {extra}  {status_txt}".rstrip())


def _attempt_line(attempt: FetchAttempt) -> str:
    """``public_html  200  ok           412ms  miss`` — strategy, status, reason, duration."""
    status = str(attempt.status_code) if attempt.status_code is not None else "-"
    reason = "ok" if attempt.ok else (attempt.error_type or "unusable")
    if not attempt.ok and attempt.error_message:
        reason += f" ({attempt.error_message[:60]})"
    return (
        f"{attempt.strategy!s:14s} {status:>4s}  {reason:28s} "
        f"{attempt.duration_ms:5d}ms  {attempt.cache_status}"
    )


def _print_read(res: ReadOut, *, as_json: bool, show_attempts: bool) -> None:
    if as_json:
        print(json.dumps(res.model_dump(mode="json"), indent=2, ensure_ascii=False, default=str))
        return
    posting = res.posting
    if posting is not None:
        where = f"{posting.platform} via {posting.strategy or '-'}"
        print(f"{posting.title} @ {posting.company or '?'}  ({where})")
        print(f"  url: {posting.canonical_url or posting.url or '-'}")
        if posting.external_id:
            print(f"  id:  {posting.external_id}")
        if posting.quality is not None:
            print(
                f"  quality: {posting.quality:.2f}  completeness: {posting.completeness or 0:.2f}"
            )
    if res.opportunity_id is None:
        print("  not stored (dry run)")
    elif res.created:
        print(f"  created opportunity {res.opportunity_id}")
    else:
        snap = "new snapshot" if res.snapshot_created else "unchanged since the last read"
        print(f"  already known: {res.opportunity_id} — {snap}")
    if res.closed:
        print("  the posting reads as CLOSED — recorded as evidence")
    for warning in res.warnings:
        print(f"  warning: {warning}")
    if show_attempts and res.attempts:
        print("attempts:")
        for attempt in res.attempts:
            print("  " + _attempt_line(attempt))


def parse_extra(pairs: list[str] | None) -> dict[str, Any]:
    """``["area=1", "full=true"]`` → ``{"area": "1", "full": True}`` (true/false become bools)."""
    out: dict[str, Any] = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise typer.BadParameter(f"--extra expects key=value, got {pair!r}")
        low = value.strip().lower()
        out[key.strip()] = True if low == "true" else False if low == "false" else value.strip()
    return out


def _text_from(text_file: Path | None) -> str | None:
    if text_file is None:
        return None
    if str(text_file) == "-":
        return sys.stdin.read()
    return text_file.read_text(encoding="utf-8")


def _sync_cmd(
    platform: Platform,
    kind: SyncKind,
    *,
    text_file: Path | None,
    export: Path | None,
    api: bool,
    use_ai: bool,
    dry_run: bool,
    as_json: bool,
    query: JobQuery | None = None,
) -> None:
    method = (
        SyncMethod.api
        if api
        else SyncMethod.export
        if export
        else SyncMethod.paste
        if text_file
        else None
    )
    req = SyncRequest(
        method=method,
        text=_text_from(text_file),
        file_path=str(export) if export else None,
        query=query,
        use_ai=use_ai,
        dry_run=dry_run,
    )
    res = _run(lambda svc: svc.sync(platform, kind, req))
    _print_result(res, as_json=as_json)


# ------------------------------------------------------------------------- matrix / connections


@app.command()
def capabilities(as_json: bool = typer.Option(False, "--json")) -> None:
    """Show the capabilities matrix (which method each platform supports per capability)."""
    caps = get_registry().capabilities()
    if as_json:
        print(json.dumps([c.model_dump(mode="json") for c in caps], indent=2))
        return
    print(f"{'platform':10s} {'profile':14s} {'jobs':14s} {'applications':14s} auth    notes")
    for c in caps:
        cell = lambda ms: "/".join(str(m) for m in ms) or "-"  # noqa: E731
        print(
            f"{c.platform!s:10s} {cell(c.profile):14s} {cell(c.jobs):14s} "
            f"{cell(c.applications):14s} {c.auth!s:7s} {c.notes}"
        )


@app.command()
def connections(as_json: bool = typer.Option(False, "--json")) -> None:
    """Connection state per platform (tokens present? account? last sync?)."""
    conns = _run(lambda svc: svc.platform.list_connections())
    if as_json:
        print(json.dumps([c.model_dump(mode="json") for c in conns], indent=2, default=str))
        return
    for c in conns:
        acct = c.account_label or c.account_id or "-"
        last = c.last_sync_at.isoformat(timespec="minutes") if c.last_sync_at else "never"
        print(
            f"{c.platform!s:10s} {c.status!s:13s} tokens={c.has_tokens!s:5s} {acct}  "
            f"last sync: {last}"
        )
        if c.last_error:
            print(f"           last error: {c.last_error}")


@app.command()
def connect(
    platform: Platform,
    open_browser: bool = typer.Option(False, "--open", help="open the authorize URL"),
    code: str | None = typer.Option(None, "--code", help="code or full redirect URL (skip prompt)"),
) -> None:
    """OAuth connect: prints the authorize URL, then exchanges the code you paste back."""
    start = _run(lambda svc: svc.platform.oauth_start(platform))
    print(f"1. Open in YOUR browser and approve:\n\n   {start.authorize_url}\n")
    print(f"2. After approval you land on {start.redirect_uri}?code=…")
    print("   Paste the code (or the whole URL) here.\n")
    if open_browser:
        webbrowser.open(start.authorize_url)
    supplied = code or typer.prompt("code")
    if supplied.startswith("http"):
        supplied = parse_qs(urlparse(supplied).query).get("code", [""])[0]
    if not supplied:
        print("error: no code supplied", file=sys.stderr)
        raise typer.Exit(1)

    async def finish(svc: PlatformSyncService) -> Any:
        async with build_http(svc.settings) as http:
            return await svc.platform.oauth_callback(platform, supplied, start.state, http=http)

    conn = _run(finish)
    print(f"connected: {conn.platform} as {conn.account_label or conn.account_id or '?'}")


@app.command()
def refresh(
    target: str = typer.Argument(
        ..., help="a platform (refresh its OAuth token) or an opportunity id (re-read the job)"
    ),
    as_json: bool = typer.Option(False, "--json"),
    show_attempts: bool = typer.Option(False, "--show-attempts"),
) -> None:
    """Refresh an access token (platform name), or re-read a stored job (opportunity id).

    One verb, two objects, told apart by the argument's shape: a uuid is a job, anything else
    must name a platform. Both are "fetch the current truth again".
    """
    try:
        opportunity_id = uuid.UUID(target)
    except ValueError:
        opportunity_id = None
    if opportunity_id is not None:
        out = _run(lambda svc: svc.refresh_job(opportunity_id))
        _print_read(out, as_json=as_json, show_attempts=show_attempts)
        return
    try:
        platform = Platform(target)
    except ValueError as exc:
        known = ", ".join(str(p) for p in get_registry().platforms())
        print(
            f"error: {target!r} is neither an opportunity id nor a platform ({known})",
            file=sys.stderr,
        )
        raise typer.Exit(1) from exc

    async def go(svc: PlatformSyncService) -> Any:
        async with build_http(svc.settings) as http:
            return await svc.platform.refresh(platform, http=http)

    conn = _run(go)
    print(f"{conn.platform}: {conn.status}, expires {conn.token_expires_at}")


@app.command()
def disconnect(platform: Platform) -> None:
    """Delete stored tokens and mark the platform disconnected."""
    conn = _run(lambda svc: svc.platform.disconnect(platform))
    print(f"{conn.platform}: {conn.status}")


@app.command()
def doctor(platform: Platform, as_json: bool = typer.Option(False, "--json")) -> None:
    """Configuration, token and (for API platforms) live reachability checks."""

    async def go(svc: PlatformSyncService) -> Any:
        async with build_http(svc.settings) as http:
            return await svc.platform.doctor(platform, http=http)

    checks = _run(go)
    if as_json:
        print(json.dumps([c.model_dump(mode="json") for c in checks], indent=2))
    else:
        for c in checks:
            mark = "OK " if c.ok else "FAIL"
            print(f"[{mark}] {c.name}: {c.detail}" + (f"  → {c.fix}" if c.fix else ""))
    if not all(c.ok for c in checks):
        raise typer.Exit(1)


# ------------------------------------------------------------------------- read one job (ADR-015)


@app.command()
def read(
    url: str = typer.Argument(..., help="the job URL you found"),
    dry_run: bool = typer.Option(False, "--dry-run", help="fetch and extract, persist nothing"),
    as_json: bool = typer.Option(False, "--json"),
    show_attempts: bool = typer.Option(False, "--show-attempts", help="list every strategy tried"),
    no_cache: bool = typer.Option(False, "--no-cache", help="bypass the in-process fetch cache"),
    strategy: FetchStrategy | None = typer.Option(None, "--strategy", help="force one strategy"),
    use_ai: bool = typer.Option(False, "--use-ai", help="AI extraction to fill the gaps"),
    notes: str | None = typer.Option(None, "--notes"),
    platform: Platform | None = typer.Option(None, "--platform", help="skip provider detection"),
) -> None:
    """Read ONE job behind a URL and file it (ADR-015): new opportunity or a fresh snapshot."""
    req = ReadRequest(
        url=url,
        dry_run=dry_run,
        use_ai=use_ai,
        no_cache=no_cache,
        strategy=strategy,
        notes=notes,
        platform=platform,
    )
    out = _run(lambda svc: svc.read_job(req))
    _print_read(out, as_json=as_json, show_attempts=show_attempts or dry_run)


@app.command()
def detect(
    url: str = typer.Argument(..., help="a job URL"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Which provider owns this URL, and what its canonical form is. No network, no writes."""
    registry = get_registry()
    hit = detect_source(url, registry)
    if hit is None:
        print(f"no connector recognises {url!r}", file=sys.stderr)
        raise typer.Exit(1)
    canonical = hit.canonical
    if as_json:
        print(
            json.dumps(
                {
                    "platform": str(hit.platform),
                    "confidence": hit.confidence,
                    "canonical_url": canonical.canonical_url,
                    "external_id": canonical.external_id,
                    "host": canonical.host,
                    "locale": canonical.locale,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    print(f"{hit.platform} (confidence {hit.confidence:.2f})")
    print(f"  canonical: {canonical.canonical_url}")
    print(f"  host:      {canonical.host}")
    if canonical.external_id:
        print(f"  id:        {canonical.external_id}")
    caps = registry.get(hit.platform).capabilities
    chain = " → ".join(str(x) for x in caps.read_job) or "none (paste only)"
    print(f"  read via:  {chain}  (access: {caps.access})")


# ------------------------------------------------------------------------- sync commands


@app.command()
def profile(
    platform: Platform,
    text_file: Path | None = typer.Option(
        None, "--text-file", help="pasted profile text ('-' = stdin)"
    ),
    export: Path | None = typer.Option(None, "--export", help="export file/dir/zip"),
    api: bool = typer.Option(False, "--api", help="force the official API"),
    use_ai: bool = typer.Option(False, "--use-ai"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Read your own profile → profile snapshot (auditable with `careeros profiles`)."""
    _sync_cmd(
        platform,
        SyncKind.profile,
        text_file=text_file,
        export=export,
        api=api,
        use_ai=use_ai,
        dry_run=dry_run,
        as_json=as_json,
    )


@app.command()
def jobs(
    platform: Platform,
    query: str | None = typer.Option(None, "--query", "-q", help="search text (API method)"),
    location: str | None = typer.Option(None, "--location"),
    remote: bool | None = typer.Option(None, "--remote/--no-remote"),
    limit: int = typer.Option(30, "--limit"),
    text_file: Path | None = typer.Option(
        None, "--text-file", help="pasted job list ('-' = stdin)"
    ),
    export: Path | None = typer.Option(None, "--export"),
    api: bool = typer.Option(False, "--api"),
    use_ai: bool = typer.Option(False, "--use-ai"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    as_json: bool = typer.Option(False, "--json"),
    extra: list[str] | None = typer.Option(
        None, "--extra", help="platform-specific knob key=value (hh: area=1; upwork: title=…)"
    ),
) -> None:
    """Search jobs → opportunities (dedup + scoring happen in the opportunities module)."""
    q = JobQuery(
        text=query, location=location, remote=remote, limit=limit, extra=parse_extra(extra)
    )
    _sync_cmd(
        platform,
        SyncKind.jobs,
        text_file=text_file,
        export=export,
        api=api or bool(query),
        use_ai=use_ai,
        dry_run=dry_run,
        as_json=as_json,
        query=q,
    )


@app.command()
def applications(
    platform: Platform,
    text_file: Path | None = typer.Option(
        None, "--text-file", help="pasted applications list ('-' = stdin)"
    ),
    export: Path | None = typer.Option(None, "--export"),
    api: bool = typer.Option(False, "--api"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Check application statuses → application observations (status history kept)."""
    _sync_cmd(
        platform,
        SyncKind.applications,
        text_file=text_file,
        export=export,
        api=api,
        use_ai=False,
        dry_run=dry_run,
        as_json=as_json,
    )


@app.command()
def sync(
    platform: str = typer.Argument("all", help="platform or 'all'"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    use_ai: bool = typer.Option(False, "--use-ai"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Run every API-backed capability of connected platforms (paste-only ones are skipped)."""
    target = None if platform == "all" else Platform(platform)
    results = _run(lambda svc: svc.sync_all(target, dry_run=dry_run, use_ai=use_ai))
    if as_json:
        print(json.dumps([r.model_dump(mode="json") for r in results], indent=2, default=str))
        return
    for r in results:
        _print_result(r, as_json=False)
    tally = Counter(r.status for r in results)
    print(
        "— "
        + ", ".join(f"{tally[s]} {s}" for s in SyncStatus if tally[s])
        + f" ({len(results)} capabilities)"
    )
    if tally[SyncStatus.failed]:
        raise typer.Exit(1)


@app.command("status")
def observations(
    platform: Platform | None = typer.Option(None, "--platform"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List observed application statuses (newest first)."""
    rows = _run(lambda svc: svc.platform.list_observations(platform=platform))
    if as_json:
        print(json.dumps([r.model_dump(mode="json") for r in rows], indent=2, default=str))
        return
    for r in rows:
        when = r.applied_at.date().isoformat() if r.applied_at else "-"
        print(
            f"{r.platform!s:10s} {r.status!s:10s} {when}  {r.job_title} @ {r.company or '?'}  "
            f"({r.status_raw})"
        )

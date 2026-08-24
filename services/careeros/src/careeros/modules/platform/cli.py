"""``careeros platform …`` — capabilities, connect/doctor, sync profile/jobs/applications.

``--dry-run`` parses or fetches and prints without touching the database.
"""

from __future__ import annotations

import asyncio
import json
import sys
import webbrowser
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import typer

from careeros.core.auth import SINGLE_USER_ID
from careeros.core.config import get_settings
from careeros.core.db import get_sessionmaker
from careeros.modules.platform.base import PlatformError
from careeros.modules.platform.enums import SyncKind, SyncMethod, SyncStatus
from careeros.modules.platform.http import build_http
from careeros.modules.platform.registry import get_registry
from careeros.modules.platform.schemas import JobQuery, SyncRequest, SyncResult
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
    except PlatformError as exc:
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
def refresh(platform: Platform) -> None:
    """Refresh the access token using the stored refresh token."""

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
) -> None:
    """Search jobs → opportunities (dedup + scoring happen in the opportunities module)."""
    q = JobQuery(text=query, location=location, remote=remote, limit=limit)
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
    failed = [r for r in results if r.status == SyncStatus.failed]
    if failed:
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

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from careeros.core.config import Settings
from careeros.modules.platform.base import NotConnected, UpstreamError
from careeros.modules.platform.http import build_http, request_json
from careeros.modules.platform.oauth import authorize_url, exchange_code, refresh_tokens
from careeros.modules.platform.schemas import OAuthConfig
from careeros.modules.platform.tokens import (
    FileTokenStore,
    OAuthTokens,
    client_credentials,
    env_tokens,
    resolve_tokens,
)
from careeros.modules.vault.enums import Platform

# --------------------------------------------------------------------------- token store


def test_file_token_store_round_trip_and_permissions(tmp_path: Path) -> None:
    store = FileTokenStore(tmp_path / "nested" / "tokens.json")
    assert store.load(Platform.hh) is None and store.platforms() == []
    tokens = OAuthTokens(
        access_token="s3cr3t-access",  # type: ignore[arg-type]
        refresh_token="s3cr3t-refresh",  # type: ignore[arg-type]
        expires_at=datetime(2026, 9, 1, tzinfo=UTC),
        scope="all",
    )
    store.save(Platform.hh, tokens)
    mode = stat.S_IMODE(store.path.stat().st_mode)
    assert mode == 0o600
    loaded = store.load(Platform.hh)
    assert loaded is not None
    assert loaded.access_token.get_secret_value() == "s3cr3t-access"
    assert loaded.refresh_token is not None
    assert loaded.refresh_token.get_secret_value() == "s3cr3t-refresh"
    assert loaded.expires_at == tokens.expires_at and loaded.scope == "all"
    assert store.platforms() == [Platform.hh]
    assert "s3cr3t-access" in store.path.read_text()  # verbatim, protected by file mode
    assert "***" in str(loaded.redacted()) and "s3cr3t" not in str(loaded.redacted())
    store.delete(Platform.hh)
    assert store.load(Platform.hh) is None


def test_file_token_store_tolerates_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    path.write_text("{not json")
    assert FileTokenStore(path).load(Platform.hh) is None


def test_env_tokens_override_store(tmp_path: Path) -> None:
    settings = Settings(hh_access_token="env-acc")  # type: ignore[arg-type]
    store = FileTokenStore(tmp_path / "t.json")
    store.save(Platform.hh, OAuthTokens(access_token="file-acc"))  # type: ignore[arg-type]
    from_env = resolve_tokens(settings, store, Platform.hh)
    assert from_env is not None and from_env.access_token.get_secret_value() == "env-acc"
    assert env_tokens(Settings(), Platform.hh) is None
    from_file = resolve_tokens(Settings(), store, Platform.hh)
    assert from_file is not None and from_file.access_token.get_secret_value() == "file-acc"
    assert resolve_tokens(Settings(), store, Platform.upwork) is None


def test_client_credentials_require_both_values() -> None:
    assert client_credentials(Settings(), Platform.hh) is None
    assert client_credentials(Settings(hh_client_id="id"), Platform.hh) is None
    creds = client_credentials(Settings(hh_client_id="id", hh_client_secret="s"), Platform.hh)  # type: ignore[arg-type]
    assert creds is not None and creds[0] == "id" and creds[1].get_secret_value() == "s"
    assert json.dumps(Settings(hh_client_secret="s").redacted_dump()["hh_client_secret"]) == '"***"'  # type: ignore[arg-type]


def test_tokens_expiry() -> None:
    now = datetime(2026, 8, 25, tzinfo=UTC)
    assert not OAuthTokens(access_token="a").is_expired(now)  # type: ignore[arg-type]
    soon = OAuthTokens(access_token="a", expires_at=now + timedelta(seconds=30))  # type: ignore[arg-type]
    assert soon.is_expired(now)
    later = OAuthTokens(access_token="a", expires_at=now + timedelta(hours=1))  # type: ignore[arg-type]
    assert not later.is_expired(now)


# --------------------------------------------------------------------------- oauth

CFG = OAuthConfig(
    authorize_url="https://hh.ru/oauth/authorize",
    token_url="https://api.hh.ru/token",
    client_id="cid",
    client_secret="csecret",  # type: ignore[arg-type]
    redirect_uri="http://localhost:8000/api/platform/oauth/hh/callback",
)


def test_authorize_url_contains_required_params() -> None:
    url = authorize_url(CFG, "st4te")
    assert url.startswith("https://hh.ru/oauth/authorize?")
    assert "response_type=code" in url and "client_id=cid" in url and "state=st4te" in url
    assert (
        "redirect_uri=http%3A%2F%2Flocalhost%3A8000%2Fapi%2Fplatform%2Foauth%2Fhh%2Fcallback" in url
    )


async def test_exchange_code_and_refresh() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL(CFG.token_url)
        form = dict(httpx.QueryParams(request.content.decode()))
        seen.append(form)
        if form["grant_type"] == "authorization_code":
            return httpx.Response(
                200,
                json={
                    "access_token": "acc1",
                    "refresh_token": "ref1",
                    "expires_in": 1209600,
                    "token_type": "bearer",
                },
            )
        return httpx.Response(200, json={"access_token": "acc2", "expires_in": 1209600})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        tokens = await exchange_code(http, Platform.hh, CFG, "the-code")
        assert tokens.access_token.get_secret_value() == "acc1"
        assert tokens.expires_at is not None
        assert timedelta(days=13) < tokens.expires_at - datetime.now(UTC) <= timedelta(days=14)
        fresh = await refresh_tokens(http, Platform.hh, CFG, tokens)
    assert seen[0]["code"] == "the-code" and seen[0]["client_secret"] == "csecret"
    assert seen[1] == {
        "grant_type": "refresh_token",
        "refresh_token": "ref1",
        "client_id": "cid",
        "client_secret": "csecret",
    }
    assert fresh.access_token.get_secret_value() == "acc2"
    assert fresh.refresh_token is not None and fresh.refresh_token.get_secret_value() == "ref1"


async def test_exchange_code_upstream_error_and_refresh_without_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(UpstreamError) as exc:
            await exchange_code(http, Platform.hh, CFG, "bad")
        assert exc.value.status_code == 400 and "invalid_grant" in exc.value.detail
        with pytest.raises(NotConnected):
            await refresh_tokens(http, Platform.hh, CFG, OAuthTokens(access_token="a"))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- http


async def test_request_json_retries_then_succeeds_and_sends_user_agent() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.headers["user-agent"].startswith("CareerOS/")
        if calls == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json={"ok": True})

    async with build_http(Settings(), transport=httpx.MockTransport(handler)) as client:
        data = await request_json(
            client, "GET", "https://api.example/x", platform=Platform.hh, backoff_s=0
        )
    assert data == {"ok": True} and calls == 2


async def test_request_json_error_mapping() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        code = int(request.url.path.strip("/"))
        if code == 200:
            return httpx.Response(200, content=b"not json")
        return httpx.Response(code, text="boom")

    async with build_http(Settings(), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(NotConnected):
            await request_json(client, "GET", "https://api.example/401", platform=Platform.hh)
        with pytest.raises(UpstreamError) as exc:
            await request_json(client, "GET", "https://api.example/500", platform=Platform.hh)
        assert exc.value.status_code == 500 and exc.value.detail == "boom"
        with pytest.raises(UpstreamError):
            await request_json(client, "GET", "https://api.example/200", platform=Platform.hh)
        assert (
            await request_json(
                client, "GET", "https://api.example/204", platform=Platform.hh, ok=(204,)
            )
            is None
        )


def test_env_tokens_are_marked_pinned_and_store_tokens_are_not(tmp_path: Path) -> None:
    from careeros.modules.platform.oauth import parse_token_response

    env = env_tokens(Settings(hh_access_token="e"), Platform.hh)  # type: ignore[arg-type]
    assert env is not None and env.pinned and env.redacted()["source"] == "env"
    store = FileTokenStore(tmp_path / "t.json")
    store.save(Platform.hh, OAuthTokens(access_token="f"))  # type: ignore[arg-type]
    loaded = store.load(Platform.hh)
    assert loaded is not None and not loaded.pinned
    # providers that return expires_in as a string still get an expiry
    tokens = parse_token_response(Platform.hh, {"access_token": "a", "expires_in": "3600"})
    assert tokens.expires_at is not None
    assert (
        parse_token_response(Platform.hh, {"access_token": "a", "expires_in": "n/a"}).expires_at
        is None
    )

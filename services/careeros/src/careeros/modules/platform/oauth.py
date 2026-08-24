"""Generic OAuth2 authorization-code helper shared by API connectors (hh.ru, Upwork)."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from careeros.core.logging import get_logger
from careeros.modules.platform.base import NotConnected, UpstreamError
from careeros.modules.platform.schemas import OAuthConfig
from careeros.modules.platform.tokens import OAuthTokens
from careeros.modules.vault.enums import Platform

log = get_logger(__name__)


def new_state() -> str:
    return secrets.token_urlsafe(24)


def authorize_url(cfg: OAuthConfig, state: str) -> str:
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri,
        "state": state,
    }
    if cfg.scopes:
        params["scope"] = " ".join(cfg.scopes)
    params.update(cfg.extra_authorize_params)
    sep = "&" if "?" in cfg.authorize_url else "?"
    return f"{cfg.authorize_url}{sep}{urlencode(params)}"


def parse_token_response(platform: Platform, data: dict[str, Any]) -> OAuthTokens:
    access = data.get("access_token")
    if not access:
        raise UpstreamError(platform, None, "token response without access_token")
    expires_at: datetime | None = None
    expires_in = data.get("expires_in")
    try:
        seconds = float(expires_in) if expires_in is not None else 0.0
    except (TypeError, ValueError):
        seconds = 0.0
    if seconds > 0:
        expires_at = datetime.now(UTC) + timedelta(seconds=seconds)
    return OAuthTokens(
        access_token=access,
        refresh_token=data.get("refresh_token") or None,
        token_type=str(data.get("token_type") or "bearer"),
        expires_at=expires_at,
        scope=data.get("scope") or None,
    )


async def _token_request(
    http: httpx.AsyncClient, platform: Platform, cfg: OAuthConfig, form: dict[str, str]
) -> OAuthTokens:
    kwargs: dict[str, Any] = {"data": form, "headers": {"Accept": "application/json"}}
    if cfg.token_auth == "basic":
        kwargs["auth"] = httpx.BasicAuth(cfg.client_id, cfg.client_secret.get_secret_value())
    else:
        kwargs["data"] = {
            **form,
            "client_id": cfg.client_id,
            "client_secret": cfg.client_secret.get_secret_value(),
        }
    try:
        resp = await http.post(cfg.token_url, **kwargs)
    except httpx.HTTPError as exc:
        raise UpstreamError(platform, None, f"{type(exc).__name__}: {exc}") from exc
    if resp.status_code != 200:
        raise UpstreamError(platform, resp.status_code, resp.text[:300])
    try:
        data = resp.json()
    except ValueError as exc:
        raise UpstreamError(platform, resp.status_code, "non-JSON token response") from exc
    tokens = parse_token_response(platform, data)
    log.info("platform.oauth_tokens", platform=str(platform), grant=form.get("grant_type"))
    return tokens


async def exchange_code(
    http: httpx.AsyncClient, platform: Platform, cfg: OAuthConfig, code: str
) -> OAuthTokens:
    return await _token_request(
        http,
        platform,
        cfg,
        {"grant_type": "authorization_code", "code": code, "redirect_uri": cfg.redirect_uri},
    )


async def refresh_tokens(
    http: httpx.AsyncClient, platform: Platform, cfg: OAuthConfig, tokens: OAuthTokens
) -> OAuthTokens:
    if tokens.refresh_token is None:
        raise NotConnected(platform, "no refresh token — reconnect")
    fresh = await _token_request(
        http,
        platform,
        cfg,
        {"grant_type": "refresh_token", "refresh_token": tokens.refresh_token.get_secret_value()},
    )
    if fresh.refresh_token is None:  # some providers rotate only the access token
        fresh = fresh.model_copy(update={"refresh_token": tokens.refresh_token})
    return fresh

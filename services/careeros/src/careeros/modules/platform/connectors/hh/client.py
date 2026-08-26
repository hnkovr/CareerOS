"""hh.ru REST client over the official JSON API (https://api.hh.ru/openapi/redoc).

ADR-005: official endpoints only — no HTML, no cookies, no passwords. Every request carries
``HH-User-Agent`` and ``User-Agent`` (hh answers ``400 bad_user_agent`` without them) plus the
user's Bearer token when the connector passes ``auth``. ``platform.http.request_json`` already maps
401 → ``NotConnected``; hh reports token problems as ``403 {"errors":[{"type":"oauth", …}]}``,
which is mapped here as well so callers can branch on ``NotConnected`` uniformly.

Two methods deliberately step outside that mapping: ``vacancy_raw`` (the read-one path needs the
status code, not an exception) and ``app_token`` (an application token for public reads).
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from pydantic import SecretStr

from careeros.modules.platform.base import NotConnected, UpstreamError
from careeros.modules.platform.http import request_json, request_text
from careeros.modules.platform.oauth import parse_token_response
from careeros.modules.platform.tokens import OAuthTokens
from careeros.modules.vault.enums import Platform

BASE_URL = "https://api.hh.ru"
MAX_PER_PAGE = 100  # GET /vacancies: per_page ≤ 100, search depth ≤ 2000 items (OpenAPI)
NEGOTIATIONS_PER_PAGE = 50
MAX_NEGOTIATION_PAGES = 5
MAX_DETAIL_FETCHES = 20

_OAUTH_VALUE = re.compile(r'"value"\s*:\s*"([a-z_]+)"')


def _expect_dict(data: Any, what: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise UpstreamError(Platform.hh, None, f"{what}: expected a JSON object")
    return data


def _expect_items(data: Any, what: str) -> list[dict[str, Any]]:
    items = _expect_dict(data, what).get("items")
    if not isinstance(items, list):
        raise UpstreamError(Platform.hh, None, f"{what}: missing items[]")
    return [i for i in items if isinstance(i, dict)]


class HHClient:
    """Thin wrapper: one method per endpoint the connector uses, nothing else."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        user_agent: str,
        auth: dict[str, str] | None = None,
        base_url: str = BASE_URL,
    ) -> None:
        self._http = http
        self._user_agent = user_agent
        self._auth = dict(auth or {})
        self._base = base_url.rstrip("/")

    @property
    def authenticated(self) -> bool:
        return "Authorization" in self._auth

    def headers(self) -> dict[str, str]:
        return {"HH-User-Agent": self._user_agent, "User-Agent": self._user_agent, **self._auth}

    async def get(
        self, path: str, *, params: dict[str, Any] | None = None, retries: int = 2
    ) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            return await request_json(
                self._http,
                "GET",
                f"{self._base}{path}",
                platform=Platform.hh,
                headers=self.headers(),
                params=clean,
                retries=retries,
            )
        except UpstreamError as exc:
            if exc.status_code == 403 and '"oauth"' in exc.detail:
                m = _OAUTH_VALUE.search(exc.detail)
                value = m.group(1) if m else "bad_authorization"
                raise NotConnected(
                    Platform.hh,
                    f"token rejected (403 oauth/{value}) — careeros platform refresh hh, "
                    "or connect again",
                ) from exc
            raise

    # ---- current user / resumes
    async def me(self) -> dict[str, Any]:
        return _expect_dict(await self.get("/me"), "GET /me")

    async def resumes_mine(self) -> list[dict[str, Any]]:
        # VERIFY LIVE: /resumes/mine is the ``resumes_url`` of GET /me and documented in the legacy
        # docs, but it is not listed as a path in the public OpenAPI spec.
        return _expect_items(await self.get("/resumes/mine"), "GET /resumes/mine")

    async def resume(self, resume_id: str) -> dict[str, Any]:
        return _expect_dict(await self.get(f"/resumes/{resume_id}"), "GET /resumes/{id}")

    # ---- vacancies (public; token optional)
    async def vacancies(self, params: dict[str, Any], *, retries: int = 2) -> dict[str, Any]:
        data = await self.get("/vacancies", params=params, retries=retries)
        return _expect_dict(data, "GET /vacancies")

    async def vacancy(self, vacancy_id: str) -> dict[str, Any]:
        return _expect_dict(await self.get(f"/vacancies/{vacancy_id}"), "GET /vacancies/{id}")

    async def vacancy_raw(
        self,
        vacancy_id: str,
        *,
        params: dict[str, Any] | None = None,
        retries: int = 2,
    ) -> tuple[int, str, str | None]:
        """``GET /vacancies/{id}`` as ``(status, body, content_type)`` — nothing raised on status.

        The read-one path (ADR-015 §4) keeps 403/404/429 as diagnostics instead of collapsing
        them into an exception, so it needs the status code rather than a decoded payload.
        Retries (429/502/503/504 with ``Retry-After``) still come from ``http._send``.
        """
        status, text, content_type, _ = await request_text(
            self._http,
            "GET",
            f"{self._base}/vacancies/{vacancy_id}",
            platform=Platform.hh,
            ok=None,
            retries=retries,
            accept="application/json",
            headers=self.headers(),
            params={k: v for k, v in (params or {}).items() if v is not None},
        )
        return status, text, content_type

    async def app_token(self, client_id: str, client_secret: SecretStr) -> OAuthTokens:
        """Application token: ``POST /token`` with ``grant_type=client_credentials``.

        hh issues it to a registered application for public data — no user, no scopes, no refresh
        token. Needed because an anonymous ``GET /vacancies/{id}`` was refused with
        ``403 {"errors":[{"type":"forbidden"}]}`` on 2026-08-26. The token is short-lived and
        stays in memory: it never reaches the token store, which holds user grants only.
        """
        data = await request_json(
            self._http,
            "POST",
            f"{self._base}/token",
            platform=Platform.hh,
            headers={**self.headers(), "Accept": "application/json"},
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret.get_secret_value(),
            },
            retries=1,
        )
        return parse_token_response(Platform.hh, _expect_dict(data, "POST /token"))

    async def similar_vacancies(self, resume_id: str, *, per_page: int) -> dict[str, Any]:
        # VERIFY LIVE: "vacancies similar to a resume" comes from the legacy docs; the public spec
        # only lists /vacancies/{vacancy_id}/similar_vacancies.
        data = await self.get(
            f"/resumes/{resume_id}/similar_vacancies", params={"per_page": per_page, "page": 0}
        )
        return _expect_dict(data, "GET /resumes/{id}/similar_vacancies")

    async def suggest_areas(self, text: str) -> list[dict[str, Any]]:
        # VERIFY LIVE: /suggests/areas?text=… resolves a region name to its dictionary id.
        data = await self.get("/suggests/areas", params={"text": text})
        return _expect_items(data, "GET /suggests/areas")

    # ---- negotiations (responses / invitations)
    async def negotiations(
        self, page: int, *, per_page: int = NEGOTIATIONS_PER_PAGE
    ) -> dict[str, Any]:
        params = {"order_by": "updated_at", "order": "desc", "per_page": per_page, "page": page}
        return _expect_dict(await self.get("/negotiations", params=params), "GET /negotiations")

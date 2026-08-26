"""JustJoin.it candidate API — exactly one endpoint: the detail of one offer by slug.

``GET https://justjoin.it/api/candidate-api/offers/<slug>`` is public and undocumented. It is
called **only** for a URL the user supplied (read-one, ADR-015 / plan D1): ``robots.txt``
disallows ``/api/`` for crawlers and the user terms (14.07.2026, §11.3) forbid automated
downloading of the service's data, so this module deliberately exposes **no** listing / search
function — the policy is enforced by absence, not by a flag. ``tests/platform/test_justjoin.py``
asserts that this module defines nothing else.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from careeros.modules.platform.base import UpstreamError
from careeros.modules.platform.http import request_json
from careeros.modules.vault.enums import Platform

BASE_URL = "https://justjoin.it"
OFFER_DETAIL_PATH = "/api/candidate-api/offers/{slug}"


def offer_detail_url(slug: str) -> str:
    """Absolute URL of one offer's detail record."""
    return BASE_URL + OFFER_DETAIL_PATH.format(slug=quote(slug.strip("/"), safe=""))


async def offer_detail(
    http: httpx.AsyncClient, slug: str, *, retries: int = 1, backoff_s: float = 0.5
) -> dict[str, Any]:
    """One offer as JSON. Raises ``UpstreamError`` for any non-200 / non-object answer.

    Retries and ``Retry-After`` are the shared policy of ``platform.http.request_json``.
    """
    data = await request_json(
        http,
        "GET",
        offer_detail_url(slug),
        platform=Platform.justjoin,
        ok=(200,),
        retries=retries,
        backoff_s=backoff_s,
    )
    if not isinstance(data, dict):
        raise UpstreamError(Platform.justjoin, 200, "offer detail: expected a JSON object")
    return data

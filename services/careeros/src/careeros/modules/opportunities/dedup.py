"""Deduplication keys. Never auto-merge — flag ``possible_duplicate_of`` and let the user decide."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from rapidfuzz import fuzz

_TRACKING = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "ref",
    "refid",
    "trk",
    "trackingid",
    "fbclid",
    "gclid",
    "src",
}


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING
    ]
    path = re.sub(r"/+$", "", parts.path) or "/"
    return urlunsplit(
        (
            parts.scheme.lower() or "https",
            parts.netloc.lower().removeprefix("www."),
            path,
            urlencode(sorted(query)),
            "",
        )
    )


def _norm_text(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def dedup_key(*, url: str | None, title: str | None, company: str | None, raw_text: str) -> str:
    if url:
        return "url:" + hashlib.sha1(normalize_url(url).encode()).hexdigest()[:20]
    basis = f"{_norm_text(company)}|{_norm_text(title)}" if title else _norm_text(raw_text)[:500]
    return "txt:" + hashlib.sha1(basis.encode()).hexdigest()[:20]


def similarity(title_a: str, company_a: str | None, title_b: str, company_b: str | None) -> float:
    a = f"{_norm_text(company_a)} {_norm_text(title_a)}".strip()
    b = f"{_norm_text(company_b)} {_norm_text(title_b)}".strip()
    return float(fuzz.token_set_ratio(a, b))


FUZZY_THRESHOLD = 92.0

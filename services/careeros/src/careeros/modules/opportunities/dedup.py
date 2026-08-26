"""Deduplication keys and content fingerprints (ADR-016 §4).

Never auto-merge — flag ``possible_duplicate_of`` and let the user decide.
"""

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


# ------------------------------------------------------------------- fingerprint (ADR-016)

_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
#: "12 views", "3 просмотра", "17 applicants", "5 откликов", "2 candidates" — page counters.
_COUNTER_RE = re.compile(
    r"\b\d[\d\s,.]*\s*"
    r"(?:views?|viewed|applicants?|applications?|candidates?|responses?|clicks?"
    r"|просмотр\w*|отклик\w*|кандидат\w*|заявк\w*|показ\w*)\b",
    re.IGNORECASE,
)
#: Relative dates: "3 days ago", "posted 2 hours ago", "5 дней назад", "today", "вчера".
_RELATIVE_RE = re.compile(
    r"\b(?:\d+\s*(?:sec(?:ond)?s?|min(?:ute)?s?|hours?|days?|weeks?|months?|years?"
    r"|сек\w*|мин\w*|час\w*|дн\w*|ден[ьяь]\w*|недел\w*|месяц\w*|год\w*|лет)\s*(?:ago|назад))\b"
    r"|\b(?:today|yesterday|just now|сегодня|вчера|только что)\b",
    re.IGNORECASE,
)
_MONTHS = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    r"|янв\w*|фев\w*|мар\w*|апр\w*|ма[йя]|июн\w*|июл\w*|авг\w*|сен\w*|окт\w*|ноя\w*|дек\w*"
)
#: Absolute dates: 2026-08-26(T10:00), 26.08.2026, 08/26/2026, 26 August 2026, Aug 26, 2026.
_DATE_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[t ]\d{2}:\d{2}(?::\d{2})?(?:z|[+-]\d{2}:?\d{2})?)?\b"
    r"|\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b"
    rf"|\b\d{{1,2}}\s+(?:{_MONTHS})\.?(?:\s+\d{{4}})?\b"
    rf"|\b(?:{_MONTHS})\.?\s+\d{{1,2}}(?:,?\s+\d{{4}})?\b",
    re.IGNORECASE,
)
_WS_RE = re.compile(r"\s+")


def fingerprint(text: str) -> str:
    """Stable digest of a posting's *content*: ``fp:`` + sha1[:20] of the normalised text.

    Normalisation drops what changes on every page load — URLs (tracking params, session
    ids), view/applicant counters, absolute and relative dates — and collapses case and
    whitespace. Salary figures, requirements and locations survive, so a fingerprint change
    means the job itself changed and a new ``OpportunityRaw`` snapshot is warranted.
    """
    value = text.lower()
    value = _URL_RE.sub(" ", value)
    value = _COUNTER_RE.sub(" ", value)
    value = _RELATIVE_RE.sub(" ", value)
    value = _DATE_RE.sub(" ", value)
    value = _WS_RE.sub(" ", value).strip()
    return "fp:" + hashlib.sha1(value.encode()).hexdigest()[:20]


def identity_candidates(
    *,
    platform: str | None,
    external_id: str | None,
    canonical_url: str | None,
    company: str | None,
    title: str | None,
    location: str | None,
    fingerprint: str | None,
) -> list[tuple[str, str]]:
    """Ordered ``(layer, key)`` pairs to look an existing job up by, strongest evidence first.

    Layers (ADR-016 §4): ``external_id`` → ``canonical_url`` → ``company_title_location`` →
    ``fingerprint``. Fuzzy (``similarity``) and semantic matching are not keys and stay with the
    caller. A match on the first two layers is identity; the rest are *candidates* to flag.
    """
    out: list[tuple[str, str]] = []
    if platform and external_id:
        out.append(("external_id", f"{platform.strip().lower()}:{external_id.strip()}"))
    if canonical_url:
        out.append(("canonical_url", normalize_url(canonical_url)))
    if company and title:
        basis = "|".join((_norm_text(company), _norm_text(title), _norm_text(location)))
        out.append(
            ("company_title_location", "ctl:" + hashlib.sha1(basis.encode()).hexdigest()[:20])
        )
    if fingerprint:
        out.append(("fingerprint", fingerprint))
    return out

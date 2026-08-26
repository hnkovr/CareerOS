"""RocketHunt URL shapes: only ``/{en,ru}/vacancies/<uuid>`` is a vacancy (ADR-015 §1).

Pure string work — nothing here touches the network. The canonical form is always the ``en``
locale of the same uuid (the ru page is the same vacancy, ``same_as``); the locale the user
gave is kept on ``CanonicalSource.locale`` so the read can ask for that language.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

HOST = "rockethunt.ai"
HOSTS: tuple[str, ...] = (HOST, "www.rockethunt.ai")
LOCALES: tuple[str, ...] = ("en", "ru")
CANONICAL_LOCALE = "en"
BASE = f"https://{HOST}"

#: RFC 4122 version-4 identifier — the only vacancy id RocketHunt hands out (verified 2026-08-26).
UUID_V4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.IGNORECASE
)
_PATH = re.compile(r"^/(?P<locale>en|ru)/vacancies/(?P<uuid>[^/?#]+)/?$", re.IGNORECASE)


def is_rockethunt(url: str) -> bool:
    """Does ``url`` live on the RocketHunt host (subdomains included)?"""
    try:
        netloc = urlsplit(url.strip()).netloc
    except ValueError:
        return False
    host = netloc.rsplit("@", 1)[-1].split(":", 1)[0].lower().removeprefix("www.")
    return host == HOST or host.endswith("." + HOST)


def parse_vacancy(url: str) -> tuple[str, str] | None:
    """``(uuid, locale)`` for a vacancy URL, ``None`` for anything else on the site.

    Query and fragment are ignored (they carry UTM/anchor noise only); the uuid is lower-cased.
    """
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not is_rockethunt(url):
        return None
    m = _PATH.match(parts.path)
    if m is None:
        return None
    uuid = m.group("uuid")
    if not UUID_V4.match(uuid):
        return None
    return uuid.lower(), m.group("locale").lower()


def vacancy_url(uuid: str, locale: str = CANONICAL_LOCALE) -> str:
    """The page URL for one vacancy in one locale (no query, no fragment)."""
    loc = locale.lower() if locale.lower() in LOCALES else CANONICAL_LOCALE
    return f"{BASE}/{loc}/vacancies/{uuid.lower()}"

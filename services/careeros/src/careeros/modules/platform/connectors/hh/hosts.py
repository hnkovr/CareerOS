"""HeadHunter sites the connector recognises, and how each maps onto the API (ADR-015).

HH runs one API host — ``https://api.hh.ru`` — behind several country front-ends. The site a
vacancy was opened on is passed to the API as ``host=<site>`` so areas, currencies and texts come
back in that site's locale; the endpoint path is the same everywhere. Only ``hh.ru`` is verified
from this workstation (2026-08-26, and even there the anonymous read answered ``403 forbidden``);
every other row comes from HH's own documentation and stays ``verified=False`` until a live read
confirms it. Nothing here invents a different API base — an unverified row still reads through
``api.hh.ru``, which is the documented behaviour, not a guess about a regional API.

Pure data + lookup helpers: no I/O, no settings.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "API_BASE",
    "CANONICAL_HOST",
    "HOSTS",
    "HOST_NAMES",
    "HHHost",
    "api_base_for",
    "api_params_for",
    "canonical",
    "find",
]

#: Every HH front-end talks to this one API host (documented; regional bases do not exist).
API_BASE = "https://api.hh.ru"
CANONICAL_HOST = "hh.ru"


@dataclass(frozen=True, slots=True)
class HHHost:
    """One HeadHunter front-end.

    ``verified`` = a read through this site was actually observed from this repo; ``canonical``
    marks the site whose URL form the connector normalises hh.ru city subdomains to.
    """

    host: str
    country: str
    api_base: str = API_BASE
    canonical: bool = False
    verified: bool = False
    note: str = ""


HOSTS: tuple[HHHost, ...] = (
    HHHost(
        "hh.ru",
        "Russia",
        canonical=True,
        verified=True,
        note="city subdomains (spb.hh.ru, nn.hh.ru …) and m.hh.ru are the same site",
    ),
    HHHost("hh.kz", "Kazakhstan", note="to verify: host=hh.kz on GET /vacancies/{id}"),
    HHHost("headhunter.ge", "Georgia", note="to verify"),
    HHHost("headhunter.kg", "Kyrgyzstan", note="to verify"),
    HHHost("hh.uz", "Uzbekistan", note="to verify"),
    HHHost("rabota.by", "Belarus", note="to verify"),
    HHHost("hh1.az", "Azerbaijan", note="to verify"),
)

HOST_NAMES: tuple[str, ...] = tuple(h.host for h in HOSTS)
_BY_HOST: dict[str, HHHost] = {h.host: h for h in HOSTS}


def canonical() -> HHHost:
    """The site regional-less references belong to (``hh.ru``)."""
    return _BY_HOST[CANONICAL_HOST]


def _bare(host: str) -> str:
    """``netloc`` → a comparable host name (credentials, port, trailing dot and ``www.`` gone)."""
    value = (host or "").strip().lower().rstrip(".")
    value = value.rsplit("@", 1)[-1].split(":", 1)[0]
    return value.removeprefix("www.")


def find(host: str) -> HHHost | None:
    """The HH site ``host`` belongs to, or ``None``.

    Subdomains resolve to their parent site, which is what makes ``spb.hh.ru``, ``m.hh.ru`` and
    ``www.hh.ru`` one and the same place as ``hh.ru``.
    """
    bare = _bare(host)
    if not bare:
        return None
    site = _BY_HOST.get(bare)
    if site is not None:
        return site
    return next((_BY_HOST[name] for name in HOST_NAMES if bare.endswith("." + name)), None)


def api_base_for(host: str) -> str:
    """API base of the site ``host`` belongs to — always ``api.hh.ru`` (see the docstring above)."""
    site = find(host)
    return site.api_base if site is not None else API_BASE


def api_params_for(host: str) -> dict[str, str]:
    """Query parameters that pin an API read to a regional site.

    ``{}`` for hh.ru (and anything unknown), ``{"host": "<site>"}`` for the regional front-ends —
    HH's documented way of asking for a site's own locale. **To verify**: no regional read has
    been performed from this repo yet, so the parameter is declared, not observed.
    """
    site = find(host)
    if site is None or site.canonical:
        return {}
    return {"host": site.host}

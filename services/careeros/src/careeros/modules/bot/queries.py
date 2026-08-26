"""Job-search query texts, derived from the vault's positionings (GH #28).

The vault has no `queries` collection, and P0 gives the bot no vault-write path
(ADR-012), so there is nothing for chat to "save". What the vault *does* define is
positioning: a name, and the keywords a matching posting must contain. That is a
job-search query in all but name, so this module projects one out of the other.

Two properties make the projection usable as an index for `/urls <n>`:

* it is **deterministic** — same vault, same list, same order, so the number the
  user reads in one message still means the same query in the next one;
* it is **total** — every positioning yields exactly one query, so a number that
  addressed something yesterday cannot silently address nothing today.

The order is the positioning id, not the vault's file order or a "most useful
first" ranking: ids are stable across edits, while any ranking would renumber the
whole list the moment a single positioning changed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: `Analytics Engineer (dbt-centric)` searches badly; `Analytics Engineer` searches
#: well. The parenthetical is a note to the owner, not part of the job title.
_PARENTHETICAL = re.compile(r"\s*\([^)]*\)")


@dataclass(frozen=True)
class SavedQuery:
    """One query text plus what it came from, so the bot can show its provenance."""

    index: int
    positioning_id: str
    text: str
    keywords: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)
    is_default: bool = False


def query_titles(name: str) -> list[str]:
    """The searchable titles inside a positioning name, most specific first.

    A slash in `Senior Data Engineer / Analytics Engineer` reads as "or" to a human
    and as a literal character to a job board — pasted whole it matches nothing. So
    the segments are separated, the first becomes the query, and the rest are shown
    rather than dropped.
    """
    cleaned = _PARENTHETICAL.sub("", name)
    return [" ".join(part.split()) for part in cleaned.split("/") if part.strip()]


def query_text(name: str) -> str:
    """The searchable part of a positioning name."""
    titles = query_titles(name)
    return titles[0] if titles else ""


def build_queries(vault_data: object) -> list[SavedQuery]:
    """Project every positioning in the vault into one job-search query."""
    positionings = sorted(
        getattr(vault_data, "positioning", []) or [], key=lambda p: str(getattr(p, "id", ""))
    )
    default = str(getattr(getattr(vault_data, "meta", None), "default_positioning", "") or "")
    out: list[SavedQuery] = []
    for i, p in enumerate(positionings, start=1):
        pid = str(getattr(p, "id", ""))
        titles = query_titles(str(getattr(p, "name", "") or pid))
        if not titles:
            continue
        out.append(
            SavedQuery(
                index=i,
                positioning_id=pid,
                text=titles[0],
                keywords=[str(k) for k in (getattr(p, "keywords_must", None) or [])],
                alternatives=titles[1:],
                is_default=pid == default,
            )
        )
    return out


def resolve_index(queries: list[SavedQuery], token: str) -> SavedQuery | None:
    """The query a bare number addresses, or None when the token is not one."""
    raw = token.strip()
    if not raw.isdigit():
        return None
    wanted = int(raw)
    return next((q for q in queries if q.index == wanted), None)


def render_queries(queries: list[SavedQuery]) -> str:
    """Plain-text listing: number, text, and the keywords that refine it."""
    if not queries:
        return (
            "no positionings in the vault, so there are no queries to derive.\n"
            "add one under positioning/ and it will appear here."
        )
    lines: list[str] = []
    for q in queries:
        mark = " ← default" if q.is_default else ""
        lines.append(f"{q.index}. {q.text}{mark}")
        if q.alternatives:
            lines.append(f"   also: {' · '.join(q.alternatives)}")
        if q.keywords:
            lines.append(f"   keywords: {', '.join(q.keywords)}")
    lines.append("")
    lines.append('use one with: /urls 1 hh,upwork — or quote your own: /urls "…"')
    lines.append("derived from the vault's positionings; edit them there to change this list")
    return "\n".join(lines)

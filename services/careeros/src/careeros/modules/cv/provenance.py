"""Provenance guard (ADR-010 §2): a generated bullet is accepted only if every cited fact id exists,
every number it states appears in its source facts, and it names no company outside its sources.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from careeros.modules.vault import schema as s

_NUMBER_RE = re.compile(
    r"(?<![a-z0-9])\$?(\d[\d,]*(?:\.\d+)?(?:\s?%|\s?[kmbx](?![a-z]))?)", re.IGNORECASE
)


@dataclass(frozen=True)
class FactSource:
    id: str
    company_id: str | None
    text: str  # everything a bullet may legitimately draw numbers from


def fact_sources(data: s.VaultData) -> dict[str, FactSource]:
    out: dict[str, FactSource] = {}
    for a in data.achievements:
        metric_text = " ".join(
            " ".join(filter(None, [m.name, m.value, m.unit, m.baseline, m.note])) for m in a.metrics
        )
        out[a.id] = FactSource(
            a.id, a.company_id, " ".join([a.title, *a.facts, metric_text, a.period or ""])
        )
    for p in data.projects:
        metric_text = " ".join(
            " ".join(filter(None, [m.name, m.value, m.unit, m.baseline, m.note])) for m in p.metrics
        )
        out[p.id] = FactSource(
            p.id,
            p.company_id,
            " ".join(
                filter(
                    None,
                    [p.name, p.summary, p.problem, p.solution, p.outcome, p.period, metric_text],
                )
            ),
        )
    for e in data.experience:
        out[e.id] = FactSource(
            e.id,
            e.company_id,
            " ".join([e.company_name, e.summary, *e.responsibilities, *(r.title for r in e.roles)]),
        )
    for sk in data.skills:
        out[sk.id] = FactSource(sk.id, None, " ".join([sk.name, *sk.aliases, str(sk.years or "")]))
    out[data.profile.id] = FactSource(
        data.profile.id, None, " ".join([data.profile.summary_core, data.profile.headline_baseline])
    )
    return out


def _numbers(text: str) -> list[str]:
    return [n.replace(",", "").replace(" ", "").lower() for n in _NUMBER_RE.findall(text)]


def check_bullet(
    text: str,
    derived_from: list[str],
    sources: dict[str, FactSource],
    company_names: dict[str, str],
) -> list[str]:
    """Return a list of problems (empty = bullet passes)."""
    problems: list[str] = []
    unknown = [fid for fid in derived_from if fid not in sources]
    if unknown:
        problems.append(f"unknown fact ids: {', '.join(unknown)}")
    cited = [sources[fid] for fid in derived_from if fid in sources]
    if not cited:
        return problems or ["no valid sources cited"]

    source_text = " ".join(c.text for c in cited)
    source_numbers = set(_numbers(source_text))
    for num in _numbers(text):
        if num not in source_numbers:
            problems.append(f"number '{num}' not present in cited facts")

    cited_companies = {c.company_id for c in cited if c.company_id}
    lowered = text.lower()
    for company_id, name in company_names.items():
        if company_id not in cited_companies and name.lower() in lowered:
            problems.append(f"mentions company '{name}' outside cited facts")
    return problems


def company_name_map(data: s.VaultData) -> dict[str, str]:
    return {e.company_id: e.company_name for e in data.experience}

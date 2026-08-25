"""Market intelligence over the *observed* opportunity stream (brief §37). Never presented as
global market research — every payload carries the observation window and sample size."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from itertools import combinations
from statistics import median
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.modules.vault import schema as s

DISCLAIMER = "Based on your observed opportunity stream — not global market research."


class TechDemand(BaseModel):
    technology: str
    count: int
    share: float
    avg_score: float | None = None
    market_groups: list[str] = Field(default_factory=list)


class Combo(BaseModel):
    technologies: list[str]
    count: int


class CompensationStats(BaseModel):
    kind: str  # annual | hourly
    currency: str
    n: int
    p25: float
    median: float
    p75: float


class MarketOut(BaseModel):
    disclaimer: str = DISCLAIMER
    window_days: int
    since: datetime
    sample_size: int
    technologies: list[TechDemand]
    combos: list[Combo]
    remote_policy: dict[str, int]
    contract_type: dict[str, int]
    seniority: dict[str, int]
    sources: dict[str, int]
    recommendations: dict[str, int]
    compensation: list[CompensationStats]
    avg_score: float | None


def _canon(data: s.VaultData) -> dict[str, tuple[str, list[str]]]:
    """lower alias → (display name, market groups)."""
    out: dict[str, tuple[str, list[str]]] = {}
    for sk in data.skills:
        groups = [str(g) for g in sk.market_groups]
        out[sk.name.lower()] = (sk.name, groups)
        for a in sk.aliases:
            out.setdefault(a.lower(), (sk.name, groups))
    if data.scoring:
        for group, techs in data.scoring.tech_groups.items():
            for t in techs:
                name, groups = out.get(t.lower(), (t, []))
                if str(group) not in groups:
                    groups = [*groups, str(group)]
                out[t.lower()] = (name, groups)
        for alias, canon in data.scoring.aliases.items():
            if canon.lower() in out:
                out.setdefault(alias.lower(), out[canon.lower()])
    return out


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    idx = (len(ordered) - 1) * q
    lo, hi = int(idx), min(int(idx) + 1, len(ordered) - 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (idx - lo), 2)


def compute_market(
    data: s.VaultData,
    stream: list[dict[str, Any]],
    *,
    window_days: int = 90,
    now: datetime | None = None,
) -> MarketOut:
    now = now or datetime.now(UTC)
    since = now - timedelta(days=window_days)
    rows = [r for r in stream if r["received_at"] is None or r["received_at"] >= since]
    canon = _canon(data)

    tech_counts: Counter[str] = Counter()
    tech_scores: dict[str, list[float]] = {}
    tech_groups: dict[str, list[str]] = {}
    combo_counts: Counter[tuple[str, str]] = Counter()
    for r in rows:
        names: set[str] = set()
        for t in r["technologies"]:
            name, groups = canon.get(str(t).lower(), (str(t), []))
            names.add(name)
            tech_groups.setdefault(name, groups)
        for name in names:
            tech_counts[name] += 1
            if r["score"] is not None:
                tech_scores.setdefault(name, []).append(float(r["score"]))
        for pair in combinations(sorted(names), 2):
            combo_counts[pair] += 1

    n = len(rows)
    technologies = [
        TechDemand(
            technology=name,
            count=count,
            share=round(count / n, 3) if n else 0.0,
            avg_score=round(sum(tech_scores[name]) / len(tech_scores[name]), 1)
            if tech_scores.get(name)
            else None,
            market_groups=tech_groups.get(name, []),
        )
        for name, count in tech_counts.most_common(30)
    ]
    combos = [
        Combo(technologies=list(pair), count=c)
        for pair, c in combo_counts.most_common(10)
        if c >= 2
    ]

    def dist(key: str) -> dict[str, int]:
        return dict(Counter(str(r[key] or "unknown") for r in rows).most_common())

    annual: dict[str, list[float]] = {}
    hourly: dict[str, list[float]] = {}
    for r in rows:
        comp = r["compensation"]
        if not comp:
            continue
        value = comp.get("max") or comp.get("min")
        if value is None:
            continue
        cur = comp.get("currency") or "?"
        period = comp.get("period")
        if period == "hour":
            hourly.setdefault(cur, []).append(float(value))
        elif period == "day":
            hourly.setdefault(cur, []).append(float(value) / 8)
        elif period == "month":
            annual.setdefault(cur, []).append(float(value) * 12)
        elif period == "year":
            annual.setdefault(cur, []).append(float(value))
    comp_stats = [
        CompensationStats(
            kind=kind,
            currency=cur,
            n=len(v),
            p25=_percentile(v, 0.25),
            median=round(median(v), 2),
            p75=_percentile(v, 0.75),
        )
        for kind, bucket in (("annual", annual), ("hourly", hourly))
        for cur, v in sorted(bucket.items())
        if len(v) >= 2
    ]
    scores = [float(r["score"]) for r in rows if r["score"] is not None]
    return MarketOut(
        window_days=window_days,
        since=since,
        sample_size=n,
        technologies=technologies,
        combos=combos,
        remote_policy=dist("remote_policy"),
        contract_type=dist("contract_type"),
        seniority=dist("seniority"),
        sources=dist("source"),
        recommendations=dist("recommendation"),
        compensation=comp_stats,
        avg_score=round(sum(scores) / len(scores), 1) if scores else None,
    )


async def market_for(
    session: AsyncSession, data: s.VaultData, *, window_days: int = 90
) -> MarketOut:
    from careeros.modules.opportunities.service import opportunity_stream

    return compute_market(data, await opportunity_stream(session), window_days=window_days)

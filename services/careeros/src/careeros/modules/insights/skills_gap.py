"""Skills gap (brief §38) and portfolio planner (§39): demand from the observed stream vs the
vault's skill inventory and evidence. 'I know X' ≠ 'I can prove X' — statuses keep them apart."""

from __future__ import annotations

from collections import Counter
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.modules.insights.market import _canon
from careeros.modules.vault import schema as s
from careeros.modules.vault.enums import ItemStatus, SkillLevel, SkillTier


class SkillStatus(StrEnum):
    evidenced = "evidenced"  # in vault with evidence or achievement/project backing
    claimed = "claimed"  # in vault at proficient/expert, no evidence
    known = "known"  # in vault at working/learning level
    missing = "missing"  # demanded, not in vault
    worth_learning = "worth_learning"  # missing/known AND meaningful demand or strategic


class SkillGapItem(BaseModel):
    technology: str
    status: SkillStatus
    demand: int
    demand_share: float
    tier: SkillTier | None = None
    level: SkillLevel | None = None
    evidence: list[str] = Field(default_factory=list, description="fact ids proving it")
    market_groups: list[str] = Field(default_factory=list)
    suggested_action: str | None = None


class PortfolioSuggestion(BaseModel):
    technology: str
    gap: SkillStatus
    demand: int
    suggested_proof: str
    project_id: str | None = None
    estimated_roi: str  # high | medium | low
    why: str


class SkillsGapOut(BaseModel):
    disclaimer: str = (
        "Demand counts come from your observed opportunity stream and cover technologies in "
        "the vault vocabulary (skills + scoring groups); add a skill or scoring alias to start "
        "tracking a new one."
    )
    sample_size: int
    items: list[SkillGapItem]
    counts: dict[str, int]
    portfolio: list[PortfolioSuggestion]


WORTH_LEARNING_MIN_DEMAND = 3
STRATEGIC_GROUPS = {"strategic_core", "agentic"}


def _evidence_map(data: s.VaultData) -> dict[str, list[str]]:
    """skill name (lower) → fact ids that back it (skill evidence, achievements, projects)."""
    backing: dict[str, list[str]] = {}
    for sk in data.skills:
        ids = [f"{e.type}:{e.ref}" for e in sk.evidence]
        backing[sk.name.lower()] = ids
    for a in data.achievements:
        if a.status == ItemStatus.retired:
            continue
        for t in a.technologies.all():
            backing.setdefault(t.lower(), [])
            if a.id not in backing[t.lower()]:
                backing[t.lower()].append(a.id)
    for p in data.projects:
        if p.status == ItemStatus.retired:
            continue
        for t in p.technologies:
            backing.setdefault(t.lower(), [])
            if p.id not in backing[t.lower()]:
                backing[t.lower()].append(p.id)
    return backing


def compute_skills_gap(data: s.VaultData, stream: list[dict[str, Any]]) -> SkillsGapOut:
    canon = _canon(data)
    skills_by_name = {sk.name.lower(): sk for sk in data.skills if sk.status != ItemStatus.retired}
    alias_to_skill = {a.lower(): sk for sk in skills_by_name.values() for a in sk.aliases}
    backing = _evidence_map(data)

    demand: Counter[str] = Counter()
    for r in stream:
        for name in {canon.get(str(t).lower(), (str(t), []))[0] for t in r["technologies"]}:
            demand[name] += 1
    n = len(stream)

    items: list[SkillGapItem] = []
    seen: set[str] = set()
    for tech, count in demand.most_common():
        key = tech.lower()
        sk = skills_by_name.get(key) or alias_to_skill.get(key)
        groups = canon.get(key, (tech, []))[1]
        evidence = backing.get(sk.name.lower() if sk else key, [])
        if sk is None:
            status = SkillStatus.missing
        elif evidence:
            status = SkillStatus.evidenced
        elif sk.level in (SkillLevel.expert, SkillLevel.proficient):
            status = SkillStatus.claimed
        else:
            status = SkillStatus.known
        strategic = bool(set(groups) & STRATEGIC_GROUPS) or (
            sk is not None and sk.tier == SkillTier.target
        )
        if status in (SkillStatus.missing, SkillStatus.known) and (
            count >= WORTH_LEARNING_MIN_DEMAND or strategic
        ):
            status = SkillStatus.worth_learning
        action = None
        if status == SkillStatus.claimed:
            action = (
                "Add evidence (project, certification, metric-backed achievement) — "
                "claims without proof lose in interviews"
            )
        elif status == SkillStatus.worth_learning:
            action = "Build a small public proof; demand is real in your stream"
        elif status == SkillStatus.missing:
            action = "Low demand so far — watch, don't chase"
        items.append(
            SkillGapItem(
                technology=sk.name if sk else tech,
                status=status,
                demand=count,
                demand_share=round(count / n, 3) if n else 0.0,
                tier=sk.tier if sk else None,
                level=sk.level if sk else None,
                evidence=evidence[:5],
                market_groups=groups,
                suggested_action=action,
            )
        )
        seen.add(key)

    # vault skills never demanded — still worth knowing they are un-evidenced
    for key, sk in skills_by_name.items():
        if key in seen or any(a.lower() in seen for a in sk.aliases):
            continue
        evidence = backing.get(key, [])
        status = (
            SkillStatus.evidenced
            if evidence
            else (
                SkillStatus.claimed
                if sk.level in (SkillLevel.expert, SkillLevel.proficient)
                else SkillStatus.known
            )
        )
        items.append(
            SkillGapItem(
                technology=sk.name,
                status=status,
                demand=0,
                demand_share=0.0,
                tier=sk.tier,
                level=sk.level,
                evidence=evidence[:5],
                market_groups=[str(g) for g in sk.market_groups],
            )
        )

    counts = dict(Counter(str(i.status) for i in items))
    public_projects = [p for p in data.projects if p.public and p.status != ItemStatus.retired]
    portfolio: list[PortfolioSuggestion] = []
    for item in items:
        if item.status not in (SkillStatus.worth_learning, SkillStatus.claimed) or item.demand == 0:
            continue
        strategic = bool(set(item.market_groups) & STRATEGIC_GROUPS)
        roi = (
            "high"
            if (item.demand_share >= 0.2 and strategic)
            else "medium"
            if (item.demand_share >= 0.1 or strategic)
            else "low"
        )
        host = public_projects[0] if public_projects else None
        proof = (
            f"Add a {item.technology} path to {host.name}"
            if host
            else f"Publish a small {item.technology} demo repository"
        )
        if item.status == SkillStatus.claimed:
            proof = (
                f"Attach evidence for {item.technology}: link a project or write a "
                "metric-backed achievement"
            )
        portfolio.append(
            PortfolioSuggestion(
                technology=item.technology,
                gap=item.status,
                demand=item.demand,
                suggested_proof=proof,
                project_id=host.id if host and item.status == SkillStatus.worth_learning else None,
                estimated_roi=roi,
                why=(
                    f"appears in {item.demand} of {n} observed opportunities "
                    f"({round(item.demand_share * 100)}%)"
                    + ("; strategic-core/agentic group" if strategic else "")
                ),
            )
        )
    roi_rank = {"high": 0, "medium": 1, "low": 2}
    portfolio.sort(key=lambda p: (roi_rank[p.estimated_roi], -p.demand))
    return SkillsGapOut(sample_size=n, items=items, counts=counts, portfolio=portfolio[:10])


async def skills_gap_for(session: AsyncSession, data: s.VaultData) -> SkillsGapOut:
    from careeros.modules.opportunities.service import opportunity_stream

    return compute_skills_gap(data, await opportunity_stream(session))

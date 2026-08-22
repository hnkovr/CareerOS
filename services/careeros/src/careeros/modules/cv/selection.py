"""Deterministic fact selection: which experiences, achievements, projects and skills go into a
variant, and in what order. No AI here (ADR-010) — the output is a set of fact ids with scores.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from careeros.modules.cv.keywords import extract_known_tech, keyword_hits, tech_vocabulary
from careeros.modules.vault import schema as s
from careeros.modules.vault.enums import ItemStatus, SkillTier

TIER_ORDER = {SkillTier.first_priority: 0, SkillTier.additional: 1, SkillTier.target: 2}
LEVEL_ORDER = {"expert": 0, "proficient": 1, "working": 2, "learning": 3}


@dataclass
class ScoredAchievement:
    achievement: s.Achievement
    score: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class SelectedExperience:
    experience: s.Experience
    achievements: list[ScoredAchievement]


@dataclass
class Selection:
    variant: s.CVVariant
    positioning: s.Positioning
    channel: s.ChannelRules
    experiences: list[SelectedExperience]
    projects: list[s.Project]
    skills: list[s.Skill]
    jd_keywords: list[str]
    positioning_keywords: list[str]

    def fact_ids(self) -> set[str]:
        ids = {a.achievement.id for e in self.experiences for a in e.achievements}
        ids |= {e.experience.id for e in self.experiences}
        ids |= {p.id for p in self.projects}
        ids |= {sk.id for sk in self.skills}
        return ids


def _visible(item: s.VaultItem, platform: str) -> bool:
    return item.status != ItemStatus.retired and item.visibility.allows(platform)


def _achievement_text(a: s.Achievement) -> str:
    return " ".join([a.title, *a.facts, *a.keywords, *a.technologies.all()])


def score_achievement(
    a: s.Achievement, positioning: s.Positioning, jd_keywords: list[str]
) -> ScoredAchievement:
    score = 0.0
    reasons: list[str] = []
    text = _achievement_text(a)
    if a.id in positioning.emphasize.achievements:
        score += 3
        reasons.append("emphasized by positioning")
    if a.id in positioning.deemphasize:
        score -= 5
        reasons.append("de-emphasized by positioning")
    must = keyword_hits(text, positioning.keywords_must)
    nice = keyword_hits(text, positioning.keywords_nice)
    jd = keyword_hits(text, jd_keywords)
    score += len(must) * 1.0 + len(nice) * 0.5 + len(jd) * 1.5
    if must:
        reasons.append(f"must-keywords: {', '.join(must)}")
    if jd:
        reasons.append(f"JD keywords: {', '.join(jd)}")
    if a.metrics:
        score += 0.5
        reasons.append("has metrics")
    if a.status == ItemStatus.verified:
        score += 0.25
    return ScoredAchievement(a, round(score, 2), reasons)


def select_facts(data: s.VaultData, variant: s.CVVariant, jd_text: str | None = None) -> Selection:
    positioning = data.by_id(data.positioning)[variant.positioning_id]
    channel = data.by_id(data.channels)[variant.channel_id]
    platform = str(channel.platform)
    achievements = data.by_id(data.achievements)
    projects_by_id = data.by_id(data.projects)

    vocab = tech_vocabulary(data)
    jd_keywords = extract_known_tech(jd_text, vocab) if jd_text else []
    positioning_keywords = [*positioning.keywords_must, *positioning.keywords_nice]

    cutoff: date | None = None
    if variant.include.years_back:
        today = date.today()
        cutoff = date(today.year - variant.include.years_back, today.month, 1)

    max_bullets = variant.include.max_bullets_per_role or channel.limits.bullets_per_role

    experiences: list[SelectedExperience] = []
    for exp in sorted(data.experience, key=lambda e: e.start, reverse=True):
        if not _visible(exp, platform):
            continue
        if variant.include.companies and exp.company_id not in variant.include.companies:
            continue
        if cutoff and exp.end is not None and exp.end < cutoff:
            continue
        scored = [
            score_achievement(achievements[aid], positioning, jd_keywords)
            for aid in exp.achievement_ids
            if aid in achievements and _visible(achievements[aid], platform)
        ]
        scored = [x for x in scored if x.score > -1]
        scored.sort(key=lambda x: (-x.score, x.achievement.id))
        experiences.append(SelectedExperience(exp, scored[:max_bullets]))

    def project_score(p: s.Project) -> float:
        sc = 3.0 if p.id in positioning.emphasize.projects else 0.0
        if p.id in positioning.deemphasize:
            sc -= 5
        text = " ".join(
            filter(None, [p.name, p.summary, p.problem, p.solution, p.outcome, *p.technologies])
        )
        sc += (
            len(keyword_hits(text, positioning_keywords)) * 0.5
            + len(keyword_hits(text, jd_keywords)) * 1.5
        )
        if p.public:
            sc += 0.5
        return sc

    projects = [p for p in projects_by_id.values() if _visible(p, platform)]
    projects.sort(key=lambda p: (-project_score(p), p.id))
    projects = [p for p in projects if project_score(p) > -1]
    if variant.include.max_projects is not None:
        projects = projects[: variant.include.max_projects]

    allowed_tiers = set(variant.include.skills_tiers)
    skills = [
        sk
        for sk in data.skills
        if sk.tier in allowed_tiers
        and sk.status != ItemStatus.retired
        and sk.id not in positioning.deemphasize
    ]

    def skill_key(sk: s.Skill) -> tuple[int, int, int, str]:
        emphasized = 0 if sk.id in positioning.emphasize.skills else 1
        return (emphasized, TIER_ORDER[sk.tier], LEVEL_ORDER.get(str(sk.level), 9), sk.name.lower())

    skills.sort(key=skill_key)
    if channel.limits.skills_max:
        skills = skills[: channel.limits.skills_max]

    return Selection(
        variant=variant,
        positioning=positioning,
        channel=channel,
        experiences=experiences,
        projects=projects,
        skills=skills,
        jd_keywords=jd_keywords,
        positioning_keywords=positioning_keywords,
    )

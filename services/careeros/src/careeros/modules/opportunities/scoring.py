"""Deterministic, explainable scoring over ``scoring/model.yaml`` (ADR-010 §1).

Every dimension returns 0-100 with an explanation and the signals it used. AI never changes these
numbers; it interprets them.
"""

from __future__ import annotations

from dataclasses import dataclass

from careeros.modules.cv.keywords import normalize
from careeros.modules.opportunities.enums import (
    CompensationPeriod,
    ContractType,
    EmploymentType,
    Recommendation,
    RemotePolicy,
    Seniority,
    Source,
)
from careeros.modules.opportunities.schemas import DimensionScore, OpportunityExtraction, ScoreOut
from careeros.modules.vault import schema as s
from careeros.modules.vault.enums import MarketGroup, ScoreDimension, SkillLevel, SkillTier

LEVEL_WEIGHT = {
    SkillLevel.expert: 1.0,
    SkillLevel.proficient: 0.85,
    SkillLevel.working: 0.6,
    SkillLevel.learning: 0.3,
}
TIER_BONUS = {SkillTier.first_priority: 0.1, SkillTier.additional: 0.0, SkillTier.target: 0.05}

STARTUP_SIGNALS = (
    "startup",
    "start-up",
    "seed",
    "series a",
    "series b",
    "early-stage",
    "early stage",
    "mvp",
    "ownership",
    "0 to 1",
    "zero to one",
    "small team",
    "founding",
    "scale-up",
    "scaleup",
    "yc ",
    "y combinator",
)
ENTERPRISE_SIGNALS = (
    "enterprise",
    "fortune 500",
    "compliance",
    "sox",
    "gdpr",
    "bank",
    "insurance",
    "regulated",
    "global organization",
    "multinational",
    "10,000",
    "1000+ employees",
    "large-scale",
    "governance",
)
EFFORT_SIGNALS = (
    ("take-home", "take-home assignment"),
    ("take home", "take-home assignment"),
    ("assignment", "assignment"),
    ("case study", "case study"),
    ("5 rounds", "long process"),
    ("6 rounds", "long process"),
    ("multiple rounds", "long process"),
    ("relocation required", "relocation"),
    ("relocate", "relocation"),
    ("cover letter", "cover letter required"),
    ("video introduction", "video intro required"),
)


@dataclass
class ScoringContext:
    data: s.VaultData
    model: s.ScoringModel
    skills_by_name: dict[str, s.Skill]
    group_terms: dict[MarketGroup, set[str]]

    @classmethod
    def build(cls, data: s.VaultData) -> ScoringContext:
        if data.scoring is None:
            raise ValueError("vault has no scoring/model.yaml")
        by_name: dict[str, s.Skill] = {}
        for sk in data.skills:
            by_name[sk.name.lower()] = sk
            for alias in sk.aliases:
                by_name.setdefault(alias.lower(), sk)
        for alias, canon in data.scoring.aliases.items():
            if canon.lower() in by_name:
                by_name.setdefault(alias.lower(), by_name[canon.lower()])
        groups = {g: {t.lower() for t in techs} for g, techs in data.scoring.tech_groups.items()}
        return cls(data, data.scoring, by_name, groups)

    def canonical(self, tech: str) -> str:
        t = tech.lower()
        return self.model.aliases.get(t, t)

    def in_group(self, tech: str, group: MarketGroup) -> bool:
        t = self.canonical(tech)
        sk = self.skills_by_name.get(t)
        if sk and group in sk.market_groups:
            return True
        return t in self.group_terms.get(group, set())


def _dim(
    name: ScoreDimension,
    score: float,
    weight: float,
    explanation: str,
    signals: list[str] | None = None,
) -> DimensionScore:
    return DimensionScore(
        name=name,
        score=int(max(0, min(100, round(score)))),
        weight=weight,
        explanation=explanation,
        signals=signals or [],
    )


def score_technical(ctx: ScoringContext, ex: OpportunityExtraction) -> tuple[float, str, list[str]]:
    techs = [t for t in ex.technologies]
    if not techs:
        return 50, "no technologies detected — neutral", []
    matched: list[str] = []
    missing: list[str] = []
    total = 0.0
    for t in techs:
        sk = ctx.skills_by_name.get(ctx.canonical(t))
        if sk is None:
            missing.append(t)
            continue
        w = LEVEL_WEIGHT[sk.level] + TIER_BONUS[sk.tier]
        total += min(1.0, w)
        matched.append(f"{t} ({sk.level})")
    score = 100 * total / len(techs)
    if missing and len(missing) >= len(techs) / 2:
        score -= 10
    expl = f"{len(matched)}/{len(techs)} technologies covered by verified skills"
    signals = [f"+ {m}" for m in matched] + [f"- missing: {m}" for m in missing]
    return score, expl, signals


def score_seniority(ctx: ScoringContext, ex: OpportunityExtraction) -> tuple[float, str, list[str]]:
    targets = {t.lower() for t in ctx.model.seniority_targets}
    if ex.seniority is None:
        return 60, "seniority not stated", []
    if str(ex.seniority) in targets:
        return 100, f"{ex.seniority} matches targets", [str(ex.seniority)]
    if ex.seniority == Seniority.mid:
        return 55, "mid-level role — below target seniority", ["mid"]
    return 20, f"{ex.seniority} is far from target seniority", [str(ex.seniority)]


def _annualize(
    comp_min: float | None, comp_max: float | None, period: CompensationPeriod | None
) -> tuple[float | None, str]:
    value = comp_max or comp_min
    if value is None:
        return None, "n/a"
    if period == CompensationPeriod.hour:
        return value, "hourly"
    if period == CompensationPeriod.day:
        return value / 8, "hourly"
    if period == CompensationPeriod.month:
        return value * 12, "annual"
    if period == CompensationPeriod.project:
        return None, "project"
    return value, "annual"


def score_compensation(
    ctx: ScoringContext, ex: OpportunityExtraction
) -> tuple[float, str, list[str]]:
    comp = ex.compensation
    if comp is None or comp.is_empty():
        return 55, "compensation not stated — neutral", []
    value, kind = _annualize(comp.min, comp.max, comp.period)
    if value is None:
        return 60, "project-based compensation — neutral", [comp.raw or ""]
    targets = ctx.model.compensation
    lo, target = (
        (targets.min_hourly, targets.target_hourly)
        if kind == "hourly"
        else (targets.min_annual, targets.target_annual)
    )
    if comp.currency and comp.currency != targets.currency:
        signals = [f"currency {comp.currency} ≠ {targets.currency} (not converted)"]
    else:
        signals = []
    if value >= target:
        score = 100
    elif value >= lo:
        score = 60 + 35 * (value - lo) / max(1, target - lo)
    else:
        score = max(0, 50 * value / lo)
    return (
        score,
        f"{kind} {value:,.0f} vs min {lo:,} / target {target:,} {targets.currency}",
        [*signals, comp.raw or ""],
    )


def score_remote_us(ctx: ScoringContext, ex: OpportunityExtraction) -> tuple[float, str, list[str]]:
    regions = {r.upper() for r in ex.remote_regions}
    home = ctx.model.eligibility.home_country.upper()
    contractor_ok = ex.contract_type in (
        ContractType.b2b,
        ContractType.freelance,
        ContractType.contract_to_hire,
    )
    signals = []
    if ex.remote_policy == RemotePolicy.remote_global:
        score, expl = 100, "remote worldwide"
    elif ex.remote_policy == RemotePolicy.remote_region:
        if regions & {"US", "UK", "LATAM", "APAC"} and not (regions & {home, "EU"}):
            score, expl = 10, f"remote restricted to {', '.join(sorted(regions))}"
        elif regions & {home, "EU"}:
            score, expl = 85, f"remote includes {', '.join(sorted(regions & {home, 'EU'}))}"
        else:
            score, expl = 60, "remote with unclear region"
    elif ex.remote_policy in (RemotePolicy.hybrid, RemotePolicy.onsite):
        score, expl = 0, f"{ex.remote_policy} — not remote"
    else:
        score, expl = 40, "remote policy unknown"
    if ex.employment_type == EmploymentType.full_time and not contractor_ok and "US" in regions:
        score = min(score, 30)
        signals.append("W-2 employment likely; contractor status not mentioned")
    if contractor_ok:
        signals.append("contractor-friendly")
    return score, expl, signals


def score_eu(ctx: ScoringContext, ex: OpportunityExtraction) -> tuple[float, str, list[str]]:
    regions = {r.upper() for r in ex.remote_regions}
    if ex.remote_policy == RemotePolicy.remote_global:
        return 95, "remote worldwide (EU timezone compatible)", []
    if regions & {"EU", "PL", "UK", "GE"}:
        return (
            90 if ex.remote_policy != RemotePolicy.onsite else 45,
            f"EU-region role ({', '.join(sorted(regions))})",
            [],
        )
    if "US" in regions:
        return 15, "US-restricted", []
    if ex.remote_policy in (RemotePolicy.hybrid, RemotePolicy.onsite):
        return 30, "on-site/hybrid outside home location", []
    return 50, "region unclear", []


def score_poland(ctx: ScoringContext, ex: OpportunityExtraction) -> tuple[float, str, list[str]]:
    regions = {r.upper() for r in ex.remote_regions}
    relocation = {c.upper() for c in ctx.model.eligibility.relocation_targets}
    if regions & relocation:
        return 90, "relocation-target country mentioned", sorted(regions & relocation)
    if "EU" in regions or ex.remote_policy == RemotePolicy.remote_global:
        return 70, "EU/global remote keeps the relocation option open", []
    return 30, "no relocation-target signal", []


def score_upwork(
    ctx: ScoringContext, ex: OpportunityExtraction, source: Source, text: str
) -> tuple[float, str, list[str]]:
    norm = normalize(text)
    offer_hits = [
        o.title
        for o in ctx.data.offers
        if any(f" {t.lower()} " in norm for t in o.technologies[:3])
    ]
    if (
        source == Source.upwork
        or ex.contract_type == ContractType.freelance
        or ex.employment_type == EmploymentType.project
    ):
        return min(100, 70 + 10 * len(offer_hits)), "freelance/project-shaped", offer_hits[:3]
    return 20, "not a freelance engagement", []


def _signal_score(
    text: str, signals: tuple[str, ...], base: int, step: int
) -> tuple[float, list[str]]:
    lowered = text.lower()
    hits = [sg for sg in signals if sg in lowered]
    return min(100, base + step * len(hits)), hits


def score_startup(
    ctx: ScoringContext, ex: OpportunityExtraction, text: str
) -> tuple[float, str, list[str]]:
    score, hits = _signal_score(text, STARTUP_SIGNALS, 30, 15)
    return score, f"{len(hits)} startup signals", hits


def score_enterprise(
    ctx: ScoringContext, ex: OpportunityExtraction, text: str
) -> tuple[float, str, list[str]]:
    score, hits = _signal_score(text, ENTERPRISE_SIGNALS, 30, 15)
    enterprise_stack = [
        t
        for t in ex.technologies
        if ctx.canonical(t) in {"snowflake", "databricks", "airflow", "aws", "spark", "kafka"}
    ]
    score = min(100, score + 5 * len(enterprise_stack))
    return (
        score,
        f"{len(hits)} enterprise signals, {len(enterprise_stack)} enterprise-stack techs",
        [*hits, *enterprise_stack],
    )


def score_learning(ctx: ScoringContext, ex: OpportunityExtraction) -> tuple[float, str, list[str]]:
    if not ex.technologies:
        return 30, "no technologies detected", []
    growth = []
    for t in ex.technologies:
        sk = ctx.skills_by_name.get(ctx.canonical(t))
        if (
            sk is None
            or sk.tier == SkillTier.target
            or sk.level in (SkillLevel.working, SkillLevel.learning)
        ):
            growth.append(t)
    share = len(growth) / len(ex.technologies)
    return (
        20 + 80 * share,
        f"{len(growth)}/{len(ex.technologies)} technologies would grow target skills",
        growth,
    )


def score_strategic(
    ctx: ScoringContext, ex: OpportunityExtraction, text: str
) -> tuple[float, str, list[str]]:
    strategic = [t for t in ex.technologies if ctx.in_group(t, MarketGroup.strategic_core)]
    agentic = [t for t in ex.technologies if ctx.in_group(t, MarketGroup.agentic)]
    lowered = text.lower()
    agentic_words = [
        w
        for w in ("llm", "rag", "agent", "ai-ready", "semantic layer", "genai", "generative ai")
        if w in lowered
    ]
    share = (len(strategic) + len(agentic)) / max(1, len(ex.technologies)) if ex.technologies else 0
    score = 20 + 60 * share + 10 * min(2, len(agentic_words))
    return (
        score,
        f"{len(strategic)} strategic-core, {len(agentic)} agentic techs, "
        f"{len(agentic_words)} AI signals",
        [*strategic, *agentic, *agentic_words],
    )


def score_effort(
    ctx: ScoringContext, ex: OpportunityExtraction, text: str
) -> tuple[float, str, list[str]]:
    lowered = text.lower()
    hits = sorted({label for needle, label in EFFORT_SIGNALS if needle in lowered})
    return max(20, 100 - 20 * len(hits)), f"{len(hits)} effort signals", hits


def score_risk(ctx: ScoringContext, ex: OpportunityExtraction) -> tuple[float, str, list[str]]:
    flags = list(ex.red_flags)
    return max(0, 100 - 20 * len(flags)), f"{len(flags)} red flags", flags


def score_opportunity(
    ctx: ScoringContext,
    ex: OpportunityExtraction,
    *,
    source: Source,
    text: str,
    vault_sha: str | None,
) -> ScoreOut:
    dims_cfg = ctx.model.dimensions
    computed: dict[ScoreDimension, tuple[float, str, list[str]]] = {
        ScoreDimension.technical_fit: score_technical(ctx, ex),
        ScoreDimension.seniority_fit: score_seniority(ctx, ex),
        ScoreDimension.compensation_fit: score_compensation(ctx, ex),
        ScoreDimension.remote_us_fit: score_remote_us(ctx, ex),
        ScoreDimension.eu_fit: score_eu(ctx, ex),
        ScoreDimension.poland_fallback_fit: score_poland(ctx, ex),
        ScoreDimension.upwork_fit: score_upwork(ctx, ex, source, text),
        ScoreDimension.startup_fit: score_startup(ctx, ex, text),
        ScoreDimension.enterprise_fit: score_enterprise(ctx, ex, text),
        ScoreDimension.learning_roi: score_learning(ctx, ex),
        ScoreDimension.strategic_upside: score_strategic(ctx, ex, text),
        ScoreDimension.application_effort: score_effort(ctx, ex, text),
        ScoreDimension.risk: score_risk(ctx, ex),
    }
    dimensions: list[DimensionScore] = []
    weighted = 0.0
    weight_sum = 0.0
    for name, (score, expl, signals) in computed.items():
        cfg = dims_cfg.get(name)
        if cfg is None or not cfg.enabled:
            continue
        d = _dim(name, score, cfg.weight, expl, signals)
        dimensions.append(d)
        weighted += d.score * cfg.weight
        weight_sum += cfg.weight
    overall = round(weighted / weight_sum) if weight_sum else 0
    dimensions.insert(
        0, _dim(ScoreDimension.overall_fit, overall, 0.0, "weighted sum of enabled dimensions")
    )

    by_name = {d.name: d for d in dimensions}
    th = ctx.model.thresholds
    reasons: list[str] = []
    if overall >= th.high_priority:
        rec = Recommendation.high_priority
    elif overall >= th.apply:
        rec = Recommendation.apply
    elif overall >= th.watch:
        rec = Recommendation.watch
    else:
        rec = Recommendation.ignore
    reasons.append(
        f"overall {overall} vs thresholds high≥{th.high_priority}, "
        f"apply≥{th.apply}, watch≥{th.watch}"
    )

    comp_unknown = ex.compensation is None or ex.compensation.is_empty()
    comp_low = not comp_unknown and by_name[ScoreDimension.compensation_fit].score < 50
    if rec in (Recommendation.apply, Recommendation.high_priority):
        if source in (Source.recruiter, Source.email, Source.direct):
            rec = Recommendation.reply_now
            reasons.append("inbound from a person — reply instead of applying cold")
        elif comp_low and by_name[ScoreDimension.technical_fit].score >= 70:
            rec = Recommendation.negotiate
            reasons.append("strong technical fit but compensation below minimum")
        elif ex.remote_policy == RemotePolicy.unknown or comp_unknown:
            rec = Recommendation.ask_questions_first
            reasons.append("remote policy or compensation unknown — clarify before investing")
    if (
        by_name[ScoreDimension.remote_us_fit].score == 0
        and by_name[ScoreDimension.eu_fit].score <= 30
        and rec != Recommendation.ignore
    ):
        rec = Recommendation.watch if overall >= th.watch else Recommendation.ignore
        reasons.append("not remote-compatible — downgraded")

    return ScoreOut(
        overall=overall,
        recommendation=rec,
        dimensions=dimensions,
        scoring_version=ctx.model.version,
        vault_sha=vault_sha,
        reasons=reasons,
    )

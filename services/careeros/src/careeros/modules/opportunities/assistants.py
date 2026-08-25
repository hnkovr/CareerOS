"""Interview & negotiation intelligence + comparison-ranking guard (P3).

Deterministic frames come first — what the vault can *prove* for this opportunity (story
materials with fact ids; matched / claimed-only / missing technologies; the compensation position
against the owner's targets and the observed stream). AI then organises a frame into a prep plan,
a negotiation script or a ranked comparison. It never adds facts: stories must cite vault ids and
pass the provenance guard (ADR-010 §2), every number in a negotiation line must come from the frame
or a cited fact, and a ranking must list each compared opportunity exactly once.
"""

from __future__ import annotations

import math
import uuid
from typing import Any

from careeros.modules.cv.provenance import (
    FactSource,
    check_bullet,
    company_name_map,
    fact_sources,
    numbers_in,
)
from careeros.modules.opportunities.enums import (
    CompensationPeriod,
    ContractType,
    EmploymentType,
    RemotePolicy,
)
from careeros.modules.opportunities.schemas import (
    CompareRankingOutput,
    CompBand,
    Compensation,
    ExpectedQuestion,
    InterviewFrame,
    InterviewPrepOutput,
    LeverageFact,
    LeveragePoint,
    NegotiationFrame,
    NegotiationPlanOutput,
    OpportunityOut,
    ScoreOut,
    Story,
    StoryMaterial,
)
from careeros.modules.vault import schema as s

EMPLOYMENT_STAGES = ["recruiter_screen", "technical", "system_design", "final"]
FREELANCE_STAGES = ["discovery", "client_call", "proposal"]
WEAK_DIMENSION = 50
MAX_MATERIALS = 8
MAX_LEVERAGE = 5
AT_TARGET_BAND = 0.10  # an offer within +10 % of the target counts as "at target"


def _norm(name: str) -> str:
    return name.strip().lower()


def track_for(opp: OpportunityOut) -> str:
    if opp.contract_type == ContractType.freelance or opp.employment_type == EmploymentType.project:
        return "freelance"
    return "employment"


def _canonical(data: s.VaultData) -> dict[str, str]:
    """lower-cased skill name / alias → canonical skill name (scoring aliases included)."""
    out: dict[str, str] = {}
    for sk in data.skills:
        out[_norm(sk.name)] = sk.name
        for alias in sk.aliases:
            out.setdefault(_norm(alias), sk.name)
    if data.scoring:
        for alias, canon in data.scoring.aliases.items():
            out.setdefault(_norm(alias), canon)
    return out


def _metric_lines(metrics: list[s.Metric]) -> list[str]:
    lines: list[str] = []
    for m in metrics:
        parts = [f"{m.name}:", m.value, m.unit or ""]
        if m.baseline:
            parts.append(f"(baseline {m.baseline})")
        lines.append(" ".join(p for p in parts if p))
    return lines


# ----------------------------------------------------------------------------- interview


def story_materials(data: s.VaultData, technologies: list[str]) -> list[StoryMaterial]:
    """Vault items citing at least one of the opportunity's technologies, best-evidenced first."""
    canon = _canonical(data)
    wanted = {_norm(canon.get(_norm(t), t)) for t in technologies}
    companies = {e.company_id: e.company_name for e in data.experience}

    def matched(techs: list[str]) -> list[str]:
        found: list[str] = []
        for t in techs:
            c = canon.get(_norm(t), t)
            if _norm(c) in wanted and c not in found:
                found.append(c)
        return found

    out: list[StoryMaterial] = []
    for a in data.achievements:
        if a.status == "retired":
            continue
        m = matched([*a.technologies.all(), *a.keywords])
        if m:
            out.append(
                StoryMaterial(
                    fact_id=a.id,
                    kind="achievement",
                    title=a.title,
                    company=companies.get(a.company_id),
                    technologies=m,
                    facts=list(a.facts),
                    metrics=_metric_lines(a.metrics),
                )
            )
    for p in data.projects:
        if p.status == "retired":
            continue
        m = matched(p.technologies)
        if m:
            out.append(
                StoryMaterial(
                    fact_id=p.id,
                    kind="project",
                    title=p.name,
                    company=companies.get(p.company_id) if p.company_id else None,
                    technologies=m,
                    facts=[x for x in (p.summary, p.problem, p.solution, p.outcome) if x],
                    metrics=_metric_lines(p.metrics),
                )
            )
    for e in data.experience:
        if e.status == "retired":
            continue
        m = matched(e.technologies)
        if m:
            out.append(
                StoryMaterial(
                    fact_id=e.id,
                    kind="experience",
                    title=f"{e.roles[0].title} @ {e.company_name}",
                    company=e.company_name,
                    technologies=m,
                    facts=[e.summary, *e.responsibilities[:3]],
                    metrics=[],
                )
            )
    out.sort(key=lambda m: (-len(m.technologies), -len(m.metrics), m.kind != "achievement"))
    return out


def default_questions(opp: OpportunityOut, missing: list[str]) -> list[str]:
    """What the posting leaves open — derived from fields, not from AI."""
    q: list[str] = []
    freelance = track_for(opp) == "freelance"
    if opp.compensation is None or opp.compensation.is_empty():
        q.append(
            "What is the budget or hourly rate range for this engagement?"
            if freelance
            else "What is the compensation range for this role?"
        )
    if opp.remote_policy in (RemotePolicy.unknown, RemotePolicy.hybrid):
        q.append("Is the role fully remote, and which regions or time-zone overlap do you expect?")
    if opp.contract_type is None:
        q.append("Is this an employment contract or a B2B/contractor engagement?")
    if opp.seniority is None:
        q.append("How do you define the scope and seniority of this position?")
    if missing:
        q.append(f"How central are {', '.join(missing[:3])} to the day-to-day work?")
    q.append("What does the process look like from here — stages, interviewers and timeline?")
    return q


def interview_frame(
    data: s.VaultData, opp: OpportunityOut, score: ScoreOut | None
) -> InterviewFrame:
    canon = _canonical(data)
    materials = story_materials(data, opp.technologies)
    matched: list[str] = []
    for m in materials:
        for t in m.technologies:
            if t not in matched:
                matched.append(t)
    claimed_only: list[str] = []
    missing: list[str] = []
    for t in opp.technologies:
        c = canon.get(_norm(t))
        if c is None:
            if t not in missing:
                missing.append(t)
        elif c not in matched and c not in claimed_only:
            claimed_only.append(c)
    weak = [
        f"{d.name}: {d.explanation}"
        for d in (score.dimensions if score else [])
        if d.score < WEAK_DIMENSION
    ]
    track = track_for(opp)
    return InterviewFrame(
        track=track,  # type: ignore[arg-type]
        stages=FREELANCE_STAGES if track == "freelance" else EMPLOYMENT_STAGES,
        matched=matched,
        claimed_only=claimed_only,
        missing=missing,
        materials=materials[:MAX_MATERIALS],
        weak_dimensions=weak,
        questions_to_ask=default_questions(opp, missing),
    )


def _uncited_problems(text: str, companies: dict[str, str]) -> list[str]:
    problems: list[str] = []
    if numbers_in(text):
        problems.append("states numbers without citing facts")
    lowered = text.lower()
    for name in companies.values():
        if name.lower() in lowered:
            problems.append(f"mentions company '{name}' without citing facts")
    return problems


def guard_interview(
    plan: InterviewPrepOutput, data: s.VaultData
) -> tuple[InterviewPrepOutput, list[str]]:
    """Drop stories/answers that cite unknown facts, invent numbers or name uncited employers."""
    sources = fact_sources(data)
    companies = company_name_map(data)
    rejected: list[str] = []
    stories: list[Story] = []
    for st in plan.stories:
        text = " ".join([st.title, st.situation, st.action, st.result])
        problems = check_bullet(text, st.derived_from, sources, companies)
        if problems:
            rejected.append(f"story '{st.title}': " + "; ".join(problems))
        else:
            stories.append(st)
    questions: list[ExpectedQuestion] = []
    for q in plan.expected_questions:
        text = " ".join([q.question, q.why, q.answer_outline])
        problems = (
            check_bullet(text, q.derived_from, sources, companies)
            if q.derived_from
            else _uncited_problems(text, companies)
        )
        if problems:
            rejected.append(f"answer to '{q.question}': " + "; ".join(problems))
        else:
            questions.append(q)
    return plan.model_copy(update={"stories": stories, "expected_questions": questions}), rejected


# ----------------------------------------------------------------------------- negotiation


def _normalise(
    comp: Compensation | None, notes: list[str]
) -> tuple[str | None, float | None, float | None]:
    """→ (basis, min, max) in annual or hourly units; basis None when it cannot be normalised."""
    if comp is None or comp.is_empty():
        return None, None, None
    lo, hi = comp.min, comp.max
    period = comp.period
    if period is None:
        ref = hi if hi is not None else lo
        assert ref is not None
        if ref < 500:
            period = CompensationPeriod.hour
        elif ref < 20000:
            period = CompensationPeriod.month
        else:
            period = CompensationPeriod.year
        notes.append(f"no period stated — read as per {period.value} from the magnitude")
    if period == CompensationPeriod.hour:
        return "hourly", lo, hi
    if period == CompensationPeriod.year:
        return "annual", lo, hi
    if period == CompensationPeriod.month:
        return (
            "annual",
            (lo * 12 if lo is not None else None),
            (hi * 12 if hi is not None else None),
        )
    notes.append(f"per-{period.value} figures are not normalised")
    return None, lo, hi


def _percentile(values: list[float], q: float) -> float:
    xs = sorted(values)
    pos = (len(xs) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def observed_band(
    stream: list[dict[str, Any]], *, basis: str, currency: str, exclude_id: str
) -> CompBand:
    """Compensation percentiles of the *other* observed opportunities on the same basis/currency."""
    vals: list[float] = []
    for r in stream:
        if r.get("id") == exclude_id:
            continue
        raw = r.get("compensation")
        if not raw or (raw.get("currency") or "").upper() != currency.upper():
            continue
        try:
            comp = Compensation.model_validate(raw)
        except ValueError:
            continue
        b, lo, hi = _normalise(comp, [])
        if b != basis:
            continue
        v = hi if hi is not None else lo
        if v is not None:
            vals.append(float(v))
    if not vals:
        return CompBand()
    return CompBand(
        n=len(vals),
        p25=float(round(_percentile(vals, 0.25))),
        median=float(round(_percentile(vals, 0.5))),
        p75=float(round(_percentile(vals, 0.75))),
    )


def _allowed_numbers(values: list[float | None]) -> list[str]:
    """Every rendering of a frame number the guard accepts ("140000", "140k", "12.5", "12.5%")."""
    out: set[str] = set()
    for v in values:
        if v is None:
            continue
        if float(v).is_integer():
            n = int(v)
            out.add(str(n))
            out.add(f"{n}%")
            if n >= 1000 and n % 100 == 0:
                out.add(f"{n / 1000:g}k")
        else:
            out.add(f"{v:g}")
            out.add(f"{v:g}%")
    return sorted(out)


def negotiation_frame(
    data: s.VaultData, opp: OpportunityOut, stream: list[dict[str, Any]]
) -> NegotiationFrame:
    notes: list[str] = []
    unknowns: list[str] = []
    targets = data.scoring.compensation if data.scoring else None
    track = track_for(opp)
    basis, lo, hi = _normalise(opp.compensation, notes)
    if basis is None:
        basis = "hourly" if track == "freelance" else "annual"
    offered_ccy = opp.compensation.currency if opp.compensation else None
    currency = (offered_ccy or (targets.currency if targets else "USD")).upper()

    target: float | None = None
    floor: float | None = None
    if targets is None:
        unknowns.append("no compensation targets in scoring/model.yaml")
    elif targets.currency.upper() != currency:
        unknowns.append(
            f"offer in {currency}, targets in {targets.currency} — no conversion applied"
        )
    else:
        target = float(targets.target_annual if basis == "annual" else targets.target_hourly)
        floor = float(targets.min_annual if basis == "annual" else targets.min_hourly)
    if lo is None and hi is None:
        unknowns.append("compensation not stated")
    if opp.remote_policy == RemotePolicy.unknown:
        unknowns.append("remote policy not stated")
    if opp.contract_type is None:
        unknowns.append("contract type not stated")

    band = observed_band(stream, basis=basis, currency=currency, exclude_id=str(opp.id))
    position = "unknown"
    gap: float | None = None
    ref = hi if hi is not None else lo
    if ref is not None and target is not None and floor is not None:
        if ref < floor:
            position = "below_floor"
        elif ref < target:
            position = "below_target"
        elif ref <= target * (1 + AT_TARGET_BAND):
            position = "at_target"
        else:
            position = "above_target"
        gap = round((target - ref) / target * 100, 1)
    step = 1000 if basis == "annual" else 5
    candidates = [v for v in (target, band.p75) if v is not None]
    anchor = float(round(max(candidates) / step) * step) if candidates else None
    if anchor is not None and band.p75 is not None and target is not None and band.p75 > target:
        notes.append("anchor lifted to the observed p75 — the stream pays above your target")

    leverage = [
        LeverageFact(
            fact_id=m.fact_id, title=m.title, technologies=m.technologies, metrics=m.metrics
        )
        for m in story_materials(data, opp.technologies)[:MAX_LEVERAGE]
    ]
    allowed = _allowed_numbers(
        [lo, hi, target, floor, anchor, band.p25, band.median, band.p75, gap]
    )
    if opp.compensation and opp.compensation.raw:
        allowed = sorted({*allowed, *numbers_in(opp.compensation.raw)})
    return NegotiationFrame(
        basis=basis,  # type: ignore[arg-type]
        currency=currency,
        offered_min=lo,
        offered_max=hi,
        offered_raw=opp.compensation.raw if opp.compensation else None,
        offered_currency=offered_ccy,
        target=target,
        floor=floor,
        anchor=anchor,
        observed=band,
        position=position,  # type: ignore[arg-type]
        gap_to_target_pct=gap,
        leverage=leverage,
        unknowns=unknowns,
        notes=notes,
        allowed_numbers=allowed,
    )


def _line_problems(
    text: str,
    derived_from: list[str],
    *,
    allowed: set[str],
    sources: dict[str, FactSource],
    companies: dict[str, str],
) -> list[str]:
    problems: list[str] = []
    unknown = [f for f in derived_from if f not in sources]
    if unknown:
        problems.append(f"unknown fact ids: {', '.join(unknown)}")
    cited = [sources[f] for f in derived_from if f in sources]
    cited_numbers = set(numbers_in(" ".join(c.text for c in cited)))
    foreign = sorted(set(numbers_in(text)) - allowed - cited_numbers)
    if foreign:
        problems.append(f"numbers not in the frame or cited facts: {', '.join(foreign)}")
    cited_companies = {c.company_id for c in cited if c.company_id}
    lowered = text.lower()
    for company_id, name in companies.items():
        if company_id not in cited_companies and name.lower() in lowered:
            problems.append(f"mentions company '{name}' outside cited facts")
    return problems


def guard_negotiation(
    plan: NegotiationPlanOutput, frame: NegotiationFrame, data: s.VaultData
) -> tuple[NegotiationPlanOutput, list[str]]:
    """Keep only lines whose numbers come from the frame or cited facts; never let a made-up
    figure reach the owner's mouth."""
    sources = fact_sources(data)
    companies = company_name_map(data)
    allowed = set(frame.allowed_numbers)
    rejected: list[str] = []

    def keep(label: str, text: str, derived: list[str] | None = None) -> bool:
        problems = _line_problems(
            text, derived or [], allowed=allowed, sources=sources, companies=companies
        )
        if problems:
            rejected.append(f"{label} '{text[:60]}': " + "; ".join(problems))
            return False
        return True

    update: dict[str, Any] = {}
    if not keep("rationale", plan.rationale):
        update["rationale"] = (
            f"Position: {frame.position.replace('_', ' ')} (frame numbers only; AI rationale "
            "rejected by the provenance guard)."
        )
    if plan.counter_ask and not keep("counter_ask", plan.counter_ask):
        update["counter_ask"] = None
    update["leverage"] = [
        lp
        for lp in plan.leverage
        if isinstance(lp, LeveragePoint) and keep("leverage", lp.point, lp.derived_from)
    ]
    for field in ("concessions", "script", "questions", "risks"):
        update[field] = [line for line in getattr(plan, field) if keep(field, line)]
    return plan.model_copy(update=update), rejected


# ----------------------------------------------------------------------------- compare


def ranking_problem(out: CompareRankingOutput, ids: list[uuid.UUID]) -> str | None:
    """None when the AI ranking is a permutation of the compared ids with ranks 1..n."""
    expected = {str(i) for i in ids}
    got = [r.opportunity_id for r in out.ranking]
    if len(got) != len(expected) or set(got) != expected:
        return "ranking must list each compared opportunity exactly once"
    if sorted(r.rank for r in out.ranking) != list(range(1, len(ids) + 1)):
        return "ranks must be 1..n without gaps"
    return None

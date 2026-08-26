"""Read-only tools the assistant may call (ADR-014).

Every tool is a thin, typed wrapper over a module's *service layer* (never its ORM). Each result
is recorded in ``ToolContext.observed`` so the provenance guard can check that the final answer
states only numbers the model actually saw, and every entity id a tool surfaces is recorded in
``ToolContext.seen_ids`` so it may be cited in ``derived_from``. No tool writes anything —
actions stay behind Suggestions/Actions and human approval (ADR-010).
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.config import Settings
from careeros.modules.ai.schemas import ToolCall, ToolSpec
from careeros.modules.ai.service import AIService
from careeros.modules.assistant.schemas import ToolInfo
from careeros.modules.cv.provenance import fact_sources
from careeros.modules.opportunities.service import OpportunityService, opportunity_stream
from careeros.modules.pipeline.service import PipelineService, application_summaries
from careeros.modules.profiles.service import ProfileService, open_drift_count
from careeros.modules.vault.service import Vault


@dataclass
class ToolContext:
    settings: Settings
    vault: Vault
    ai: AIService
    session: AsyncSession
    user_id: uuid.UUID
    observed: list[str] = field(default_factory=list)
    seen_ids: set[str] = field(default_factory=set)


Handler = Callable[[ToolContext, Any], Awaitable[Any]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Handler

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            input_schema=self.input_model.model_json_schema(),
        )


class ToolRegistry:
    def __init__(self, tools: Sequence[Tool]) -> None:
        self._tools = {t.name: t for t in tools}

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self._tools.values()]

    def infos(self) -> list[ToolInfo]:
        return [ToolInfo(name=t.name, description=t.description) for t in self._tools.values()]

    async def execute(self, ctx: ToolContext, call: ToolCall) -> str:
        """Validate the arguments, run the handler, return JSON text for the model."""
        tool = self._tools.get(call.name)
        if tool is None:
            raise KeyError(f"unknown tool '{call.name}'; available: {', '.join(self._tools)}")
        args = tool.input_model.model_validate(call.arguments)
        result = await tool.handler(ctx, args)
        text = json.dumps(result, default=str, ensure_ascii=False)
        ctx.observed.append(text)
        return text


# ----------------------------------------------------------------------------- vault tools


class FactsArgs(BaseModel):
    section: Literal[
        "profile",
        "positioning",
        "skills",
        "achievements",
        "projects",
        "experience",
        "education",
        "all",
    ] = "all"
    limit: int = Field(default=20, ge=1, le=50, description="max items per list section")


async def get_career_facts(ctx: ToolContext, a: FactsArgs) -> dict[str, Any]:
    data = ctx.vault.require()
    want = (
        {"profile", "positioning", "skills", "achievements", "projects", "experience", "education"}
        if a.section == "all"
        else {a.section}
    )
    companies = {e.company_id: e.company_name for e in data.experience}
    out: dict[str, Any] = {}
    if "profile" in want:
        p = data.profile
        out["profile"] = {
            "id": p.id,
            "headline": p.headline_baseline,
            "summary": p.summary_core,
            "location": p.location.model_dump(mode="json"),
            "eligibility": p.eligibility.model_dump(mode="json"),
        }
        ctx.seen_ids.add(p.id)
    if "positioning" in want:
        pos = data.by_id(data.positioning)[data.meta.default_positioning]
        out["positioning"] = pos.model_dump(
            mode="json",
            include={"id", "name", "headline", "summary", "target_markets", "keywords_must"},
        )
        ctx.seen_ids.add(pos.id)
    if "skills" in want:
        out["skills"] = [
            {
                "id": s.id,
                "name": s.name,
                "tier": str(s.tier),
                "level": str(s.level),
                "years": s.years,
            }
            for s in data.skills[: a.limit]
        ]
        ctx.seen_ids.update(s.id for s in data.skills)
    if "achievements" in want:
        out["achievements"] = [
            {
                "id": x.id,
                "title": x.title,
                "company": companies.get(x.company_id),
                "facts": x.facts,
                "metrics": [m.model_dump(mode="json", exclude_none=True) for m in x.metrics],
                "technologies": x.technologies.all(),
                "period": x.period,
            }
            for x in data.achievements[: a.limit]
        ]
        ctx.seen_ids.update(x.id for x in data.achievements)
    if "projects" in want:
        out["projects"] = [
            {
                "id": x.id,
                "name": x.name,
                "summary": x.summary,
                "outcome": x.outcome,
                "technologies": x.technologies,
            }
            for x in data.projects[: a.limit]
        ]
        ctx.seen_ids.update(x.id for x in data.projects)
    if "experience" in want:
        out["experience"] = [
            {
                "id": e.id,
                "company": e.company_name,
                "roles": [
                    {"title": r.title, "start": str(r.start), "end": str(r.end) if r.end else None}
                    for r in e.roles
                ],
                "summary": e.summary,
                "technologies": e.technologies,
            }
            for e in data.experience[: a.limit]
        ]
        ctx.seen_ids.update(e.id for e in data.experience)
    if "education" in want:
        out["education"] = [
            x.model_dump(mode="json", exclude_none=True) for x in data.education[: a.limit]
        ]
        ctx.seen_ids.update(x.id for x in data.education)
    return out


class SearchArgs(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    limit: int = Field(default=8, ge=1, le=20)


_TOKEN_RE = re.compile(r"[a-z0-9+#.]+")


async def search_facts(ctx: ToolContext, a: SearchArgs) -> dict[str, Any]:
    """Keyword search over every citeable vault item (achievements, projects, experience,
    skills, profile) — deterministic, no embeddings needed."""
    data = ctx.vault.require()
    sources = fact_sources(data)
    tokens = [t for t in _TOKEN_RE.findall(a.query.lower()) if len(t) > 1]
    scored: list[tuple[int, str, str]] = []
    for fid, src in sources.items():
        text = src.text.lower()
        hits = sum(1 for t in tokens if t in text)
        if hits:
            scored.append((hits, fid, src.text))
    scored.sort(key=lambda x: (-x[0], x[1]))
    items = [
        {"id": fid, "matched_terms": hits, "excerpt": text[:400]}
        for hits, fid, text in scored[: a.limit]
    ]
    ctx.seen_ids.update(i["id"] for i in items)
    return {"query": a.query, "count": len(scored), "items": items}


# ----------------------------------------------------------------------------- operational tools

_OPPORTUNITY_FIELDS = {
    "id",
    "title",
    "company_name",
    "source",
    "status",
    "contract_type",
    "employment_type",
    "remote_policy",
    "remote_regions",
    "seniority",
    "compensation",
    "requirements",
    "preferred",
    "technologies",
    "summary",
    "red_flags",
    "deadline",
    "received_at",
}


class OpportunityArgs(BaseModel):
    opportunity_id: uuid.UUID


async def get_opportunity(ctx: ToolContext, a: OpportunityArgs) -> dict[str, Any]:
    svc = OpportunityService(
        ctx.settings, ctx.vault, ctx.ai, session=ctx.session, user_id=ctx.user_id
    )
    d = await svc.get(a.opportunity_id)
    out = d.model_dump(mode="json", include=_OPPORTUNITY_FIELDS)
    if d.score:
        out["score"] = {
            "overall": d.score.overall,
            "recommendation": str(d.score.recommendation),
            "reasons": d.score.reasons,
            "dimensions": [
                {"name": str(x.name), "score": x.score, "explanation": x.explanation}
                for x in d.score.dimensions
            ],
        }
    if d.analysis:
        out["analysis"] = d.analysis.model_dump(
            mode="json",
            include={"verdict", "executive_summary", "gaps", "risks", "next_action"},
        )
    ctx.seen_ids.add(str(d.id))
    return out


class ListOpportunitiesArgs(BaseModel):
    status: Literal["new", "watching", "applied", "ignored", "archived"] | None = None
    min_score: int | None = Field(default=None, ge=0, le=100)
    limit: int = Field(default=10, ge=1, le=30)


async def list_opportunities(ctx: ToolContext, a: ListOpportunitiesArgs) -> dict[str, Any]:
    rows = await opportunity_stream(ctx.session, limit=500)
    rows = [
        r
        for r in rows
        if (a.status is None or r["status"] == a.status)
        and (a.min_score is None or (r["score"] or 0) >= a.min_score)
    ]
    keys = (
        "id",
        "title",
        "company",
        "status",
        "score",
        "recommendation",
        "remote_policy",
        "contract_type",
        "technologies",
        "received_at",
    )
    items = [{k: r.get(k) for k in keys} for r in rows[: a.limit]]
    ctx.seen_ids.update(str(i["id"]) for i in items)
    return {"count": len(rows), "items": items}


class ApplicationsArgs(BaseModel):
    application_id: uuid.UUID | None = Field(default=None, description="one application, in full")
    opportunity_id: uuid.UUID | None = Field(default=None, description="filter by opportunity")
    limit: int = Field(default=10, ge=1, le=30)


async def get_applications(ctx: ToolContext, a: ApplicationsArgs) -> dict[str, Any]:
    if a.application_id is not None:
        d = await PipelineService(ctx.session, ctx.user_id).get(a.application_id)
        item = d.model_dump(
            mode="json",
            include={
                "id",
                "opportunity_id",
                "opportunity_title",
                "company_name",
                "kind",
                "stage",
                "applied_at",
                "next_follow_up_at",
                "closed_at",
                "notes",
                "score_overall",
            },
        )
        item["events"] = [
            e.model_dump(mode="json", include={"kind", "at", "title"}) for e in d.events
        ]
        item["interviews"] = [
            i.model_dump(mode="json", include={"kind", "scheduled_at", "outcome"})
            for i in d.interviews
        ]
        ctx.seen_ids.update({str(d.id), str(d.opportunity_id)})
        return {"count": 1, "items": [item]}
    rows = await application_summaries(ctx.session, opportunity_id=a.opportunity_id, limit=a.limit)
    ctx.seen_ids.update(str(r["id"]) for r in rows)
    return {"count": len(rows), "items": rows}


class HealthArgs(BaseModel):
    platform: str | None = Field(default=None, description="e.g. linkedin, upwork; all if omitted")


async def get_profile_health(ctx: ToolContext, a: HealthArgs) -> dict[str, Any]:
    svc = ProfileService(ctx.settings, ctx.vault, ctx.ai, session=ctx.session, user_id=ctx.user_id)
    rows = await svc.platform_health()
    platforms = [
        r.model_dump(mode="json")
        for r in rows
        if a.platform is None or str(r.platform) == a.platform
    ]
    return {"platforms": platforms, "open_drift": await open_drift_count(ctx.session)}


TOOLS: list[Tool] = [
    Tool(
        "get_career_facts",
        "The owner's verified facts from the vault: profile, positioning, skills, achievements "
        "(with metrics), projects, experience, education. Every item carries its fact id.",
        FactsArgs,
        get_career_facts,
    ),
    Tool(
        "search_facts",
        "Keyword search over the owner's citeable facts; returns fact ids with excerpts. Use it "
        "before answering anything about what the owner has done or can prove.",
        SearchArgs,
        search_facts,
    ),
    Tool(
        "get_opportunity",
        "One opportunity: parsed fields, the deterministic score breakdown and the latest AI "
        "analysis, if any.",
        OpportunityArgs,
        get_opportunity,
    ),
    Tool(
        "list_opportunities",
        "The observed opportunity stream, newest first, optionally filtered by status and "
        "minimum score.",
        ListOpportunitiesArgs,
        list_opportunities,
    ),
    Tool(
        "get_applications",
        "Applications in the pipeline (stage, follow-ups); one application in full when "
        "application_id is given, including its timeline and interviews.",
        ApplicationsArgs,
        get_applications,
    ),
    Tool(
        "get_profile_health",
        "Health score and open findings per platform profile, plus the number of open drift "
        "findings between profiles and the vault.",
        HealthArgs,
        get_profile_health,
    ),
]


def default_registry() -> ToolRegistry:
    return ToolRegistry(TOOLS)

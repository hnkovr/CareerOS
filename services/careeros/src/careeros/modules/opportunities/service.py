"""Opportunity service: ingest → parse → dedup → score → AI analysis → compare → external prompt."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from careeros.core.config import Settings
from careeros.core.logging import get_logger
from careeros.modules.ai.provider import AIError
from careeros.modules.ai.schemas import BundleOut, BundleRequest
from careeros.modules.ai.service import AIService
from careeros.modules.cv.keywords import tech_vocabulary
from careeros.modules.opportunities.assistants import (
    guard_interview,
    guard_negotiation,
    interview_frame,
    negotiation_frame,
    ranking_problem,
)
from careeros.modules.opportunities.dedup import FUZZY_THRESHOLD, dedup_key, similarity
from careeros.modules.opportunities.enums import OpportunityStatus, Recommendation, Source
from careeros.modules.opportunities.models import (
    Opportunity,
    OpportunityAnalysis,
    OpportunityRaw,
    OpportunityScore,
)
from careeros.modules.opportunities.parser import merge_extractions, parse_text
from careeros.modules.opportunities.schemas import (
    AnalysisOut,
    CompareOut,
    CompareRankingOutput,
    CompareRow,
    Compensation,
    IngestRequest,
    InterviewPrepOut,
    InterviewPrepOutput,
    NegotiationOut,
    NegotiationPlanOutput,
    OpportunityAnalysisOutput,
    OpportunityDetail,
    OpportunityExtraction,
    OpportunityOut,
    Recruiter,
    ScoreOut,
)
from careeros.modules.opportunities.scoring import ScoringContext, score_opportunity
from careeros.modules.vault.service import Vault

log = get_logger(__name__)


class OpportunityError(Exception):
    pass


def _raw_payload(req: IngestRequest) -> dict[str, Any] | None:
    """Verbatim source payload when the caller has one, else the structured fields."""
    payload: dict[str, Any] | None = req.raw_payload
    if payload is None and req.structured is not None:
        payload = req.structured.model_dump(mode="json")
    if req.external_id:
        payload = {**(payload or {}), "external_id": req.external_id}
    return payload


class OpportunityNotFound(OpportunityError):
    pass


class OpportunityService:
    def __init__(
        self,
        settings: Settings,
        vault: Vault,
        ai: AIService,
        *,
        session: AsyncSession,
        user_id: uuid.UUID,
    ) -> None:
        self.settings = settings
        self.vault = vault
        self.ai = ai
        self.session = session
        self.user_id = user_id

    # ------------------------------------------------------------------ ingest
    async def ingest(self, req: IngestRequest) -> OpportunityDetail:
        if not (req.text or req.structured or req.url):
            raise OpportunityError("provide text, structured fields or a url")
        data = self.vault.require()
        raw_text = (
            req.text
            or (req.structured.summary if req.structured and req.structured.summary else "")
            or req.url
            or ""
        )
        now = datetime.now(UTC)
        raw = OpportunityRaw(
            user_id=self.user_id,
            source=str(req.source),
            url=req.url,
            raw_text=raw_text,
            raw_payload=_raw_payload(req),
            content_hash=hashlib.sha256(raw_text.encode()).hexdigest(),
            capture_method="structured"
            if req.structured
            else ("url" if req.url and not req.text else "paste"),
            captured_at=req.received_at or now,
        )

        vocab = tech_vocabulary(data)
        parsed = parse_text(raw_text, vocab, url=req.url) if raw_text else None
        extraction = parsed.extraction if parsed else OpportunityExtraction()
        confidence = parsed.confidence if parsed else 0.0
        parser = parsed.parser if parsed else "none"
        if req.structured:
            extraction = merge_extractions(req.structured, extraction)
            confidence = max(confidence, 0.9)
            parser = "structured+" + parser

        if req.use_ai and raw_text:
            try:
                run = await self.ai.structured(
                    "opportunity_extract",
                    {"raw_text": raw_text[:12000], "source": str(req.source), "url": req.url},
                    OpportunityExtraction,
                    provider=req.provider,
                    entity_type="opportunity_raw",
                    entity_id=str(raw.id),
                )
                extraction = merge_extractions(extraction, run.data)
                confidence = max(confidence, 0.95)
                parser += "+ai"
            except AIError as exc:
                log.warning("opportunity.ai_extract_failed", error=str(exc))

        title = extraction.title or (req.url or "Untitled opportunity")[:300]
        key = dedup_key(
            url=req.url, title=extraction.title, company=extraction.company, raw_text=raw_text
        )
        duplicate_of = await self._find_duplicate(key, title, extraction.company)

        opp = Opportunity(
            user_id=self.user_id,
            raw=raw,
            source=str(req.source),
            url=req.url,
            title=title[:300],
            company_name=extraction.company,
            contract_type=str(extraction.contract_type) if extraction.contract_type else None,
            employment_type=str(extraction.employment_type) if extraction.employment_type else None,
            location=extraction.location,
            remote_policy=str(extraction.remote_policy),
            remote_regions=list(extraction.remote_regions),
            timezone_range=extraction.timezone_range,
            compensation=extraction.compensation.model_dump(mode="json")
            if extraction.compensation
            else None,
            seniority=str(extraction.seniority) if extraction.seniority else None,
            requirements=list(extraction.requirements),
            preferred=list(extraction.preferred),
            technologies=list(extraction.technologies),
            responsibilities=list(extraction.responsibilities),
            description_md=raw_text,
            summary=extraction.summary,
            red_flags=list(extraction.red_flags),
            recruiter=extraction.recruiter.model_dump(mode="json")
            if extraction.recruiter
            else None,
            received_at=req.received_at or now,
            deadline=extraction.deadline,
            status=str(OpportunityStatus.new),
            dedup_key=key,
            possible_duplicate_of=duplicate_of,
            parser=parser,
            parse_confidence=confidence,
            notes=req.notes,
        )
        self.session.add(opp)
        await self.session.flush()

        score = self._score(data, extraction, req.source, raw_text)
        self.session.add(self._score_row(score, opp.id))
        await self.session.commit()
        log.info(
            "opportunity.ingested",
            id=str(opp.id),
            title=opp.title,
            score=score.overall,
            rec=str(score.recommendation),
        )
        return await self.get(opp.id)

    async def _find_duplicate(self, key: str, title: str, company: str | None) -> uuid.UUID | None:
        exact = await self.session.scalar(
            select(Opportunity)
            .where(Opportunity.dedup_key == key)
            .order_by(Opportunity.created_at)
            .limit(1)
        )
        if exact is not None:
            return exact.id
        recent = (
            await self.session.scalars(
                select(Opportunity).order_by(Opportunity.created_at.desc()).limit(200)
            )
        ).all()
        for row in recent:
            if similarity(title, company, row.title, row.company_name) >= FUZZY_THRESHOLD:
                return row.id
        return None

    # ------------------------------------------------------------------ scoring
    def _score(
        self, data, extraction: OpportunityExtraction, source: Source, text: str
    ) -> ScoreOut:  # type: ignore[no-untyped-def]
        ctx = ScoringContext.build(data)
        return score_opportunity(
            ctx, extraction, source=source, text=text, vault_sha=self.vault.head_sha()
        )

    def _score_row(self, score: ScoreOut, opportunity_id: uuid.UUID) -> OpportunityScore:
        return OpportunityScore(
            opportunity_id=opportunity_id,
            scoring_version=score.scoring_version,
            vault_sha=score.vault_sha,
            overall=score.overall,
            recommendation=str(score.recommendation),
            dimensions=[d.model_dump(mode="json") for d in score.dimensions],
            reasons=list(score.reasons),
            computed_at=datetime.now(UTC),
        )

    async def rescore(self, opportunity_id: uuid.UUID) -> OpportunityDetail:
        row = await self._row(opportunity_id)
        data = self.vault.require()
        score = self._score(
            data, self._extraction_from_row(row), Source(row.source), row.description_md or ""
        )
        self.session.add(self._score_row(score, row.id))
        await self.session.commit()
        return await self.get(opportunity_id)

    # ------------------------------------------------------------------ AI analysis
    async def analyze(
        self, opportunity_id: uuid.UUID, *, provider: str | None = None
    ) -> OpportunityDetail:
        row = await self._row(opportunity_id)
        data = self.vault.require()
        score = self._latest_score(row)
        if score is None:
            score = self._score(
                data, self._extraction_from_row(row), Source(row.source), row.description_md or ""
            )
            self.session.add(self._score_row(score, row.id))
        positioning = data.by_id(data.positioning)[data.meta.default_positioning]
        run = await self.ai.structured(
            "opportunity_analysis",
            {
                "positioning": positioning.model_dump(mode="json"),
                "profile": data.profile.model_dump(mode="json"),
                "skills": [
                    {"name": sk.name, "tier": str(sk.tier), "level": str(sk.level)}
                    for sk in data.skills
                ],
                "opportunity": self._to_out(row, score, None).model_dump(mode="json"),
                "score": {
                    "overall": score.overall,
                    "recommendation": str(score.recommendation),
                    "dimensions": [
                        {
                            "name": str(d.name),
                            "score": d.score,
                            "weight": d.weight,
                            "explanation": d.explanation,
                        }
                        for d in score.dimensions
                    ],
                },
                "cv_variants": [v.id for v in data.cv_variants],
            },
            OpportunityAnalysisOutput,
            provider=provider,
            entity_type="opportunity",
            entity_id=str(opportunity_id),
        )
        variants = {v.id for v in data.cv_variants}
        payload = run.data.model_dump(mode="json")
        if run.data.recommended_cv_variant and run.data.recommended_cv_variant not in variants:
            payload["recommended_cv_variant"] = data.meta.default_cv_variant
        self.session.add(
            OpportunityAnalysis(
                opportunity_id=row.id,
                ai_run_id=run.run_id,
                provider=run.response.provider,
                model=run.response.model,
                payload=payload,
            )
        )
        await self.session.commit()
        return await self.get(opportunity_id)

    async def external_prompt(self, opportunity_id: uuid.UUID, target: str) -> BundleOut:
        row = await self._row(opportunity_id)
        data = self.vault.require()
        positioning = data.by_id(data.positioning)[data.meta.default_positioning]
        facts = [
            {"id": a.id, "title": a.title, "facts": a.facts}
            for a in data.achievements
            if a.status != "retired" and a.id in positioning.emphasize.achievements
        ] or [{"id": a.id, "title": a.title, "facts": a.facts} for a in data.achievements[:6]]
        comp = data.scoring.compensation if data.scoring else None
        constraints = [
            f"Based in {data.profile.location.city}, {data.profile.location.country}; "
            f"works as contractor: {data.profile.eligibility.works_as_contractor}",
            (
                f"Compensation targets: {comp.target_annual} {comp.currency}/year "
                f"or {comp.target_hourly}/hour"
                if comp
                else "Compensation targets: not configured"
            ),
            "Do not invent facts about me; use only the listed ones.",
        ]
        return await self.ai.bundle(
            BundleRequest(
                prompt_id="external_opportunity_analysis",
                target=target,  # type: ignore[arg-type]
                inputs={
                    "positioning": positioning.model_dump(mode="json"),
                    "profile": data.profile.model_dump(mode="json"),
                    "facts": facts,
                    "opportunity": row.description_md or row.summary or row.title,
                    "constraints": constraints,
                },
                entity_type="opportunity",
                entity_id=str(opportunity_id),
            )
        )

    # ------------------------------------------------------------------ read / update
    async def list(
        self,
        *,
        status: OpportunityStatus | None = None,
        min_score: int | None = None,
        limit: int = 100,
    ) -> list[OpportunityOut]:
        stmt = (
            select(Opportunity)
            .options(selectinload(Opportunity.scores), selectinload(Opportunity.analyses))
            .order_by(Opportunity.received_at.desc())
            .limit(limit)
        )
        if status:
            stmt = stmt.where(Opportunity.status == str(status))
        rows = (await self.session.scalars(stmt)).all()
        out = []
        for row in rows:
            score = self._latest_score(row)
            if min_score is not None and (score is None or score.overall < min_score):
                continue
            out.append(self._to_out(row, score, self._latest_analysis(row)))
        return out

    async def get(self, opportunity_id: uuid.UUID) -> OpportunityDetail:
        row = await self._row(opportunity_id)
        base = self._to_out(row, self._latest_score(row), self._latest_analysis(row))
        return OpportunityDetail(
            **base.model_dump(),
            description_md=row.description_md,
            raw_text=row.raw.raw_text if row.raw else None,
        )

    async def set_status(
        self, opportunity_id: uuid.UUID, status: OpportunityStatus
    ) -> OpportunityDetail:
        row = await self._row(opportunity_id)
        row.status = str(status)
        await self.session.commit()
        return await self.get(opportunity_id)

    async def compare(
        self, ids: list[uuid.UUID], *, use_ai: bool = False, provider: str | None = None
    ) -> CompareOut:
        rows = []
        for oid in ids:
            row = await self._row(oid)
            score = self._latest_score(row)
            if score is None:
                raise OpportunityError(f"{oid} has no score")
            rows.append((row, score))
        dim_names = [str(d.name) for d in rows[0][1].dimensions]
        out_rows = [
            CompareRow(
                id=row.id,
                title=row.title,
                company_name=row.company_name,
                overall=score.overall,
                recommendation=score.recommendation,
                dimensions={str(d.name): d.score for d in score.dimensions},
                compensation=(row.compensation or {}).get("raw"),
                remote_policy=row.remote_policy,  # type: ignore[arg-type]
            )
            for row, score in rows
        ]
        ranked = [r.id for r in sorted(out_rows, key=lambda r: -r.overall)]
        out = CompareOut(rows=out_rows, ranked=ranked, dimension_names=dim_names)
        if not use_ai:
            return out
        # §31 comparison mode: AI weighs the deterministic rows — it never re-scores them, and a
        # ranking that is not a permutation of the compared ids is dropped, not "fixed".
        data = self.vault.require()
        positioning = data.by_id(data.positioning)[data.meta.default_positioning]
        comp = data.scoring.compensation if data.scoring else None
        run = await self.ai.structured(
            "opportunity_compare",
            {
                "positioning": positioning.model_dump(mode="json"),
                "targets": comp.model_dump(mode="json") if comp else "not configured",
                "dimension_names": dim_names,
                "rows": [r.model_dump(mode="json") for r in out_rows],
            },
            CompareRankingOutput,
            provider=provider,
            entity_type="opportunity_compare",
        )
        problem = ranking_problem(run.data, ids)
        if problem:
            log.warning("opportunities.compare.ranking_rejected", problem=problem)
            return out.model_copy(
                update={"ranking_note": f"AI ranking rejected: {problem}", "ai_run_id": run.run_id}
            )
        return out.model_copy(
            update={
                "ranking": sorted(run.data.ranking, key=lambda r: r.rank),
                "recommendation": run.data.recommendation,
                "tradeoffs": run.data.tradeoffs,
                "ai_run_id": run.run_id,
            }
        )

    # ------------------------------------------------------------------ P3 assistants
    async def interview_prep(
        self, opportunity_id: uuid.UUID, *, use_ai: bool = True, provider: str | None = None
    ) -> InterviewPrepOut:
        """Deterministic evidence map + (optionally) an AI prep plan; stories cite vault ids."""
        detail = await self.get(opportunity_id)
        data = self.vault.require()
        frame = interview_frame(data, detail, detail.score)
        out = InterviewPrepOut(opportunity_id=detail.id, frame=frame)
        if not use_ai:
            return out
        positioning = data.by_id(data.positioning)[data.meta.default_positioning]
        run = await self.ai.structured(
            "interview_prep",
            {
                "positioning": positioning.model_dump(mode="json"),
                "profile": data.profile.model_dump(mode="json"),
                "opportunity": _brief(detail),
                "score": detail.score.model_dump(mode="json") if detail.score else None,
                "frame": frame.model_dump(mode="json"),
                "analysis": detail.analysis.model_dump(mode="json") if detail.analysis else None,
            },
            InterviewPrepOutput,
            provider=provider,
            entity_type="opportunity",
            entity_id=str(detail.id),
        )
        plan, rejected = guard_interview(run.data, data)
        suggestion_id = await self.ai.record_suggestion(
            target_type="interview_prep",
            target_ref=str(detail.id),
            title=f"Interview prep: {detail.title}",
            payload={"plan": plan.model_dump(mode="json"), "provenance_rejected": rejected},
            ai_run_id=run.run_id,
        )
        return out.model_copy(
            update={
                "plan": plan,
                "provenance_rejected": rejected,
                "ai_run_id": run.run_id,
                "suggestion_id": suggestion_id,
                "provider": run.response.provider,
                "model": run.response.model,
            }
        )

    async def negotiation(
        self, opportunity_id: uuid.UUID, *, use_ai: bool = True, provider: str | None = None
    ) -> NegotiationOut:
        """Compensation position (offer vs targets vs observed stream) + optional AI script;
        every number in it comes from the frame or a cited fact."""
        detail = await self.get(opportunity_id)
        data = self.vault.require()
        stream = await opportunity_stream(self.session)
        frame = negotiation_frame(data, detail, stream)
        out = NegotiationOut(opportunity_id=detail.id, frame=frame)
        if not use_ai:
            return out
        positioning = data.by_id(data.positioning)[data.meta.default_positioning]
        run = await self.ai.structured(
            "negotiation_plan",
            {
                "positioning": positioning.model_dump(mode="json"),
                "profile": data.profile.model_dump(mode="json"),
                "opportunity": _brief(detail),
                "frame": frame.model_dump(mode="json"),
                "analysis": detail.analysis.model_dump(mode="json") if detail.analysis else None,
            },
            NegotiationPlanOutput,
            provider=provider,
            entity_type="opportunity",
            entity_id=str(detail.id),
        )
        plan, rejected = guard_negotiation(run.data, frame, data)
        suggestion_id = await self.ai.record_suggestion(
            target_type="negotiation_plan",
            target_ref=str(detail.id),
            title=f"Negotiation plan: {detail.title}",
            payload={
                "frame": frame.model_dump(mode="json"),
                "plan": plan.model_dump(mode="json"),
                "provenance_rejected": rejected,
            },
            ai_run_id=run.run_id,
        )
        return out.model_copy(
            update={
                "plan": plan,
                "provenance_rejected": rejected,
                "ai_run_id": run.run_id,
                "suggestion_id": suggestion_id,
                "provider": run.response.provider,
                "model": run.response.model,
            }
        )

    # ------------------------------------------------------------------ internals
    async def _row(self, opportunity_id: uuid.UUID) -> Opportunity:
        row = await self.session.get(
            Opportunity,
            opportunity_id,
            options=[
                selectinload(Opportunity.scores),
                selectinload(Opportunity.analyses),
                selectinload(Opportunity.raw),
            ],
            populate_existing=True,  # identity-map hits must still apply the eager options
        )
        if row is None:
            raise OpportunityNotFound(str(opportunity_id))
        return row

    @staticmethod
    def _latest_score(row: Opportunity) -> ScoreOut | None:
        if not row.scores:
            return None
        s = row.scores[0]
        return ScoreOut(
            overall=s.overall,
            recommendation=Recommendation(s.recommendation),
            dimensions=s.dimensions,  # type: ignore[arg-type]
            scoring_version=s.scoring_version,
            vault_sha=s.vault_sha,
            computed_at=s.computed_at,
            reasons=list(s.reasons or []),
        )

    @staticmethod
    def _latest_analysis(row: Opportunity) -> AnalysisOut | None:
        if not row.analyses:
            return None
        a = row.analyses[0]
        return AnalysisOut(
            **a.payload,
            ai_run_id=a.ai_run_id,
            provider=a.provider,
            model=a.model,
            created_at=a.created_at,
        )

    @staticmethod
    def _extraction_from_row(row: Opportunity) -> OpportunityExtraction:
        return OpportunityExtraction(
            title=row.title,
            company=row.company_name,
            contract_type=row.contract_type,  # type: ignore[arg-type]
            employment_type=row.employment_type,  # type: ignore[arg-type]
            location=row.location,
            remote_policy=row.remote_policy,  # type: ignore[arg-type]
            remote_regions=list(row.remote_regions or []),
            timezone_range=row.timezone_range,
            compensation=Compensation.model_validate(row.compensation)
            if row.compensation
            else None,
            seniority=row.seniority,  # type: ignore[arg-type]
            requirements=list(row.requirements or []),
            preferred=list(row.preferred or []),
            technologies=list(row.technologies or []),
            responsibilities=list(row.responsibilities or []),
            recruiter=Recruiter.model_validate(row.recruiter) if row.recruiter else None,
            deadline=row.deadline,
            summary=row.summary,
            red_flags=list(row.red_flags or []),
        )

    @staticmethod
    def _to_out(
        row: Opportunity, score: ScoreOut | None, analysis: AnalysisOut | None
    ) -> OpportunityOut:
        return OpportunityOut(
            id=row.id,
            source=Source(row.source),
            url=row.url,
            title=row.title,
            company_name=row.company_name,
            contract_type=row.contract_type,  # type: ignore[arg-type]
            employment_type=row.employment_type,  # type: ignore[arg-type]
            location=row.location,
            remote_policy=row.remote_policy,  # type: ignore[arg-type]
            remote_regions=list(row.remote_regions or []),
            timezone_range=row.timezone_range,
            compensation=Compensation.model_validate(row.compensation)
            if row.compensation
            else None,
            seniority=row.seniority,  # type: ignore[arg-type]
            requirements=list(row.requirements or []),
            preferred=list(row.preferred or []),
            technologies=list(row.technologies or []),
            responsibilities=list(row.responsibilities or []),
            summary=row.summary,
            red_flags=list(row.red_flags or []),
            recruiter=Recruiter.model_validate(row.recruiter) if row.recruiter else None,
            received_at=row.received_at,
            deadline=row.deadline,
            status=OpportunityStatus(row.status),
            dedup_key=row.dedup_key,
            possible_duplicate_of=row.possible_duplicate_of,
            parse_confidence=row.parse_confidence,
            parser=row.parser,
            notes=row.notes,
            created_at=row.created_at,
            score=score,
            analysis=analysis,
        )


_BRIEF_FIELDS = {
    "id", "title", "company_name", "contract_type", "employment_type", "remote_policy",
    "remote_regions", "timezone_range", "seniority", "compensation", "requirements", "preferred",
    "technologies", "responsibilities", "summary", "red_flags", "deadline",
}  # fmt: skip


def _brief(opp: OpportunityOut) -> dict[str, Any]:
    """The prompt-facing view of an opportunity: parsed fields only, never the raw paste."""
    return opp.model_dump(mode="json", include=_BRIEF_FIELDS)


async def new_opportunity_stats(session: AsyncSession) -> tuple[int, dict[str, Any] | None]:
    """Service-level read for other modules: (# status=new, best new by latest score)."""
    from sqlalchemy import func as _func

    latest = (
        select(
            OpportunityScore.opportunity_id, _func.max(OpportunityScore.computed_at).label("latest")
        )
        .group_by(OpportunityScore.opportunity_id)
        .subquery()
    )
    stmt = (
        select(Opportunity, OpportunityScore.overall, OpportunityScore.recommendation)
        .join(OpportunityScore, OpportunityScore.opportunity_id == Opportunity.id)
        .join(
            latest,
            (latest.c.opportunity_id == OpportunityScore.opportunity_id)
            & (latest.c.latest == OpportunityScore.computed_at),
        )
        .where(Opportunity.status == "new")
        .order_by(OpportunityScore.overall.desc())
    )
    rows = (await session.execute(stmt)).all()
    best = None
    if rows:
        opp, overall, rec = rows[0]
        best = {
            "id": str(opp.id),
            "title": opp.title,
            "company": opp.company_name,
            "score": overall,
            "recommendation": rec,
        }
    return len(rows), best


async def opportunity_stream(session: AsyncSession, *, limit: int = 2000) -> list[dict[str, Any]]:
    """Service-level read for insights: lightweight rows of the observed opportunity stream."""
    from sqlalchemy import func as _func

    latest = (
        select(
            OpportunityScore.opportunity_id, _func.max(OpportunityScore.computed_at).label("latest")
        )
        .group_by(OpportunityScore.opportunity_id)
        .subquery()
    )
    stmt = (
        select(Opportunity, OpportunityScore.overall, OpportunityScore.recommendation)
        .outerjoin(latest, latest.c.opportunity_id == Opportunity.id)
        .outerjoin(
            OpportunityScore,
            (OpportunityScore.opportunity_id == Opportunity.id)
            & (OpportunityScore.computed_at == latest.c.latest),
        )
        .order_by(Opportunity.received_at.desc())
        .limit(limit)
    )
    out: list[dict[str, Any]] = []
    for opp, overall, rec in (await session.execute(stmt)).all():
        out.append(
            {
                "id": str(opp.id),
                "title": opp.title,
                "company": opp.company_name,
                "source": opp.source,
                "status": opp.status,
                "received_at": opp.received_at,
                "technologies": list(opp.technologies or []),
                "remote_policy": opp.remote_policy,
                "remote_regions": list(opp.remote_regions or []),
                "contract_type": opp.contract_type,
                "seniority": opp.seniority,
                "compensation": dict(opp.compensation or {}) if opp.compensation else None,
                "score": overall,
                "recommendation": rec,
            }
        )
    return out

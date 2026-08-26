"""Opportunity service: ingest → parse → dedup → score → AI analysis → compare → external prompt."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
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
from careeros.modules.opportunities.dedup import (
    FUZZY_THRESHOLD,
    dedup_key,
    fingerprint,
    normalize_url,
    similarity,
)
from careeros.modules.opportunities.deps import (
    find_opportunity_id_by_external_id,
    find_opportunity_id_by_url,
)
from careeros.modules.opportunities.enums import (
    AUTHORITY_ORDER,
    FieldSource,
    OpportunityStatus,
    Recommendation,
    Source,
    SourceRelation,
)
from careeros.modules.opportunities.models import (
    Opportunity,
    OpportunityAnalysis,
    OpportunityRaw,
    OpportunityScore,
    OpportunitySource,
)
from careeros.modules.opportunities.parser import merge_extractions, parse_text
from careeros.modules.opportunities.schemas import (
    AnalysisOut,
    CompareOut,
    CompareRankingOutput,
    CompareRow,
    Compensation,
    FieldChange,
    IngestRequest,
    InterviewPrepOut,
    InterviewPrepOutput,
    NegotiationOut,
    NegotiationPlanOutput,
    OpportunityAnalysisOutput,
    OpportunityDetail,
    OpportunityDiffOut,
    OpportunityExtraction,
    OpportunityOut,
    OpportunitySnapshotOut,
    OpportunitySourceOut,
    Recruiter,
    ScoreOut,
    SnapshotIn,
    SourceIn,
)
from careeros.modules.opportunities.scoring import ScoringContext, score_opportunity
from careeros.modules.vault.enums import Platform
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


# ------------------------------------------------------------- provenance helpers (ADR-016)

_PLATFORM_VALUES = frozenset(str(p) for p in Platform)
_BOARD_SOURCES = frozenset(
    {
        "hh",
        "upwork",
        "linkedin",
        "wellfound",
        "indeed",
        "getmatch",
        "toptal",
        "rockethunt",
        "justjoin",
    }
)
#: Extraction fields that are *facts about the posting* and so carry evidence. ``summary`` and
#: ``red_flags`` are our own reading of it, not something a source claimed.
_EVIDENCE_FIELDS = (
    "title",
    "company",
    "contract_type",
    "employment_type",
    "location",
    "remote_policy",
    "remote_regions",
    "timezone_range",
    "compensation",
    "seniority",
    "requirements",
    "preferred",
    "technologies",
    "responsibilities",
    "recruiter",
    "deadline",
)
#: What ``diff`` reports on. The last three are not extraction fields but a read path may put
#: them into ``extracted`` (closed postings, apply links); they are compared when present.
_DIFF_FIELDS = (*_EVIDENCE_FIELDS, "status", "closed", "apply_url")


def _platform_for(req_platform: str | None, source: Source) -> str | None:
    """The vault ``Platform`` a capture came from: as stated, else the source when it is one."""
    if req_platform:
        return str(req_platform)
    return str(source) if str(source) in _PLATFORM_VALUES else None


def _default_authority(source: Source, capture_method: str) -> FieldSource:
    """Best-effort authority of a plain ingest (a read path states it explicitly instead)."""
    value = str(source)
    if value == str(Source.website):
        return FieldSource.employer_page
    if value in (str(Source.email), str(Source.recruiter)):
        return FieldSource.recruiter_message
    if value in _BOARD_SOURCES:
        return (
            FieldSource.board_api
            if capture_method in ("structured", "api")
            else FieldSource.board_page
        )
    return FieldSource.manual


def _authority_rank(source: str) -> int:
    """Position in ``AUTHORITY_ORDER`` (lower is stronger); unknown labels rank last."""
    try:
        return AUTHORITY_ORDER.index(FieldSource(source))
    except ValueError:
        return len(AUTHORITY_ORDER)


def _evidence_from_extraction(
    extraction: OpportunityExtraction,
    *,
    source: FieldSource | str,
    source_url: str | None,
    observed_at: datetime,
    confidence: float | None = None,
) -> list[dict[str, Any]]:
    """Per stated field: ``{field, value, source, source_url, observed_at, confidence}``."""
    data = extraction.model_dump(mode="json")
    items: list[dict[str, Any]] = []
    for field in _EVIDENCE_FIELDS:
        value = data.get(field)
        if value in (None, "", [], {}) or (field == "remote_policy" and value == "unknown"):
            continue
        items.append(
            {
                "field": field,
                "value": value,
                "source": str(source),
                "source_url": source_url,
                "observed_at": observed_at.isoformat(),
                "confidence": confidence,
            }
        )
    return items


def _merge_evidence(
    current: dict[str, Any] | None, items: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Append evidence, never replace it: a value already claimed by the same source at the same
    URL is not repeated; a *different* value is added next to the old one — conflicts are kept."""
    merged: dict[str, list[dict[str, Any]]] = {
        k: list(v) for k, v in (current or {}).items() if isinstance(v, list)
    }
    for item in items:
        field = str(item["field"])
        entry = {
            k: item.get(k) for k in ("value", "source", "source_url", "observed_at", "confidence")
        }
        entries = merged.setdefault(field, [])
        if any(
            e.get("value") == entry["value"]
            and e.get("source") == entry["source"]
            and e.get("source_url") == entry["source_url"]
            for e in entries
        ):
            continue
        entries.append(entry)
    return merged


def _resolve_extraction(
    incoming: OpportunityExtraction, evidence: dict[str, Any], incoming_source: str
) -> OpportunityExtraction:
    """The displayed value of each field is the highest-authority claim (ADR-016 §3).

    A refresh from a weaker source (an aggregator, an archive) must not overwrite what the
    employer or the board API stated; equal or stronger sources win with their latest claim.
    """
    rank_in = _authority_rank(incoming_source)
    data = incoming.model_dump(mode="json")
    changed = False
    for field in _EVIDENCE_FIELDS:
        best: tuple[int, str, Any] | None = None
        for entry in evidence.get(field, []) or []:
            rank = _authority_rank(str(entry.get("source") or ""))
            if rank >= rank_in:
                continue
            observed = str(entry.get("observed_at") or "")
            if best is None or rank < best[0] or (rank == best[0] and observed > best[1]):
                best = (rank, observed, entry.get("value"))
        if best is not None and best[2] is not None and data.get(field) != best[2]:
            data[field] = best[2]
            changed = True
    return OpportunityExtraction.model_validate(data) if changed else incoming


def _diff_value(value: Any) -> Any:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, list):
        return sorted(value, key=str)
    return value


def _diff_extracted(
    before: dict[str, Any] | None, after: dict[str, Any] | None
) -> list[FieldChange]:
    a, b = before or {}, after or {}
    changes: list[FieldChange] = []
    for field in _DIFF_FIELDS:
        if field not in a and field not in b:
            continue
        if _diff_value(a.get(field)) != _diff_value(b.get(field)):
            changes.append(FieldChange(field=field, before=a.get(field), after=b.get(field)))
    return changes


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
        # ADR-016 identity layers before the dedup key: the provider's own id, then the
        # canonical URL. Both only *flag* ``possible_duplicate_of`` — nothing is merged.
        platform = _platform_for(req.platform, req.source)
        identity_url = req.canonical_url or req.url
        canonical_url = normalize_url(identity_url) if identity_url else None
        duplicate_of: uuid.UUID | None = None
        if platform and req.external_id:
            duplicate_of = await find_opportunity_id_by_external_id(
                self.session, platform, req.external_id, user_id=self.user_id
            )
        if duplicate_of is None and canonical_url:
            duplicate_of = await find_opportunity_id_by_url(
                self.session, canonical_url, user_id=self.user_id
            )
        if duplicate_of is None:
            duplicate_of = await self._find_duplicate(key, title, extraction.company)

        authority = _default_authority(req.source, raw.capture_method)
        raw.opportunity_id = None  # set once the opportunity has an id (below)
        raw.fingerprint = fingerprint(raw_text) if raw_text else None
        raw.fetched_url = req.url
        raw.extracted = extraction.model_dump(mode="json")
        opp = Opportunity(
            user_id=self.user_id,
            raw=raw,
            source=str(req.source),
            url=req.url,
            title=title[:300],
            **self._extraction_columns(extraction),
            description_md=raw_text,
            received_at=req.received_at or now,
            status=str(OpportunityStatus.new),
            dedup_key=key,
            possible_duplicate_of=duplicate_of,
            parser=parser,
            parse_confidence=confidence,
            notes=req.notes,
            platform=platform,
            external_id=req.external_id,
            canonical_url=canonical_url,
            field_evidence=_merge_evidence(
                None,
                _evidence_from_extraction(
                    extraction,
                    source=authority,
                    source_url=req.url,
                    observed_at=req.received_at or now,
                    confidence=confidence,
                ),
            ),
        )
        self.session.add(opp)
        await self.session.flush()
        raw.opportunity_id = opp.id
        self.session.add(
            OpportunitySource(
                user_id=self.user_id,
                opportunity_id=opp.id,
                platform=platform or str(Platform.other),
                external_id=req.external_id,
                source_url=req.url,
                canonical_url=canonical_url,
                relation=str(SourceRelation.primary),
                authority=str(authority),
                strategy=raw.capture_method,
                raw_id=raw.id,
                fetched_at=raw.captured_at,
                content_hash=raw.content_hash,
            )
        )

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
            platform=row.platform,
            external_id=row.external_id,
            canonical_url=row.canonical_url,
        )

    @staticmethod
    def _extraction_columns(extraction: OpportunityExtraction) -> dict[str, Any]:
        """``Opportunity`` column values for an extraction — shared by ingest and snapshots."""
        return {
            "company_name": extraction.company,
            "contract_type": str(extraction.contract_type) if extraction.contract_type else None,
            "employment_type": str(extraction.employment_type)
            if extraction.employment_type
            else None,
            "location": extraction.location,
            "remote_policy": str(extraction.remote_policy),
            "remote_regions": list(extraction.remote_regions),
            "timezone_range": extraction.timezone_range,
            "compensation": extraction.compensation.model_dump(mode="json")
            if extraction.compensation
            else None,
            "seniority": str(extraction.seniority) if extraction.seniority else None,
            "requirements": list(extraction.requirements),
            "preferred": list(extraction.preferred),
            "technologies": list(extraction.technologies),
            "responsibilities": list(extraction.responsibilities),
            "summary": extraction.summary,
            "red_flags": list(extraction.red_flags),
            "recruiter": extraction.recruiter.model_dump(mode="json")
            if extraction.recruiter
            else None,
            "deadline": extraction.deadline,
        }

    # ------------------------------------------------------------------ provenance (ADR-016)
    async def _require(self, opportunity_id: uuid.UUID) -> Opportunity:
        row = await self.session.get(Opportunity, opportunity_id)
        if row is None:
            raise OpportunityNotFound(str(opportunity_id))
        return row

    async def record_source(self, opportunity_id: uuid.UUID, src: SourceIn) -> OpportunitySourceOut:
        """Upsert one ``opportunity_source`` row by ``(platform, external_id)``, else by
        ``(platform, canonical_url)``. Stated fields overwrite, absent ones are kept."""
        row = await self._require(opportunity_id)
        identity_url = src.canonical_url or src.source_url
        canonical = normalize_url(identity_url) if identity_url else None
        conditions = []
        if src.external_id:
            conditions.append(OpportunitySource.external_id == src.external_id)
        if canonical:
            conditions.append(OpportunitySource.canonical_url == canonical)
        existing: OpportunitySource | None = None
        if conditions:
            existing = await self.session.scalar(
                select(OpportunitySource)
                .where(
                    OpportunitySource.opportunity_id == row.id,
                    OpportunitySource.platform == src.platform,
                    or_(*conditions),
                )
                .order_by(OpportunitySource.created_at)
                .limit(1)
            )
        if existing is None:
            existing = OpportunitySource(
                user_id=self.user_id,
                opportunity_id=row.id,
                platform=src.platform,
                relation=str(src.relation),
                authority=str(src.authority),
            )
            self.session.add(existing)
        existing.external_id = src.external_id or existing.external_id
        existing.source_url = src.source_url or existing.source_url
        existing.canonical_url = canonical or existing.canonical_url
        existing.original_url = src.original_url or existing.original_url
        existing.relation = str(src.relation)
        existing.authority = str(src.authority)
        existing.strategy = src.strategy or existing.strategy
        existing.raw_id = src.raw_id or existing.raw_id
        existing.fetched_at = src.fetched_at or existing.fetched_at
        existing.published_at = src.published_at or existing.published_at
        existing.content_hash = src.content_hash or existing.content_hash
        existing.is_archive = src.is_archive
        if src.confidence is not None:
            existing.confidence = src.confidence
        if src.meta:
            existing.meta = {**(existing.meta or {}), **src.meta}
        await self.session.commit()
        return self._source_out(existing)

    async def list_sources(self, opportunity_id: uuid.UUID) -> list[OpportunitySourceOut]:
        row = await self._require(opportunity_id)
        rows = (
            await self.session.scalars(
                select(OpportunitySource)
                .where(OpportunitySource.opportunity_id == row.id)
                .order_by(OpportunitySource.created_at)
            )
        ).all()
        return [self._source_out(r) for r in rows]

    async def record_snapshot(
        self, opportunity_id: uuid.UUID, snap: SnapshotIn
    ) -> tuple[OpportunitySnapshotOut, bool]:
        """Store a re-read as a new ``OpportunityRaw`` **only** when its fingerprint differs from
        the latest one; returns ``(snapshot, created)``.

        A live snapshot becomes the opportunity's current raw and refreshes the extraction
        columns — through the field-evidence authority order, so a weaker source never
        overwrites a stronger claim. An archive snapshot is history only: evidence is recorded,
        the live view is untouched.
        """
        row = await self._row(opportunity_id)
        fp = fingerprint(snap.raw_text)
        latest = await self._latest_raw(row)
        if latest is not None and (latest.fingerprint or fingerprint(latest.raw_text)) == fp:
            log.info("opportunity.snapshot_unchanged", id=str(row.id), raw_id=str(latest.id))
            return self._snapshot_out(latest), False

        now = datetime.now(UTC)
        captured_at = snap.captured_at or now
        extraction: OpportunityExtraction | None = None
        if snap.extracted is not None:
            extraction = OpportunityExtraction.model_validate(snap.extracted)
        elif snap.raw_text.strip():
            parsed = parse_text(
                snap.raw_text,
                tech_vocabulary(self.vault.require()),
                url=snap.fetched_url or row.url,
            )
            extraction = parsed.extraction
        raw = OpportunityRaw(
            user_id=self.user_id,
            source=row.source,
            url=snap.resolved_url or snap.fetched_url or row.url,
            raw_text=snap.raw_text,
            raw_payload=snap.raw_payload,
            content_hash=snap.content_hash or hashlib.sha256(snap.raw_text.encode()).hexdigest(),
            capture_method=snap.capture_method,
            captured_at=captured_at,
            opportunity_id=row.id,
            fingerprint=fp,
            strategy=snap.strategy,
            fetched_url=snap.fetched_url,
            resolved_url=snap.resolved_url,
            is_archive=snap.is_archive,
            archive_ts=snap.archive_ts,
            quality=snap.quality,
            extracted=extraction.model_dump(mode="json") if extraction else None,
        )
        self.session.add(raw)
        await self.session.flush()

        if extraction is not None:
            authority = snap.authority or (
                FieldSource.archive if snap.is_archive else FieldSource.board_page
            )
            evidence = _merge_evidence(
                row.field_evidence,
                _evidence_from_extraction(
                    extraction,
                    source=authority,
                    source_url=snap.source_url or snap.fetched_url,
                    observed_at=snap.archive_ts or captured_at,
                    confidence=snap.quality,
                ),
            )
            row.field_evidence = evidence
            if not snap.is_archive:
                resolved = _resolve_extraction(extraction, evidence, str(authority))
                for column, value in self._extraction_columns(resolved).items():
                    setattr(row, column, value)
                if resolved.title:
                    row.title = resolved.title[:300]
        if not snap.is_archive:
            row.raw_id = raw.id
            row.description_md = snap.raw_text
        await self.session.commit()
        log.info(
            "opportunity.snapshot_created",
            id=str(row.id),
            raw_id=str(raw.id),
            archive=snap.is_archive,
            strategy=snap.strategy,
        )
        return self._snapshot_out(raw), True

    async def list_snapshots(self, opportunity_id: uuid.UUID) -> list[OpportunitySnapshotOut]:
        """Chronological captures of one job (the pre-ADR-016 raw included when still unlinked)."""
        return [self._snapshot_out(r) for r in await self._raws(opportunity_id)]

    async def diff(
        self,
        opportunity_id: uuid.UUID,
        *,
        from_raw_id: uuid.UUID | None = None,
        to_raw_id: uuid.UUID | None = None,
    ) -> OpportunityDiffOut:
        """Field changes between two snapshots' ``extracted`` (defaults: previous → latest)."""
        raws = await self._raws(opportunity_id)
        by_id = {r.id: r for r in raws}
        to = by_id.get(to_raw_id) if to_raw_id is not None else (raws[-1] if raws else None)
        if to_raw_id is not None and to is None:
            raise OpportunityNotFound(f"snapshot {to_raw_id}")
        if from_raw_id is not None:
            frm = by_id.get(from_raw_id)
            if frm is None:
                raise OpportunityNotFound(f"snapshot {from_raw_id}")
        elif to is not None:
            index = raws.index(to)
            frm = raws[index - 1] if index > 0 else None
        else:
            frm = None
        return OpportunityDiffOut(
            from_raw_id=frm.id if frm else None,
            to_raw_id=to.id if to else None,
            from_captured_at=frm.captured_at if frm else None,
            to_captured_at=to.captured_at if to else None,
            # A single snapshot has nothing to differ from — not "everything was added".
            changes=_diff_extracted(frm.extracted, to.extracted) if frm and to else [],
        )

    async def merge_field_evidence(
        self, opportunity_id: uuid.UUID, evidence: list[dict[str, Any]]
    ) -> dict[str, list[dict[str, Any]]]:
        """Add ``{field, value, source, source_url?, observed_at?, confidence?}`` items; nothing is
        dropped, disagreeing values sit side by side (ADR-016 §3)."""
        row = await self._require(opportunity_id)
        for item in evidence:
            if not item.get("field") or not item.get("source"):
                raise OpportunityError("evidence items need 'field' and 'source'")
        merged = _merge_evidence(row.field_evidence, evidence)
        row.field_evidence = merged
        await self.session.commit()
        return merged

    async def _latest_raw(self, row: Opportunity) -> OpportunityRaw | None:
        latest = await self.session.scalar(
            select(OpportunityRaw)
            .where(OpportunityRaw.opportunity_id == row.id)
            .order_by(OpportunityRaw.captured_at.desc(), OpportunityRaw.created_at.desc())
            .limit(1)
        )
        return latest or row.raw

    async def _raws(self, opportunity_id: uuid.UUID) -> list[OpportunityRaw]:
        row = await self._require(opportunity_id)
        raws = list(
            (
                await self.session.scalars(
                    select(OpportunityRaw)
                    .where(OpportunityRaw.opportunity_id == row.id)
                    .order_by(OpportunityRaw.captured_at, OpportunityRaw.created_at)
                )
            ).all()
        )
        if all(r.id != row.raw_id for r in raws):
            legacy = await self.session.get(OpportunityRaw, row.raw_id)
            if legacy is not None:
                raws.insert(0, legacy)
        return raws

    @staticmethod
    def _source_out(s: OpportunitySource) -> OpportunitySourceOut:
        return OpportunitySourceOut(
            id=s.id,
            opportunity_id=s.opportunity_id,
            platform=s.platform,
            external_id=s.external_id,
            source_url=s.source_url,
            canonical_url=s.canonical_url,
            original_url=s.original_url,
            relation=SourceRelation(s.relation),
            authority=FieldSource(s.authority),
            strategy=s.strategy,
            raw_id=s.raw_id,
            fetched_at=s.fetched_at,
            published_at=s.published_at,
            content_hash=s.content_hash,
            is_archive=s.is_archive,
            confidence=s.confidence,
            meta=s.meta,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )

    @staticmethod
    def _snapshot_out(r: OpportunityRaw) -> OpportunitySnapshotOut:
        return OpportunitySnapshotOut(
            id=r.id,
            opportunity_id=r.opportunity_id,
            captured_at=r.captured_at,
            capture_method=r.capture_method,
            source=r.source,
            url=r.url,
            strategy=r.strategy,
            fingerprint=r.fingerprint,
            content_hash=r.content_hash,
            is_archive=r.is_archive,
            archive_ts=r.archive_ts,
            quality=r.quality,
            fetched_url=r.fetched_url,
            resolved_url=r.resolved_url,
            extracted=r.extracted,
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


async def top_new_opportunities(
    session: AsyncSession, *, min_score: int = 80, limit: int = 5
) -> list[dict[str, Any]]:
    """Service-level read for insights: new opportunities whose latest score clears the bar."""
    from sqlalchemy import func as _func

    latest = (
        select(
            OpportunityScore.opportunity_id, _func.max(OpportunityScore.computed_at).label("latest")
        )
        .group_by(OpportunityScore.opportunity_id)
        .subquery()
    )
    stmt = (
        select(Opportunity, OpportunityScore.overall)
        .join(OpportunityScore, OpportunityScore.opportunity_id == Opportunity.id)
        .join(
            latest,
            (latest.c.opportunity_id == OpportunityScore.opportunity_id)
            & (latest.c.latest == OpportunityScore.computed_at),
        )
        .where(
            Opportunity.status == str(OpportunityStatus.new), OpportunityScore.overall >= min_score
        )
        .order_by(OpportunityScore.overall.desc())
        .limit(limit)
    )
    return [
        {
            "id": str(opp.id),
            "title": opp.title,
            "company": opp.company_name,
            "overall": overall,
            "received_at": opp.received_at,
        }
        for opp, overall in (await session.execute(stmt)).all()
    ]

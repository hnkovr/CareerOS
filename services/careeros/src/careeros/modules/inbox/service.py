"""Inbox service: ingest → dedup → thread → classify → link → extract opportunity → reply."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from rapidfuzz import fuzz
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from careeros.core.config import Settings
from careeros.core.logging import get_logger
from careeros.modules.ai.provider import AIError
from careeros.modules.ai.service import AIService
from careeros.modules.inbox.classify import classify_email, normalize_subject, parse_raw_email
from careeros.modules.inbox.enums import ATTENTION_CLASSES, MessageClass
from careeros.modules.inbox.models import Message, Thread
from careeros.modules.inbox.schemas import (
    ClassificationOut,
    EmailIn,
    InboxAIClassification,
    InboxStats,
    MessageLinks,
    MessageOut,
    MessageUpdate,
    ReplyDraftOutput,
    ReplySuggestionOut,
    SuggestReplyRequest,
    ThreadOut,
)
from careeros.modules.opportunities.enums import Source
from careeros.modules.opportunities.models import Contact, Opportunity
from careeros.modules.opportunities.schemas import IngestRequest
from careeros.modules.opportunities.service import OpportunityService
from careeros.modules.pipeline.enums import EventKind
from careeros.modules.pipeline.schemas import EventIn
from careeros.modules.pipeline.service import PipelineService
from careeros.modules.vault.service import Vault

if TYPE_CHECKING:
    from careeros.modules.ai.suggestions import SuggestionOut

log = get_logger(__name__)

AI_REFINE_THRESHOLD = 0.6
EXTRACT_CLASSES = {
    MessageClass.new_opportunity,
    MessageClass.recruiter_outreach,
    MessageClass.client_lead,
}
EXTRACT_MIN_BODY = 200
EXTRACT_MIN_TECH_HITS = 2
LINK_SIMILARITY = 90.0


def _mention_score(opp: Opportunity, text: str) -> float:
    """How strongly ``text`` mentions this opportunity. A known company name plus the start of the
    title is enough — follow-up emails rarely repeat the full posting title."""
    title = opp.title.lower()
    company = (opp.company_name or "").lower()
    title_prefix = title[:40]
    if company and len(company) >= 5 and company in text:
        return max(float(fuzz.partial_ratio(title_prefix, text)), 90.0)
    return float(fuzz.partial_ratio(title, text)) if len(title) >= 8 else 0.0


class InboxError(Exception):
    pass


class MessageNotFound(InboxError):
    pass


class InboxService:
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
    async def ingest(
        self, email: EmailIn, *, use_ai: bool = False, provider: str | None = None
    ) -> MessageOut:
        if email.raw and (not email.from_email or not email.subject):
            parsed = parse_raw_email(email.raw)
            email = email.model_copy(
                update={k: v for k, v in parsed.items() if not getattr(email, k, None)}
            )

        content_hash = hashlib.sha256(
            f"{email.from_email}|{email.subject}|{email.body_text}".encode()
        ).hexdigest()
        existing = await self.session.scalar(
            select(Message).where(Message.content_hash == content_hash)
        )
        if existing is not None:
            log.info("inbox.duplicate", message=str(existing.id))
            return self._to_out(existing)

        received_at = email.received_at or datetime.now(UTC)
        counterpart = (
            email.from_email
            if email.direction == "inbound"
            else (email.to[0] if email.to else None)
        )
        subject_norm = normalize_subject(email.subject)
        thread = await self.session.scalar(
            select(Thread).where(
                Thread.subject_norm == subject_norm, Thread.counterpart_email == counterpart
            )
        )
        if thread is None:
            thread = Thread(
                user_id=self.user_id,
                provider=str(email.provider),
                subject_norm=subject_norm,
                counterpart_email=counterpart,
                last_message_at=received_at,
            )
            self.session.add(thread)
            await self.session.flush()
        thread.last_message_at = max(thread.last_message_at, received_at)

        verdict = classify_email(email)
        classified_by = "rules"
        if use_ai and verdict.confidence < AI_REFINE_THRESHOLD:
            refined = await self._refine_with_ai(email, verdict, provider)
            if refined is not None:
                verdict, classified_by = refined, "ai"

        message = Message(
            user_id=self.user_id,
            thread_id=thread.id,
            provider=str(email.provider),
            provider_message_id=email.provider_message_id,
            direction=str(email.direction),
            from_email=email.from_email,
            from_name=email.from_name,
            to=list(email.to),
            subject=email.subject,
            body_text=email.body_text,
            headers=dict(email.headers),
            received_at=received_at,
            content_hash=content_hash,
            classification=str(verdict.classification),
            urgency=str(verdict.urgency),
            classified_by=classified_by,
            classification_confidence=verdict.confidence,
            classification_signals=list(verdict.signals),
            deadline_hint=verdict.deadline_hint,
        )
        self.session.add(message)
        await self.session.flush()

        await self._link(message)
        if (
            MessageClass(message.classification) in EXTRACT_CLASSES
            and message.opportunity_id is None
            and message.direction == "inbound"
        ):
            await self._extract_opportunity(message, use_ai=use_ai, provider=provider)
        await self.session.commit()

        if message.application_id is not None:
            try:
                await PipelineService(self.session, self.user_id).add_event(
                    message.application_id,
                    EventIn(
                        kind=EventKind.message_received,
                        title=f"Email: {message.subject or '(no subject)'}",
                        body=(message.body_text or "")[:500],
                    ),
                )
            except Exception:
                log.warning("inbox.timeline_event_failed", message=str(message.id))

        log.info(
            "inbox.ingested",
            message=str(message.id),
            cls=message.classification,
            urgency=message.urgency,
            linked_opportunity=str(message.opportunity_id) if message.opportunity_id else None,
            extracted=message.extracted_opportunity,
        )
        return self._to_out(message)

    async def _refine_with_ai(
        self, email: EmailIn, verdict: ClassificationOut, provider: str | None
    ) -> ClassificationOut | None:
        try:
            run = await self.ai.structured(
                "inbox_classify",
                {
                    "subject": email.subject or "",
                    "from_email": email.from_email or "",
                    "body": email.body_text[:6000],
                    "rules_verdict": f"{verdict.classification} ({verdict.confidence})",
                },
                InboxAIClassification,
                provider=provider,
                entity_type="message",
            )
            return ClassificationOut(
                classification=run.data.classification,
                urgency=run.data.urgency,
                confidence=0.8,
                signals=[*verdict.signals, f"ai: {run.data.reasoning or 'refined'}"][:8],
                deadline_hint=run.data.deadline_hint or verdict.deadline_hint,
            )
        except AIError as exc:
            log.warning("inbox.ai_classify_failed", error=str(exc))
            return None

    async def _link(self, message: Message) -> None:
        if message.from_email:
            contact = await self.session.scalar(
                select(Contact).where(Contact.email.ilike(message.from_email))
            )
            if contact is not None:
                message.contact_id = contact.id
                message.company_id = contact.company_id
                contact.last_contact_at = message.received_at

        text = f"{message.subject or ''} {message.body_text[:2000]}".lower()
        recent = (
            await self.session.scalars(
                select(Opportunity).order_by(Opportunity.created_at.desc()).limit(200)
            )
        ).all()
        best: tuple[float, Opportunity] | None = None
        for opp in recent:
            score = _mention_score(opp, text)
            if score >= LINK_SIMILARITY and (best is None or score > best[0]):
                best = (score, opp)
        if best is not None:
            message.opportunity_id = best[1].id
        if message.opportunity_id is not None:
            from careeros.modules.pipeline.models import Application

            app = await self.session.scalar(
                select(Application).where(Application.opportunity_id == message.opportunity_id)
            )
            if app is not None:
                message.application_id = app.id

    async def _extract_opportunity(
        self, message: Message, *, use_ai: bool, provider: str | None
    ) -> None:
        from careeros.modules.cv.keywords import extract_known_tech, tech_vocabulary

        if len(message.body_text) < EXTRACT_MIN_BODY:
            return
        try:
            data = self.vault.require()
        except Exception:  # vault invalid → skip extraction, never block ingest
            return
        tech_hits = extract_known_tech(message.body_text, tech_vocabulary(data))
        if len(tech_hits) < EXTRACT_MIN_TECH_HITS:
            return
        source = (
            Source.recruiter
            if message.classification == str(MessageClass.recruiter_outreach)
            else Source.email
        )
        opp_service = OpportunityService(
            self.settings, self.vault, self.ai, session=self.session, user_id=self.user_id
        )
        detail = await opp_service.ingest(
            IngestRequest(
                source=source,
                text=message.body_text,
                use_ai=use_ai,
                provider=provider,
                received_at=message.received_at,
                notes=f"extracted from email {message.id}",
            )
        )
        message.opportunity_id = detail.id
        message.extracted_opportunity = True

    # ------------------------------------------------------------------ read / update
    async def list_messages(
        self,
        *,
        classification: MessageClass | None = None,
        unread_only: bool = False,
        needs_attention: bool = False,
        limit: int = 100,
    ) -> list[MessageOut]:
        stmt = select(Message).order_by(Message.received_at.desc()).limit(limit)
        if classification:
            stmt = stmt.where(Message.classification == str(classification))
        if unread_only:
            stmt = stmt.where(Message.read_at.is_(None))
        if needs_attention:
            stmt = stmt.where(Message.classification.in_([str(c) for c in ATTENTION_CLASSES]))
        rows = (await self.session.scalars(stmt)).all()
        return [self._to_out(m) for m in rows]

    async def get_thread(self, thread_id: uuid.UUID) -> ThreadOut:
        thread = await self.session.get(
            Thread, thread_id, options=[selectinload(Thread.messages)], populate_existing=True
        )
        if thread is None:
            raise MessageNotFound(str(thread_id))
        return ThreadOut(
            id=thread.id,
            subject_norm=thread.subject_norm,
            counterpart_email=thread.counterpart_email,
            last_message_at=thread.last_message_at,
            message_count=len(thread.messages),
            messages=[self._to_out(m) for m in thread.messages],
        )

    async def update_message(self, message_id: uuid.UUID, req: MessageUpdate) -> MessageOut:
        message = await self.session.get(Message, message_id)
        if message is None:
            raise MessageNotFound(str(message_id))
        if req.classification is not None:
            message.classification = str(req.classification)
            message.classified_by = "user"
            message.classification_confidence = 1.0
        if req.urgency is not None:
            message.urgency = str(req.urgency)
        if req.links is not None:
            for field in ("opportunity_id", "company_id", "contact_id", "application_id"):
                value = getattr(req.links, field)
                if value is not None:
                    setattr(message, field, value)
        if req.mark_read:
            message.read_at = datetime.now(UTC)
        await self.session.commit()
        return self._to_out(message)

    async def suggest_reply(
        self, message_id: uuid.UUID, req: SuggestReplyRequest
    ) -> ReplySuggestionOut:
        message = await self.session.get(Message, message_id)
        if message is None:
            raise MessageNotFound(str(message_id))
        data = self.vault.require()
        positioning = data.by_id(data.positioning)[data.meta.default_positioning]
        opportunity_context = None
        if message.opportunity_id:
            opp = await self.session.get(Opportunity, message.opportunity_id)
            if opp:
                opportunity_context = (
                    f"{opp.title} @ {opp.company_name or '?'} — {opp.summary or ''}"[:500]
                )
        run = await self.ai.structured(
            "inbox_reply",
            {
                "message": {
                    "from_name": message.from_name,
                    "from_email": message.from_email,
                    "subject": message.subject,
                    "body_text": message.body_text[:6000],
                },
                "positioning": positioning.model_dump(mode="json"),
                "profile": data.profile.model_dump(mode="json"),
                "intent": req.intent,
                "instructions": req.instructions,
                "opportunity_context": opportunity_context,
            },
            ReplyDraftOutput,
            provider=req.provider,
            entity_type="message",
            entity_id=str(message_id),
        )
        suggestion_id = await self.ai.record_suggestion(
            target_type="reply",
            target_ref=str(message_id),
            title=f"Reply: {message.subject or '(no subject)'}",
            payload={"subject": run.data.subject, "body": run.data.body, "intent": req.intent},
            ai_run_id=run.run_id,
        )
        return ReplySuggestionOut(
            suggestion_id=suggestion_id,
            subject=run.data.subject,
            body=run.data.body,
            notes=run.data.notes,
            ai_run_id=run.run_id,
        )

    async def reply_sent(self, message_id: uuid.UUID, suggestion_id: uuid.UUID) -> SuggestionOut:
        """The human confirms they sent the (approved) reply: suggestion → executed, timeline
        event + follow-up on the linked application, thread marked read."""
        from careeros.modules.ai.suggestions import transition

        message = await self.session.get(Message, message_id)
        if message is None:
            raise MessageNotFound(str(message_id))
        result: SuggestionOut = await transition(
            self.session, suggestion_id, "executed", note=f"reply sent for message {message_id}"
        )
        message.read_at = message.read_at or datetime.now(UTC)
        await self.session.commit()
        if message.application_id is not None:
            from careeros.modules.pipeline.enums import EventKind as _EK
            from careeros.modules.pipeline.schemas import EventIn as _EIn

            await PipelineService(self.session, self.user_id).add_event(
                message.application_id,
                _EIn(kind=_EK.message_sent, title=f"Replied: {message.subject or '(no subject)'}"),
            )
        return result

    async def stats(self) -> InboxStats:
        rows = (
            await self.session.execute(
                select(Message.classification, func.count()).group_by(Message.classification)
            )
        ).all()
        by_class = {cls: n for cls, n in rows}
        unread = await self.session.scalar(
            select(func.count()).select_from(Message).where(Message.read_at.is_(None))
        )
        attention = sum(n for cls, n in by_class.items() if MessageClass(cls) in ATTENTION_CLASSES)
        return InboxStats(
            total=sum(by_class.values()),
            unread=unread or 0,
            needs_attention=attention,
            by_class=by_class,
        )

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _to_out(m: Message) -> MessageOut:
        return MessageOut(
            id=m.id,
            thread_id=m.thread_id,
            provider=m.provider,  # type: ignore[arg-type]
            direction=m.direction,  # type: ignore[arg-type]
            from_email=m.from_email,
            from_name=m.from_name,
            to=list(m.to or []),
            subject=m.subject,
            body_text=m.body_text,
            received_at=m.received_at,
            classification=MessageClass(m.classification),
            urgency=m.urgency,  # type: ignore[arg-type]
            classified_by=m.classified_by,
            classification_confidence=m.classification_confidence,
            classification_signals=list(m.classification_signals or []),
            deadline_hint=m.deadline_hint,
            read_at=m.read_at,
            links=MessageLinks(
                opportunity_id=m.opportunity_id,
                company_id=m.company_id,
                contact_id=m.contact_id,
                application_id=m.application_id,
            ),
            extracted_opportunity=m.extracted_opportunity,
            created_at=m.created_at,
        )

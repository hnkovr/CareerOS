"""Assistant service (ADR-014): tool loop over the read-only registry + provenance guard."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.config import Settings
from careeros.core.logging import get_logger
from careeros.modules.ai.service import AIService
from careeros.modules.assistant.schemas import AskRequest, AskResponse, AssistantOutput, ToolInfo
from careeros.modules.assistant.tools import ToolContext, ToolRegistry, default_registry
from careeros.modules.cv.provenance import fact_sources, numbers_in
from careeros.modules.vault import schema as s
from careeros.modules.vault.service import Vault

log = get_logger(__name__)

WITHHELD = "Answer withheld by the provenance guard"


def guard_answer(
    out: AssistantOutput, data: s.VaultData, *, observed: list[str], seen_ids: set[str]
) -> list[str]:
    """Problems with the model's answer (empty = passes).

    * every id in ``derived_from`` must be a vault fact id or an entity id a tool surfaced;
    * every number in the answer must appear in a cited fact or in a tool result the model saw —
      the assistant may summarise what it observed, never add figures.
    """
    problems: list[str] = []
    sources = fact_sources(data)
    known = set(sources) | data.fact_ids() | seen_ids
    unknown = [fid for fid in out.derived_from if fid not in known]
    if unknown:
        problems.append(f"unknown ids in derived_from: {', '.join(unknown)}")
    cited_text = " ".join(sources[f].text for f in out.derived_from if f in sources)
    allowed = set(numbers_in(cited_text)) | set(numbers_in(" ".join(observed)))
    text = " ".join(filter(None, [out.answer, out.suggested_next_action]))
    foreign = sorted(set(numbers_in(text)) - allowed)
    if foreign:
        problems.append(f"numbers not seen in tool results or cited facts: {', '.join(foreign)}")
    return problems


class AssistantService:
    def __init__(
        self,
        settings: Settings,
        vault: Vault,
        ai: AIService,
        *,
        session: AsyncSession,
        user_id: uuid.UUID,
        registry: ToolRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.vault = vault
        self.ai = ai
        self.session = session
        self.user_id = user_id
        self.registry = registry or default_registry()

    def tools(self) -> list[ToolInfo]:
        return self.registry.infos()

    async def ask(self, req: AskRequest) -> AskResponse:
        data = self.vault.require()
        ctx = ToolContext(
            settings=self.settings,
            vault=self.vault,
            ai=self.ai,
            session=self.session,
            user_id=self.user_id,
            # ids the owner handed over with the question are legitimately citeable
            seen_ids={str(i) for i in (req.opportunity_id, req.application_id) if i},
        )
        positioning = data.by_id(data.positioning)[data.meta.default_positioning]
        run = await self.ai.with_tools(
            "assistant_chat",
            {
                "question": req.question,
                "context": {
                    "opportunity_id": str(req.opportunity_id) if req.opportunity_id else None,
                    "application_id": str(req.application_id) if req.application_id else None,
                },
                "positioning": positioning.model_dump(mode="json"),
                "tools": [t.model_dump() for t in self.registry.infos()],
            },
            self.registry.specs(),
            lambda call: self.registry.execute(ctx, call),
            AssistantOutput,
            provider=req.provider,
            entity_type="assistant",
            entity_id=str(req.opportunity_id or req.application_id or "") or None,
            max_steps=req.max_steps,
        )
        problems = guard_answer(run.data, data, observed=ctx.observed, seen_ids=ctx.seen_ids)
        guarded = bool(problems)
        if guarded:
            log.warning("assistant.answer_guarded", problems=problems, run_id=str(run.run_id))
        return AskResponse(
            answer=run.data.answer if not guarded else f"{WITHHELD}: " + "; ".join(problems),
            derived_from=run.data.derived_from,
            suggested_next_action=None if guarded else run.data.suggested_next_action,
            confidence="low" if guarded else run.data.confidence,
            guarded=guarded,
            provenance_problems=problems,
            tools_used=run.steps,
            turns=run.turns,
            ai_run_id=run.run_id,
            provider=run.response.provider,
            model=run.response.model,
        )

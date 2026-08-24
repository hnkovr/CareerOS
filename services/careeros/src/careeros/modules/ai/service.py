"""AI gateway service: render prompt → call provider (w/ fallbacks) → validate → record run."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.config import Settings
from careeros.core.logging import get_logger
from careeros.modules.ai.models import AIRun
from careeros.modules.ai.prompts import PromptRegistry, RenderedPrompt
from careeros.modules.ai.provider import AIError, AIOutputInvalid, AIProvider, AIUnavailable
from careeros.modules.ai.registry import ProviderRegistry
from careeros.modules.ai.schemas import (
    AIRunOut,
    BundleOut,
    BundleRequest,
    DevPacketOut,
    DevPacketRequest,
    FeedbackIn,
    GenerateRequest,
    GenerateResponse,
    PromptInfo,
    ProviderInfo,
)

log = get_logger(__name__)

# Best-effort prefill links. Copy/paste is the contract; links are a convenience (ADR-003 Mode B).
DEEP_LINKS: dict[str, str | None] = {
    "chatgpt": "https://chatgpt.com/?q={q}",
    "claude": "https://claude.ai/new?q={q}",
    "perplexity": "https://www.perplexity.ai/search?q={q}",
    "gemini": None,
    "grok": None,
    "generic": None,
}
DEEP_LINK_MAX_CHARS = 6000


@dataclass
class RunResult[T: BaseModel]:
    data: T
    response: GenerateResponse
    run_id: uuid.UUID | None
    retries: int
    prompt: RenderedPrompt


def inputs_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


class AIService:
    def __init__(
        self,
        settings: Settings,
        providers: ProviderRegistry,
        prompts: PromptRegistry,
        *,
        session: AsyncSession | None = None,
        user_id: uuid.UUID | None = None,
    ) -> None:
        self.settings = settings
        self.providers = providers
        self.prompts = prompts
        self.session = session
        self.user_id = user_id

    # ------------------------------------------------------------------ info
    def provider_infos(self) -> list[ProviderInfo]:
        return self.providers.infos()

    def prompt_infos(self) -> list[PromptInfo]:
        return [
            PromptInfo(
                id=lp.prompt.id,
                version=lp.prompt.version,
                area=lp.prompt.area,
                purpose=lp.prompt.purpose,
                inputs=list(lp.prompt.inputs),
                output_schema=lp.prompt.output_schema,
                provider_preferences=list(lp.prompt.provider_preferences),
                source=lp.source,  # type: ignore[arg-type]
            )
            for lp in self.prompts.all().values()
        ]

    # ------------------------------------------------------------------ core
    async def structured[T: BaseModel](
        self,
        prompt_id: str,
        inputs: dict[str, Any],
        schema: type[T],
        *,
        provider: str | None = None,
        model: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> RunResult[T]:
        rendered = self.prompts.render(prompt_id, **inputs)
        if rendered.output_schema and rendered.output_schema != schema.__name__:
            log.warning(
                "ai.schema_mismatch",
                prompt=prompt_id,
                declared=rendered.output_schema,
                used=schema.__name__,
            )
        chain = self.providers.chain(provider or self._preferred(rendered))
        errors: list[str] = []
        last_text = ""
        total_retries = 0
        last_exc: Exception | None = None

        for prov in chain:
            req = GenerateRequest(
                system=rendered.system,
                prompt=rendered.user,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=True,
            )
            retries_here = 0
            while True:
                try:
                    obj, resp = await prov.structured(req, schema)
                except AIUnavailable as exc:
                    last_exc = exc
                    log.warning("ai.provider_unavailable", provider=prov.name, error=str(exc))
                    break
                except AIError as exc:
                    last_exc = exc
                    log.warning("ai.provider_error", provider=prov.name, error=str(exc))
                    break
                except Exception as exc:  # SDK / network errors
                    last_exc = exc
                    log.warning("ai.call_failed", provider=prov.name, error=str(exc))
                    break
                last_text = resp.text
                try:
                    data = schema.model_validate(obj)
                except ValidationError as exc:
                    first = exc.errors()[0]
                    msg = (
                        f"{prov.name}: {exc.error_count()} validation error(s): "
                        f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}"
                    )
                    errors.append(msg)
                    if retries_here >= self.settings.ai_structured_max_retries:
                        last_exc = AIOutputInvalid(msg, last_text=last_text, errors=errors)
                        break
                    retries_here += 1
                    total_retries += 1
                    req = req.model_copy(
                        update={
                            "prompt": rendered.user
                            + "\n\nYour previous answer was rejected by the validator:\n"
                            + msg
                            + "\nReturn a corrected JSON object only."
                        }
                    )
                    continue
                run_id = await self._record(
                    rendered,
                    prov,
                    resp,
                    inputs,
                    output=data.model_dump(mode="json"),
                    valid=True,
                    retries=retries_here,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    schema=schema,
                )
                return RunResult(
                    data=data, response=resp, run_id=run_id, retries=total_retries, prompt=rendered
                )

        await self._record(
            rendered,
            chain[0],
            None,
            inputs,
            output=None,
            valid=False,
            retries=total_retries,
            entity_type=entity_type,
            entity_id=entity_id,
            schema=schema,
            error=str(last_exc) if last_exc else "; ".join(errors),
            status="failed",
        )
        if isinstance(last_exc, AIOutputInvalid):
            raise last_exc
        raise AIUnavailable(f"all providers failed: {last_exc or errors}")

    async def generate(
        self,
        prompt_id: str,
        inputs: dict[str, Any],
        *,
        provider: str | None = None,
        model: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> tuple[str, GenerateResponse, uuid.UUID | None]:
        rendered = self.prompts.render(prompt_id, **inputs)
        prov = self.providers.get(provider or self._preferred(rendered))
        req = GenerateRequest(
            system=rendered.system,
            prompt=rendered.user,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        resp = await prov.generate(req)
        run_id = await self._record(
            rendered,
            prov,
            resp,
            inputs,
            output={"text": resp.text},
            valid=True,
            retries=0,
            entity_type=entity_type,
            entity_id=entity_id,
            schema=None,
        )
        return resp.text, resp, run_id

    # ------------------------------------------------------------------ mode B / C
    async def bundle(self, req: BundleRequest) -> BundleOut:
        rendered = self.prompts.render(req.prompt_id, **req.inputs)
        text = (f"{rendered.system}\n\n---\n\n" if rendered.system else "") + rendered.user
        link_tpl = DEEP_LINKS.get(req.target)
        deep_link = (
            link_tpl.format(q=quote(text))
            if link_tpl and len(text) <= DEEP_LINK_MAX_CHARS
            else None
        )
        run_id = await self._record_external(
            rendered,
            req.inputs,
            mode="external_bundle",
            target=req.target,
            entity_type=req.entity_type,
            entity_id=req.entity_id,
        )
        return BundleOut(
            target=req.target,
            title=f"{rendered.prompt_id} v{rendered.version} → {req.target}",
            text=text,
            deep_link=deep_link,
            prompt_id=rendered.prompt_id,
            prompt_version=rendered.version,
            run_id=run_id,
        )

    async def dev_packet(
        self, req: DevPacketRequest, generated_dir: Path | None = None
    ) -> DevPacketOut:
        rendered = self.prompts.render("dev_task_packet", **req.model_dump())
        out_dir = Path(generated_dir or self.settings.generated_dir) / "dev-tasks"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{req.slug}.md"
        path.write_text(rendered.user + "\n", encoding="utf-8")
        run_id = await self._record_external(
            rendered,
            req.model_dump(),
            mode="dev_packet",
            target=req.agent,
            entity_type=req.entity_type,
            entity_id=req.entity_id,
        )
        return DevPacketOut(agent=req.agent, path=str(path), markdown=rendered.user, run_id=run_id)

    async def record_suggestion(
        self,
        *,
        target_type: str,
        target_ref: str,
        title: str,
        payload: dict[str, Any],
        ai_run_id: uuid.UUID | None = None,
    ) -> uuid.UUID | None:
        """Store an approval-gated AI proposal (ADR-010 §3). Returns None without a session."""
        if self.session is None or self.user_id is None:
            return None
        from careeros.modules.ai.models import Suggestion

        row = Suggestion(
            user_id=self.user_id,
            ai_run_id=ai_run_id,
            target_type=target_type,
            target_ref=target_ref,
            title=title[:300],
            payload=payload,
        )
        self.session.add(row)
        await self.session.commit()
        return row.id

    # ------------------------------------------------------------------ ledger
    async def list_runs(
        self, *, entity_type: str | None = None, entity_id: str | None = None, limit: int = 50
    ) -> list[AIRunOut]:
        if self.session is None:
            return []
        stmt = select(AIRun).order_by(AIRun.created_at.desc()).limit(limit)
        if entity_type:
            stmt = stmt.where(AIRun.entity_type == entity_type)
        if entity_id:
            stmt = stmt.where(AIRun.entity_id == entity_id)
        rows: Sequence[AIRun] = (await self.session.scalars(stmt)).all()
        return [self._to_out(r) for r in rows]

    async def get_run(self, run_id: uuid.UUID) -> AIRunOut | None:
        if self.session is None:
            return None
        row = await self.session.get(AIRun, run_id)
        return self._to_out(row) if row else None

    async def feedback(self, run_id: uuid.UUID, fb: FeedbackIn) -> AIRunOut | None:
        if self.session is None:
            return None
        row = await self.session.get(AIRun, run_id)
        if row is None:
            return None
        row.feedback = fb.feedback
        row.feedback_note = fb.note
        await self.session.commit()
        return self._to_out(row)

    # ------------------------------------------------------------------ internals
    def _preferred(self, rendered: RenderedPrompt) -> str | None:
        for name in rendered.provider_preferences:
            try:
                if self.providers.get(name).info().configured:
                    return name
            except AIUnavailable:
                continue
        return None

    def _cost(self, model: str, tokens_in: int, tokens_out: int) -> float | None:
        pricing = self.settings.ai_pricing.get(model)
        if not pricing:
            return None
        return round((tokens_in * pricing[0] + tokens_out * pricing[1]) / 1_000_000, 6)

    async def _record(
        self,
        rendered: RenderedPrompt,
        prov: AIProvider,
        resp: GenerateResponse | None,
        inputs: dict[str, Any],
        *,
        output: dict[str, Any] | None,
        valid: bool,
        retries: int,
        entity_type: str | None,
        entity_id: str | None,
        schema: type[BaseModel] | None,
        error: str | None = None,
        status: str = "ok",
    ) -> uuid.UUID | None:
        if self.session is None or self.user_id is None:
            return None
        run = AIRun(
            user_id=self.user_id,
            prompt_id=rendered.prompt_id,
            prompt_version=rendered.version,
            provider=prov.name,
            model=resp.model if resp else "",
            mode="builtin",
            inputs_hash=inputs_hash(inputs),
            inputs_payload=json.loads(json.dumps(inputs, default=str))
            if self.settings.ai_store_inputs
            else None,
            output=output,
            output_schema=schema.__name__ if schema else None,
            valid=valid,
            retries=retries,
            tokens_in=resp.usage.input_tokens if resp else 0,
            tokens_out=resp.usage.output_tokens if resp else 0,
            cost_usd=self._cost(resp.model, resp.usage.input_tokens, resp.usage.output_tokens)
            if resp
            else None,
            latency_ms=resp.latency_ms if resp else 0,
            status=status,
            error=error,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        self.session.add(run)
        await self.session.commit()
        return run.id

    async def _record_external(
        self,
        rendered: RenderedPrompt,
        inputs: dict[str, Any],
        *,
        mode: str,
        target: str,
        entity_type: str | None,
        entity_id: str | None,
    ) -> uuid.UUID | None:
        if self.session is None or self.user_id is None:
            return None
        run = AIRun(
            user_id=self.user_id,
            prompt_id=rendered.prompt_id,
            prompt_version=rendered.version,
            provider=target,
            model="external",
            mode=mode,
            inputs_hash=inputs_hash(inputs),
            inputs_payload=json.loads(json.dumps(inputs, default=str))
            if self.settings.ai_store_inputs
            else None,
            output={"text": rendered.user},
            valid=True,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        self.session.add(run)
        await self.session.commit()
        return run.id

    @staticmethod
    def _to_out(r: AIRun) -> AIRunOut:
        return AIRunOut(
            id=r.id,
            prompt_id=r.prompt_id,
            prompt_version=r.prompt_version,
            provider=r.provider,
            model=r.model,
            mode=r.mode,  # type: ignore[arg-type]
            status=r.status,
            valid=r.valid,
            retries=r.retries,
            tokens_in=r.tokens_in,
            tokens_out=r.tokens_out,
            cost_usd=r.cost_usd,
            latency_ms=r.latency_ms,
            entity_type=r.entity_type,
            entity_id=r.entity_id,
            feedback=r.feedback,
            created_at=r.created_at,
            output=r.output,
            error=r.error,
        )

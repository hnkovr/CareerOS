"""CV service: generate variants, persist artifacts with provenance, compare."""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from careeros.core.config import Settings
from careeros.core.db import utcnow
from careeros.core.ids import uuid7
from careeros.core.logging import get_logger
from careeros.modules.ai.service import AIService
from careeros.modules.cv.builder import build_document
from careeros.modules.cv.compare import compare_documents
from careeros.modules.cv.models import CVArtifact, GeneratedBullet
from careeros.modules.cv.provenance import company_name_map, fact_sources
from careeros.modules.cv.rendercv_adapter import CVRenderError, render
from careeros.modules.cv.rewrite import rewrite_with_ai
from careeros.modules.cv.schemas import (
    CVArtifactOut,
    CVComparison,
    CVDocument,
    CVFiles,
    GenerateCVRequest,
    Generation,
    VariantOut,
    files_to_dict,
)
from careeros.modules.cv.selection import select_facts
from careeros.modules.vault.service import Vault

log = get_logger(__name__)


class CVError(Exception):
    pass


class VariantNotFound(CVError):
    pass


class CVService:
    def __init__(
        self,
        settings: Settings,
        vault: Vault,
        ai: AIService,
        *,
        session: AsyncSession | None = None,
        user_id: uuid.UUID | None = None,
    ) -> None:
        self.settings = settings
        self.vault = vault
        self.ai = ai
        self.session = session
        self.user_id = user_id

    # ------------------------------------------------------------------ variants
    def variants(self) -> list[VariantOut]:
        data = self.vault.require()
        return [
            VariantOut(
                id=v.id,
                name=v.name,
                description=v.description,
                positioning_id=v.positioning_id,
                channel_id=v.channel_id,
                length=str(v.length),
                sections=[str(s) for s in v.sections],
                theme=v.rendercv_theme,
            )
            for v in data.cv_variants
        ]

    # ------------------------------------------------------------------ generate
    async def generate(
        self, req: GenerateCVRequest, *, context_text: str | None = None
    ) -> CVArtifactOut:
        data = self.vault.require()
        variants = data.by_id(data.cv_variants)
        if req.variant_id not in variants:
            raise VariantNotFound(
                f"unknown CV variant '{req.variant_id}' (known: {', '.join(variants)})"
            )
        variant = variants[req.variant_id]
        vault_sha = self.vault.head_sha()
        jd_text = "\n".join(filter(None, [req.jd_text, context_text])) or None
        sel = select_facts(data, variant, jd_text)

        artifact_id = uuid7()
        warnings: list[str] = []
        generation = Generation()
        ai_bullets = None
        summary = None
        ai_used = False

        if req.use_ai:
            try:
                configured = self.ai.providers.get(req.provider).info().configured
            except Exception:  # unknown provider name
                configured = False
            if configured:
                rw = await rewrite_with_ai(
                    self.ai,
                    data,
                    sel,
                    fact_sources(data),
                    company_name_map(data),
                    context=jd_text,
                    provider=req.provider,
                    entity_id=str(artifact_id),
                )
                warnings.extend(rw.warnings)
                ai_bullets = rw.bullets or None
                summary = rw.summary
                ai_used = bool(rw.bullets or rw.summary)
                generation = Generation(
                    provider=rw.provider,
                    model=rw.model,
                    prompt_versions=rw.prompt_versions,
                    ai_run_ids=rw.run_ids,
                )
            else:
                warnings.append(
                    "AI requested but no configured provider; generated from verbatim facts"
                )

        doc = build_document(
            data,
            sel,
            vault_sha=vault_sha,
            ai_bullets=ai_bullets,
            summary=summary,
            warnings=warnings,
        )
        doc.generation = generation

        out_dir = Path(self.settings.generated_dir) / "cv" / variant.id / str(artifact_id)
        try:
            rendered = render(doc, out_dir, list(req.formats))
        except CVRenderError as exc:
            warnings.append(str(exc))
            rendered = None
        files = rendered.files if rendered else CVFiles()
        status = "ready" if rendered else "failed"

        artifact = CVArtifact(
            id=artifact_id,
            user_id=self.user_id or uuid.UUID(int=0),
            variant_id=variant.id,
            positioning_id=doc.positioning_id,
            channel_id=doc.channel_id,
            opportunity_id=req.opportunity_id,
            vault_sha=vault_sha,
            ai_used=ai_used,
            provider=generation.provider,
            model=generation.model,
            prompt_versions=generation.prompt_versions,
            status=status,
            files=files_to_dict(files),
            document=doc.model_dump(mode="json"),
            summary_text=doc.summary.text if doc.summary else None,
            warnings=list(warnings),
            render_log=rendered.log if rendered else None,
        )
        for order, (section, group, b) in enumerate(doc.all_bullets()):
            artifact.bullets.append(
                GeneratedBullet(
                    section=section,
                    group_ref=group,
                    order=order,
                    text=b.text,
                    derived_from=list(b.derived_from),
                    source=b.source,
                    verified=b.verified,
                    user_edited=b.user_edited,
                )
            )
        if self.session is not None and self.user_id is not None:
            self.session.add(artifact)
            await self.session.commit()
        log.info(
            "cv.generated", variant=variant.id, artifact=str(artifact_id), ai=ai_used, status=status
        )
        return self._to_out(artifact, doc)

    # ------------------------------------------------------------------ read
    async def list_artifacts(
        self, *, variant_id: str | None = None, limit: int = 50
    ) -> list[CVArtifactOut]:
        if self.session is None:
            return []
        stmt = (
            select(CVArtifact)
            .options(selectinload(CVArtifact.bullets))
            .order_by(CVArtifact.created_at.desc())
            .limit(limit)
        )
        if variant_id:
            stmt = stmt.where(CVArtifact.variant_id == variant_id)
        rows = (await self.session.scalars(stmt)).all()
        return [self._to_out(r, None) for r in rows]

    async def get_artifact(
        self, artifact_id: uuid.UUID, *, with_document: bool = True
    ) -> CVArtifactOut | None:
        if self.session is None:
            return None
        row = await self.session.get(
            CVArtifact, artifact_id, options=[selectinload(CVArtifact.bullets)]
        )
        if row is None:
            return None
        return self._to_out(row, CVDocument.model_validate(row.document) if with_document else None)

    async def compare(self, a_id: uuid.UUID, b_id: uuid.UUID) -> CVComparison:
        a = await self.get_artifact(a_id)
        b = await self.get_artifact(b_id)
        if a is None or b is None or a.document is None or b.document is None:
            raise CVError("artifact not found")
        return compare_documents(a.document, b.document, label_a=str(a_id), label_b=str(b_id))

    @staticmethod
    def _to_out(row: CVArtifact, doc: CVDocument | None) -> CVArtifactOut:
        return CVArtifactOut(
            id=row.id,
            variant_id=row.variant_id,
            positioning_id=row.positioning_id,
            channel_id=row.channel_id,
            opportunity_id=row.opportunity_id,
            vault_sha=row.vault_sha,
            ai_used=row.ai_used,
            provider=row.provider,
            model=row.model,
            status=row.status,
            files=CVFiles.model_validate(row.files),
            summary_text=row.summary_text,
            bullet_count=len(row.bullets),
            warnings=list(row.warnings or []),
            created_at=row.created_at or utcnow(),
            document=doc,
        )

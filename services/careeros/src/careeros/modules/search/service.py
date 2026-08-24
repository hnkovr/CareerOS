"""Query side: FTS always; semantic merged in when embeddings exist for the current model."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Float, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.config import Settings
from careeros.core.logging import get_logger
from careeros.modules.ai.provider import AIError, AIUnavailable
from careeros.modules.ai.service import AIService
from careeros.modules.search.indexer import reindex
from careeros.modules.search.models import SearchDocument
from careeros.modules.search.schemas import (
    DocKind,
    ReindexOut,
    ReindexRequest,
    SearchHit,
    SearchOut,
)
from careeros.modules.vault.service import Vault

log = get_logger(__name__)

SNIPPET_LEN = 220


class SearchService:
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

    async def reindex(self, req: ReindexRequest) -> ReindexOut:
        counts, embedded, model = await reindex(
            self.session, self.user_id, self.vault, self.ai, embed=req.embed, provider=req.provider
        )
        return ReindexOut(
            indexed=counts, embedded=embedded, embeddings_model=model, finished_at=datetime.now(UTC)
        )

    async def search(
        self, query: str, *, kinds: list[DocKind] | None = None, limit: int = 20
    ) -> SearchOut:
        total = await self.session.scalar(select(func.count()).select_from(SearchDocument)) or 0
        hits: dict[tuple[str, str], SearchHit] = {}

        tsv = func.to_tsvector("english", SearchDocument.title + " " + SearchDocument.text)
        tsq = func.plainto_tsquery("english", query)
        stmt = (
            select(SearchDocument, cast(func.ts_rank(tsv, tsq), Float))
            .where(tsv.op("@@")(tsq))
            .order_by(func.ts_rank(tsv, tsq).desc())
            .limit(limit)
        )
        if kinds:
            stmt = stmt.where(SearchDocument.kind.in_([str(k) for k in kinds]))
        for doc, rank in (await self.session.execute(stmt)).all():
            hits[(doc.kind, doc.ref_id)] = self._hit(doc, min(1.0, rank * 2), "fts")

        semantic_used = False
        vec = await self._query_vector(query)
        if vec is not None:
            distance = SearchDocument.embedding.cosine_distance(vec)
            sem_stmt = (
                select(SearchDocument, cast(distance, Float))
                .where(and_(SearchDocument.embedding.is_not(None)))
                .order_by(distance)
                .limit(limit)
            )
            if kinds:
                sem_stmt = sem_stmt.where(SearchDocument.kind.in_([str(k) for k in kinds]))
            rows = (await self.session.execute(sem_stmt)).all()
            semantic_used = bool(rows)
            for doc, dist in rows:
                score = max(0.0, 1.0 - float(dist))
                if score < 0.3:
                    continue
                key = (doc.kind, doc.ref_id)
                if key in hits:
                    hits[key].score = max(hits[key].score, score)
                    hits[key].matched_by = "both"
                else:
                    hits[key] = self._hit(doc, score, "semantic")

        ordered = sorted(hits.values(), key=lambda h: -h.score)[:limit]
        return SearchOut(
            query=query, hits=ordered, semantic_used=semantic_used, indexed_documents=total
        )

    async def _query_vector(self, query: str) -> list[float] | None:
        provider_name = self.settings.ai_embeddings_provider
        if not provider_name:
            return None
        try:
            prov = self.ai.providers.get(provider_name)
            result = await prov.embeddings([query], self.settings.ai_embeddings_model)
            return result.vectors[0]
        except (AIError, AIUnavailable, IndexError) as exc:
            log.warning("search.query_embed_failed", error=str(exc))
            return None

    @staticmethod
    def _hit(doc: SearchDocument, score: float, matched_by: str) -> SearchHit:
        return SearchHit(
            kind=DocKind(doc.kind),
            ref_id=doc.ref_id,
            title=doc.title,
            snippet=doc.text[:SNIPPET_LEN],
            score=round(float(score), 3),
            matched_by=matched_by,
            url_path=doc.url_path,
        )

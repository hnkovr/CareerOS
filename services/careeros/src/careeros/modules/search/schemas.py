from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class DocKind(StrEnum):
    fact = "fact"
    opportunity = "opportunity"
    message = "message"
    cv_artifact = "cv_artifact"
    contact = "contact"


class SearchHit(BaseModel):
    kind: DocKind
    ref_id: str
    title: str
    snippet: str
    score: float
    matched_by: str = Field(description="fts | semantic | both")
    url_path: str | None = None


class SearchOut(BaseModel):
    query: str
    hits: list[SearchHit]
    semantic_used: bool
    indexed_documents: int


class ReindexRequest(BaseModel):
    embed: bool = Field(
        default=False, description="also compute embeddings (needs a configured provider)"
    )
    provider: str | None = None


class ReindexOut(BaseModel):
    indexed: dict[str, int]
    embedded: int
    embeddings_model: str | None
    finished_at: datetime

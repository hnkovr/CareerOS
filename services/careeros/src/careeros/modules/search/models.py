from __future__ import annotations

from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from careeros.core.db import Base, OwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class SearchDocument(UUIDPrimaryKeyMixin, OwnedMixin, TimestampMixin, Base):
    """Denormalized search cache. Rebuilt by the indexer; safe to truncate at any time."""

    __tablename__ = "search_document"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", "ref_id", name="uq_search_document_ref"),
        Index(
            "ix_search_document_tsv",
            text("to_tsvector('english', coalesce(title, '') || ' ' || text)"),
            postgresql_using="gin",
        ),
    )

    kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    ref_id: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    url_path: Mapped[str | None] = mapped_column(String(300))
    embedding: Mapped[Any | None] = mapped_column(Vector(), nullable=True)
    embedded_with: Mapped[str | None] = mapped_column(String(120))

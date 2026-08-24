from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.db
async def test_reindex_and_fts_search(db_client: AsyncClient) -> None:
    r = await db_client.post(
        "/api/opportunities/ingest",
        json={
            "source": "manual",
            "text": (
                "Search Slice Engineer at Sonar Search Labs (remote worldwide).\n"
                "Requirements:\n- ClickHouse, dbt\n$120k/year."
            ),
        },
    )
    assert r.status_code == 201
    opp_id = r.json()["id"]

    r = await db_client.post("/api/search/reindex", json={"embed": False})
    assert r.status_code == 200, r.text
    idx = r.json()
    assert idx["indexed"]["fact"] >= 40 and idx["indexed"]["opportunity"] >= 1
    assert idx["embedded"] == 0 and idx["embeddings_model"] is None

    # facts from the vault
    r = await db_client.get("/api/search", params={"q": "clickhouse migration"})
    out = r.json()
    assert out["semantic_used"] is False and out["indexed_documents"] > 40
    kinds = {h["kind"] for h in out["hits"]}
    assert "fact" in kinds
    assert any(h["ref_id"] == "ach_northwind_clickhouse" for h in out["hits"])
    assert all(h["matched_by"] == "fts" for h in out["hits"])

    # operational rows
    r = await db_client.get("/api/search", params={"q": "sonar search labs"})
    assert any(h["ref_id"] == opp_id for h in r.json()["hits"])

    # kind filter
    r = await db_client.get("/api/search", params={"q": "clickhouse", "kind": "opportunity"})
    assert all(h["kind"] == "opportunity" for h in r.json()["hits"])

    # too-short query is rejected
    r = await db_client.get("/api/search", params={"q": "c"})
    assert r.status_code == 422


@pytest.mark.db
async def test_semantic_search_with_fake_embeddings(db_client: AsyncClient, settings) -> None:  # type: ignore[no-untyped-def]
    """Fake provider embeds by keyword buckets so cosine similarity is meaningful in-test."""
    from careeros.modules.ai.deps import get_provider_registry
    from careeros.modules.ai.providers.fake import FakeProvider
    from careeros.modules.ai.schemas import EmbeddingsResponse, Usage

    class EmbedFake(FakeProvider):
        name = "fake"

        async def embeddings(
            self, texts: list[str], model: str | None = None
        ) -> EmbeddingsResponse:
            def vec(t: str) -> list[float]:
                t = t.lower()
                return [
                    1.0 if "clickhouse" in t else 0.0,
                    1.0 if ("bigquery" in t or "cost" in t) else 0.0,
                    1.0 if "kafka" in t or "streaming" in t else 0.0,
                    0.1,
                ]

            return EmbeddingsResponse(
                vectors=[vec(t) for t in texts], provider="fake", model="fake-embed", usage=Usage()
            )

    get_provider_registry(settings).register(EmbedFake(), make_default=False)
    settings.ai_embeddings_provider = "fake"
    try:
        r = await db_client.post("/api/search/reindex", json={"embed": True, "provider": "fake"})
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["embedded"] > 40 and out["embeddings_model"] == "fake/fake-embed"

        r = await db_client.get("/api/search", params={"q": "realtime clickhouse analytics"})
        result = r.json()
        assert result["semantic_used"] is True
        top_kinds = [h["matched_by"] for h in result["hits"][:5]]
        assert "both" in top_kinds or "semantic" in top_kinds
        assert any(
            "clickhouse" in h["title"].lower() or "clickhouse" in h["snippet"].lower()
            for h in result["hits"][:5]
        )
    finally:
        settings.ai_embeddings_provider = None

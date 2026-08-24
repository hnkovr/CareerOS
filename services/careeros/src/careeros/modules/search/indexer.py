"""Builds search documents from the owning modules and (optionally) embeds them."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.logging import get_logger
from careeros.modules.ai.provider import AIError, AIUnavailable
from careeros.modules.ai.service import AIService
from careeros.modules.search.models import SearchDocument
from careeros.modules.search.schemas import DocKind
from careeros.modules.vault.service import Vault

log = get_logger(__name__)

EMBED_BATCH = 64
MAX_TEXT = 4000


@dataclass(frozen=True)
class Doc:
    kind: DocKind
    ref_id: str
    title: str
    text: str
    url_path: str | None = None


def vault_documents(vault: Vault) -> list[Doc]:
    data = vault.require()
    docs: list[Doc] = []
    for a in data.achievements:
        docs.append(
            Doc(
                DocKind.fact,
                a.id,
                a.title,
                " ".join([a.title, *a.facts, *a.keywords, *a.technologies.all()]),
                f"/vault/achievements/{a.id}",
            )
        )
    for p in data.projects:
        docs.append(
            Doc(
                DocKind.fact,
                p.id,
                p.name,
                " ".join(
                    filter(
                        None, [p.name, p.summary, p.problem, p.solution, p.outcome, *p.technologies]
                    )
                ),
                f"/vault/projects/{p.id}",
            )
        )
    for e in data.experience:
        docs.append(
            Doc(
                DocKind.fact,
                e.id,
                f"{e.company_name} — {e.roles[0].title}",
                " ".join([e.company_name, e.summary, *e.responsibilities, *e.technologies]),
                f"/vault/experience/{e.id}",
            )
        )
    for sk in data.skills:
        docs.append(
            Doc(
                DocKind.fact,
                sk.id,
                sk.name,
                " ".join([sk.name, sk.category, *sk.aliases]),
                f"/vault/skills/{sk.id}",
            )
        )
    for o in data.offers:
        docs.append(
            Doc(
                DocKind.fact,
                o.id,
                o.title,
                " ".join([o.title, o.customer_problem, *o.deliverables, *o.technologies]),
                f"/vault/offers/{o.id}",
            )
        )
    return docs


async def operational_documents(session: AsyncSession) -> list[Doc]:
    from careeros.modules.cv.models import CVArtifact
    from careeros.modules.inbox.models import Message
    from careeros.modules.opportunities.models import Contact, Opportunity

    docs: list[Doc] = []
    for opp in (await session.scalars(select(Opportunity))).all():
        docs.append(
            Doc(
                DocKind.opportunity,
                str(opp.id),
                f"{opp.title} @ {opp.company_name or '?'}",
                " ".join(
                    filter(
                        None,
                        [
                            opp.title,
                            opp.company_name,
                            opp.summary,
                            " ".join(opp.technologies or []),
                            " ".join(opp.requirements or []),
                            (opp.description_md or "")[:2000],
                        ],
                    )
                ),
                f"/opportunities/{opp.id}",
            )
        )
    for msg in (await session.scalars(select(Message))).all():
        docs.append(
            Doc(
                DocKind.message,
                str(msg.id),
                msg.subject or "(no subject)",
                " ".join(filter(None, [msg.subject, msg.from_email, msg.body_text[:2000]])),
                "/inbox",
            )
        )
    for art in (await session.scalars(select(CVArtifact))).all():
        docs.append(
            Doc(
                DocKind.cv_artifact,
                str(art.id),
                f"CV {art.variant_id}",
                " ".join(filter(None, [art.variant_id, art.positioning_id, art.summary_text])),
                f"/cv/{art.id}",
            )
        )
    for contact in (await session.scalars(select(Contact))).all():
        docs.append(
            Doc(
                DocKind.contact,
                str(contact.id),
                contact.name,
                " ".join(filter(None, [contact.name, contact.email, contact.role, contact.notes])),
                "/contacts",
            )
        )
    return docs


async def reindex(
    session: AsyncSession,
    user_id: uuid.UUID,
    vault: Vault,
    ai: AIService,
    *,
    embed: bool = False,
    provider: str | None = None,
) -> tuple[dict[str, int], int, str | None]:
    docs = vault_documents(vault) + await operational_documents(session)
    await session.execute(delete(SearchDocument).where(SearchDocument.user_id == user_id))
    rows = [
        SearchDocument(
            user_id=user_id,
            kind=str(d.kind),
            ref_id=d.ref_id,
            title=d.title[:400],
            text=d.text[:MAX_TEXT],
            url_path=d.url_path,
        )
        for d in docs
    ]
    session.add_all(rows)
    await session.flush()

    embedded = 0
    model_used: str | None = None
    if embed:
        try:
            prov = ai.providers.get(provider or ai.settings.ai_embeddings_provider or None)
            for i in range(0, len(rows), EMBED_BATCH):
                batch = rows[i : i + EMBED_BATCH]
                result = await prov.embeddings(
                    [f"{r.title}\n{r.text}"[:MAX_TEXT] for r in batch],
                    ai.settings.ai_embeddings_model,
                )
                model_used = f"{result.provider}/{result.model}"
                for row, vec in zip(batch, result.vectors, strict=True):
                    row.embedding = vec
                    row.embedded_with = model_used
                embedded += len(batch)
        except (AIError, AIUnavailable) as exc:
            log.warning("search.embed_failed", error=str(exc))
    await session.commit()

    counts: dict[str, int] = {}
    for d in docs:
        counts[str(d.kind)] = counts.get(str(d.kind), 0) + 1
    log.info("search.reindexed", total=len(rows), embedded=embedded, model=model_used)
    return counts, embedded, model_used

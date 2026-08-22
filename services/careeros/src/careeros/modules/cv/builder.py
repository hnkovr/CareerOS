"""Assemble a ``CVDocument`` from a selection (+ optional AI bullets/summary)."""

from __future__ import annotations

from collections import defaultdict
from urllib.parse import urlparse

from careeros.modules.cv.keywords import keyword_hits
from careeros.modules.cv.schemas import (
    Bullet,
    CVDocument,
    CVHeader,
    EducationEntryDoc,
    ExperienceEntryDoc,
    OfferDoc,
    OneLineDoc,
    ProjectEntryDoc,
    PublicationDoc,
    SkillGroupDoc,
    TestimonialDoc,
)
from careeros.modules.cv.selection import Selection
from careeros.modules.vault import schema as s
from careeros.modules.vault.enums import ItemStatus


def fact_bullets(exp_sel: list) -> list[Bullet]:
    """Deterministic fallback: strongest facts verbatim, one bullet per fact (max 2 per item)."""
    out: list[Bullet] = []
    for scored in exp_sel:
        a = scored.achievement
        for line in a.facts[:2]:
            out.append(Bullet(text=line, derived_from=[a.id], source="fact"))
    return out


def _username(url: str | None) -> str | None:
    if not url:
        return None
    path = urlparse(url).path.strip("/")
    return path.split("/")[-1] if path else None


def build_document(
    data: s.VaultData,
    sel: Selection,
    *,
    vault_sha: str | None,
    ai_bullets: dict[str, list[Bullet]] | None = None,
    project_bullets: dict[str, list[Bullet]] | None = None,
    summary: Bullet | None = None,
    warnings: list[str] | None = None,
) -> CVDocument:
    profile = data.profile
    pos = sel.positioning
    platform = str(sel.channel.platform)
    limit = sel.variant.include.max_bullets_per_role or sel.channel.limits.bullets_per_role

    experience_docs: list[ExperienceEntryDoc] = []
    for se in sel.experiences:
        e = se.experience
        bullets = (ai_bullets or {}).get(e.company_id) or fact_bullets(se.achievements)
        roles = sorted(e.roles, key=lambda r: r.start, reverse=True)
        position = (
            roles[0].title if len(roles) == 1 else " → ".join(r.title for r in reversed(roles))
        )
        experience_docs.append(
            ExperienceEntryDoc(
                experience_id=e.id,
                company=e.company_name,
                position=position,
                start=e.start,
                end=e.end,
                location=roles[0].location,
                summary=e.summary if sel.channel.limits.bullet_chars >= 200 else None,
                bullets=bullets[:limit],
            )
        )

    project_docs: list[ProjectEntryDoc] = []
    for p in sel.projects:
        bullets = (project_bullets or {}).get(p.id) or [
            Bullet(text=t, derived_from=[p.id]) for t in filter(None, [p.solution, p.outcome])
        ]
        project_docs.append(
            ProjectEntryDoc(
                project_id=p.id,
                name=p.name,
                period=p.period,
                summary=p.summary,
                bullets=bullets[:3],
                links=list(p.links),
            )
        )

    groups: dict[str, list[s.Skill]] = defaultdict(list)
    for sk in sel.skills:
        groups[sk.category].append(sk)
    skill_docs = [
        SkillGroupDoc(
            label=cat.replace("_", " ").title(),
            items=[sk.name for sk in items],
            derived_from=[sk.id for sk in items],
        )
        for cat, items in groups.items()
    ]

    if summary is None:
        summary = Bullet(text=profile.summary_core, derived_from=[profile.id], source="fact")

    doc = CVDocument(
        variant_id=sel.variant.id,
        variant_name=sel.variant.name,
        positioning_id=pos.id,
        channel_id=sel.channel.id,
        vault_sha=vault_sha,
        locale=sel.variant.locale,
        theme=sel.variant.rendercv_theme,
        sections=list(sel.variant.sections),
        header=CVHeader(
            name=profile.name,
            headline=pos.headline,
            email=profile.contacts.email,
            phone=profile.contacts.phone,
            website=profile.contacts.website,
            location=f"{profile.location.city}, {profile.location.country}",
            github=_username(profile.contacts.github),
            linkedin=_username(profile.contacts.linkedin),
        ),
        summary=summary,
        experience=experience_docs,
        projects=project_docs,
        skills=skill_docs,
        education=[
            EducationEntryDoc(
                education_id=ed.id,
                institution=ed.institution,
                degree=ed.degree,
                field=ed.field,
                start=ed.start,
                end=ed.end,
            )
            for ed in data.education
            if ed.status != ItemStatus.retired
        ],
        certifications=[
            OneLineDoc(
                id=c.id,
                label=c.name,
                details=f"{c.issuer}{f' ({c.issued.year})' if c.issued else ''}",
            )
            for c in data.certifications
            if c.status != ItemStatus.retired and c.visibility.allows(platform)
        ],
        publications=[
            PublicationDoc(
                id=p.id,
                title=p.title,
                kind=p.kind,
                url=p.url,
                published=p.published,
                summary=p.summary,
            )
            for p in data.publications
            if p.status != ItemStatus.retired and p.visibility.allows(platform)
        ],
        languages=[OneLineDoc(id=lg.id, label=lg.name, details=lg.level) for lg in data.languages],
        offers=[
            OfferDoc(
                offer_id=o.id,
                title=o.title,
                customer_problem=o.customer_problem,
                deliverables=list(o.deliverables),
                timeline=o.timeline,
            )
            for o in data.offers
            if o.status != ItemStatus.retired
            and (not pos.emphasize.offers or o.id in pos.emphasize.offers)
            and (not o.platforms or sel.channel.platform in o.platforms)
        ],
        testimonials=[
            TestimonialDoc(id=t.id, quote=t.quote, author=t.author, author_role=t.author_role)
            for t in data.testimonials
            if t.permission_to_publish and t.status != ItemStatus.retired
        ],
        jd_keywords=sel.jd_keywords,
        warnings=warnings or [],
    )
    corpus = (
        " ".join(b.text for _, _, b in doc.all_bullets())
        + " "
        + " ".join(i for g in doc.skills for i in g.items)
    )
    doc.keywords = keyword_hits(corpus, [*sel.positioning_keywords, *sel.jd_keywords])
    return doc

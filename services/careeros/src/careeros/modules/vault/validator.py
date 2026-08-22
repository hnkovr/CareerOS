"""Referential integrity and consistency rules over a loaded ``VaultData``."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from collections.abc import Set as AbstractSet

from careeros.modules.vault import schema as s
from careeros.modules.vault.enums import EvidenceType, ItemStatus
from careeros.modules.vault.loader import VaultIssue


def _dupes(file: str, items: list[s.VaultItem | s.Prompt], issues: list[VaultIssue]) -> None:
    for item_id, n in Counter(i.id for i in items).items():
        if n > 1:
            issues.append(VaultIssue("error", file, item_id, f"duplicate id ({n}x)"))


def validate_vault(data: s.VaultData) -> list[VaultIssue]:
    issues: list[VaultIssue] = []

    companies = {e.company_id for e in data.experience}
    achievements = data.by_id(data.achievements)
    projects = data.by_id(data.projects)
    skills = data.by_id(data.skills)
    offers = data.by_id(data.offers)
    testimonials = data.by_id(data.testimonials)
    certifications = data.by_id(data.certifications)
    publications = data.by_id(data.publications)
    positioning = data.by_id(data.positioning)
    channels = data.by_id(data.channels)
    variants = data.by_id(data.cv_variants)
    prompts = {p.id: p for p in data.prompts}

    for name, items in (
        ("source/experience.yaml", data.experience),
        ("source/achievements.yaml", data.achievements),
        ("source/projects.yaml", data.projects),
        ("source/skills.yaml", data.skills),
        ("source/offers.yaml", data.offers),
        ("source/testimonials.yaml", data.testimonials),
        ("positioning/", data.positioning),
        ("channels/", data.channels),
        ("cv/variants/", data.cv_variants),
        ("prompts/", data.prompts),
    ):
        _dupes(name, items, issues)  # type: ignore[arg-type]

    def ref(
        file: str,
        owner: str,
        field: str,
        target: str,
        pool: Mapping[str, object] | AbstractSet[str],
    ) -> None:
        if target not in pool:
            issues.append(
                VaultIssue("error", file, f"{owner}.{field}", f"unknown reference '{target}'")
            )

    def evidence(file: str, item: s.VaultItem) -> None:
        for ev in item.evidence:
            pool: Mapping[str, object] | None = {
                EvidenceType.project: projects,
                EvidenceType.testimonial: testimonials,
                EvidenceType.certification: certifications,
                EvidenceType.publication: publications,
            }.get(ev.type)
            if pool is not None:
                ref(file, item.id, f"evidence[{ev.type}]", ev.ref, pool)

    def retired_ref(file: str, owner: s.VaultItem, target: s.VaultItem, field: str) -> None:
        if target.status == ItemStatus.retired and owner.status != ItemStatus.retired:
            issues.append(
                VaultIssue(
                    "warning", file, f"{owner.id}.{field}", f"references retired '{target.id}'"
                )
            )

    # experience → achievements/projects
    for exp in data.experience:
        f = "source/experience.yaml"
        for aid in exp.achievement_ids:
            ref(f, exp.id, "achievement_ids", aid, achievements)
            if aid in achievements:
                retired_ref(f, exp, achievements[aid], "achievement_ids")
                if achievements[aid].company_id != exp.company_id:
                    issues.append(
                        VaultIssue(
                            "warning",
                            f,
                            f"{exp.id}.achievement_ids",
                            f"'{aid}' belongs to another company",
                        )
                    )
        for pid in exp.project_ids:
            ref(f, exp.id, "project_ids", pid, projects)
        evidence(f, exp)

    for ach in data.achievements:
        f = "source/achievements.yaml"
        ref(f, ach.id, "company_id", ach.company_id, companies)
        evidence(f, ach)
        if ach.status == ItemStatus.verified and not ach.evidence and not ach.metrics:
            issues.append(
                VaultIssue("warning", f, ach.id, "verified achievement without evidence or metrics")
            )

    for proj in data.projects:
        f = "source/projects.yaml"
        if proj.company_id is not None:
            ref(f, proj.id, "company_id", proj.company_id, companies)
        evidence(f, proj)

    for sk in data.skills:
        evidence("source/skills.yaml", sk)

    for off in data.offers:
        f = "source/offers.yaml"
        for pid in off.proof:
            if pid not in projects and pid not in testimonials:
                issues.append(
                    VaultIssue("error", f, f"{off.id}.proof", f"unknown reference '{pid}'")
                )

    for t in data.testimonials:
        if t.company_id is not None:
            ref("source/testimonials.yaml", t.id, "company_id", t.company_id, companies)

    for pos in data.positioning:
        f = f"positioning/{pos.id}.yaml"
        for sid in pos.emphasize.skills:
            ref(f, pos.id, "emphasize.skills", sid, skills)
        for aid in pos.emphasize.achievements:
            ref(f, pos.id, "emphasize.achievements", aid, achievements)
        for pid in pos.emphasize.projects:
            ref(f, pos.id, "emphasize.projects", pid, projects)
        for oid in pos.emphasize.offers:
            ref(f, pos.id, "emphasize.offers", oid, offers)
        for did in pos.deemphasize:
            if did not in skills and did not in achievements and did not in projects:
                issues.append(
                    VaultIssue("error", f, f"{pos.id}.deemphasize", f"unknown reference '{did}'")
                )

    for var in data.cv_variants:
        f = f"cv/variants/{var.id}.yaml"
        ref(f, var.id, "positioning_id", var.positioning_id, positioning)
        ref(f, var.id, "channel_id", var.channel_id, channels)
        for cid in var.include.companies:
            ref(f, var.id, "include.companies", cid, companies)

    ref("vault.yaml", "meta", "default_positioning", data.meta.default_positioning, positioning)
    ref("vault.yaml", "meta", "default_cv_variant", data.meta.default_cv_variant, variants)

    for p in prompts.values():
        if p.output_schema and not p.output_schema.isidentifier():
            issues.append(
                VaultIssue("error", f"prompts/{p.id}.yaml", "output_schema", "not a model name")
            )

    if data.scoring is not None:
        known = {t.lower() for techs in data.scoring.tech_groups.values() for t in techs}
        for sk in data.skills:
            if (
                sk.market_groups
                and sk.name.lower() not in known
                and not any(a.lower() in known for a in sk.aliases)
            ):
                issues.append(
                    VaultIssue(
                        "warning",
                        "source/skills.yaml",
                        sk.id,
                        "has market_groups but is not listed in scoring tech_groups (aliases?)",
                    )
                )

    return issues

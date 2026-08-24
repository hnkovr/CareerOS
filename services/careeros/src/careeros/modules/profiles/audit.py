"""Deterministic profile audit: compare a snapshot with canonical facts and channel rules.

Pure functions over ``VaultData`` + ``SnapshotIn`` → findings and scores. AI refines later; it
never replaces these checks (ADR-010).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from careeros.modules.cv.keywords import contains_keyword, normalize
from careeros.modules.profiles.enums import SEVERITY_PENALTY, AuditCategory, Severity
from careeros.modules.profiles.schemas import FindingOut, SnapshotIn
from careeros.modules.vault import schema as s
from careeros.modules.vault.enums import ItemStatus, Platform, SkillTier

ENGINE_VERSION = "audit-v1"
STALE_SNAPSHOT_DAYS = 90
_YEARS_RE = re.compile(r"(\d{1,2})\+?\s+years", re.IGNORECASE)


def _f(
    category: AuditCategory,
    severity: Severity,
    problem: str,
    why: str,
    *,
    suggestion: str | None = None,
    facts: list[str] | None = None,
) -> FindingOut:
    return FindingOut(
        category=category,
        severity=severity,
        problem=problem,
        why_it_matters=why,
        suggested_change=suggestion,
        source_fact_ids=facts or [],
    )


def _snapshot_text(snap: SnapshotIn) -> str:
    parts = [snap.headline or "", snap.about or "", snap.raw_text or ""]
    parts += [f"{e.company} {e.title or ''} {e.description or ''}" for e in snap.experience]
    parts += [str(p) for p in snap.projects]
    parts += snap.skills
    return " ".join(parts)


def audit_snapshot(
    data: s.VaultData, snap: SnapshotIn, *, now: datetime | None = None
) -> list[FindingOut]:
    now = now or datetime.now(UTC)
    channel = next((c for c in data.channels if c.platform == snap.platform), None)
    positioning = data.by_id(data.positioning)[data.meta.default_positioning]
    findings: list[FindingOut] = []
    text_norm = normalize(_snapshot_text(snap))
    platform = snap.platform

    # --- completeness (channel required sections)
    presence = {
        "headline": bool(snap.headline),
        "about": bool(snap.about),
        "summary": bool(snap.about),
        "experience": bool(snap.experience) or bool(snap.raw_text),
        "skills": len(snap.skills) >= 3,
        "projects": bool(snap.projects) or bool(snap.portfolio),
        "portfolio": bool(snap.portfolio) or bool(snap.projects),
        "featured": bool(snap.portfolio),
        "certifications": True,  # rarely captured; do not punish blindly
        "offers": bool(snap.portfolio) or bool(snap.projects),
        "testimonials": True,
        "links": True,
        "education": True,
    }
    if channel:
        for section in channel.required_sections:
            if not presence.get(section, True):
                findings.append(
                    _f(
                        AuditCategory.completeness,
                        Severity.high,
                        f"section '{section}' is empty on {platform}",
                        f"{channel.name} rewards completeness; empty '{section}' costs visibility",
                        suggestion=f"Fill '{section}' from the vault (see suggested sources)",
                    )
                )

    # --- freshness
    captured = snap.captured_at or now
    age_days = (now - captured).days
    if age_days > STALE_SNAPSHOT_DAYS:
        findings.append(
            _f(
                AuditCategory.freshness,
                Severity.medium,
                f"snapshot is {age_days} days old",
                "audits against a stale snapshot may miss what is actually live",
                suggestion="Re-capture the profile before applying changes",
            )
        )
    current = [e for e in data.experience if e.end is None]
    if current and (snap.experience or snap.raw_text):
        newest = current[0]
        if newest.company_name.lower() not in text_norm:
            findings.append(
                _f(
                    AuditCategory.freshness,
                    Severity.critical,
                    f"current position at {newest.company_name} is missing",
                    "an outdated experience section reads as an inactive or stale profile",
                    suggestion=f"Add the {newest.company_name} role ({newest.roles[0].title})",
                    facts=[newest.id],
                )
            )

    # --- keyword coverage vs positioning
    missing_must = [k for k in positioning.keywords_must if not contains_keyword(text_norm, k)]
    if missing_must:
        findings.append(
            _f(
                AuditCategory.keyword_coverage,
                Severity.high if len(missing_must) > 2 else Severity.medium,
                f"must-have keywords absent: {', '.join(missing_must[:8])}",
                "recruiter search and platform matching are keyword-driven",
                suggestion="Work these into the headline, about or skills sections",
                facts=[positioning.id],
            )
        )

    # --- channel fit (limits)
    if channel:
        if (
            snap.headline
            and channel.limits.headline_chars
            and len(snap.headline) > channel.limits.headline_chars
        ):
            findings.append(
                _f(
                    AuditCategory.channel_fit,
                    Severity.high,
                    f"headline is {len(snap.headline)} chars "
                    f"(limit {channel.limits.headline_chars})",
                    "over-limit headlines get truncated exactly where the keywords live",
                    suggestion="Shorten the headline below the platform limit",
                )
            )
        if (
            snap.about
            and channel.limits.about_chars
            and len(snap.about) > channel.limits.about_chars
        ):
            findings.append(
                _f(
                    AuditCategory.channel_fit,
                    Severity.medium,
                    f"about is {len(snap.about)} chars (limit {channel.limits.about_chars})",
                    "the tail of an over-limit about section is never read",
                )
            )
        if (
            channel.cta
            and snap.about
            and channel.cta.split()[0].lower() not in (snap.about or "").lower()
        ):
            has_cta = any(
                w in (snap.about or "").lower()
                for w in ("message me", "contact me", "book a call", "reach out", "get in touch")
            )
            if not has_cta:
                findings.append(
                    _f(
                        AuditCategory.call_to_action,
                        Severity.high if platform == Platform.upwork else Severity.medium,
                        "about section has no call to action",
                        f"{channel.name} profiles convert when they say what to do next",
                        suggestion=f'End the about section with a CTA, e.g. "{channel.cta}"',
                    )
                )

    # --- consistency: years of experience
    claimed_years = [int(m) for m in _YEARS_RE.findall(f"{snap.headline or ''} {snap.about or ''}")]
    actual = now.year - data.profile.years_experience_since
    for years in claimed_years:
        if abs(years - actual) > 1:
            findings.append(
                _f(
                    AuditCategory.consistency,
                    Severity.high,
                    f"profile claims {years} years of experience; canonical data implies ~{actual}",
                    "cross-platform contradictions surface in background checks and interviews",
                    suggestion=f"State {actual}+ years everywhere",
                    facts=[data.profile.id],
                )
            )
            break

    # --- consistency: claimed skills unknown to the vault
    vault_skill_names = {sk.name.lower() for sk in data.skills} | {
        a.lower() for sk in data.skills for a in sk.aliases
    }
    unknown = [sk for sk in snap.skills if sk.lower() not in vault_skill_names]
    if unknown:
        findings.append(
            _f(
                AuditCategory.consistency,
                Severity.medium,
                f"skills listed but not in the vault: {', '.join(unknown[:8])}",
                "claims without canonical backing cannot be defended with evidence",
                suggestion="Add them to the vault with evidence, or remove them from the profile",
            )
        )

    # --- positioning fit: first-priority skills missing from the profile
    first_priority = [
        sk
        for sk in data.skills
        if sk.tier == SkillTier.first_priority and sk.status != ItemStatus.retired
    ]
    missing_fp = [sk.name for sk in first_priority if not contains_keyword(text_norm, sk.name)]
    if missing_fp:
        findings.append(
            _f(
                AuditCategory.positioning_fit,
                Severity.high if len(missing_fp) > 2 else Severity.medium,
                f"first-priority skills not visible: {', '.join(missing_fp)}",
                "the profile should lead with the skills the positioning leads with",
                facts=[sk.id for sk in first_priority if sk.name in missing_fp],
            )
        )

    # --- proof / metrics
    body = f"{snap.about or ''} " + " ".join(e.description or "" for e in snap.experience)
    if body.strip() and not re.search(r"\d", body):
        metric_facts = [a.id for a in data.achievements if a.metrics][:5]
        findings.append(
            _f(
                AuditCategory.proof_metrics,
                Severity.high,
                "no measurable results anywhere in the profile text",
                "numbers are what recruiters and clients scan for",
                suggestion="Add 2-3 quantified achievements from the vault",
                facts=metric_facts,
            )
        )

    # --- portfolio coverage (public projects)
    public_projects = [p for p in data.projects if p.public and p.status != ItemStatus.retired]
    if platform in (Platform.upwork, Platform.wellfound, Platform.linkedin) and public_projects:
        missing_projects = [p for p in public_projects if p.name.lower() not in text_norm]
        if missing_projects:
            findings.append(
                _f(
                    AuditCategory.portfolio_coverage,
                    Severity.medium if platform == Platform.upwork else Severity.nice,
                    "public portfolio projects not shown: "
                    + ", ".join(p.name for p in missing_projects[:3]),
                    "portfolio proof converts better than claims",
                    facts=[p.id for p in missing_projects],
                )
            )

    # --- compensation positioning (rates on freelance platforms)
    if platform in (Platform.upwork, Platform.toptal) and not snap.rates:
        findings.append(
            _f(
                AuditCategory.compensation_positioning,
                Severity.medium,
                "no rate captured on a freelance platform",
                "an absent or outdated rate mis-positions every proposal",
                suggestion="Set the rate consistent with scoring targets",
            )
        )

    # --- remote eligibility / location clarity
    if platform in (Platform.linkedin, Platform.wellfound):
        remote_words = ("remote", "contractor", "b2b", "timezone", "utc", "cet")
        if snap.about and not any(w in snap.about.lower() for w in remote_words):
            findings.append(
                _f(
                    AuditCategory.remote_eligibility_clarity,
                    Severity.nice,
                    "about section does not state remote/contractor availability",
                    "recruiters filter on eligibility before anything else",
                    suggestion="State remote availability, contractor setup and timezone overlap",
                    facts=[data.profile.id],
                )
            )
        city = data.profile.location.city.lower()
        if snap.about and city not in text_norm and "remote" not in (snap.headline or "").lower():
            findings.append(
                _f(
                    AuditCategory.location_clarity,
                    Severity.nice,
                    "location is not clear from the profile",
                    "ambiguous location causes mismatched outreach",
                    facts=[data.profile.id],
                )
            )

    # --- credibility (certifications on linkedin)
    if platform == Platform.linkedin:
        missing_certs = [
            c
            for c in data.certifications
            if c.status != ItemStatus.retired and c.name.lower() not in text_norm
        ]
        if missing_certs:
            findings.append(
                _f(
                    AuditCategory.credibility,
                    Severity.nice,
                    f"certifications not shown: {', '.join(c.name for c in missing_certs[:3])}",
                    "certifications feed recruiter filters and credibility",
                    facts=[c.id for c in missing_certs],
                )
            )

    return findings


def category_scores(findings: list[FindingOut]) -> dict[str, int]:
    scores = {c: 100 for c in AuditCategory}
    for f in findings:
        scores[f.category] = max(0, scores[f.category] - SEVERITY_PENALTY[f.severity])
    return {str(k): v for k, v in scores.items()}


def health_score(scores: dict[str, int], findings: list[FindingOut] | None = None) -> int:
    """0-100. Severity-driven: the category mean alone dilutes real problems across 13 categories,
    so the score is the total severity penalty (softened) subtracted from 100."""
    if findings is None:
        total = sum(100 - v for v in scores.values())
    else:
        total = sum(SEVERITY_PENALTY[f.severity] for f in findings)
    return max(0, 100 - round(total / 2))

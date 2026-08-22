"""AI rewriting of selected facts into bullets, guarded by provenance checks.

Any bullet that fails the guard is dropped and replaced by the verbatim fact — the CV is never
blocked by a bad model answer, and nothing unverifiable reaches the document.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from careeros.modules.ai.provider import AIError
from careeros.modules.ai.service import AIService
from careeros.modules.cv.provenance import FactSource, check_bullet
from careeros.modules.cv.schemas import Bullet, CVBulletsOutput, CVSummaryOutput
from careeros.modules.cv.selection import Selection
from careeros.modules.vault import schema as s


@dataclass
class RewriteResult:
    bullets: dict[str, list[Bullet]] = field(default_factory=dict)  # company_id → bullets
    summary: Bullet | None = None
    warnings: list[str] = field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    prompt_versions: dict[str, int] = field(default_factory=dict)
    run_ids: list[uuid.UUID] = field(default_factory=list)


def _facts_payload(sel: Selection) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for se in sel.experiences:
        for scored in se.achievements:
            a = scored.achievement
            out.append(
                {
                    "id": a.id,
                    "company": se.experience.company_name,
                    "company_id": se.experience.company_id,
                    "title": a.title,
                    "facts": list(a.facts),
                    "metrics": [
                        f"{m.name}: {m.value}{' ' + m.unit if m.unit else ''}"
                        + (f" (baseline {m.baseline})" if m.baseline else "")
                        for m in a.metrics
                    ],
                }
            )
    return out


async def rewrite_with_ai(
    ai: AIService,
    data: s.VaultData,
    sel: Selection,
    sources: dict[str, FactSource],
    company_names: dict[str, str],
    *,
    context: str | None,
    provider: str | None,
    entity_id: str | None,
) -> RewriteResult:
    result = RewriteResult()
    allowed_ids = {scored.achievement.id for se in sel.experiences for scored in se.achievements}
    company_of_fact = {
        scored.achievement.id: se.experience.company_id
        for se in sel.experiences
        for scored in se.achievements
    }

    try:
        run = await ai.structured(
            "cv_bullets",
            {
                "positioning": sel.positioning.model_dump(mode="json"),
                "channel": sel.channel.model_dump(mode="json"),
                "facts": _facts_payload(sel),
                "context": context,
            },
            CVBulletsOutput,
            provider=provider,
            entity_type="cv",
            entity_id=entity_id,
        )
    except AIError as exc:
        result.warnings.append(f"AI bullets unavailable ({exc}); using verbatim facts")
        return result

    result.provider, result.model = run.response.provider, run.response.model
    result.prompt_versions["cv_bullets"] = run.prompt.version
    if run.run_id:
        result.run_ids.append(run.run_id)

    for group in run.data.groups:
        kept: list[Bullet] = []
        for b in group.bullets:
            cited = [fid for fid in b.derived_from if fid in allowed_ids]
            problems = check_bullet(b.text, b.derived_from, sources, company_names)
            if not cited:
                problems.append("cites no selected fact")
            elif any(company_of_fact.get(fid) != group.company_id for fid in cited):
                problems.append("cites facts from another company")
            if problems:
                result.warnings.append(f"dropped bullet '{b.text[:60]}…': {'; '.join(problems)}")
                continue
            kept.append(Bullet(text=b.text.strip(), derived_from=cited, source="ai"))
        if kept:
            result.bullets[group.company_id] = kept

    # summary
    highlights = [
        {"id": scored.achievement.id, "text": scored.achievement.facts[0]}
        for se in sel.experiences
        for scored in se.achievements[:2]
    ]
    try:
        srun = await ai.structured(
            "cv_summary",
            {
                "positioning": sel.positioning.model_dump(mode="json"),
                "channel": sel.channel.model_dump(mode="json"),
                "profile": data.profile.model_dump(mode="json"),
                "highlights": highlights,
                "context": context,
            },
            CVSummaryOutput,
            provider=provider,
            entity_type="cv",
            entity_id=entity_id,
        )
        result.prompt_versions["cv_summary"] = srun.prompt.version
        if srun.run_id:
            result.run_ids.append(srun.run_id)
        cited = [fid for fid in srun.data.derived_from if fid in sources]
        problems = check_bullet(srun.data.text, cited or [data.profile.id], sources, company_names)
        # summary may name companies of the facts it cites; other company mentions are flagged
        if problems:
            result.warnings.append(f"dropped AI summary: {'; '.join(problems)}")
        else:
            result.summary = Bullet(
                text=srun.data.text.strip(), derived_from=cited or [data.profile.id], source="ai"
            )
    except AIError as exc:
        result.warnings.append(f"AI summary unavailable ({exc}); using core summary")
    return result

"""Parsing and rendering for /cv, /cv update and /cv improve (GH #29, #30).

`meta` in the owner's original wording means the CORE cv — the master variant that
channel variants project from — not a metadata field. The repo's vocabulary is
positioning → channel → variant, and the vault names the core one in
`meta.default_cv_variant`, so that is what the aliases below resolve to.

Rendering rule, from invariant 2: a generated bullet exists only as a claim about
the facts it derives from. This module therefore prints `derived_from[]` next to
every bullet it shows, and where it shows fewer bullets than the diff holds it says
so — a silently truncated diff reads as "that is all that changed", which is a
different and wrong claim.
"""

from __future__ import annotations

from dataclasses import dataclass

from careeros.modules.bot.formatting import escape_md

#: Names that mean "the core CV" rather than a channel variant.
CORE_ALIASES = frozenset({"meta", "core", "master", "main", "default"})

#: How many bullets of each kind a diff card prints before summarising the rest.
#: A phone-sized answer beats a complete one that nobody scrolls; the artifact
#: JSON holds every bullet either way.
DIFF_BULLETS = 6

USAGE = (
    "/cv — list variants\n"
    "/cv update [meta|<variant>] — regenerate from the facts, no AI\n"
    "/cv improve [meta|<variant>] — let AI rewrite the selected facts, then show the diff"
)


@dataclass(frozen=True)
class CVCommand:
    action: str  # "show" | "update" | "improve"
    variant: str | None  # None = the core variant


def parse_cv_command(text: str) -> CVCommand:
    """Parse `/cv`, `/cv update [in] <variant>`, `/cv improve [in] <variant>`.

    `in` is accepted and ignored: the owner asked for "update CV in linkedin", and
    rejecting the preposition they wrote would be pedantry, not validation.
    """
    parts = text.split()[1:]  # drop the command word itself
    if not parts or parts[0].lower() in ("list", "variants"):
        return CVCommand(action="show", variant=None)
    action = parts[0].lower()
    if action not in ("update", "improve"):
        raise ValueError(f"unknown /cv action: {parts[0]}\n\n{USAGE}")
    rest = [p for p in parts[1:] if p.lower() != "in"]
    if not rest:
        return CVCommand(action=action, variant=None)
    if len(rest) > 1:
        raise ValueError(f"one variant at a time, got: {' '.join(rest)}\n\n{USAGE}")
    name = rest[0].strip().lower()
    return CVCommand(action=action, variant=None if name in CORE_ALIASES else name)


def resolve_variant(vault_data: object, requested: str | None) -> str:
    """Variant id to generate: the requested one, else the vault's core variant."""
    known = [str(getattr(v, "id", "")) for v in (getattr(vault_data, "cv_variants", None) or [])]
    if requested is None:
        core = str(getattr(getattr(vault_data, "meta", None), "default_cv_variant", "") or "")
        if not core:
            raise ValueError("the vault names no default cv variant (meta.default_cv_variant)")
        return core
    if requested not in known:
        raise ValueError(f"unknown cv variant: {requested}. known: {', '.join(sorted(known))}")
    return requested


def variants_card(variants: list[object], core_id: str | None) -> str:
    """MarkdownV2 listing of every variant, marking the core one."""
    if not variants:
        return escape_md("the vault defines no cv variants")
    lines = ["*CV variants*"]
    for v in variants:
        vid = str(getattr(v, "id", ""))
        name = str(getattr(v, "name", "") or "")
        mark = " ← core" if vid == core_id else ""
        lines.append(escape_md(f"· {vid} — {name}{mark}"))
    lines.append("")
    lines.append(escape_md(USAGE))
    return "\n".join(lines)


def artifact_card(artifact: object, *, header: str) -> str:
    """MarkdownV2 summary of one generated artifact, warnings included.

    Warnings are printed rather than logged: the provenance guard reports here, and
    a CV that lost a bullet to a rejected claim must not look like a clean run.
    """
    status = str(getattr(artifact, "status", "?"))
    lines = [f"*{escape_md(header)}*", ""]
    lines.append(escape_md(f"variant: {getattr(artifact, 'variant_id', '?')}"))
    lines.append(escape_md(f"status: {status}  ·  bullets: {getattr(artifact, 'bullet_count', 0)}"))

    if getattr(artifact, "ai_used", False):
        provider = getattr(artifact, "provider", None) or "?"
        model = getattr(artifact, "model", None) or "?"
        lines.append(escape_md(f"ai: {provider}/{model}"))
    else:
        lines.append(escape_md("ai: not used — verbatim facts"))

    sha = getattr(artifact, "vault_sha", None)
    if sha:
        lines.append(escape_md(f"vault: {str(sha)[:12]}"))

    warnings = list(getattr(artifact, "warnings", None) or [])
    if warnings:
        lines.append("")
        lines.append(escape_md(f"⚠ {len(warnings)} warning(s):"))
        lines.extend(escape_md(f"· {w}") for w in warnings)
    return "\n".join(lines)


def _bullet_lines(diffs: list[object], *, side: str) -> list[str]:
    """Render up to DIFF_BULLETS entries, each with the facts it derives from."""
    lines: list[str] = []
    for d in diffs[:DIFF_BULLETS]:
        text = getattr(d, side, None) or ""
        lines.append(escape_md(f"· {text}"))
        derived = list(getattr(d, "derived_from", None) or [])
        # Never dropped: a generated bullet without its provenance is an unsourced
        # claim, which is exactly what invariant 2 exists to prevent.
        lines.append(escape_md(f"    from: {', '.join(derived) if derived else '(none)'}"))
    if len(diffs) > DIFF_BULLETS:
        lines.append(escape_md(f"    … and {len(diffs) - DIFF_BULLETS} more"))
    return lines


def diff_card(comparison: object) -> str:
    """MarkdownV2 rendering of what the AI pass changed against the verbatim facts."""
    rewritten = list(getattr(comparison, "rewritten", None) or [])
    added = list(getattr(comparison, "added", None) or [])
    removed = list(getattr(comparison, "removed", None) or [])
    unchanged = getattr(comparison, "unchanged", 0)

    lines = ["*What AI changed*", ""]
    lines.append(
        escape_md(
            f"rewritten {len(rewritten)} · added {len(added)} · "
            f"removed {len(removed)} · unchanged {unchanged}"
        )
    )
    if not (rewritten or added or removed):
        lines.append("")
        lines.append(escape_md("nothing changed — the bullets are the facts as written"))
        return "\n".join(lines)

    for label, diffs, side in (
        ("rewritten", rewritten, "text_b"),
        ("added", added, "text_b"),
        ("removed", removed, "text_a"),
    ):
        if not diffs:
            continue
        lines.append("")
        lines.append(f"*{escape_md(label)}*")
        lines.extend(_bullet_lines(diffs, side=side))

    only_b = list(getattr(comparison, "keywords_only_b", None) or [])
    if only_b:
        lines.append("")
        lines.append(escape_md(f"new keywords: {', '.join(only_b)}"))
    return "\n".join(lines)

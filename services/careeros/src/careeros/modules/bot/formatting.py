"""MarkdownV2 helpers.

Telegram rejects an unescaped reserved character with a 400 that names a byte
offset, not the character — so escaping is done in one place and tested.
"""

from __future__ import annotations

#: Every character Telegram requires escaping in MarkdownV2.
_RESERVED = r"_*[]()~`>#+-=|{}.!"


def escape_md(text: str) -> str:
    """Escape all MarkdownV2 reserved characters."""
    return "".join("\\" + ch if ch in _RESERVED else ch for ch in text)


#: A phone card must fit on a phone; the full breakdown is one tap away.
TOP_DIMENSIONS = 3


def _contribution(dimension: object) -> float:
    """What actually moved the overall number: score x weight.

    Ordering by raw score would promote a 92% dimension weighted at 0.05 over a
    70% one weighted at 0.25, which misrepresents why the total came out as it did.
    """
    return getattr(dimension, "score", 0) * getattr(dimension, "weight", 0.0)


def score_card(detail: object) -> str:
    """Render one opportunity as a MarkdownV2 triage card.

    Everything interpolated is escaped: job titles routinely contain parentheses,
    hyphens and periods, and Telegram answers an unescaped one with a 400 that
    names a byte offset rather than the character.
    """
    title = escape_md(str(getattr(detail, "title", "") or "untitled"))
    lines = [f"*{title}*"]

    company = getattr(detail, "company_name", None)
    if company:
        lines.append(escape_md(str(company)))

    score = getattr(detail, "score", None)
    if score is None:
        lines.append("")
        lines.append(escape_md("not scored (no vault scoring model available)"))
    else:
        recommendation = getattr(getattr(score, "recommendation", None), "value", "")
        head = f"*{getattr(score, 'overall', '?')}*/100"
        if recommendation:
            head += escape_md(f" — {str(recommendation).replace('_', ' ')}")
        lines.append("")
        lines.append(head)
        top = sorted(getattr(score, "dimensions", []), key=_contribution, reverse=True)
        for dimension in top[:TOP_DIMENSIONS]:
            name = escape_md(str(getattr(dimension, "name", "")))
            lines.append(
                f"· {name} {getattr(dimension, 'score', '?')} — "
                f"{escape_md(str(getattr(dimension, 'explanation', '')))}"
            )

    if getattr(detail, "possible_duplicate_of", None):
        lines.append("")
        lines.append(escape_md("⚠ possible duplicate of an opportunity you already have"))

    return "\n".join(lines)

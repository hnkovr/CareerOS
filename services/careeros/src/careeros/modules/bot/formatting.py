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


#: Telegram rejects a sendMessage body over this length outright (400, no partial
#: send). A CV diff or a variant listing overruns it easily, so every outbound text
#: goes through `chunk_message` rather than trusting it to be short.
MESSAGE_LIMIT = 4096


def chunk_message(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Split an ALREADY-ESCAPED message into pieces Telegram will accept.

    Splitting after escaping, and only on newlines, is what keeps this safe: a
    MarkdownV2 escape is a backslash glued to the character after it, and escaping
    never puts a backslash before a newline — so a newline boundary can never fall
    inside an escape pair. A single line longer than the limit still has to be cut
    mid-text, and there the cut backs off one character rather than orphan a
    trailing backslash, which Telegram would answer with a 400.
    """
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(line) > limit:
            cut = limit
            # An odd run of trailing backslashes means the last one escapes the
            # character we are about to cut away; keep them together.
            while cut > 0 and (len(line[:cut]) - len(line[:cut].rstrip("\\"))) % 2 == 1:
                cut -= 1
            chunks.append(line[:cut])
            line = line[cut:]
        current = line
    if current:
        chunks.append(current)
    return [c for c in chunks if c]

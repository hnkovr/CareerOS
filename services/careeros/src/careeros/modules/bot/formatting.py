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


#: How much of a uuid a chat handle shows. Eight hex characters is 4 billion values
#: against a personal backlog of hundreds, and it fits a phone line next to a title.
SHORT_ID_CHARS = 8

#: Lists of strengths / gaps / risks are capped so one analysis stays one screen.
ANALYSIS_ITEMS = 3


def short_id(opportunity_id: object) -> str:
    """The handle printed in listings and accepted back by `/opp`."""
    return str(opportunity_id)[:SHORT_ID_CHARS]


def matches_short_id(opportunity_id: object, wanted: str) -> bool:
    """True when `wanted` is a prefix of this id, dashes optional.

    Prefix matching rather than equality because the whole point of the short handle
    is that nobody types 36 characters on a phone — but the full uuid must keep
    working, so both spellings resolve.
    """
    full = str(opportunity_id).lower()
    probe = wanted.strip().lower()
    return bool(probe) and (full.startswith(probe) or full.replace("-", "").startswith(probe))


def ranked_list(items: list[object]) -> str:
    """Plain-text ranked listing, each row carrying a tappable `/opp_<handle>`.

    Plain rather than MarkdownV2: the handle contains an underscore, which is a
    MarkdownV2 italic marker, and Telegram renders a bare `/opp_ab12cd34` as a
    tappable command only in unformatted text.
    """
    if not items:
        return "nothing scored yet — forward me a job description"
    lines: list[str] = []
    for rank, item in enumerate(items, start=1):
        score = getattr(getattr(item, "score", None), "overall", None)
        company = getattr(item, "company_name", None)
        title = str(getattr(item, "title", "") or "untitled")
        head = f"{rank}. {score if score is not None else '--'} · {title}"
        if company:
            head += f" — {company}"
        lines.append(head)
        lines.append(f"   /opp_{short_id(getattr(item, 'id', ''))}")
    return "\n".join(lines)


def analysis_card(detail: object) -> str:
    """MarkdownV2 rendering of one AI analysis of an already-computed score.

    The verdict is AI's reading of the deterministic breakdown, never a second
    opinion on the number (invariant 4), so the score is shown next to it rather
    than replaced by it.
    """
    analysis = getattr(detail, "analysis", None)
    if analysis is None:
        return escape_md("no analysis came back — check the AI provider configuration")

    title = escape_md(str(getattr(detail, "title", "") or "untitled"))
    verdict = escape_md(str(getattr(analysis, "verdict", "?")).replace("_", " "))
    score = getattr(getattr(detail, "score", None), "overall", None)
    head = f"*{verdict}*" + (escape_md(f" · score {score}/100") if score is not None else "")

    lines = [f"*{title}*", "", head, "", escape_md(str(getattr(analysis, "executive_summary", "")))]
    for label, attr in (("strengths", "strengths"), ("gaps", "gaps"), ("risks", "risks")):
        values = list(getattr(analysis, attr, None) or [])
        if not values:
            continue
        lines.append("")
        lines.append(f"*{escape_md(label)}*")
        lines.extend(escape_md(f"· {v}") for v in values[:ANALYSIS_ITEMS])
        if len(values) > ANALYSIS_ITEMS:
            lines.append(escape_md(f"    … and {len(values) - ANALYSIS_ITEMS} more"))

    next_action = getattr(analysis, "next_action", None)
    if next_action:
        lines.append("")
        lines.append(escape_md(f"next: {next_action}"))
    return "\n".join(lines)

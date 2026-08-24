"""Deterministic email classification (ADR-010: rules first, AI refines uncertain cases only)."""

from __future__ import annotations

import re

from careeros.modules.inbox.enums import MessageClass, Urgency
from careeros.modules.inbox.schemas import ClassificationOut, EmailIn

PLATFORM_DOMAINS = {
    "linkedin.com": "LinkedIn",
    "wellfound.com": "Wellfound",
    "angel.co": "Wellfound",
    "upwork.com": "Upwork",
    "toptal.com": "Toptal",
    "hh.ru": "hh",
    "indeed.com": "Indeed",
    "getmatch.ru": "getmatch",
    "glassdoor.com": "Glassdoor",
}

_NOREPLY_RE = re.compile(
    r"^(no-?reply|notifications?|jobs-noreply|updates|digest|alerts?)@", re.IGNORECASE
)

# (class, urgency, patterns searched in subject+body, weight)
_RULES: list[tuple[MessageClass, Urgency, tuple[str, ...], float]] = [
    (
        MessageClass.offer,
        Urgency.high,
        ("job offer", "offer letter", "pleased to offer", "offer of employment", "рады предложить"),
        0.95,
    ),
    (
        MessageClass.rejection,
        Urgency.low,
        (
            "unfortunately",
            "not moving forward",
            "decided to proceed with other",
            "will not be moving",
            "position has been filled",
            "не готовы сделать предложение",
            "выбрали другого",
        ),
        0.9,
    ),
    (
        MessageClass.interview,
        Urgency.high,
        (
            "interview",
            "schedule a call",
            "book a time",
            "calendly.com",
            "technical screen",
            "meet the team",
            "собеседование",
            "созвон",
        ),
        0.85,
    ),
    (
        MessageClass.application_update,
        Urgency.normal,
        (
            "application received",
            "application update",
            "your application",
            "thanks for applying",
            "заявка получена",
        ),
        0.8,
    ),
    (
        MessageClass.client_lead,
        Urgency.high,
        (
            "your proposal",
            "project budget",
            "statement of work",
            "scope of work",
            "consulting engagement",
            "discovery call",
        ),
        0.7,
    ),
    (
        MessageClass.recruiter_outreach,
        Urgency.normal,
        (
            "i came across your profile",
            "your background",
            "exciting opportunity",
            "we are hiring",
            "reaching out about",
            "open role",
            "current opportunity",
            "интересная вакансия",
            "рассмотрите вакансию",
        ),
        0.75,
    ),
    (
        MessageClass.new_opportunity,
        Urgency.normal,
        (
            "job description",
            "requirements:",
            "responsibilities:",
            "must have",
            "nice to have",
            "we're looking for a",
            "we are looking for a",
        ),
        0.65,
    ),
    (
        MessageClass.follow_up_required,
        Urgency.normal,
        ("just following up", "any update", "gentle reminder", "circling back"),
        0.7,
    ),
    (
        MessageClass.spam_noise,
        Urgency.low,
        (
            "unsubscribe from marketing",
            "webinar invitation",
            "special discount",
            "limited offer for you",
        ),
        0.6,
    ),
]

_DEADLINE_RE = re.compile(
    r"(?:by|before|until|deadline[:\s]+|no later than)\s+"
    r"((?:mon|tue|wed|thu|fri|sat|sun)[a-z]*|tomorrow|today"
    r"|\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?"
    r"|[a-z]+\s+\d{1,2}(?:st|nd|rd|th)?)",
    re.IGNORECASE,
)


def classify_email(email: EmailIn) -> ClassificationOut:
    text = f"{email.subject or ''}\n{email.body_text}".lower()
    signals: list[str] = []
    sender = (email.from_email or "").lower()
    domain = sender.rsplit("@", 1)[-1] if "@" in sender else ""
    platform = next(
        (label for d, label in PLATFORM_DOMAINS.items() if domain == d or domain.endswith("." + d)),
        None,
    )

    best: tuple[MessageClass, Urgency, float] | None = None
    for cls, urgency, patterns, weight in _RULES:
        hits = [p for p in patterns if p in text]
        if not hits:
            continue
        score = min(1.0, weight + 0.05 * (len(hits) - 1))
        signals.extend(f"{cls}: '{h}'" for h in hits[:3])
        if best is None or score > best[2]:
            best = (cls, urgency, score)

    # платформенные нотификации перекрываются только жёсткими классами
    overridable = best is None or best[0] in (
        MessageClass.new_opportunity,
        MessageClass.recruiter_outreach,
        MessageClass.spam_noise,
    )
    if platform and _NOREPLY_RE.match(sender) and overridable:
        best = (MessageClass.platform_notification, Urgency.low, 0.85)
        signals.append(f"platform noreply sender ({platform})")

    if best is None:
        best = (MessageClass.other, Urgency.normal, 0.2)
        signals.append("no rule matched")

    deadline = _DEADLINE_RE.search(text)
    return ClassificationOut(
        classification=best[0],
        urgency=best[1],
        confidence=round(best[2], 2),
        signals=signals[:8],
        deadline_hint=deadline.group(0)[:200] if deadline else None,
    )


_HEADER_RE = re.compile(r"^(from|to|subject|date)\s*:\s*(.+)$", re.IGNORECASE)
_ADDR_RE = re.compile(r"([\w.+-]+@[\w-]+\.[\w.-]+)")
_NAME_ADDR_RE = re.compile(r'^\s*"?([^"<]+?)"?\s*<[^>]+>\s*$')


def parse_raw_email(raw: str) -> dict[str, object]:
    """Best-effort parse of a pasted email (headers block + body). Never raises."""
    lines = raw.splitlines()
    fields: dict[str, str] = {}
    body_start = 0
    for i, line in enumerate(lines[:30]):
        m = _HEADER_RE.match(line.strip())
        if m:
            fields[m.group(1).lower()] = m.group(2).strip()
            body_start = i + 1
        elif fields and not line.strip():
            body_start = i + 1
            break
    body = "\n".join(lines[body_start:]).strip() or raw
    out: dict[str, object] = {"body_text": body}
    if "subject" in fields:
        out["subject"] = fields["subject"][:500]
    if "from" in fields:
        addr = _ADDR_RE.search(fields["from"])
        if addr:
            out["from_email"] = addr.group(1)
        name = _NAME_ADDR_RE.match(fields["from"])
        if name:
            out["from_name"] = name.group(1).strip()
    if "to" in fields:
        out["to"] = _ADDR_RE.findall(fields["to"])
    return out


def normalize_subject(subject: str | None) -> str:
    if not subject:
        return "(no subject)"
    s = subject.strip()
    while True:
        stripped = re.sub(r"^(re|fwd?|fw)\s*:\s*", "", s, flags=re.IGNORECASE)
        if stripped == s:
            break
        s = stripped
    return s[:500] or "(no subject)"

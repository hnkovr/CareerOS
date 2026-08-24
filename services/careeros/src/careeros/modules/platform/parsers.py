"""Shared heuristics for pasted page text (EN + RU). Pure functions; unknown → ``None``."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from careeros.modules.platform.enums import ApplicationStatus
from careeros.modules.platform.schemas import ApplicationObservationIn, JobPosting, ProfileRead
from careeros.modules.profiles.schemas import SnapshotExperienceItem
from careeros.modules.vault.enums import Platform

_WS = re.compile("[ \\t\u00a0]+")
_URL = re.compile(r"https?://[^\s<>\"')\]]+")
_SPLIT_SKILLS = re.compile(r"\s*[,;·•|/]\s*|\s{2,}")

_MONTHS: dict[str, int] = {}
for _i, _names in enumerate(
    (
        ("jan", "january", "янв", "января", "январь"),
        ("feb", "february", "фев", "февраля", "февраль"),
        ("mar", "march", "мар", "марта", "март"),
        ("apr", "april", "апр", "апреля", "апрель"),
        ("may", "мая", "май"),
        ("jun", "june", "июн", "июня", "июнь"),
        ("jul", "july", "июл", "июля", "июль"),
        ("aug", "august", "авг", "августа", "август"),
        ("sep", "sept", "september", "сен", "сент", "сентября", "сентябрь"),
        ("oct", "october", "окт", "октября", "октябрь"),
        ("nov", "november", "ноя", "нояб", "ноября", "ноябрь"),
        ("dec", "december", "дек", "декабря", "декабрь"),
    ),
    start=1,
):
    for _n in _names:
        _MONTHS[_n] = _i

_REL = re.compile(
    r"(?P<n>\d+)\s*(?P<unit>minute|min|hour|hr|day|week|month|year|минут|час|дн|ден|недел|месяц|год|лет)",
    re.IGNORECASE,
)
_UNIT_DAYS = {
    "minute": 0,
    "min": 0,
    "hour": 0,
    "hr": 0,
    "day": 1,
    "week": 7,
    "month": 30,
    "year": 365,
    "минут": 0,
    "час": 0,
    "дн": 1,
    "ден": 1,
    "недел": 7,
    "месяц": 30,
    "год": 365,
    "лет": 365,
}
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?")
_DMY_DOT = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_MDY_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
_DAY_MONTH_YEAR = re.compile(r"\b(\d{1,2})\s+([A-Za-zА-Яа-яё]+)\.?,?\s+(\d{4})\b")
_MONTH_DAY_YEAR = re.compile(r"\b([A-Za-zА-Яа-яё]+)\.?\s+(\d{1,2}),?\s+(\d{4})\b")
_MONTH_YEAR = re.compile(r"\b([A-Za-zА-Яа-яё]{3,})\.?\s+(\d{4})\b")


def split_lines(text: str) -> list[str]:
    """Strip, collapse inner whitespace, drop blank lines."""
    out: list[str] = []
    for raw in text.splitlines():
        line = _WS.sub(" ", raw).strip()
        if line:
            out.append(line)
    return out


def blocks(text: str) -> list[list[str]]:
    """Paragraph blocks: consecutive non-blank lines."""
    out: list[list[str]] = []
    cur: list[str] = []
    for raw in text.splitlines():
        line = _WS.sub(" ", raw).strip()
        if line:
            cur.append(line)
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def find_urls(text: str) -> list[str]:
    return [u.rstrip(".,;") for u in _URL.findall(text)]


def _month(name: str) -> int | None:
    return _MONTHS.get(name.lower().rstrip("."))


def parse_date(s: str, *, now: datetime | None = None) -> datetime | None:
    """ISO / European / US dates, month names (EN+RU), and relative phrases → aware UTC datetime."""
    if not s:
        return None
    now = now or datetime.now(UTC)
    text = s.strip()
    low = text.lower()

    if m := _ISO.search(text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh = int(m.group(4) or 0)
        mm = int(m.group(5) or 0)
        ss = int(m.group(6) or 0)
        try:
            return datetime(y, mo, d, hh, mm, ss, tzinfo=UTC)
        except ValueError:
            return None
    if m := _DMY_DOT.search(text):
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=UTC)
        except ValueError:
            return None
    if m := _MDY_SLASH.search(text):
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        try:
            return datetime(y, int(m.group(1)), int(m.group(2)), tzinfo=UTC)
        except ValueError:
            return None
    if m := _DAY_MONTH_YEAR.search(text):
        mo = _month(m.group(2))
        if mo:
            try:
                return datetime(int(m.group(3)), mo, int(m.group(1)), tzinfo=UTC)
            except ValueError:
                return None
    if m := _MONTH_DAY_YEAR.search(text):
        mo = _month(m.group(1))
        if mo:
            try:
                return datetime(int(m.group(3)), mo, int(m.group(2)), tzinfo=UTC)
            except ValueError:
                return None
    if m := _MONTH_YEAR.search(text):
        mo = _month(m.group(1))
        if mo:
            return datetime(int(m.group(2)), mo, 1, tzinfo=UTC)
    if any(w in low for w in ("today", "сегодня", "just now", "только что")):
        return now
    if any(w in low for w in ("yesterday", "вчера")):
        return now - timedelta(days=1)
    if m := _REL.search(low):
        n = int(m.group("n"))
        unit = m.group("unit").lower()
        days = next((v for k, v in _UNIT_DAYS.items() if unit.startswith(k)), None)
        if days is None:
            return None
        if days == 0:
            hours = n if unit.startswith(("hour", "hr", "час")) else 0
            return now - timedelta(hours=hours, minutes=n if hours == 0 else 0)
        return now - timedelta(days=n * days)
    return None


_STATUS_RULES: tuple[tuple[ApplicationStatus, tuple[str, ...]], ...] = (
    (ApplicationStatus.withdrawn, ("withdraw", "withdrew", "отозван", "отозвал")),
    (ApplicationStatus.offer, ("offer", "hired", "matched", "оффер", "предложение о работе")),
    (
        ApplicationStatus.interview,
        ("interview", "screen", "shortlist", "собеседован", "интервью", "техническ"),
    ),
    (
        ApplicationStatus.rejected,
        (
            "reject",
            "not selected",
            "declined",
            "not moving forward",
            "unsuccessful",
            "closed",
            "отказ",
            "отклонен",
            "не подошл",
        ),
    ),
    (ApplicationStatus.invited, ("invit", "приглаш")),
    (ApplicationStatus.viewed, ("viewed", "seen", "просмотр")),
    (
        ApplicationStatus.applied,
        ("applied", "submitted", "sent", "active", "pending", "отклик", "отправлен", "response"),
    ),
)


def normalize_status(raw: str) -> ApplicationStatus:
    low = (raw or "").lower()
    for status, needles in _STATUS_RULES:
        if any(n in low for n in needles):
            return status
    return ApplicationStatus.unknown


_TITLE_COMPANY = re.compile(
    r"^(?P<title>.+?)\s+(?:at|@|в компании|—|–|·|•|\||»)\s+(?P<company>.+?)$", re.IGNORECASE
)
_TITLE_COMPANY_DASH = re.compile(r"^(?P<title>[^-]+?)\s+-\s+(?P<company>[^-]+?)$")


def guess_title_company(line: str) -> tuple[str | None, str | None]:
    """'Title at Company' / 'Title — Company' / 'Title · Company' → (title, company)."""
    line = _WS.sub(" ", line).strip()
    if not line:
        return None, None
    for rx in (_TITLE_COMPANY, _TITLE_COMPANY_DASH):
        if m := rx.match(line):
            return m.group("title").strip(" -·|"), m.group("company").strip(" -·|")
    return line, None


_PERIOD = re.compile(r"\b(19|20)\d{2}\b")
_PERIOD_SEP = re.compile(
    r"[–—-]|\bto\b|\bnow\b|present|current|настоящее|по н\.?\s?в\.?|по сей", re.IGNORECASE
)


def looks_like_period(line: str) -> bool:
    """'2023 – now', 'Jan 2020 - Mar 2023', 'Aug 2024 – настоящее время'."""
    return bool(_PERIOD.search(line) and _PERIOD_SEP.search(line)) and len(line) <= 60


def best_status(lines: list[str]) -> tuple[ApplicationStatus, str]:
    """Most specific recognised status across lines (rule order = specificity)."""
    best_idx = len(_STATUS_RULES)
    best = (ApplicationStatus.unknown, "")
    for ln in lines:
        st = normalize_status(ln)
        if st == ApplicationStatus.unknown:
            continue
        idx = next(i for i, (s, _) in enumerate(_STATUS_RULES) if s == st)
        if idx < best_idx:
            best_idx, best = idx, (st, ln)
    return best


def split_skills(line: str) -> list[str]:
    parts = [p.strip(" .") for p in _SPLIT_SKILLS.split(line)]
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if p and p.lower() not in seen and len(p) <= 60:
            seen.add(p.lower())
            out.append(p)
    return out


_H_ABOUT = re.compile(r"^(about|summary|overview|bio|о себе|обо мне|описание)\s*:?$", re.IGNORECASE)
_H_SKILLS = re.compile(
    r"^(skills|top skills|key skills|expertise|навыки|ключевые навыки|стек)\s*:?\s*(?P<inline>.*)$",
    re.IGNORECASE,
)
_H_EXPERIENCE = re.compile(
    r"^(experience|work experience|employment|опыт работы|опыт)\s*:?$", re.IGNORECASE
)
_H_OTHER = re.compile(
    r"^(education|projects|portfolio|certifications|languages|licenses|образование|проекты|"
    r"портфолио|сертификаты|языки|contact|контакты|recommendations|рекомендации)\s*:?$",
    re.IGNORECASE,
)


def _is_header(line: str) -> bool:
    return bool(
        _H_ABOUT.match(line)
        or _H_SKILLS.match(line)
        or _H_EXPERIENCE.match(line)
        or _H_OTHER.match(line)
    )


def generic_profile(text: str, platform: Platform) -> ProfileRead:
    """Headline = first line; About/Skills/Experience sections by their headers; raw kept."""
    lines = split_lines(text)
    headline = lines[0][:300] if lines else None
    about: list[str] = []
    skills: list[str] = []
    experience: list[SnapshotExperienceItem] = []
    section: str | None = None
    for line in lines[1:]:
        if _H_ABOUT.match(line):
            section = "about"
            continue
        if m := _H_SKILLS.match(line):
            section = "skills"
            if m.group("inline"):
                skills.extend(split_skills(m.group("inline")))
            continue
        if _H_EXPERIENCE.match(line):
            section = "experience"
            continue
        if _H_OTHER.match(line):
            section = None
            continue
        if section == "about":
            about.append(line)
        elif section == "skills":
            skills.extend(split_skills(line))
        elif section == "experience":
            title, company = guess_title_company(line)
            if company and not looks_like_period(line):
                experience.append(SnapshotExperienceItem(company=company, title=title))
            elif experience and experience[-1].period is None and looks_like_period(line):
                experience[-1].period = line
            elif experience and experience[-1].description is None:
                experience[-1].description = line
    return ProfileRead(
        platform=platform,
        headline=headline,
        about=" ".join(about) or None,
        skills=skills,
        experience=experience,
        raw_text=text,
    )


_NOISE = re.compile(
    r"^(apply|apply now|easy apply|save|saved|promoted|откликнуться|показать контакты|share|"
    r"report|\d+ applicants?)$",
    re.IGNORECASE,
)


def generic_jobs(text: str, platform: Platform, *, limit: int = 100) -> list[JobPosting]:
    """One posting per paragraph block: first line → title/company, URLs captured, raw kept."""
    out: list[JobPosting] = []
    for block in blocks(text):
        lines = [ln for ln in block if not _NOISE.match(ln)]
        if not lines:
            continue
        title, company = guess_title_company(lines[0])
        if not title or title.startswith("http"):
            continue
        urls = find_urls("\n".join(block))
        location = next(
            (
                ln
                for ln in lines[1:4]
                if re.search(r"remote|hybrid|on-?site|удал[её]нн|гибрид|офис", ln, re.IGNORECASE)
            ),
            None,
        )
        out.append(
            JobPosting(
                platform=platform,
                title=title[:300],
                company=company,
                location=location,
                url=urls[0] if urls else None,
                raw_text="\n".join(block),
            )
        )
        if len(out) >= limit:
            break
    return out


def generic_applications(
    text: str, platform: Platform, *, now: datetime | None = None
) -> list[ApplicationObservationIn]:
    """One observation per block: title/company, first recognisable status and date."""
    out: list[ApplicationObservationIn] = []
    for block in blocks(text):
        lines = [ln for ln in block if not _NOISE.match(ln)]
        if not lines:
            continue
        title, company = guess_title_company(lines[0])
        if not title or title.startswith("http"):
            continue
        status, status_raw = best_status(lines[1:])
        applied_at: datetime | None = None
        for ln in lines[1:]:
            if applied_at is None:
                applied_at = parse_date(ln, now=now)
        urls = find_urls("\n".join(block))
        out.append(
            ApplicationObservationIn(
                platform=platform,
                job_title=title[:300],
                company=company,
                job_url=urls[0] if urls else None,
                status_raw=status_raw,
                status=status,
                applied_at=applied_at,
                raw_payload={"lines": block},
            )
        )
    return out

"""LinkedIn page-paste heuristics: profile page, job search list, "My jobs → Applied" list.

Pure functions; nothing is invented — unrecognised lines stay in ``raw_text`` / ``raw_payload``.
LinkedIn copies carry each visible string twice (an aria-hidden twin), so consecutive duplicate
lines are collapsed first. When the LinkedIn shape is not recognised the shared generic parsers
take over, so any paste still yields something.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from careeros.modules.platform import parsers as generic
from careeros.modules.platform.enums import ApplicationStatus
from careeros.modules.platform.schemas import ApplicationObservationIn, JobPosting, ProfileRead
from careeros.modules.profiles.schemas import SnapshotExperienceItem
from careeros.modules.vault.enums import Platform

PLATFORM = Platform.linkedin

# ------------------------------------------------------------------------------ line hygiene

_VERIFIED = re.compile(r"\s+with verification$", re.IGNORECASE)
_ABBREV = re.compile(r"\b(?P<n>\d+)\s*(?P<u>mo|yr|y|w|d|h|m)\b", re.IGNORECASE)
_ABBREV_UNITS = {
    "mo": "month",
    "yr": "year",
    "y": "year",
    "w": "week",
    "d": "day",
    "h": "hour",
    "m": "minute",
}


def expand_relative(text: str) -> str:
    """LinkedIn's compact relative times ('3d ago', '2w', '1mo', '5h') → words parse_date knows."""

    def repl(m: re.Match[str]) -> str:
        n = int(m.group("n"))
        unit = _ABBREV_UNITS[m.group("u").lower()]
        return f"{n} {unit}{'' if n == 1 else 's'}"

    return _ABBREV.sub(repl, text)


def clean_lines(text: str) -> list[str]:
    """split_lines + drop 'with verification' badges + collapse consecutive duplicate lines."""
    out: list[str] = []
    for raw in generic.split_lines(text):
        line = _VERIFIED.sub("", raw)
        if out and out[-1] == line:
            continue
        out.append(line)
    return out


def _when(line: str, *, now: datetime | None) -> datetime | None:
    return generic.parse_date(expand_relative(line), now=now)


# ------------------------------------------------------------------------------ shared shapes

_EMPLOYMENT = (
    r"full-time|part-time|self-employed|freelance|contract|internship|apprenticeship|seasonal|"
    r"temporary|полная занятость|частичная занятость|фриланс|контракт|стажировка|самозанятость"
)
_COMPANY_TYPE = re.compile(rf"^(?P<company>.+?)\s*·\s*(?:{_EMPLOYMENT})\b.*$", re.IGNORECASE)
_GROUP_HEADER = re.compile(
    rf"^(?:{_EMPLOYMENT})(?:\s*·\s*.+)?$|^\d+\s*(?:yrs?|mos?|г\.|лет|мес\.?)(?:\s+\d+\s*(?:mos?|мес\.?))?$",
    re.IGNORECASE,
)
_MON = r"[A-Za-zА-Яа-яё]{3,9}\.?"
_YEAR = r"(?:19|20)\d{2}(?:\s*г\.)?"
_PERIOD = re.compile(
    rf"^(?:{_MON}\s+)?{_YEAR}\s*[-–—]\s*(?:(?:{_MON}\s+)?{_YEAR}|present|now|current|"
    r"настоящее время|н\.\s?в\.)$",
    re.IGNORECASE,
)
_DURATION_SUFFIX = re.compile(r"\s*·\s*.*$")
_WORKPLACE = re.compile(r"\b(remote|hybrid|on-?site|удал[её]нн?о|гибрид|в офисе)\b", re.IGNORECASE)
_CITY_COUNTRY = re.compile(r"^[^.!?,]{2,40},\s[^.!?]{2,60}$")


def _period_of(line: str) -> str | None:
    """'Jan 2023 - Present · 3 yrs 8 mos' → 'Jan 2023 - Present'; None when not a date range."""
    head = _DURATION_SUFFIX.sub("", line).strip()
    return head if _PERIOD.match(head) else None


def _is_location(line: str) -> bool:
    if len(line) > 60 or line.endswith((".", "!", "?")):
        return False
    return bool(_WORKPLACE.search(line) or _CITY_COUNTRY.match(line))


def _looks_like_title(line: str) -> bool:
    return (
        0 < len(line) <= 80
        and len(line.split()) <= 8
        and not line.endswith((".", "!", "?"))
        and not _is_location(line)
    )


# ------------------------------------------------------------------------------ profile page

_SECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("about", re.compile(r"^(about|общие сведения|о себе)$", re.IGNORECASE)),
    ("skills", re.compile(r"^(top skills|skills|навыки|основные навыки)$", re.IGNORECASE)),
    ("experience", re.compile(r"^(experience|опыт работы)$", re.IGNORECASE)),
    ("education", re.compile(r"^(education|образование)$", re.IGNORECASE)),
    ("languages", re.compile(r"^(languages|языки)$", re.IGNORECASE)),
    (
        "certifications",
        re.compile(
            r"^(licenses & certifications|licenses and certifications|certifications|"
            r"лицензии и сертификаты|сертификаты)$",
            re.IGNORECASE,
        ),
    ),
    ("projects", re.compile(r"^(projects|проекты)$", re.IGNORECASE)),
)
_OTHER_SECTIONS = re.compile(
    r"^(activity|featured|interests|recommendations|honors & awards|volunteering|courses|"
    r"publications|organizations|causes|test scores|patents|people also viewed|"
    r"people you may know|resources|analytics|highlights|services|contact info|"
    r"активность|интересы|рекомендации|курсы|публикации|организации|волонт[её]рство)$",
    re.IGNORECASE,
)
_PREAMBLE_NOISE = re.compile(
    r"^(·\s*)?(1st|2nd|3rd)\+?$|^(she|he|they)/(her|him|them)(/\w+)?$|"
    r"^\d+\+?\s+(connections|followers|подписчик\w*|контакт\w*)$|"
    r"^(contact info|open to work|message|more|connect|follow|pending|add profile section|"
    r"enhance profile|resources|open to|available|написать сообщение|установить контакт)$|"
    r"mutual connections?$",
    re.IGNORECASE,
)
_SEE_MORE = re.compile(
    r"^(…\s*)?(see more|show more|see less|показать (ещё|еще|больше))$", re.IGNORECASE
)
_SHOW_ALL = re.compile(r"^show all\b|^показать все\b", re.IGNORECASE)
_SKILL_NOISE = re.compile(
    r"endorse|show all|experiences? across|skill assessment|^passed\b|\bat\b|\bв компании\b|"
    r"^\d+\s+(endorsements?|experiences?)|logo$",
    re.IGNORECASE,
)
_SKILLS_INLINE = re.compile(r"^(skills|навыки)\s*:\s*(?P<rest>.+)$", re.IGNORECASE)
_PLUS_SKILLS = re.compile(r"^\+\d+\s+(skills?|навык\w*)$", re.IGNORECASE)
_PROFICIENCY = re.compile(
    r"proficiency|native|bilingual|elementary|limited working|владение|родной|уровень",
    re.IGNORECASE,
)
_ISSUED = re.compile(r"^(issued|выдан[оа]?)\s+(?P<when>.+)$", re.IGNORECASE)


def _section_of(line: str) -> str | None:
    for key, rx in _SECTION_PATTERNS:
        if rx.match(line):
            return key
    if _OTHER_SECTIONS.match(line):
        return line.lower()
    return None


def _split_sections(lines: list[str]) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """Preamble lines + ordered (section, lines) pairs; a section may repeat (Top skills/Skills)."""
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for line in lines:
        key = _section_of(line)
        if key is not None:
            current = []
            sections.append((key, current))
            continue
        if current is None:
            preamble.append(line)
        else:
            current.append(line)
    return preamble, sections


def _parse_experience(lines: list[str], skills_out: list[str]) -> list[SnapshotExperienceItem]:
    """Anchor on date-range lines; the lines before form the header, the lines after the trailer.

    Handles the per-position shape (Title / Company · Full-time / Dates / Location / Description)
    and the grouped shape (Company / Full-time · total / Title / Dates / … / Title / Dates / …).
    Location lines are not stored on the item (no field); they stay in ``raw_text``.
    """
    periods = [i for i, ln in enumerate(lines) if _period_of(ln)]
    parsed: list[tuple[int, int, str | None, str | None]] = []  # period idx, header lines, …
    group_company: str | None = None
    prev_end = 0
    for p in periods:
        head = lines[prev_end:p]
        prev_end = p + 1
        title: str | None = None
        company: str | None = None
        used = 0
        if len(head) >= 3 and _GROUP_HEADER.match(head[-2]):
            group_company = head[-3]
            title, company, used = head[-1], head[-3], 3
        elif head and (m := _COMPANY_TYPE.match(head[-1])):
            company = m.group("company").strip()
            group_company = None
            if len(head) >= 2 and _looks_like_title(head[-2]):
                title, used = head[-2], 2
            else:
                used = 1
        elif group_company and head and (len(head) == 1 or not _looks_like_title(head[-2])):
            title, company, used = head[-1], group_company, 1
        elif len(head) >= 2 and _looks_like_title(head[-2]):
            title, company, used = head[-2], head[-1], 2
            group_company = None
        elif head:
            title, company = generic.guess_title_company(head[-1])
            used = 1
        parsed.append((p, used, title, company))

    items: list[SnapshotExperienceItem] = []
    for j, (p, _used, title, company) in enumerate(parsed):
        if not company:
            continue
        if j + 1 < len(parsed):
            next_p, next_used, _, _ = parsed[j + 1]
            trailer = lines[p + 1 : next_p - next_used]
        else:
            trailer = lines[p + 1 :]
        desc: list[str] = []
        for ln in trailer:
            if _SHOW_ALL.match(ln) or _SEE_MORE.match(ln) or _PLUS_SKILLS.match(ln):
                continue
            if m := _SKILLS_INLINE.match(ln):
                _add_skills(skills_out, m.group("rest"))
                continue
            if _is_location(ln) or _GROUP_HEADER.match(ln):
                continue
            desc.append(ln)
        items.append(
            SnapshotExperienceItem(
                company=company,
                title=title,
                period=_period_of(lines[p]),
                description="\n".join(desc) or None,
            )
        )
    return items


def _add_skills(skills: list[str], line: str) -> None:
    seen = {s.lower() for s in skills}
    for s in generic.split_skills(line):
        if _PLUS_SKILLS.match(s):
            continue
        if s.lower() not in seen:
            seen.add(s.lower())
            skills.append(s)


def _parse_education(lines: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    prev_end = 0
    for p, ln in enumerate(lines):
        period = _period_of(ln)
        if period is None:
            continue
        head = [h for h in lines[prev_end:p] if not _SHOW_ALL.match(h)]
        prev_end = p + 1
        if not head:
            continue
        school = head[-2] if len(head) >= 2 else head[-1]
        degree = head[-1] if len(head) >= 2 else None
        out.append({"school": school, "degree": degree, "period": period})
    return out


def _parse_languages(lines: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, ln in enumerate(lines):
        if i and _PROFICIENCY.search(ln) and not _PROFICIENCY.search(lines[i - 1]):
            out.append({"name": lines[i - 1], "proficiency": ln})
    return out


def _parse_certifications(lines: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    prev_end = 0
    for i, ln in enumerate(lines):
        m = _ISSUED.match(ln)
        if not m:
            continue
        head = [h for h in lines[prev_end:i] if not _SHOW_ALL.match(h)]
        prev_end = i + 1
        if not head:
            continue
        out.append(
            {
                "name": head[-2] if len(head) >= 2 else head[-1],
                "authority": head[-1] if len(head) >= 2 else None,
                "issued": m.group("when").strip(),
            }
        )
    return out


def parse_profile(text: str) -> ProfileRead:
    """LinkedIn profile page copy → ProfileRead; generic layout parser when not LinkedIn-shaped."""
    lines = clean_lines(text)
    preamble, sections = _split_sections(lines)
    preamble = [ln for ln in preamble if not _PREAMBLE_NOISE.match(ln)]
    if len(preamble) < 2 or not sections:
        pr = generic.generic_profile(text, PLATFORM)
        pr.raw_payload = {"layout": "generic"}
        return pr
    name, headline = preamble[0], preamble[1]
    location = preamble[2] if len(preamble) > 2 else None
    if location:
        location = re.split(r"\s*·\s*", location)[0] or None

    about: list[str] = []
    skills: list[str] = []
    experience: list[SnapshotExperienceItem] = []
    education: list[dict[str, Any]] = []
    languages: list[dict[str, Any]] = []
    certifications: list[dict[str, Any]] = []
    for key, body in sections:
        if key == "about":
            about.extend(ln for ln in body if not _SEE_MORE.match(ln))
        elif key == "skills":
            for ln in body:
                if _SKILL_NOISE.search(ln) or _SEE_MORE.match(ln):
                    continue
                _add_skills(skills, ln)
        elif key == "experience":
            experience.extend(_parse_experience(body, skills))
        elif key == "education":
            education.extend(_parse_education(body))
        elif key == "languages":
            languages.extend(_parse_languages(body))
        elif key == "certifications":
            certifications.extend(_parse_certifications(body))
    return ProfileRead(
        platform=PLATFORM,
        headline=headline[:300],
        about="\n".join(about) or None,
        experience=experience,
        skills=skills,
        preferences={
            "name": name,
            "location": location,
            "education": education,
            "languages": languages,
            "certifications": certifications,
        },
        raw_text=text,
        raw_payload={
            "layout": "linkedin",
            "name": name,
            "location": location,
            "sections": [key for key, _ in sections],
        },
    )


# ------------------------------------------------------------------------------ job search list

_EASY_APPLY = re.compile(r"^easy apply$|^быстрый отклик$", re.IGNORECASE)
_PROMOTED = re.compile(r"\bpromoted\b|\bреклама\b|\bпродвигается\b", re.IGNORECASE)
_APPLICANTS = re.compile(r"(?P<n>\d+)\s+(applicants?|откликов|отклика|соискател\w*)", re.IGNORECASE)
_POSTED = re.compile(
    r"\b(ago|назад)\b|^(today|yesterday|сегодня|вчера)\b|^\d+\s*(h|d|w|mo|m|yr)\b|^reposted\b",
    re.IGNORECASE,
)
_META = re.compile(
    r"^(viewed|saved|apply|save|be an early applicant|actively hiring|"
    r"actively reviewing applicants|your profile matches this job|hide|dismiss|show more|"
    r"company logo|\d+ school alum\w* works? here|"
    r"\d+ connections? works? here|просмотрено|сохранено)$|[$€£₽]|\b\d+\s*k\b",
    re.IGNORECASE,
)


def _header_of(head: list[str]) -> tuple[str | None, str | None, int]:
    """Last 1–2 lines of ``head`` as (title, company, lines used)."""
    if len(head) >= 2 and _looks_like_title(head[-2]) and not _is_meta(head[-2]):
        return head[-2], head[-1], 2
    if head:
        title, company = generic.guess_title_company(head[-1])
        return title, company, 1
    return None, None, 0


def _is_meta(line: str) -> bool:
    return bool(
        _META.search(line)
        or _EASY_APPLY.match(line)
        or _PROMOTED.search(line)
        or _APPLICANTS.search(line)
        or _POSTED.search(line)
    )


def _jobs_from_lines(lines: list[str], *, now: datetime | None) -> list[JobPosting]:
    anchors = [i for i, ln in enumerate(lines) if _is_location(ln)]
    # a title such as "Data Engineer (Remote)" followed by company + real location: keep the latter
    anchors = [a for a in anchors if a + 2 not in anchors]
    entries: list[tuple[int, int, str, str | None, str]] = []  # start, loc index, title, company
    prev_end = 0
    for loc_i in anchors:
        title, company, used = _header_of(lines[prev_end:loc_i])
        prev_end = loc_i + 1
        if not title or not used:
            continue
        entries.append((loc_i - used, loc_i, title, company, lines[loc_i]))
    out: list[JobPosting] = []
    for k, (start, loc_i, title, company, location) in enumerate(entries):
        end = entries[k + 1][0] if k + 1 < len(entries) else len(lines)
        entry = lines[start:end]
        meta = lines[loc_i + 1 : end]
        posted = next((ln for ln in meta if _POSTED.search(ln)), None)
        applicants = next((int(m.group("n")) for ln in meta if (m := _APPLICANTS.search(ln))), None)
        raw_text = "\n".join(entry)
        urls = generic.find_urls(raw_text)
        out.append(
            JobPosting(
                platform=PLATFORM,
                title=title[:300],
                company=company,
                location=location,
                url=urls[0] if urls else None,
                posted_at=_when(posted, now=now) if posted else None,
                raw_text=raw_text,
                raw_payload={
                    "lines": entry,
                    "easy_apply": any(_EASY_APPLY.match(ln) for ln in meta),
                    "promoted": any(_PROMOTED.search(ln) for ln in meta),
                    "applicants": applicants,
                    "posted": posted,
                },
            )
        )
    return out


def parse_jobs(text: str, *, now: datetime | None = None, limit: int = 100) -> list[JobPosting]:
    """Job search / recommendations list copy → postings (blank-line blocks or one run-on list)."""
    out: list[JobPosting] = []
    for block in generic.blocks(text):
        lines = clean_lines("\n".join(block))
        found = _jobs_from_lines(lines, now=now)
        if not found:
            found = generic.generic_jobs("\n".join(lines), PLATFORM, limit=limit)
        out.extend(found)
        if len(out) >= limit:
            break
    return out[:limit]


# ------------------------------------------------------------------------------ applied list

_APPLIED = re.compile(
    r"^(applied|application (sent|submitted)|you applied|отклик отправлен|вы откликнулись)\b",
    re.IGNORECASE,
)
_LI_STATUS: tuple[tuple[re.Pattern[str], ApplicationStatus | None], ...] = (
    (re.compile(r"^application viewed", re.IGNORECASE), ApplicationStatus.viewed),
    (re.compile(r"^resume downloaded", re.IGNORECASE), ApplicationStatus.viewed),
    (re.compile(r"^no longer accepting applications", re.IGNORECASE), None),
    (
        re.compile(r"^(applied|application (sent|submitted))", re.IGNORECASE),
        ApplicationStatus.applied,
    ),
)
_SPECIFICITY: tuple[ApplicationStatus, ...] = (
    ApplicationStatus.withdrawn,
    ApplicationStatus.offer,
    ApplicationStatus.interview,
    ApplicationStatus.rejected,
    ApplicationStatus.invited,
    ApplicationStatus.viewed,
    ApplicationStatus.applied,
)


def _li_status(line: str) -> tuple[ApplicationStatus | None, bool]:
    """(status, matched): LinkedIn chips first; ``None`` status = posting closed (no verdict)."""
    for rx, status in _LI_STATUS:
        if rx.match(line):
            return status, True
    st = generic.normalize_status(line)
    return (st, True) if st != ApplicationStatus.unknown else (None, False)


def _applications_from_lines(
    lines: list[str], *, now: datetime | None
) -> list[ApplicationObservationIn]:
    anchors = [i for i, ln in enumerate(lines) if _APPLIED.match(ln)]
    if not anchors:
        return []
    headers: list[tuple[int, int, str, str | None, str | None]] = []  # start, anchor, title …
    prev_end = 0
    for a in anchors:
        head = lines[prev_end:a]
        prev_end = a + 1
        location = head[-1] if len(head) >= 2 and _is_location(head[-1]) else None
        body = head[:-1] if location else head
        if not body:
            continue
        closed_chip = any(rx.match(body[-1]) for rx, _ in _LI_STATUS)
        if closed_chip:
            continue
        if len(body) >= 2 and not any(rx.match(body[-2]) for rx, _ in _LI_STATUS):
            title, company, used = body[-2], body[-1], 2
        else:
            title, company = generic.guess_title_company(body[-1])
            used = 1
        if not title:
            continue
        headers.append((a - used - (1 if location else 0), a, title, company, location))
    out: list[ApplicationObservationIn] = []
    for k, (start, a, title, company, location) in enumerate(headers):
        end = headers[k + 1][0] if k + 1 < len(headers) else len(lines)
        entry = lines[start:end]
        status_lines = lines[a + 1 : end]
        candidates: list[tuple[ApplicationStatus, str]] = [(ApplicationStatus.applied, lines[a])]
        notes: list[str] = []
        closed = False
        for ln in status_lines:
            st, matched = _li_status(ln)
            if matched and st is None:
                closed = True
                notes.append(ln)
            elif st is not None:
                candidates.append((st, ln))
        status, status_raw = min(candidates, key=lambda c: _SPECIFICITY.index(c[0]))
        urls = generic.find_urls("\n".join(entry))
        out.append(
            ApplicationObservationIn(
                platform=PLATFORM,
                job_title=title[:300],
                company=company,
                job_url=urls[0] if urls else None,
                status_raw=status_raw,
                status=status,
                applied_at=_when(lines[a], now=now),
                raw_payload={
                    "lines": entry,
                    "location": location,
                    "status_lines": status_lines,
                    "posting_closed": closed,
                    "notes": notes,
                },
            )
        )
    return out


def parse_applications(text: str, *, now: datetime | None = None) -> list[ApplicationObservationIn]:
    """'My jobs → Applied' copy → observations (generic parser for blocks without 'Applied')."""
    out: list[ApplicationObservationIn] = []
    for block in generic.blocks(text):
        lines = clean_lines("\n".join(block))
        found = _applications_from_lines(lines, now=now)
        if not found:
            found = generic.generic_applications("\n".join(lines), PLATFORM, now=now)
        out.extend(found)
    return out

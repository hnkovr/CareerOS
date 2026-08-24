"""Heuristics for text pasted from hh.ru pages (Russian UI). Pure; unknown → ``None``; raw kept.

Three page shapes are recognised — the resume page / «Мои резюме» list, the vacancy search list
(cards ending with «Откликнуться» and a date) and «Отклики и приглашения». Text that carries none
of the hh markers falls back to the shared generic parsers.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from careeros.modules.opportunities.enums import CompensationPeriod, EmploymentType, RemotePolicy
from careeros.modules.opportunities.schemas import Compensation, OpportunityExtraction
from careeros.modules.platform import parsers
from careeros.modules.platform.enums import ApplicationStatus
from careeros.modules.platform.schemas import ApplicationObservationIn, JobPosting, ProfileRead
from careeros.modules.profiles.schemas import SnapshotExperienceItem
from careeros.modules.vault.enums import Platform

PLATFORM = Platform.hh
_WS = re.compile(r"[ \t  ]+")

# Markers deciding whether a paste is an hh page at all; otherwise the generic parsers apply.
PROFILE_MARKERS = (
    "ключевые навыки",
    "опыт работы",
    "специализации",
    "мои резюме",
    "обо мне",
    "желаемая должность",
    "hh.ru",
)
JOBS_MARKERS = (
    "откликнуться",
    "показать контакты",
    "быстрый отклик",
    "можно удалённо",
    "можно удаленно",
    "hh.ru",
    "₽",
)
APPLICATIONS_MARKERS = ("отклик", "приглашен", "отказ", "просмотрен", "hh.ru")


def looks_like_hh(text: str, markers: tuple[str, ...]) -> bool:
    low = text.lower()
    return any(m in low for m in markers)


# ------------------------------------------------------------------------------ salary lines

_NUM = re.compile(r"\d{1,3}(?: \d{3})+|\d+")
_CURRENCY = re.compile(
    r"(?P<sym>[₽$€₸₴₾])"
    r"|(?<![A-Za-zА-Яа-яё])(?P<code>руб\.?|р\.|RUB|RUR|USD|EUR|KZT|BYN|UAH|GEL|UZS|KGS|AZN)"
    r"(?![A-Za-zА-Яа-яё])",
    re.IGNORECASE,
)
_SYMBOLS = {"₽": "RUB", "$": "USD", "€": "EUR", "₸": "KZT", "₴": "UAH", "₾": "GEL"}
_CODES = {"руб": "RUB", "руб.": "RUB", "р.": "RUB", "rur": "RUB"}
_FROM = re.compile(r"(?<![а-яё])от\s+\d", re.IGNORECASE)
_TO = re.compile(r"(?<![а-яё])до\s+\d", re.IGNORECASE)
_PERIOD_HINTS: tuple[tuple[CompensationPeriod, tuple[str, ...]], ...] = (
    (CompensationPeriod.hour, ("в час", "за час", "/час", "почасов")),
    (CompensationPeriod.day, ("в день", "за день", "за смену", "в смену")),
    (CompensationPeriod.year, ("в год", "за год")),
)


def parse_salary_line(line: str) -> Compensation | None:
    """«от 300 000 ₽ за месяц, на руки» / «250 000 – 350 000 ₽» / «до 4 000 $» → Compensation."""
    text = _WS.sub(" ", line).strip()
    cur = _CURRENCY.search(text)
    if not cur:
        return None
    nums = [int(n.replace(" ", "")) for n in _NUM.findall(text)]
    if not nums:
        return None
    low = text.lower()
    mn: float | None
    mx: float | None
    if len(nums) >= 2:
        mn, mx = float(nums[0]), float(nums[1])
    elif _FROM.search(low):
        mn, mx = float(nums[0]), None
    elif _TO.search(low):
        mn, mx = None, float(nums[0])
    else:
        mn = mx = float(nums[0])
    symbol, code = cur.group("sym"), cur.group("code")
    currency = _SYMBOLS.get(symbol or "") if symbol else _CODES.get((code or "").lower())
    if currency is None and code:
        currency = code.upper()
    period = next(
        (p for p, hints in _PERIOD_HINTS if any(h in low for h in hints)),
        CompensationPeriod.month,
    )
    return Compensation(
        min=mn, max=mx, currency=currency, period=period, type="salary", raw=line.strip()
    )


# ------------------------------------------------------------------------------ list dates

_RU_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
    "янв": 1,
    "фев": 2,
    "мар": 3,
    "апр": 4,
    "июн": 6,
    "июл": 7,
    "авг": 8,
    "сен": 9,
    "сент": 9,
    "окт": 10,
    "ноя": 11,
    "дек": 12,
}
_DAY_MONTH = re.compile(
    r"^(?P<d>\d{1,2})\s+(?P<m>[а-яё]+)\.?(?:\s+(?P<y>\d{4}))?(?:,?\s*\d{1,2}:\d{2})?$",
    re.IGNORECASE,
)
_DMY = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$")
_RELATIVE = re.compile(
    r"^(сегодня|вчера|только что|\d+\s+(?:минут[уы]?|час(?:а|ов)?|дн(?:я|ей)|день)\s+назад)$",
    re.IGNORECASE,
)


def parse_list_date(line: str, *, now: datetime | None = None) -> datetime | None:
    """Date-only lines of hh lists: «20 августа», «20 августа 2026», «Вчера», «12.08.2026»."""
    now = now or datetime.now(UTC)
    text = _WS.sub(" ", line).strip()
    if m := _DAY_MONTH.match(text):
        month = _RU_MONTHS.get(m.group("m").lower())
        if month is None:
            return None
        year = int(m.group("y")) if m.group("y") else now.year
        try:
            dt = datetime(year, month, int(m.group("d")), tzinfo=UTC)
        except ValueError:
            return None
        if not m.group("y") and dt > now + timedelta(days=1):  # «3 сентября» seen in August
            try:
                dt = dt.replace(year=year - 1)
            except ValueError:
                return None
        return dt
    if _DMY.match(text) or _RELATIVE.match(text):
        return parsers.parse_date(text, now=now)
    return None


def is_date_line(line: str) -> bool:
    text = _WS.sub(" ", line).strip()
    if m := _DAY_MONTH.match(text):
        return m.group("m").lower() in _RU_MONTHS
    return bool(_DMY.match(text) or _RELATIVE.match(text))


# ------------------------------------------------------------------------------ list cards

_ACTION = re.compile(
    r"^(откликнуться|быстрый отклик|показать контакты|показать телефон|написать|в избранное|"
    r"скрыть|пожаловаться|подробнее|хочу тут работать|apply|easy apply)$",
    re.IGNORECASE,
)
_NOISE = re.compile(
    r"^(реклама|вакансия дня|премиум|premium|ещё \d+ вакансий|еще \d+ вакансий|"
    r"похожие вакансии|\d[.,]\d|нет отзывов|отзывы|\d+ отзыв(?:а|ов)?|новая|new|hot|"
    r"вакансия в архиве|есть новые сообщения|чат с работодателем)$",
    re.IGNORECASE,
)
_NAV = re.compile(
    r"^(отклики и приглашения|мои отклики|мои резюме|все|отклики|приглашения|отказы|активные|"
    r"архив|в архиве|скрытые|показать ещё|показать еще|найдено \d+.*|вакансии|поиск)$",
    re.IGNORECASE,
)


def split_cards(text: str) -> list[list[str]]:
    """Cards of an hh list: blank lines, a date footer or content after an action button end one.

    Browser copies often lose blank lines, so «Откликнуться» / «Показать контакты» followed by more
    text, and date-only lines («20 августа», «Вчера»), also close the current card.
    """
    cards: list[list[str]] = []
    cur: list[str] = []
    after_action = False
    for raw in text.splitlines():
        line = _WS.sub(" ", raw).strip()
        if not line or _NAV.match(line):
            if cur:
                cards.append(cur)
            cur, after_action = [], False
            continue
        if is_date_line(line):
            cur.append(line)
            cards.append(cur)
            cur, after_action = [], False
            continue
        if _ACTION.match(line):
            cur.append(line)
            after_action = True
            continue
        if after_action:
            cards.append(cur)
            cur, after_action = [line], False
            continue
        cur.append(line)
    if cur:
        cards.append(cur)
    return cards


# ------------------------------------------------------------------------------ jobs

_REMOTE: tuple[tuple[RemotePolicy, tuple[str, ...]], ...] = (
    (
        RemotePolicy.remote_global,
        ("удалённо", "удаленно", "удалённая работа", "удаленная работа", "из дома", "remote"),
    ),
    (RemotePolicy.hybrid, ("гибрид",)),
    (RemotePolicy.onsite, ("в офисе", "на месте работодателя")),
)
_EMPLOYMENT: tuple[tuple[EmploymentType, tuple[str, ...]], ...] = (
    (EmploymentType.full_time, ("полная занятость",)),
    (EmploymentType.part_time, ("частичная занятость",)),
    (EmploymentType.project, ("проектная работа", "проектная занятость")),
)
_EXPERIENCE = re.compile(r"^(опыт\b.*|без опыта.*)$", re.IGNORECASE)
_TAGS = re.compile(
    r"^(гибкий график|сменный график|вахта|вахтовый метод|стажировка|временная работа|"
    r"волонт[её]рство|\d+/\d+|полный день|неполный день|подработка|для студентов|"
    r"it-аккредитация|аккредитованная it-компания|нужно быть в офисе)$",
    re.IGNORECASE,
)
_VACANCY_URL = re.compile(r"hh\.ru/vacancy/(\d+)")


def _remote_from(line: str) -> RemotePolicy | None:
    low = line.lower()
    for policy, needles in _REMOTE:
        if any(n in low for n in needles):
            return policy
    return None


def _employment_from(line: str) -> EmploymentType | None:
    low = line.lower()
    for kind, needles in _EMPLOYMENT:
        if any(n in low for n in needles):
            return kind
    return None


def _skip(line: str) -> bool:
    return bool(_ACTION.match(line) or _NOISE.match(line) or _TAGS.match(line)) or line.startswith(
        "http"
    )


def parse_jobs(text: str, *, now: datetime | None = None, limit: int = 100) -> list[JobPosting]:
    """Vacancy search list: title, salary, company, city, tags (experience/remote/employment)."""
    if not looks_like_hh(text, JOBS_MARKERS):
        return parsers.generic_jobs(text, PLATFORM, limit=limit)
    out: list[JobPosting] = []
    for card in split_cards(text):
        title = card[0]
        if _skip(title) or is_date_line(title) or parse_salary_line(title) is not None:
            continue
        comp: Compensation | None = None
        company: str | None = None
        location: str | None = None
        remote = RemotePolicy.unknown
        employment: EmploymentType | None = None
        requirements: list[str] = []
        posted: datetime | None = None
        tail: list[str] = []
        for line in card[1:]:
            if comp is None and (found := parse_salary_line(line)) is not None:
                comp = found
                continue
            if is_date_line(line):
                posted = posted or parse_list_date(line, now=now)
                continue
            if _skip(line):
                continue
            if _EXPERIENCE.match(line):
                requirements.append(line)
                continue
            if len(line) <= 40 and (policy := _remote_from(line)) is not None:
                remote = policy
                continue
            if (kind := _employment_from(line)) is not None:
                employment = kind
                continue
            if company is None:
                company = line
            elif location is None:
                location = line
            else:
                tail.append(line)
        urls = parsers.find_urls("\n".join(card))
        external_id = next((m.group(1) for u in urls if (m := _VACANCY_URL.search(u))), None)
        extraction = OpportunityExtraction(
            title=title,
            company=company,
            location=location,
            remote_policy=remote,
            compensation=comp,
            employment_type=employment,
            requirements=requirements,
            summary=" ".join(tail) or None,
        )
        out.append(
            JobPosting(
                platform=PLATFORM,
                external_id=external_id,
                url=urls[0] if urls else None,
                title=title[:300],
                company=company,
                location=location,
                posted_at=posted,
                raw_text="\n".join(card),
                extraction=extraction,
            )
        )
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------------------ applications

_STATUS_RANK = {
    ApplicationStatus.withdrawn: 0,
    ApplicationStatus.offer: 1,
    ApplicationStatus.interview: 2,
    ApplicationStatus.rejected: 3,
    ApplicationStatus.invited: 4,
    ApplicationStatus.viewed: 5,
    ApplicationStatus.applied: 6,
}
_STATE_WORDS = re.compile(
    r"отклик|приглашен|отказ|просмотрен|собеседован|интервью|оффер|предложение о работе|отозван",
    re.IGNORECASE,
)


def status_from_line(line: str) -> ApplicationStatus | None:
    """hh state wording → status; «Не просмотрен» is *applied*, not *viewed*."""
    low = line.lower()
    if not _STATE_WORDS.search(low):
        return None
    if "не просмотрен" in low:
        return ApplicationStatus.applied
    if "просмотрен" in low:
        return ApplicationStatus.viewed
    status = parsers.normalize_status(low)
    return None if status == ApplicationStatus.unknown else status


def parse_applications(text: str, *, now: datetime | None = None) -> list[ApplicationObservationIn]:
    """«Отклики и приглашения»: title, company, city, state («Отклик · Просмотрен» …), date."""
    if not looks_like_hh(text, APPLICATIONS_MARKERS):
        return parsers.generic_applications(text, PLATFORM, now=now)
    out: list[ApplicationObservationIn] = []
    for card in split_cards(text):
        title = card[0]
        if _skip(title) or is_date_line(title):
            continue
        if len(card) == 1 and status_from_line(title) is not None:
            continue
        best: tuple[int, ApplicationStatus, str] | None = None
        when: datetime | None = None
        rest: list[str] = []
        for line in card[1:]:
            if is_date_line(line):
                when = when or parse_list_date(line, now=now)
                continue
            status = status_from_line(line)
            if status is not None:
                rank = _STATUS_RANK[status]
                if best is None or rank < best[0]:
                    best = (rank, status, line)
                continue
            if _skip(line):
                continue
            rest.append(line)
        urls = parsers.find_urls("\n".join(card))
        status = best[1] if best else ApplicationStatus.unknown
        out.append(
            ApplicationObservationIn(
                platform=PLATFORM,
                job_title=title[:300],
                company=rest[0] if rest else None,
                job_url=urls[0] if urls else None,
                status_raw=best[2] if best else "",
                status=status,
                applied_at=when if status == ApplicationStatus.applied else None,
                updated_at_platform=when,
                raw_payload={"lines": card},
            )
        )
    return out


# ------------------------------------------------------------------------------ profile

_SECTION = re.compile(
    r"^(ключевые навыки|навыки|обо мне|о себе|образование|повышение квалификации.*|"
    r"тесты, экзамены|знание языков|языки|гражданство.*|портфолио|дополнительная информация|"
    r"рекомендации|сертификаты|электронные сертификаты|опыт работы.*)$",
    re.IGNORECASE,
)
_EXPERIENCE_HEADER = re.compile(r"^опыт работы\b", re.IGNORECASE)
_PERIOD = re.compile(
    r"^(?:[а-яё]+\s+)?\d{4}\s*[—–-]\s*(?:(?:по\s+)?настоящее время|(?:[а-яё]+\s+)?\d{4})$",
    re.IGNORECASE,
)
_DURATION = re.compile(
    r"^\d+\s+(?:год|года|лет)(?:\s+\d+\s+(?:месяц|месяца|месяцев))?$"
    r"|^\d+\s+(?:месяц|месяца|месяцев)$",
    re.IGNORECASE,
)
_SITE_OR_LOCATION = re.compile(r"\b[\w-]+\.(?:[a-z]{2,}|рф)\b|^www\.", re.IGNORECASE)
_KEY_VALUE = re.compile(
    r"^(?P<key>занятость|график работы|проживает|специализации|гражданство)\s*:\s*(?P<val>.*)$",
    re.IGNORECASE,
)
_DESIRED_TITLE = re.compile(r"^желаемая должность\s*:\s*(?P<val>.+)$", re.IGNORECASE)


def _headline(lines: list[str]) -> str | None:
    for idx, line in enumerate(lines):
        low = line.lower().rstrip(":")
        if low == "мои резюме" and idx + 1 < len(lines):
            return lines[idx + 1][:300]
        if low.startswith("специализации") and idx > 0:
            return lines[idx - 1][:300]
        if m := _DESIRED_TITLE.match(line):
            return m.group("val").strip()[:300]
    return None


def _experience(lines: list[str]) -> list[SnapshotExperienceItem]:
    items: list[SnapshotExperienceItem] = []
    cur: dict[str, Any] | None = None
    stage = ""
    in_section = False

    def flush() -> None:
        if cur and (cur["company"] or cur["title"]):
            items.append(
                SnapshotExperienceItem(
                    company=cur["company"] or "",
                    title=cur["title"],
                    period=cur["period"],
                    description=" ".join(cur["desc"]) or None,
                )
            )

    for line in lines:
        if _EXPERIENCE_HEADER.match(line):
            in_section = True
            continue
        if not in_section:
            continue
        if _SECTION.match(line):
            break
        if _PERIOD.match(line):
            flush()
            cur = {"period": line, "company": None, "title": None, "desc": []}
            stage = "company"
            continue
        if cur is None or _DURATION.match(line):
            continue
        if stage == "company":
            cur["company"] = line
            stage = "title"
        elif stage == "title":
            if _SITE_OR_LOCATION.search(line):  # «Москва, northwind.example»
                continue
            cur["title"] = line
            stage = "desc"
        else:
            cur["desc"].append(line)
    flush()
    return items


def parse_profile(text: str) -> ProfileRead:
    """Resume page / «Мои резюме» list → profile; generic header parsing supplies skills/about."""
    base = parsers.generic_profile(text, PLATFORM)
    if not looks_like_hh(text, PROFILE_MARKERS):
        return base
    lines = parsers.split_lines(text)
    preferences: dict[str, Any] = {}
    rates: dict[str, Any] | None = None
    specializations: list[str] = []
    in_specializations = False
    for line in lines:
        if _EXPERIENCE_HEADER.match(line):
            break  # header block is over; salaries in job descriptions are not the user's rate
        if m := _KEY_VALUE.match(line):
            key, val = m.group("key").lower(), m.group("val").strip()
            in_specializations = key == "специализации"
            parts = [p.strip() for p in val.split(",") if p.strip()]
            if key == "занятость" and parts:
                preferences["employments"] = parts
            elif key == "график работы" and parts:
                preferences["schedules"] = parts
            elif key == "проживает" and val:
                preferences["area"] = val
            elif key == "гражданство" and parts:
                preferences["citizenship"] = parts[0]
            elif key == "специализации" and val:
                specializations.append(val)
            continue
        if _SECTION.match(line):
            in_specializations = False
            continue
        salary = parse_salary_line(line) if len(line) <= 40 else None
        if salary is not None:
            in_specializations = False
            if rates is None and salary.min is not None:
                amount = int(salary.min) if salary.min.is_integer() else salary.min
                rates = {"salary": amount, "currency": salary.currency, "raw": line}
            continue
        if in_specializations:
            specializations.append(line)
    if specializations:
        preferences["specializations"] = specializations
    return ProfileRead(
        platform=PLATFORM,
        headline=_headline(lines) or base.headline,
        about=base.about,
        experience=_experience(lines) or base.experience,
        skills=base.skills,
        rates=rates,
        preferences=preferences,
        raw_text=text,
    )

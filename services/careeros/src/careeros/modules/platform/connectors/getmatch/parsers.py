"""getmatch (getmatch.ru) paste parsers — Russian UI first, English tolerated.

Three pages are supported, always as text the user selected and copied (ADR-005: the site is
never fetched): «Мой профиль» → :func:`parse_profile`, the «Вакансии» cards →
:func:`parse_vacancies`, the «Отклики» rows → :func:`parse_responses`. Heuristics never invent
values — anything not stated stays ``None`` — and the pasted text is kept verbatim in
``raw_text`` / ``raw_payload``. Layouts nothing here recognises fall back to the shared generic
parsers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from careeros.modules.opportunities.enums import (
    CompensationPeriod,
    EmploymentType,
    RemotePolicy,
    Seniority,
)
from careeros.modules.opportunities.schemas import Compensation, OpportunityExtraction
from careeros.modules.platform import parsers
from careeros.modules.platform.enums import ApplicationStatus
from careeros.modules.platform.schemas import ApplicationObservationIn, JobPosting, ProfileRead
from careeros.modules.profiles.enums import CaptureMethod
from careeros.modules.profiles.schemas import SnapshotExperienceItem
from careeros.modules.vault.enums import Platform

PLATFORM = Platform.getmatch

# ------------------------------------------------------------------------------ page chrome

_CHROME = re.compile(
    r"^(getmatch|вакансии|компании|отклики|мой профиль|профиль|войти|выйти|регистрация|"
    r"редактировать|jobs|companies|responses|my profile|profile|log ?in|log ?out|sign ?in|"
    r"sign ?up|edit)$",
    re.IGNORECASE,
)
_APPLY = re.compile(
    r"^(откликнуться|отклик отправлен|вы откликнулись|apply|apply now|applied)$", re.IGNORECASE
)
_NOISE = re.compile(
    r"^(сохранить|в избранное|подробнее|показать (ещё|еще|контакты)|show more|share|"
    r"поделиться|new|новая|hot|горячая|promoted|реклама|пожаловаться|report|save|saved)$",
    re.IGNORECASE,
)
_SEP = re.compile(r"\s*[·•|]\s*|\s+/\s+")
_WS = re.compile("[ \t\u00a0]+")

_ROLE = re.compile(
    r"\b(engineer|developer|разработчик|инженер|analyst|аналитик|architect|архитектор|"
    r"scientist|manager|менеджер|lead|head|director|директор|cto|cpo|devops|sre|qa|"
    r"тестировщик|designer|дизайнер|programmer|программист|specialist|специалист|consultant|"
    r"консультант|administrator|администратор|dba|researcher|исследователь|owner|intern|"
    r"стажер|стажёр|тимлид|техлид|руководитель)\b",
    re.IGNORECASE,
)


def _looks_like_title(line: str) -> bool:
    return bool(_ROLE.search(line))


def _swap_if_needed(title: str | None, company: str | None) -> tuple[str | None, str | None]:
    if title and company and _looks_like_title(company) and not _looks_like_title(title):
        return company, title
    return title, company


def _pick_title_company(free: list[str], *, title_first: bool) -> tuple[str | None, str | None]:
    """First free line may be 'Title · Company'; otherwise the two first free lines, ordered by
    role words ('engineer', 'разработчик' …) and, when ambiguous, by the page's known order."""
    if not free:
        return None, None
    title, company = parsers.guess_title_company(free[0])
    if company:
        return _swap_if_needed(title, company)
    if len(free) == 1:
        return title, None
    a, b = free[0], free[1]
    ta, tb = _looks_like_title(a), _looks_like_title(b)
    if ta and not tb:
        return a, b
    if tb and not ta:
        return b, a
    return (a, b) if title_first else (b, a)


# ------------------------------------------------------------------------------ dates

_MONTHS: dict[str, int] = {}
for _i, _names in enumerate(
    (
        "jan january янв января январь",
        "feb february фев февраля февраль",
        "mar march мар марта март",
        "apr april апр апреля апрель",
        "may мая май",
        "jun june июн июня июнь",
        "jul july июл июля июль",
        "aug august авг августа август",
        "sep sept september сен сент сентября сентябрь",
        "oct october окт октября октябрь",
        "nov november ноя нояб ноября ноябрь",
        "dec december дек декабря декабрь",
    ),
    start=1,
):
    for _n in _names.split():
        _MONTHS[_n] = _i

_DAY_MONTH = re.compile(
    r"\b(?P<day>\d{1,2})\s+(?P<mon>[A-Za-zА-Яа-яё]{3,})\.?(?:,?\s+(?P<year>\d{4}))?"
)
_RELATIVE = re.compile(
    r"назад|ago|сегодня|вчера|today|yesterday|только что|just now", re.IGNORECASE
)
_ABSOLUTE = re.compile(
    r"\d{1,2}[./]\d{1,2}[./]\d{2,4}|\d{4}-\d{2}-\d{2}|\b[A-Za-z]{3,}\.?\s+\d{1,2},?\s+\d{4}"
)
_POSTED = re.compile(
    r"^(опубликован[оа]?|обновлен[оа]?|размещен[оа]?|создан[оа]?|posted|updated|published|"
    r"added)\b:?\s*(?P<rest>.*)$",
    re.IGNORECASE,
)


def parse_date(s: str, *, now: datetime | None = None) -> datetime | None:
    """Shared date parsing plus the site's '12 августа' (current year) form.

    Guarded so descriptive lines ('12 лет в данных', 'Опыт от 3 лет') are never read as dates:
    a day+word pair must name a real month, everything else needs an absolute date or a
    relative phrase ('назад', 'ago', 'вчера' …).
    """
    if not s:
        return None
    now = now or datetime.now(UTC)
    if m := _DAY_MONTH.search(s):
        mo = _MONTHS.get(m.group("mon").lower())
        if mo is not None:
            year = int(m.group("year")) if m.group("year") else now.year
            try:
                return datetime(year, mo, int(m.group("day")), tzinfo=UTC)
            except ValueError:
                return None
    if _RELATIVE.search(s) or _ABSOLUTE.search(s):
        return parsers.parse_date(s, now=now)
    return None


# ------------------------------------------------------------------------------ money

# thousand separators: space, no-break, thin, narrow no-break, figure space, comma
_THOUSAND_SPACES = " \u00a0\u2009\u202f\u2007"
_SEPS = _THOUSAND_SPACES + ","
_NUM = rf"\d(?:[\d{_SEPS}]*\d)?"
_MULT = r"(?:[kк](?![\w])|\s*тыс\.?)"
_CUR_SYM = r"[$€₽]"
_CUR_WORD = r"(?:руб\.?|rub|usd|eur)"
_MONEY = re.compile(
    rf"(?P<q>\b(?:от|from|до|up to|until|to)\b)?\s*"
    rf"(?P<c1>{_CUR_SYM}|{_CUR_WORD}(?=\s))?\s*(?P<a>{_NUM})(?P<m1>{_MULT})?"
    rf"(?:\s*(?:[–—-]|до|to)\s*(?P<c2>{_CUR_SYM})?\s*(?P<b>{_NUM})(?P<m2>{_MULT})?)?"
    rf"\s*(?P<c3>{_CUR_SYM}|{_CUR_WORD}(?![\w]))?",
    re.IGNORECASE,
)
_CURRENCY = {
    "₽": "RUB",
    "$": "USD",
    "€": "EUR",
    "руб": "RUB",
    "rub": "RUB",
    "usd": "USD",
    "eur": "EUR",
}
_PER_YEAR = re.compile(
    r"(в год|/\s*год|per year|/\s*year|/\s*yr|annual|yearly|per annum|годовых)", re.IGNORECASE
)
_PER_HOUR = re.compile(r"(в час|/\s*час|per hour|/\s*hour|/\s*hr|hourly)", re.IGNORECASE)
_PER_DAY = re.compile(r"(в день|/\s*день|per day|/\s*day|daily)", re.IGNORECASE)


def _currency(*symbols: str | None) -> str | None:
    for sym in symbols:
        if sym:
            return _CURRENCY.get(sym.lower().rstrip("."))
    return None


def _amount(num: str, mult: str | None) -> float:
    clean = re.sub(f"[{_THOUSAND_SPACES}]", "", num)
    if "," in clean:
        head, _, tail = clean.rpartition(",")
        clean = clean.replace(",", "") if len(tail) == 3 and head else f"{head}.{tail}"
    value = float(clean)
    if mult:
        value *= 1000
    return int(value) if value.is_integer() else value


def _period(line: str) -> CompensationPeriod:
    if _PER_YEAR.search(line):
        return CompensationPeriod.year
    if _PER_HOUR.search(line):
        return CompensationPeriod.hour
    if _PER_DAY.search(line):
        return CompensationPeriod.day
    return CompensationPeriod.month


def parse_money(line: str, *, require_currency: bool = True) -> Compensation | None:
    """'от 300 000 ₽' / '250 000 – 350 000 ₽' / '$4 000 – $6 000' / 'до 5 000 €' / '$4k–$6k'.

    Thin / no-break spaces and commas are thousand separators; 'k', 'к' and 'тыс.' multiply
    by 1000; the symbol or code gives the currency (₽ RUB, $ USD, € EUR); the period defaults
    to month unless the line says otherwise. 'от'/'from' → min only, 'до'/'up to' → max only,
    a bare amount → min = max. Without a currency the line is not a salary (unless
    ``require_currency`` is off, for labelled expectation values).
    """
    for m in _MONEY.finditer(line):
        cur = _currency(m.group("c1"), m.group("c2"), m.group("c3"))
        a = _amount(m.group("a"), m.group("m1"))
        if cur is None and (require_currency or (a < 1000 and not m.group("m1"))):
            continue
        b = _amount(m.group("b"), m.group("m2")) if m.group("b") else None
        q = (m.group("q") or "").lower()
        if b is not None:
            lo, hi = a, b
        elif q in ("от", "from"):
            lo, hi = a, None
        elif q:
            lo, hi = None, a
        else:
            lo, hi = a, a
        return Compensation(
            min=lo, max=hi, currency=cur, period=_period(line), type="salary", raw=line
        )
    return None


# ------------------------------------------------------------------------------ tags

_LEVEL_MAP: dict[str, Seniority] = {
    "junior": Seniority.junior,
    "джун": Seniority.junior,
    "джуниор": Seniority.junior,
    "middle": Seniority.mid,
    "mid": Seniority.mid,
    "мидл": Seniority.mid,
    "senior": Seniority.senior,
    "сеньор": Seniority.senior,
    "синьор": Seniority.senior,
    "lead": Seniority.lead,
    "teamlead": Seniority.lead,
    "team lead": Seniority.lead,
    "techlead": Seniority.lead,
    "tech lead": Seniority.lead,
    "тимлид": Seniority.lead,
    "лид": Seniority.lead,
    "staff": Seniority.staff,
    "principal": Seniority.principal,
}
_LEVEL_WORD = re.compile(
    r"\b(junior|middle|mid|senior|team ?lead|tech ?lead|lead|staff|principal|джун(?:иор)?|"
    r"мидл|сеньор|синьор|тимлид|лид)\b",
    re.IGNORECASE,
)
_LEVEL_LABEL = re.compile(r"^(уровень|level|грейд|grade)\s*:\s*", re.IGNORECASE)
_FORMAT = re.compile(
    r"^(?P<fmt>удал[её]нн\w*|удал[её]нк\w*|дистанционн\w*|remote|гибрид\w*|hybrid|офис\w*|"
    r"office|on-?site)\b(?P<rest>.*)$",
    re.IGNORECASE,
)
_EMPLOYMENT: tuple[tuple[re.Pattern[str], EmploymentType | None], ...] = (
    (
        re.compile(r"^(полная занятость|полный день|full-?time|фулл?-?тайм)$", re.IGNORECASE),
        EmploymentType.full_time,
    ),
    (
        re.compile(r"^(частичная занятость|неполный день|part-?time)$", re.IGNORECASE),
        EmploymentType.part_time,
    ),
    (
        re.compile(
            r"^(проектная работа|проект|project|contract|контракт|фриланс|freelance)$",
            re.IGNORECASE,
        ),
        EmploymentType.project,
    ),
    (re.compile(r"^(стажировка|internship)$", re.IGNORECASE), None),
)
_STACK_LABEL = re.compile(
    r"^(стек|технологии|навыки|теги|stack|tech stack|technologies|skills|tags)\b\s*:?\s*"
    r"(?P<items>.*)$",
    re.IGNORECASE,
)
# known technology tags (lower-case) — used to recognise an unlabelled stack row on a card
_TECH_WORDS = """
python sql dbt clickhouse dagster airflow kafka spark pyspark hadoop hive flink
postgresql postgres mysql mongodb redis elasticsearch opensearch kubernetes k8s docker
terraform ansible aws gcp azure snowflake bigquery redshift databricks scala java kotlin
go golang rust c++ c# typescript javascript react vue angular node.js nodejs django
fastapi flask spring pandas numpy pytorch tensorflow sklearn scikit-learn ml mlops git
linux bash grafana prometheus superset tableau powerbi metabase datalens greenplum
vertica oracle mssql trino presto iceberg delta s3 rabbitmq nats graphql rest grpc etl
elt dwh 1c php laravel ruby rails swift ios android flutter nosql html css sass jira
gitlab github jenkins argo argocd helm nginx celery sqlalchemy pydantic asyncio
"""
_TECH = frozenset(_TECH_WORDS.split())


def _level_of(word: str) -> Seniority | None:
    return _LEVEL_MAP.get(re.sub(r"\s+", " ", word.strip().lower()))


def level_tag(tag: str) -> Seniority | None:
    """Whole tag is a level ('Senior', 'Middle+', 'Senior/Lead', 'Уровень: Middle') → leftmost."""
    body = _LEVEL_LABEL.sub("", tag.strip())
    parts = [p.strip(" +") for p in re.split(r"[/,]", body)]
    parts = [p for p in parts if p]
    if not parts:
        return None
    levels = [_level_of(p) for p in parts]
    if any(lv is None for lv in levels):
        return None
    return levels[0]


def level_in_text(text: str) -> Seniority | None:
    m = _LEVEL_WORD.search(text)
    return _level_of(m.group(1)) if m else None


def _policy(fmt: str) -> RemotePolicy:
    low = fmt.lower()
    if low.startswith(("удал", "дистанц", "remote")):
        return RemotePolicy.remote_global
    if low.startswith(("гибрид", "hybrid")):
        return RemotePolicy.hybrid
    return RemotePolicy.onsite


def _employment_tag(tag: str) -> tuple[bool, EmploymentType | None]:
    for rx, value in _EMPLOYMENT:
        if rx.match(tag):
            return True, value
    return False, None


def _tech_items(line: str) -> list[str]:
    """Unlabelled tag row ('Python · Kubernetes · Kafka') → items when most are known tech."""
    items = parsers.split_skills(line)
    if len(items) < 2 or _MONEY_HINT.search(line):
        return []
    known = sum(1 for it in items if it.lower() in _TECH)
    return items if known >= max(1, len(items) // 2) else []


_MONEY_HINT = re.compile(r"[$€₽]|\bруб|\brub\b|\busd\b|\beur\b", re.IGNORECASE)
_URL_ID = re.compile(r"getmatch\.ru/vacancies/(?P<id>\d+)", re.IGNORECASE)
_LABEL_COMPANY = re.compile(
    r"^(компания|company|работодатель|employer)\s*:\s*(?P<v>.+)$", re.IGNORECASE
)
_LABEL_TITLE = re.compile(
    r"^(позиция|вакансия|должность|position|title|role)\s*:\s*(?P<v>.+)$", re.IGNORECASE
)


@dataclass(slots=True)
class _Card:
    free: list[str] = field(default_factory=list)
    title: str | None = None
    company: str | None = None
    compensation: Compensation | None = None
    policy: RemotePolicy = RemotePolicy.unknown
    location: str | None = None
    seniority: Seniority | None = None
    employment: EmploymentType | None = None
    technologies: list[str] = field(default_factory=list)
    posted_at: datetime | None = None
    urls: list[str] = field(default_factory=list)
    markers: int = 0

    def add_tech(self, items: list[str]) -> None:
        seen = {t.lower() for t in self.technologies}
        for it in items:
            if it.lower() not in seen:
                seen.add(it.lower())
                self.technologies.append(it)


def _classify_tags(line: str, card: _Card) -> bool:
    """Level / format / employment tags on one line ('Гибрид · Москва', 'Junior · Полная
    занятость'); the leftover capitalised tag on a format line is the city. True if consumed."""
    tags = [t for t in _SEP.split(line) if t]
    hits = 0
    leftovers: list[str] = []
    for tag in tags:
        if (lv := level_tag(tag)) is not None:
            card.seniority = card.seniority or lv
            hits += 1
            continue
        matched, emp = _employment_tag(tag)
        if matched:
            card.employment = card.employment or emp
            hits += 1
            continue
        if m := _FORMAT.match(tag):
            if card.policy == RemotePolicy.unknown:
                card.policy = _policy(m.group("fmt"))
            rest = m.group("rest").strip(" :,()-–—·")
            if rest:
                leftovers.append(rest)
            hits += 1
            continue
        leftovers.append(tag)
    if hits == 0:
        return False
    tech = [t for t in leftovers if t.lower() in _TECH]
    if tech:
        card.add_tech(tech)
    if card.location is None:
        cities = [
            t
            for t in leftovers
            if t not in tech and t[0].isupper() and not any(ch.isdigit() for ch in t)
        ]
        card.location = ", ".join(cities) or None
    return True


def _read_card(block: list[str], now: datetime) -> JobPosting | None:
    card = _Card()
    pending_stack = False
    for line in block:
        urls = parsers.find_urls(line)
        if urls:
            card.urls.extend(urls)
            if line.startswith("http"):
                continue
        if _CHROME.match(line) or _NOISE.match(line):
            continue
        if _APPLY.match(line):
            card.markers += 1
            continue
        if m := _LABEL_COMPANY.match(line):
            card.company = m.group("v").strip()
            continue
        if m := _LABEL_TITLE.match(line):
            card.title = m.group("v").strip()
            continue
        if m := _STACK_LABEL.match(line):
            card.markers += 1
            items = m.group("items")
            if items:
                card.add_tech(parsers.split_skills(items))
            else:
                pending_stack = True
            continue
        if pending_stack:
            pending_stack = False
            card.add_tech(parsers.split_skills(line))
            continue
        if comp := parse_money(line):
            if card.compensation is None:
                card.compensation = comp
                card.markers += 1
            continue
        if m := _POSTED.match(line):
            card.markers += 1
            if card.posted_at is None:
                card.posted_at = parse_date(m.group("rest") or line, now=now)
            continue
        if _classify_tags(line, card):
            continue
        if tech := _tech_items(line):
            card.add_tech(tech)
            card.markers += 1
            continue
        if len(line) <= 32 and (dt := parse_date(line, now=now)) is not None:
            if card.posted_at is None:
                card.posted_at = dt
            continue
        card.free.append(line)

    if card.policy != RemotePolicy.unknown and card.seniority is not None:
        card.markers += 1
    if card.markers == 0:
        return None
    title, company = card.title, card.company
    if title is None:
        free_title, free_company = _pick_title_company(card.free, title_first=False)
        title = free_title
        company = company or free_company
    if not title:
        return None
    if card.location is None and len(card.free) >= 3:
        third = card.free[2]
        if len(third) <= 40 and not any(ch.isdigit() for ch in third):
            card.location = third
    url = card.urls[0] if card.urls else None
    external_id = None
    if url and (m := _URL_ID.search(url)):
        external_id = m.group("id")
    if card.seniority is None:
        card.seniority = level_in_text(title)
    extraction = OpportunityExtraction(
        title=title[:300],
        company=company,
        location=card.location,
        remote_policy=card.policy,
        employment_type=card.employment,
        compensation=card.compensation,
        seniority=card.seniority,
        technologies=list(card.technologies),
    )
    return JobPosting(
        platform=PLATFORM,
        external_id=external_id,
        url=url,
        title=title[:300],
        company=company,
        location=card.location,
        posted_at=card.posted_at,
        raw_text="\n".join(block),
        extraction=extraction,
    )


def parse_vacancies(
    text: str, *, now: datetime | None = None, limit: int = 100
) -> list[JobPosting]:
    """«Вакансии» cards (one paragraph block per card) → postings; unknown layout → generic."""
    now = now or datetime.now(UTC)
    out: list[JobPosting] = []
    for block in parsers.blocks(text):
        posting = _read_card(block, now)
        if posting is None:
            continue
        out.append(posting)
        if len(out) >= limit:
            break
    return out or parsers.generic_jobs(text, PLATFORM, limit=limit)


# ------------------------------------------------------------------------------ responses

_STATUS_LABEL = re.compile(r"^(статус|status|этап|stage)\s*:\s*(?P<val>.+)$", re.IGNORECASE)
_STATUS_EXACT: dict[str, ApplicationStatus] = {
    "отправлен": ApplicationStatus.applied,
    "отправлено": ApplicationStatus.applied,
    "отправлен отклик": ApplicationStatus.applied,
    "отклик отправлен": ApplicationStatus.applied,
    "новый": ApplicationStatus.applied,
    "на рассмотрении": ApplicationStatus.applied,
    "рассматривается": ApplicationStatus.applied,
    "sent": ApplicationStatus.applied,
    "application sent": ApplicationStatus.applied,
    "applied": ApplicationStatus.applied,
    "submitted": ApplicationStatus.applied,
    "pending": ApplicationStatus.applied,
    "in review": ApplicationStatus.applied,
    "under review": ApplicationStatus.applied,
    "просмотрен": ApplicationStatus.viewed,
    "просмотрено": ApplicationStatus.viewed,
    "просмотрен работодателем": ApplicationStatus.viewed,
    "viewed": ApplicationStatus.viewed,
    "seen": ApplicationStatus.viewed,
    "приглашение": ApplicationStatus.invited,
    "приглашен": ApplicationStatus.invited,
    "приглашена": ApplicationStatus.invited,
    "invited": ApplicationStatus.invited,
    "invitation": ApplicationStatus.invited,
    "интервью": ApplicationStatus.interview,
    "собеседование": ApplicationStatus.interview,
    "техническое интервью": ApplicationStatus.interview,
    "interview": ApplicationStatus.interview,
    "interviewing": ApplicationStatus.interview,
    "отказ": ApplicationStatus.rejected,
    "отклонен": ApplicationStatus.rejected,
    "отклонено": ApplicationStatus.rejected,
    "rejected": ApplicationStatus.rejected,
    "declined": ApplicationStatus.rejected,
    "not selected": ApplicationStatus.rejected,
    "оффер": ApplicationStatus.offer,
    "предложение": ApplicationStatus.offer,
    "offer": ApplicationStatus.offer,
    "offer received": ApplicationStatus.offer,
    "hired": ApplicationStatus.offer,
    "отозван": ApplicationStatus.withdrawn,
    "отозвано": ApplicationStatus.withdrawn,
    "withdrawn": ApplicationStatus.withdrawn,
    "withdrew": ApplicationStatus.withdrawn,
}
_APPLIED_HINT = re.compile(r"отправлен|отклик|подан|applied|sent|submitted", re.IGNORECASE)
_UPDATED_HINT = re.compile(r"обновлен|изменен|updated|changed", re.IGNORECASE)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().replace("ё", "е")).strip(" .!:;")


def response_status(raw: str) -> ApplicationStatus:
    """'Статус: Просмотрен' / 'Отправлен' / 'Sent' … → normalized status (explicit map first)."""
    val = raw.strip()
    if m := _STATUS_LABEL.match(val):
        val = m.group("val")
    return _STATUS_EXACT.get(_norm(val)) or parsers.normalize_status(val)


def _read_row(lines: list[str], now: datetime) -> ApplicationObservationIn | None:
    body = [ln for ln in lines if not _CHROME.match(ln) and not _NOISE.match(ln)]
    if len(body) < 2:
        return None
    status_line = next((ln for ln in body if _STATUS_LABEL.match(ln)), None)
    if status_line is None:
        status_line = next((ln for ln in body[1:] if _norm(ln) in _STATUS_EXACT), None)
    dated = [
        (ln, dt)
        for ln in body[1:]
        if ln is not status_line and (dt := parse_date(ln, now=now)) is not None
    ]
    if status_line is not None:
        status, status_raw = response_status(status_line), status_line
    elif dated:
        status, status_raw = parsers.best_status(body[1:])
    else:
        return None
    date_lines = {ln for ln, _ in dated}
    free = [ln for ln in body if ln is not status_line and ln not in date_lines]
    title, company = _pick_title_company(free, title_first=True)
    if not title:
        return None
    applied_at = next((dt for ln, dt in dated if _APPLIED_HINT.search(ln)), None)
    updated_at = next((dt for ln, dt in dated if _UPDATED_HINT.search(ln)), None)
    if applied_at is None:
        applied_at = next((dt for ln, dt in dated if not _UPDATED_HINT.search(ln)), None)
    urls = parsers.find_urls("\n".join(lines))
    url = urls[0] if urls else None
    external_id = None
    if url and (m := _URL_ID.search(url)):
        external_id = m.group("id")
    return ApplicationObservationIn(
        platform=PLATFORM,
        external_id=external_id,
        job_title=title[:300],
        company=company,
        job_url=url,
        status_raw=status_raw,
        status=status,
        applied_at=applied_at,
        updated_at_platform=updated_at,
        raw_payload={"lines": list(lines)},
    )


def _table_rows(text: str) -> list[list[str]]:
    """Tab-separated rows (a copied HTML table) → cells; lines without tabs are ignored."""
    rows: list[list[str]] = []
    for raw in text.splitlines():
        if "\t" not in raw:
            continue
        cells = [_WS.sub(" ", c).strip() for c in raw.split("\t")]
        cells = [c for c in cells if c]
        if len(cells) >= 2:
            rows.append(cells)
    return rows


def parse_responses(text: str, *, now: datetime | None = None) -> list[ApplicationObservationIn]:
    """«Отклики» rows (blocks or tab-separated table) → observations; unknown → generic."""
    now = now or datetime.now(UTC)
    groups = _table_rows(text) or parsers.blocks(text)
    out: list[ApplicationObservationIn] = []
    for group in groups:
        row = _read_row(group, now)
        if row is not None:
            out.append(row)
    return out or parsers.generic_applications(text, PLATFORM, now=now)


# ------------------------------------------------------------------------------ profile

_H_ABOUT = re.compile(
    r"^(о себе|обо мне|описание|about me|about|summary|bio)\b\s*:?\s*(?P<inline>.*)$",
    re.IGNORECASE,
)
_H_SKILLS = re.compile(
    r"^(стек|навыки|ключевые навыки|технологии|skills|key skills|tech stack|stack|technologies)"
    r"\b\s*:?\s*(?P<inline>.*)$",
    re.IGNORECASE,
)
_H_EXPERIENCE = re.compile(
    r"^(опыт работы|опыт|experience|work experience|employment)\s*:?$", re.IGNORECASE
)
_H_OTHER = re.compile(
    r"^(образование|проекты|портфолио|сертификаты|языки|контакты|ссылки|соцсети|рекомендации|"
    r"education|projects|portfolio|certifications|languages|contacts?|links|recommendations)"
    r"\s*:?$",
    re.IGNORECASE,
)
_KV = re.compile(r"^(?P<key>[^:]{2,40}?)\s*:\s*(?P<val>.+)$")
_KEYS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "salary",
        re.compile(
            r"^(ожидания по зарплате|зарплатные ожидания|ожидаемая зарплата|желаемая зарплата|"
            r"зарплата|зп|salary expectations?|expected salary|desired salary|salary|"
            r"compensation)$",
            re.IGNORECASE,
        ),
    ),
    (
        "english",
        re.compile(r"^(английский( язык)?|уровень английского|english( level)?)$", re.IGNORECASE),
    ),
    (
        "remote",
        re.compile(
            r"^(удал[её]нно|удал[её]нная работа|удал[её]нка|remote|remote work)$", re.IGNORECASE
        ),
    ),
    ("format", re.compile(r"^(формат работы|формат|work format)$", re.IGNORECASE)),
    (
        "relocation",
        re.compile(
            r"^(переезд|релокация|готовность к переезду|relocation|relocate|willing to relocate)$",
            re.IGNORECASE,
        ),
    ),
    (
        "location",
        re.compile(r"^(локация|город|местоположение|location|city|based in)$", re.IGNORECASE),
    ),
    (
        "position",
        re.compile(
            r"^(позиция|должность|желаемая должность|специализация|position|title|role|"
            r"desired position)$",
            re.IGNORECASE,
        ),
    ),
    (
        "availability",
        re.compile(r"^(статус|status|availability|готовность|поиск работы)$", re.IGNORECASE),
    ),
)
_F_RELOCATION = re.compile(
    r"^(?P<neg>не\s+|not\s+)?(готов[а]?\s+к\s+переезду|готов[а]?\s+переехать|"
    r"рассматриваю\s+переезд|open to relocation|willing to relocate|ready to relocate)$",
    re.IGNORECASE,
)
_F_REMOTE = re.compile(
    r"^(?P<neg>не\s+|not\s+)?(готов[а]?\s+(работать\s+)?удал[её]нно|только\s+удал[её]нно|"
    r"хочу\s+удал[её]нно|open to remote|remote only|remote-only)$",
    re.IGNORECASE,
)
_AVAILABILITY = re.compile(
    r"^(открыт[а]?\s+к\s+предложениям|ищу\s+работу|в\s+активном\s+поиске|активно\s+ищу(\s+работу)?|"
    r"рассматриваю\s+предложения|не\s+ищу\s+работу|не\s+рассматриваю\s+предложения|"
    r"open to (offers|work|opportunities)|actively looking|not looking|"
    r"looking for (a )?(new )?(job|role))$",
    re.IGNORECASE,
)
_ENGLISH_LINE = re.compile(
    r"^(английский(\s+язык)?|english)\s*[—–:-]\s*(?P<lvl>.+)$", re.IGNORECASE
)
_NAME = re.compile(r"^(?:[A-ZА-ЯЁ][a-zа-яё'’-]+\s?){2,4}$")
_LOCATION_COMMA = re.compile(r"^[^\d,]{2,40},\s*[^\d,]{2,40}$")


def _looks_like_name(line: str) -> bool:
    return bool(_NAME.match(line)) and not _looks_like_title(line)


def _is_location_line(line: str) -> bool:
    return bool(_LOCATION_COMMA.match(line)) or bool(_FORMAT.match(line) and len(line) <= 30)


def _known_key(key: str) -> str | None:
    low = key.strip().lower()
    return next((name for name, rx in _KEYS if rx.match(low)), None)


def _yes_no(val: str) -> bool | None:
    v = _norm(val)
    if v.startswith(("нет", "no", "не ", "false", "not ")):
        return False
    if v.startswith(("да", "yes", "true", "готов", "ready", "open", "возможн")):
        return True
    return None


def _format_flag(val: str) -> bool | None:
    v = _norm(val)
    if "удал" in v or "remote" in v or "дистанц" in v:
        return True
    if "офис" in v or "office" in v or "on-site" in v or "onsite" in v:
        return False
    return None


def _entry_split(line: str) -> tuple[str | None, str] | None:
    """'Позиция — Компания' (short, no sentence punctuation) → (title, company)."""
    if len(line) > 120 or line.endswith((".", "!", "?", ";")):
        return None
    title, company = parsers.guess_title_company(line)
    if not company:
        return None
    title, company = _swap_if_needed(title, company)
    return title, company or ""


def _rates(comp: Compensation, raw: str) -> dict[str, Any]:
    rates: dict[str, Any] = {}
    if comp.min is not None:
        rates["salary_min"] = comp.min
    if comp.max is not None and comp.max != comp.min:
        rates["salary_max"] = comp.max
    if comp.currency:
        rates["currency"] = comp.currency
    rates["period"] = str(comp.period) if comp.period else str(CompensationPeriod.month)
    rates["raw"] = raw
    return rates


def parse_profile(text: str, *, now: datetime | None = None) -> ProfileRead:
    """«Мой профиль»: name / position / location / status, then «О себе», «Стек», «Опыт работы»
    and the labelled facts (salary expectation, English, remote, relocation). Unknown → generic.
    """
    del now  # accepted for a uniform signature; the profile page carries no relative dates
    lines = parsers.split_lines(text)
    name: str | None = None
    headline: str | None = None
    location: str | None = None
    availability: str | None = None
    about: list[str] = []
    skills: list[str] = []
    experience: list[SnapshotExperienceItem] = []
    pending_title: str | None = None
    rates: dict[str, Any] | None = None
    english: str | None = None
    remote: bool | None = None
    relocation: bool | None = None
    section: str | None = None
    recognised = False

    for line in lines:
        if m := _H_ABOUT.match(line):
            section, recognised = "about", True
            if m.group("inline"):
                about.append(m.group("inline"))
            continue
        if m := _H_SKILLS.match(line):
            section, recognised = "skills", True
            if m.group("inline"):
                skills.extend(parsers.split_skills(m.group("inline")))
            continue
        if _H_EXPERIENCE.match(line):
            section, recognised = "experience", True
            continue
        if _H_OTHER.match(line):
            section, recognised = "other", True
            continue
        if (m := _KV.match(line)) and (key := _known_key(m.group("key"))):
            val = m.group("val").strip()
            recognised = True
            section = None
            if key == "salary":
                if comp := parse_money(val, require_currency=False):
                    rates = rates or _rates(comp, val)
            elif key == "english":
                english = english or val
            elif key == "remote":
                remote = _yes_no(val) if remote is None else remote
            elif key == "format":
                remote = _format_flag(val) if remote is None else remote
            elif key == "relocation":
                relocation = _yes_no(val) if relocation is None else relocation
            elif key == "location":
                location = location or val
            elif key == "position":
                headline = headline or val[:300]
            elif key == "availability":
                availability = availability or val
            continue
        if m := _F_RELOCATION.match(line):
            recognised, section = True, None
            relocation = not m.group("neg") if relocation is None else relocation
            continue
        if m := _F_REMOTE.match(line):
            recognised, section = True, None
            remote = not m.group("neg") if remote is None else remote
            continue
        if english is None and (m := _ENGLISH_LINE.match(line)):
            english = m.group("lvl").strip()
            continue
        if section is None:
            if _CHROME.match(line):
                continue
            if _AVAILABILITY.match(line):
                availability = availability or line
                continue
            if name is None and _looks_like_name(line):
                name = line
                continue
            if headline is None and not _is_location_line(line):
                headline = line[:300]
                continue
            if location is None and _is_location_line(line):
                location = line
            continue
        if section == "about":
            about.append(line)
        elif section == "skills":
            skills.extend(parsers.split_skills(line))
        elif section == "experience":
            if parsers.looks_like_period(line):
                if experience and experience[-1].period is None:
                    experience[-1].period = line
                pending_title = None
            elif split := _entry_split(line):
                experience.append(SnapshotExperienceItem(company=split[1], title=split[0]))
                pending_title = None
            elif pending_title is not None and len(line) <= 80:
                experience.append(SnapshotExperienceItem(company=line, title=pending_title))
                pending_title = None
            elif _looks_like_title(line) and len(line) <= 80 and not line.endswith("."):
                pending_title = line
            elif experience:
                prev = experience[-1].description
                experience[-1].description = f"{prev} {line}" if prev else line

    if not recognised:
        return parsers.generic_profile(text, PLATFORM)
    preferences: dict[str, Any] = {
        "english": english,
        "remote": remote,
        "relocation": relocation,
        "location": location,
    }
    return ProfileRead(
        platform=PLATFORM,
        capture_method=CaptureMethod.paste,
        headline=headline,
        about=" ".join(about) or None,
        experience=experience,
        skills=skills,
        rates=rates,
        availability=availability,
        preferences=preferences,
        raw_text=text,
        raw_payload={"name": name} if name else None,
    )

"""HTTP 200 is not a vacancy (ADR-015 §4). Conservative EN+RU detectors for pages that only look
like one: captcha / login / cookie interstitials, WAF blocks, error pages, empty JS shells, closed
jobs and search-result listings — plus a completeness score over the fields a posting should have.

Heuristics pair a keyword with a structural signal (short body, no JSON-LD ``JobPosting``,
title match) so a real posting that merely mentions "log in" in its chrome is not rejected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from careeros.modules.platform.fetch.artifact import FetchArtifact
from careeros.modules.platform.fetch.extract.jsonld import find_jobposting
from careeros.modules.platform.fetch.extract.text import (
    html_meta,
    html_to_text,
    markdown_body,
    markdown_title,
)

__all__ = ["Quality", "assess", "completeness_signals"]


@dataclass(frozen=True, slots=True)
class Quality:
    usable: bool
    reason: str | None
    quality: float
    completeness: float
    flags: tuple[str, ...] = ()


_I = re.IGNORECASE
_CAPTCHA = re.compile(
    r"captcha|cf-challenge|challenge-platform|are you a robot|verify (?:that )?you are (?:a )?human"
    r"|подтвердите,? что вы не робот|вы не робот|проверка,? что вы (?:не робот|человек)"
    r"|just a moment\.\.\.|attention required",
    _I,
)
_WAF = re.compile(
    r"access denied|request blocked|you have been blocked|ddos-?guard|доступ запрещ[её]н"
    r"|доступ ограничен|error 1020|\bforbidden\b|запрос заблокирован",
    _I,
)
_LOGIN = re.compile(
    r"\b(?:log ?in|sign ?in|sign up to continue|authorization required|please log in"
    r"|войдите|войти|вход в личный кабинет|авторизуйтесь|зарегистрируйтесь)\b",
    _I,
)
_COOKIE = re.compile(
    r"accept (?:all )?cookies|cookie consent|we use cookies|мы используем cookie"
    r"|использу\w+ файлы cookie",
    _I,
)
_NOT_FOUND = re.compile(
    r"page not found|\bnot found\b|\b404\b|страница не найдена|такой страницы нет"
    r"|nothing found|ничего не найдено|страница удалена",
    _I,
)
_JS_SHELL = re.compile(
    r"enable javascript|javascript is (?:required|disabled)|включите javascript"
    r"|you need to enable javascript|requires javascript",
    _I,
)
_SHELL_HTML = re.compile(
    r"<div\b[^>]*id\s*=\s*[\"'](?:root|app|__next|__nuxt)[\"'][^>]*>\s*</div>", _I
)
_CLOSED = re.compile(
    r"no longer (?:available|accepting applications|active|accepting)"
    r"|this (?:job|position|vacancy|posting|listing) (?:has )?(?:been )?"
    r"(?:closed|filled|expired|removed)"
    r"|(?:job|vacancy|posting|listing|position) (?:is |has )?(?:closed|expired|archived)"
    r"|вакансия (?:закрыта|в архиве|неактивна|снята|удалена|больше не (?:доступна|актуальна))"
    r"|в архиве с \d",
    _I,
)
_SEARCH_TITLE = re.compile(
    r"search results|jobs? (?:found|matching|in )|vacancies (?:found|matching|in )"
    r"|результат[ыа]? поиска"
    r"|найден[оа]? \d+ ваканси|\d+ jobs? in |вакансии в ",
    _I,
)
_APPLY = re.compile(r"apply now|откликнуться|\bapply\b", _I)
_LISTING_CARD = re.compile(
    r"data-(?:qa|test|testid)\s*=\s*[\"'](?:vacancy-serp__vacancy|job-card|serp-item|job-listing)",
    _I,
)
_GENERIC_TITLE = re.compile(r"^(?:home|главная|jobs|вакансии|untitled|index)$", _I)

_COMPANY = re.compile(
    r"\b(?:company|employer|hiring organization|компани[яи]|работодател[ья]|о компании)\b", _I
)
_LOCATION = re.compile(
    r"\b(?:location|remote|hybrid|on-?site|relocation|местоположение|город|удал[её]нн\w*"
    r"|гибрид\w*|офис\w*|релокац\w*)\b",
    _I,
)
_SALARY = re.compile(
    r"(?:[$€£₽₸]\s?\d[\d\s.,]*)|(?:\d[\d\s.,]*\s?(?:[$€£₽₸]|usd|eur|gbp|rub|руб|pln|zł|kzt|uah|грн)\b)"
    r"|\b(?:salary|compensation|зарплат\w*|оклад|вилка)\b",
    _I,
)
_SKILLS = re.compile(
    r"\b(?:skills|requirements|qualifications|responsibilities|tech stack|stack|навыки"
    r"|требования|обязанности|квалификац\w*|стек)\b",
    _I,
)
_DESCRIPTION_MIN = 600
_BODY_MIN = 400


def completeness_signals(
    text: str, *, title: str | None, jsonld: dict[str, Any] | None
) -> dict[str, bool]:
    """Which of title / company / description / location / salary / skills the content shows."""
    low = text.lower()
    ld = jsonld or {}
    return {
        "title": bool(title) or bool(ld.get("title")),
        "company": bool(ld.get("hiringOrganization")) or bool(_COMPANY.search(text)),
        "description": bool(ld.get("description")) or len(text) >= _DESCRIPTION_MIN,
        "location": bool(ld.get("jobLocation") or ld.get("jobLocationType"))
        or bool(_LOCATION.search(low)),
        "salary": bool(ld.get("baseSalary")) or bool(_SALARY.search(text)),
        "skills": bool(ld.get("skills") or ld.get("qualifications")) or bool(_SKILLS.search(low)),
    }


def _score(signals: dict[str, bool]) -> float:
    return round(sum(1 for v in signals.values() if v) / len(signals), 2)


def _reject(reason: str, completeness: float = 0.0) -> Quality:
    return Quality(False, reason, 0.0, completeness)


def assess(artifact: FetchArtifact) -> Quality:
    """Quality verdict for one artifact. ``usable`` = the chain may stop here."""
    if artifact.error_type:
        return _reject(artifact.error_type)
    status = artifact.status_code
    if status is not None and status >= 400:
        if status in (404, 410):
            return _reject("not_found")
        return _reject("http_error")
    if status is not None and 300 <= status < 400:
        return _reject("http_error")
    if artifact.raw_json is not None:
        return _assess_json(artifact.raw_json)
    text = artifact.raw_text or ""
    if not text.strip():
        return _reject("empty")
    if artifact.is_markdown and not artifact.is_html:
        return _assess_markdown(text)
    return _assess_html(text)


def _assess_html(html: str) -> Quality:
    jsonld = find_jobposting(html)
    meta = html_meta(html)
    page_title = meta.get("title", "")
    body = html_to_text(html)
    n = len(body)
    tlow = page_title.lower()

    # interstitials announce themselves in the <title> — strongest signal, length irrelevant
    if _CAPTCHA.search(tlow):
        return _reject("captcha")
    if _WAF.search(tlow):
        return _reject("waf_blocked")
    if jsonld is None and _NOT_FOUND.search(tlow):
        return _reject("not_found")
    if jsonld is None and _LOGIN.search(tlow) and n < 3000:
        return _reject("login_wall")

    closed = bool(_CLOSED.search(body))
    if closed and jsonld is None and n < 2000:
        return _reject("job_closed")  # the classic "no longer available" stub
    if jsonld is None and n < 2000:
        if _CAPTCHA.search(body):
            return _reject("captcha")
        if _WAF.search(body):
            return _reject("waf_blocked")
        if _JS_SHELL.search(body) or (n < 300 and _SHELL_HTML.search(html)):
            return _reject("js_shell")
        if n < 1500 and _LOGIN.search(body):
            return _reject("login_wall")
        if n < 800 and _COOKIE.search(body):
            return _reject("cookie_wall")
        if n < 1500 and _NOT_FOUND.search(body):
            return _reject("not_found")
    if jsonld is None and n < 300:
        return _reject("js_shell" if _SHELL_HTML.search(html) else "too_thin")

    if jsonld is None:
        cards = len(_LISTING_CARD.findall(html))
        applies = len(_APPLY.findall(body))
        if (
            cards >= 5
            or _SEARCH_TITLE.search(tlow)
            or (applies >= 6 and _SEARCH_TITLE.search(body))
        ):
            return _reject("search_results")

    title = _page_title(meta, jsonld)
    signals = completeness_signals(body, title=title, jsonld=jsonld)
    completeness = _score(signals)
    flags: tuple[str, ...] = ("job_closed",) if closed else ()
    quality = min(1.0, 0.2 + 0.5 * completeness + (0.3 if jsonld is not None else 0.0))
    if closed:
        quality = round(quality * 0.8, 2)
    if closed and completeness < 0.5:
        return Quality(False, "job_closed", quality, completeness, flags)
    usable = signals["title"] and (jsonld is not None or n >= _BODY_MIN) and completeness >= 0.34
    return Quality(usable, None if usable else "too_thin", round(quality, 2), completeness, flags)


def _page_title(meta: dict[str, str], jsonld: dict[str, Any] | None) -> str | None:
    if jsonld is not None and isinstance(jsonld.get("title"), str) and jsonld["title"].strip():
        return jsonld["title"].strip()
    for key in ("h1", "og:title", "title"):
        value = meta.get(key, "").strip()
        if value and not _GENERIC_TITLE.match(value):
            return value
    return None


def _assess_markdown(markdown: str) -> Quality:
    meta, body = markdown_body(markdown)
    title = meta.get("title") or markdown_title(body)
    n = len(body)
    tlow = (title or "").lower()
    if _CAPTCHA.search(tlow) or (n < 2000 and _CAPTCHA.search(body)):
        return _reject("captcha")
    if _WAF.search(tlow) or (n < 2000 and _WAF.search(body)):
        return _reject("waf_blocked")
    if _NOT_FOUND.search(tlow) or (n < 1500 and _NOT_FOUND.search(body)):
        return _reject("not_found")
    if n < 1500 and _LOGIN.search(body):
        return _reject("login_wall")
    if n < 2000 and _JS_SHELL.search(body):
        return _reject("js_shell")
    if n < 300:
        return _reject("too_thin")
    if _SEARCH_TITLE.search(tlow) and len(_APPLY.findall(body)) >= 6:
        return _reject("search_results")
    signals = completeness_signals(body, title=title, jsonld=None)
    completeness = _score(signals)
    closed = bool(_CLOSED.search(body))
    flags: tuple[str, ...] = ("job_closed", "transformed") if closed else ("transformed",)
    quality = round(min(1.0, 0.15 + 0.5 * completeness) * (0.8 if closed else 1.0), 2)
    if closed and completeness < 0.5:
        return Quality(False, "job_closed", quality, completeness, flags)
    usable = signals["title"] and n >= _BODY_MIN and completeness >= 0.34
    return Quality(usable, None if usable else "too_thin", quality, completeness, flags)


_JSON_TITLE = ("title", "name", "position", "job_title")
_JSON_DESCRIPTION = ("description", "body", "text", "content")
_JSON_COMPANY = ("company", "employer", "hiringOrganization", "company_name", "organization")
_JSON_LOCATION = ("location", "area", "city", "jobLocation", "workplace", "address")
_JSON_SALARY = ("salary", "compensation", "baseSalary", "employmentTypes", "salary_from")
_JSON_SKILLS = ("skills", "key_skills", "requiredSkills", "requirements", "technologies")


def _assess_json(data: dict[str, Any] | list[Any]) -> Quality:
    node: dict[str, Any] = data if isinstance(data, dict) else {}
    if isinstance(data, list):
        node = next((d for d in data if isinstance(d, dict)), {})

    def has(keys: tuple[str, ...]) -> bool:
        return any(node.get(k) not in (None, "", [], {}) for k in keys)

    signals = {
        "title": has(_JSON_TITLE),
        "company": has(_JSON_COMPANY),
        "description": has(_JSON_DESCRIPTION),
        "location": has(_JSON_LOCATION),
        "salary": has(_JSON_SALARY),
        "skills": has(_JSON_SKILLS),
    }
    completeness = _score(signals)
    if not node:
        return _reject("empty")
    closed = (
        any(
            node.get(k) in (True, "closed", "archived", "expired")
            for k in ("archived", "expired", "closed", "is_closed")
        )
        or node.get("isActive") is False
    )
    flags: tuple[str, ...] = ("job_closed",) if closed else ()
    quality = round(min(1.0, 0.3 + 0.7 * completeness) * (0.8 if closed else 1.0), 2)
    usable = signals["title"] and (signals["description"] or completeness >= 0.5)
    return Quality(usable, None if usable else "too_thin", quality, completeness, flags)

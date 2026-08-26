from __future__ import annotations

from enum import StrEnum


class Source(StrEnum):
    linkedin = "linkedin"
    wellfound = "wellfound"
    upwork = "upwork"
    toptal = "toptal"
    hh = "hh"
    indeed = "indeed"
    getmatch = "getmatch"
    rockethunt = "rockethunt"
    justjoin = "justjoin"
    email = "email"
    recruiter = "recruiter"
    direct = "direct"
    website = "website"
    manual = "manual"
    clipboard = "clipboard"
    share = "share"
    url = "url"


class ContractType(StrEnum):
    employment = "employment"
    b2b = "b2b"
    freelance = "freelance"
    contract_to_hire = "contract_to_hire"


class EmploymentType(StrEnum):
    full_time = "full_time"
    part_time = "part_time"
    project = "project"


class RemotePolicy(StrEnum):
    remote_global = "remote_global"
    remote_region = "remote_region"
    hybrid = "hybrid"
    onsite = "onsite"
    unknown = "unknown"


class Seniority(StrEnum):
    junior = "junior"
    mid = "mid"
    senior = "senior"
    lead = "lead"
    staff = "staff"
    principal = "principal"


class OpportunityStatus(StrEnum):
    new = "new"
    watching = "watching"
    applied = "applied"
    ignored = "ignored"
    archived = "archived"


class Recommendation(StrEnum):
    ignore = "ignore"
    watch = "watch"
    apply = "apply"
    high_priority = "high_priority"
    reply_now = "reply_now"
    ask_questions_first = "ask_questions_first"
    negotiate = "negotiate"
    prepare_interview = "prepare_interview"


class CompensationPeriod(StrEnum):
    year = "year"
    month = "month"
    hour = "hour"
    day = "day"
    project = "project"


class SourceRelation(StrEnum):
    """How an ``opportunity_source`` row relates to the canonical job (ADR-016 §2)."""

    primary = "primary"
    aggregates = "aggregates"
    repost_of = "repost_of"
    same_as = "same_as"
    mirror = "mirror"
    historical_version_of = "historical_version_of"
    possible_duplicate = "possible_duplicate"


class FieldSource(StrEnum):
    """Where an observed value came from. Declaration order **is** the authority order (ADR-016 §3):
    employer ATS/API > employer page > board structured source > board HTML > aggregator >
    aggregator estimate > archive > search result > recruiter message > LLM inference > manual."""

    employer_api = "employer_api"
    employer_page = "employer_page"
    board_api = "board_api"
    board_page = "board_page"
    aggregator = "aggregator"
    aggregator_estimate = "aggregator_estimate"
    archive = "archive"
    search_result = "search_result"
    recruiter_message = "recruiter_message"
    llm_inference = "llm_inference"
    manual = "manual"


#: Highest authority first; ``AUTHORITY_ORDER.index(src)`` ranks a source.
AUTHORITY_ORDER: tuple[FieldSource, ...] = tuple(FieldSource)

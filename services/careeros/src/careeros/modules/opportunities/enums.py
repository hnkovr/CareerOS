from __future__ import annotations

from enum import StrEnum


class Source(StrEnum):
    linkedin = "linkedin"
    wellfound = "wellfound"
    upwork = "upwork"
    toptal = "toptal"
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

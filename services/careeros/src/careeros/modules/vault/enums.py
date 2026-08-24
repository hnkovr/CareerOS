"""Enumerations shared by the vault schema and (via OpenAPI) the TS clients."""

from __future__ import annotations

from enum import StrEnum


class Platform(StrEnum):
    linkedin = "linkedin"
    wellfound = "wellfound"
    upwork = "upwork"
    toptal = "toptal"
    hh = "hh"
    indeed = "indeed"
    getmatch = "getmatch"
    ats = "ats"
    direct_outreach = "direct_outreach"
    email = "email"
    other = "other"


class ItemStatus(StrEnum):
    draft = "draft"
    verified = "verified"
    retired = "retired"


class SkillTier(StrEnum):
    first_priority = "first_priority"
    additional = "additional"
    target = "target"


class SkillLevel(StrEnum):
    expert = "expert"
    proficient = "proficient"
    working = "working"
    learning = "learning"


class MarketGroup(StrEnum):
    market_core = "market_core"
    strategic_core = "strategic_core"
    oss_builder = "oss_builder"
    agentic = "agentic"


class EmploymentType(StrEnum):
    full_time = "full_time"
    part_time = "part_time"
    contract = "contract"
    b2b = "b2b"
    freelance = "freelance"
    internship = "internship"


class ProjectKind(StrEnum):
    work = "work"
    oss = "oss"
    portfolio = "portfolio"
    consulting = "consulting"


class EvidenceType(StrEnum):
    project = "project"
    link = "link"
    testimonial = "testimonial"
    certification = "certification"
    publication = "publication"
    file = "file"


class AchievementType(StrEnum):
    achievement = "achievement"
    responsibility = "responsibility"
    result = "result"


class WorkMode(StrEnum):
    remote_us = "remote_us"
    remote_eu = "remote_eu"
    eu_b2b = "eu_b2b"
    freelance = "freelance"
    contract = "contract"
    relocation = "relocation"
    onsite_local = "onsite_local"


class TargetMarket(StrEnum):
    remote_us = "remote_us"
    remote_eu = "remote_eu"
    eu_b2b = "eu_b2b"
    freelance = "freelance"
    poland = "poland"
    startup = "startup"
    enterprise = "enterprise"
    consulting = "consulting"


class CVLength(StrEnum):
    one_page = "one_page"
    two_page = "two_page"
    full = "full"


class CVSection(StrEnum):
    summary = "summary"
    experience = "experience"
    projects = "projects"
    skills = "skills"
    education = "education"
    certifications = "certifications"
    publications = "publications"
    languages = "languages"
    offers = "offers"
    testimonials = "testimonials"


class ScoreDimension(StrEnum):
    overall_fit = "overall_fit"
    remote_us_fit = "remote_us_fit"
    eu_fit = "eu_fit"
    poland_fallback_fit = "poland_fallback_fit"
    upwork_fit = "upwork_fit"
    startup_fit = "startup_fit"
    enterprise_fit = "enterprise_fit"
    technical_fit = "technical_fit"
    seniority_fit = "seniority_fit"
    compensation_fit = "compensation_fit"
    learning_roi = "learning_roi"
    strategic_upside = "strategic_upside"
    application_effort = "application_effort"
    risk = "risk"

"""Canonical career data schema — the single source of truth (ADR-009).

JSON Schemas in ``career/schemas/`` and the TS types are generated from these models. Every item has
a stable slug ``id``; references between items are validated in ``validator.py``.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from careeros.modules.vault.enums import (
    AchievementType,
    CVLength,
    CVSection,
    EmploymentType,
    EvidenceType,
    ItemStatus,
    MarketGroup,
    Platform,
    ProjectKind,
    ScoreDimension,
    SkillLevel,
    SkillTier,
    TargetMarket,
    WorkMode,
)

SLUG_RE = r"^[a-z0-9][a-z0-9_-]{1,79}$"
Slug = Annotated[str, Field(pattern=SLUG_RE, description="stable identifier, never renamed")]
VisibilityValue = bool | Literal["optional"]


class VaultModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, use_enum_values=False)


class Evidence(VaultModel):
    type: EvidenceType
    ref: str = Field(description="item id (project/testimonial/certification/publication) or URL")
    note: str | None = None


class Metric(VaultModel):
    name: str
    value: str = Field(description="kept as text to preserve units/precision exactly as verified")
    unit: str | None = None
    baseline: str | None = None
    note: str | None = None


class Visibility(VaultModel):
    linkedin: VisibilityValue = True
    wellfound: VisibilityValue = True
    upwork: VisibilityValue = True
    toptal: VisibilityValue = True
    ats: VisibilityValue = True
    direct_outreach: VisibilityValue = True

    def allows(self, platform: Platform | str) -> bool:
        value = getattr(self, str(platform), True)
        return value is True or value == "optional"


class VaultItem(VaultModel):
    id: Slug
    status: ItemStatus = ItemStatus.draft
    visibility: Visibility = Field(default_factory=Visibility)
    evidence: list[Evidence] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    updated_at: date | None = None
    notes: str | None = Field(default=None, description="private notes, never projected")


# ----------------------------------------------------------------------------- source/


class Location(VaultModel):
    city: str
    country: str = Field(description="ISO 3166-1 alpha-2")
    timezone: str = Field(description="IANA tz, e.g. Asia/Tbilisi")


class Eligibility(VaultModel):
    eu_remote: bool = True
    us_remote_contractor: bool = True
    us_w2: bool = False
    works_as_contractor: bool = True
    relocation_targets: list[str] = Field(default_factory=list, description="ISO country codes")
    work_authorization: list[str] = Field(
        default_factory=list, description="countries with right to work"
    )


class Contacts(VaultModel):
    email: str
    phone: str | None = None
    website: str | None = None
    github: str | None = None
    linkedin: str | None = None
    calendar: str | None = None


class Profile(VaultItem):
    """Singleton: identity and public profile basics."""

    id: Slug = "profile"
    name: str
    headline_baseline: str = Field(description="the default broad headline")
    summary_core: str = Field(description="2-4 sentence factual summary")
    location: Location
    work_modes: list[WorkMode]
    eligibility: Eligibility = Field(default_factory=Eligibility)
    contacts: Contacts
    years_experience_since: int = Field(description="first professional year, e.g. 2013")
    target_roles: list[str] = Field(default_factory=list)


class Role(VaultModel):
    title: str
    start: date
    end: date | None = None
    employment_type: EmploymentType = EmploymentType.full_time
    location: str | None = None
    remote: bool = True

    @model_validator(mode="after")
    def _dates(self) -> Role:
        if self.end is not None and self.end < self.start:
            raise ValueError("role end precedes start")
        return self


class Experience(VaultItem):
    company_id: Slug
    company_name: str
    company_url: str | None = None
    industry: str | None = None
    company_size: str | None = None
    roles: list[Role] = Field(min_length=1)
    summary: str
    responsibilities: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    achievement_ids: list[Slug] = Field(default_factory=list)
    project_ids: list[Slug] = Field(default_factory=list)

    @property
    def start(self) -> date:
        return min(r.start for r in self.roles)

    @property
    def end(self) -> date | None:
        ends = [r.end for r in self.roles]
        return None if any(e is None for e in ends) else max(e for e in ends if e)


class TechnologyTiers(VaultModel):
    first_priority: list[str] = Field(default_factory=list)
    additional: list[str] = Field(default_factory=list)
    target: list[str] = Field(default_factory=list)

    def all(self) -> list[str]:
        return [*self.first_priority, *self.additional, *self.target]


class Achievement(VaultItem):
    company_id: Slug
    role_title: str | None = None
    type: AchievementType = AchievementType.achievement
    title: str = Field(description="short label for UI/provenance")
    facts: list[str] = Field(min_length=1, description="atomic, verifiable sentences")
    metrics: list[Metric] = Field(default_factory=list)
    technologies: TechnologyTiers = Field(default_factory=lambda: TechnologyTiers())
    keywords: list[str] = Field(default_factory=list)
    period: str | None = Field(default=None, description="free text, e.g. 2024-Q3")

    @field_validator("period", mode="before")
    @classmethod
    def _period_str(cls, v: object) -> object:
        return str(v) if isinstance(v, int) else v


class Project(VaultItem):
    name: str
    kind: ProjectKind
    company_id: Slug | None = None
    summary: str
    problem: str | None = None
    solution: str | None = None
    outcome: str | None = None
    technologies: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    period: str | None = None
    public: bool = False
    metrics: list[Metric] = Field(default_factory=list)

    @field_validator("period", mode="before")
    @classmethod
    def _period_str(cls, v: object) -> object:
        return str(v) if isinstance(v, int) else v


class Skill(VaultItem):
    name: str
    category: str = Field(description="e.g. orchestration, warehouse, language, cloud, ai")
    tier: SkillTier
    level: SkillLevel
    years: float | None = None
    last_used: date | None = None
    market_groups: list[MarketGroup] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)


class Education(VaultItem):
    institution: str
    degree: str
    field: str | None = None
    start: date | None = None
    end: date | None = None
    location: str | None = None
    highlights: list[str] = Field(default_factory=list)


class Certification(VaultItem):
    name: str
    issuer: str
    issued: date | None = None
    expires: date | None = None
    credential_id: str | None = None
    url: str | None = None


class Language(VaultItem):
    name: str
    level: str = Field(description="CEFR or 'native'")


class Publication(VaultItem):
    title: str
    kind: str = Field(description="article | talk | course | podcast | repo")
    url: str | None = None
    published: date | None = None
    summary: str | None = None


class Testimonial(VaultItem):
    author: str
    author_role: str | None = None
    company_id: Slug | None = None
    quote: str
    source_url: str | None = None
    given: date | None = None
    permission_to_publish: bool = False


class Link(VaultItem):
    label: str
    url: str
    kind: str = Field(default="other", description="github | website | portfolio | profile | other")


class Offer(VaultItem):
    title: str
    customer_problem: str
    deliverables: list[str] = Field(min_length=1)
    timeline: str
    ideal_client: str
    technologies: list[str] = Field(default_factory=list)
    proof: list[Slug] = Field(default_factory=list, description="project/testimonial ids")
    pricing_strategy: str
    platforms: list[Platform] = Field(default_factory=list)


# ----------------------------------------------------------------------------- positioning/


class Emphasis(VaultModel):
    skills: list[Slug] = Field(default_factory=list)
    achievements: list[Slug] = Field(default_factory=list)
    projects: list[Slug] = Field(default_factory=list)
    offers: list[Slug] = Field(default_factory=list)


class Positioning(VaultItem):
    name: str
    headline: str
    summary: str
    target_markets: list[TargetMarket]
    emphasize: Emphasis = Field(default_factory=lambda: Emphasis())
    deemphasize: list[Slug] = Field(default_factory=list)
    keywords_must: list[str] = Field(default_factory=list)
    keywords_nice: list[str] = Field(default_factory=list)
    tone: str = "concise, factual, senior"


# ----------------------------------------------------------------------------- channels/


class ChannelLimits(VaultModel):
    headline_chars: int | None = None
    about_chars: int | None = None
    bullets_per_role: int = 5
    bullet_chars: int = 220
    skills_max: int | None = None


class ChannelStyle(VaultModel):
    tone: str = "professional"
    first_person: bool = False
    metrics_first: bool = True
    keyword_density: Literal["low", "medium", "high"] = "medium"


class ChannelRules(VaultItem):
    platform: Platform
    name: str
    limits: ChannelLimits = Field(default_factory=ChannelLimits)
    priorities: list[str] = Field(
        default_factory=list, description="what this channel rewards, most important first"
    )
    required_sections: list[str] = Field(default_factory=list)
    style: ChannelStyle = Field(default_factory=ChannelStyle)
    cta: str | None = None
    default_visibility: bool = True
    notes: str | None = None


# ----------------------------------------------------------------------------- cv/variants/


class CVInclude(VaultModel):
    years_back: int | None = None
    companies: list[Slug] = Field(default_factory=list)
    max_bullets_per_role: int | None = None
    max_projects: int | None = None
    skills_tiers: list[SkillTier] = Field(default_factory=lambda: list(SkillTier))


class CVVariant(VaultItem):
    name: str
    positioning_id: Slug
    channel_id: Slug
    locale: str = "en"
    length: CVLength = CVLength.two_page
    sections: list[CVSection] = Field(min_length=1)
    rendercv_theme: str = "classic"
    include: CVInclude = Field(default_factory=lambda: CVInclude())
    description: str | None = None


# ----------------------------------------------------------------------------- scoring/


class DimensionConfig(VaultModel):
    weight: float = Field(ge=0, le=1)
    enabled: bool = True
    description: str | None = None


class EligibilityRules(VaultModel):
    home_country: str
    home_timezone: str
    contractor_ok: bool = True
    us_overlap_hours_min: int = 3
    eu_timezone_ok: bool = True
    relocation_targets: list[str] = Field(default_factory=list)


class CompensationTargets(VaultModel):
    currency: str = "USD"
    min_annual: int
    target_annual: int
    min_hourly: int
    target_hourly: int


class RecommendationThresholds(VaultModel):
    high_priority: int = 80
    apply: int = 65
    watch: int = 45


class ScoringModel(VaultModel):
    version: int = 1
    tech_groups: dict[MarketGroup, list[str]]
    aliases: dict[str, str] = Field(default_factory=dict, description="alias → canonical tech name")
    dimensions: dict[ScoreDimension, DimensionConfig]
    eligibility: EligibilityRules
    compensation: CompensationTargets
    seniority_targets: list[str] = Field(
        default_factory=lambda: ["senior", "lead", "staff", "principal"]
    )
    thresholds: RecommendationThresholds = Field(default_factory=RecommendationThresholds)

    @model_validator(mode="after")
    def _weights(self) -> ScoringModel:
        enabled = {
            k: v
            for k, v in self.dimensions.items()
            if v.enabled and k != ScoreDimension.overall_fit
        }
        total = sum(v.weight for v in enabled.values())
        if enabled and abs(total - 1.0) > 0.01:
            raise ValueError(f"enabled dimension weights must sum to 1.0 (got {total:.3f})")
        return self


# ----------------------------------------------------------------------------- prompts/


class Prompt(VaultModel):
    id: Slug
    version: int = 1
    purpose: str
    area: str = Field(
        description="opportunity | cv | profile | inbox | interview | negotiation | dev-agent"
    )
    inputs: list[str] = Field(default_factory=list, description="names of template variables")
    output_schema: str | None = Field(
        default=None, description="Pydantic model name validated against"
    )
    provider_preferences: list[str] = Field(default_factory=list)
    updated_at: date | None = None
    system: str | None = Field(default=None, description="Jinja2 template for the system prompt")
    template: str = Field(description="Jinja2 template for the user prompt")

    @field_validator("template")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("template is empty")
        return v


# ----------------------------------------------------------------------------- files


class VaultMeta(VaultModel):
    version: int = 1
    owner: str
    default_positioning: Slug
    default_cv_variant: Slug


class ItemsFile[T](VaultModel):
    items: list[T] = Field(default_factory=list)


class ExperienceFile(ItemsFile[Experience]): ...


class AchievementsFile(ItemsFile[Achievement]): ...


class ProjectsFile(ItemsFile[Project]): ...


class SkillsFile(ItemsFile[Skill]): ...


class EducationFile(ItemsFile[Education]): ...


class CertificationsFile(ItemsFile[Certification]): ...


class LanguagesFile(ItemsFile[Language]): ...


class PublicationsFile(ItemsFile[Publication]): ...


class TestimonialsFile(ItemsFile[Testimonial]): ...


class LinksFile(ItemsFile[Link]): ...


class OffersFile(ItemsFile[Offer]): ...


# ----------------------------------------------------------------------------- whole vault


class VaultData(VaultModel):
    meta: VaultMeta
    profile: Profile
    experience: list[Experience] = Field(default_factory=list)
    achievements: list[Achievement] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)
    publications: list[Publication] = Field(default_factory=list)
    testimonials: list[Testimonial] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)
    offers: list[Offer] = Field(default_factory=list)
    positioning: list[Positioning] = Field(default_factory=list)
    channels: list[ChannelRules] = Field(default_factory=list)
    cv_variants: list[CVVariant] = Field(default_factory=list)
    scoring: ScoringModel | None = None
    prompts: list[Prompt] = Field(default_factory=list)

    def by_id[T: VaultItem](self, items: list[T]) -> dict[str, T]:
        return {i.id: i for i in items}

    def fact_ids(self) -> set[str]:
        """IDs that may appear in ``derived_from[]`` of generated content."""
        return {
            *(a.id for a in self.achievements),
            *(p.id for p in self.projects),
            *(e.id for e in self.experience),
            *(s.id for s in self.skills),
            *(c.id for c in self.certifications),
            *(e.id for e in self.education),
            *(p.id for p in self.publications),
            *(t.id for t in self.testimonials),
            *(o.id for o in self.offers),
            self.profile.id,
        }


def is_slug(value: str) -> bool:
    return re.match(SLUG_RE, value) is not None

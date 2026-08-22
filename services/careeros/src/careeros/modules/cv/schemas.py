"""CV document model (our contract; RenderCV is a rendering detail) and API schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from careeros.modules.vault.enums import CVSection

# ----------------------------------------------------------------------------- document


class Bullet(BaseModel):
    text: str
    derived_from: list[str] = Field(min_length=1, description="canonical fact ids")
    verified: bool = True
    user_edited: bool = False
    source: Literal["fact", "ai"] = "fact"


class ExperienceEntryDoc(BaseModel):
    experience_id: str
    company: str
    position: str
    start: date
    end: date | None
    location: str | None = None
    summary: str | None = None
    bullets: list[Bullet] = Field(default_factory=list)


class ProjectEntryDoc(BaseModel):
    project_id: str
    name: str
    period: str | None = None
    summary: str
    bullets: list[Bullet] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)


class SkillGroupDoc(BaseModel):
    label: str
    items: list[str]
    derived_from: list[str]


class EducationEntryDoc(BaseModel):
    education_id: str
    institution: str
    degree: str
    field: str | None
    start: date | None
    end: date | None


class OneLineDoc(BaseModel):
    id: str
    label: str
    details: str


class PublicationDoc(BaseModel):
    id: str
    title: str
    kind: str
    url: str | None
    published: date | None
    summary: str | None


class OfferDoc(BaseModel):
    offer_id: str
    title: str
    customer_problem: str
    deliverables: list[str]
    timeline: str


class TestimonialDoc(BaseModel):
    id: str
    quote: str
    author: str
    author_role: str | None


class CVHeader(BaseModel):
    name: str
    headline: str
    email: str
    phone: str | None = None
    website: str | None = None
    location: str
    github: str | None = None
    linkedin: str | None = None


class Generation(BaseModel):
    provider: str | None = None
    model: str | None = None
    prompt_versions: dict[str, int] = Field(default_factory=dict)
    ai_run_ids: list[uuid.UUID] = Field(default_factory=list)


class CVDocument(BaseModel):
    variant_id: str
    variant_name: str
    positioning_id: str
    channel_id: str
    vault_sha: str | None
    locale: str = "en"
    theme: str = "classic"
    sections: list[CVSection]
    header: CVHeader
    summary: Bullet | None = None
    experience: list[ExperienceEntryDoc] = Field(default_factory=list)
    projects: list[ProjectEntryDoc] = Field(default_factory=list)
    skills: list[SkillGroupDoc] = Field(default_factory=list)
    education: list[EducationEntryDoc] = Field(default_factory=list)
    certifications: list[OneLineDoc] = Field(default_factory=list)
    publications: list[PublicationDoc] = Field(default_factory=list)
    languages: list[OneLineDoc] = Field(default_factory=list)
    offers: list[OfferDoc] = Field(default_factory=list)
    testimonials: list[TestimonialDoc] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list, description="ATS keywords present in the CV")
    jd_keywords: list[str] = Field(
        default_factory=list, description="keywords detected in the JD context"
    )
    generation: Generation = Field(default_factory=Generation)
    warnings: list[str] = Field(default_factory=list)

    def all_bullets(self) -> list[tuple[str, str, Bullet]]:
        """(section, group ref, bullet) for every bullet incl. summary."""
        out: list[tuple[str, str, Bullet]] = []
        if self.summary:
            out.append(("summary", "summary", self.summary))
        for e in self.experience:
            out.extend(("experience", e.experience_id, b) for b in e.bullets)
        for p in self.projects:
            out.extend(("projects", p.project_id, b) for b in p.bullets)
        return out


# ----------------------------------------------------------------------------- AI output schemas


class AIBullet(BaseModel):
    text: str = Field(min_length=10, max_length=400)
    derived_from: list[str] = Field(min_length=1)


class AIBulletGroup(BaseModel):
    company_id: str
    bullets: list[AIBullet]


class CVBulletsOutput(BaseModel):
    groups: list[AIBulletGroup]


class CVSummaryOutput(BaseModel):
    text: str = Field(min_length=40, max_length=1200)
    derived_from: list[str] = Field(min_length=1)


# ----------------------------------------------------------------------------- API


class GenerateCVRequest(BaseModel):
    variant_id: str
    opportunity_id: uuid.UUID | None = None
    jd_text: str | None = Field(default=None, description="paste a JD to tailor selection/wording")
    use_ai: bool = True
    provider: str | None = None
    formats: list[Literal["pdf", "md", "typst", "json"]] = Field(
        default_factory=lambda: ["pdf", "md", "json"]
    )


class CVFiles(BaseModel):
    pdf: str | None = None
    md: str | None = None
    typst: str | None = None
    json_: str | None = Field(default=None, alias="json")

    model_config = {"populate_by_name": True}


class CVArtifactOut(BaseModel):
    id: uuid.UUID
    variant_id: str
    positioning_id: str
    channel_id: str
    opportunity_id: uuid.UUID | None
    vault_sha: str | None
    ai_used: bool
    provider: str | None
    model: str | None
    status: str
    files: CVFiles
    summary_text: str | None
    bullet_count: int
    warnings: list[str]
    created_at: datetime
    document: CVDocument | None = None


class BulletDiff(BaseModel):
    group: str
    text_a: str | None
    text_b: str | None
    derived_from: list[str]


class CVComparison(BaseModel):
    a: str
    b: str
    added: list[BulletDiff]
    removed: list[BulletDiff]
    rewritten: list[BulletDiff]
    unchanged: int
    keywords_only_a: list[str]
    keywords_only_b: list[str]
    sections_a: list[str]
    sections_b: list[str]


class CompareRequest(BaseModel):
    a: uuid.UUID
    b: uuid.UUID


class VariantOut(BaseModel):
    id: str
    name: str
    description: str | None
    positioning_id: str
    channel_id: str
    length: str
    sections: list[str]
    theme: str


def files_to_dict(files: CVFiles) -> dict[str, Any]:
    return files.model_dump(by_alias=True)

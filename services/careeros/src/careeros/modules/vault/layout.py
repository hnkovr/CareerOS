"""Vault directory layout: which file holds which collection, and the model that validates it."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from careeros.modules.vault import schema as s


@dataclass(frozen=True)
class Collection:
    name: str
    path: str  # relative to vault root; a directory (trailing '/') means one item per file
    file_model: type[BaseModel]
    item_model: type[BaseModel] | None  # None for singletons
    singleton: bool = False
    per_file: bool = False

    def resolve(self, root: Path) -> Path:
        return root / self.path


COLLECTIONS: dict[str, Collection] = {
    "meta": Collection("meta", "vault.yaml", s.VaultMeta, None, singleton=True),
    "profile": Collection("profile", "source/profile.yaml", s.Profile, None, singleton=True),
    "experience": Collection(
        "experience", "source/experience.yaml", s.ExperienceFile, s.Experience
    ),
    "achievements": Collection(
        "achievements", "source/achievements.yaml", s.AchievementsFile, s.Achievement
    ),
    "projects": Collection("projects", "source/projects.yaml", s.ProjectsFile, s.Project),
    "skills": Collection("skills", "source/skills.yaml", s.SkillsFile, s.Skill),
    "education": Collection("education", "source/education.yaml", s.EducationFile, s.Education),
    "certifications": Collection(
        "certifications", "source/certifications.yaml", s.CertificationsFile, s.Certification
    ),
    "languages": Collection("languages", "source/languages.yaml", s.LanguagesFile, s.Language),
    "publications": Collection(
        "publications", "source/publications.yaml", s.PublicationsFile, s.Publication
    ),
    "testimonials": Collection(
        "testimonials", "source/testimonials.yaml", s.TestimonialsFile, s.Testimonial
    ),
    "links": Collection("links", "source/links.yaml", s.LinksFile, s.Link),
    "offers": Collection("offers", "source/offers.yaml", s.OffersFile, s.Offer),
    "positioning": Collection(
        "positioning", "positioning/", s.Positioning, s.Positioning, per_file=True
    ),
    "channels": Collection("channels", "channels/", s.ChannelRules, s.ChannelRules, per_file=True),
    "cv_variants": Collection(
        "cv_variants", "cv/variants/", s.CVVariant, s.CVVariant, per_file=True
    ),
    "scoring": Collection("scoring", "scoring/model.yaml", s.ScoringModel, None, singleton=True),
    "prompts": Collection("prompts", "prompts/", s.Prompt, s.Prompt, per_file=True),
}

OPTIONAL_COLLECTIONS = {
    "education",
    "certifications",
    "languages",
    "publications",
    "testimonials",
    "links",
    "offers",
    "scoring",
    "prompts",
}

EDITABLE_COLLECTIONS = [c for c in COLLECTIONS if c not in {"meta"}]


def yaml_files(root: Path, collection: Collection) -> list[Path]:
    target = collection.resolve(root)
    if collection.per_file:
        return sorted(p for p in target.rglob("*.yaml") if p.is_file()) if target.exists() else []
    return [target] if target.exists() else []

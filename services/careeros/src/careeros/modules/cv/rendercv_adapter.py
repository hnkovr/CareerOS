"""RenderCV adapter (ADR-006): ``CVDocument`` → RenderCV input → typst / pdf / markdown.

No other module imports ``rendercv``. Outputs land in ``<out_dir>/`` next to ``input.yaml`` so the
render is reproducible by hand with ``rendercv render input.yaml``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from careeros.core.logging import get_logger
from careeros.modules.cv.schemas import CVDocument, CVFiles
from careeros.modules.vault.enums import CVSection
from careeros.modules.vault.yamlio import dump_yaml

log = get_logger(__name__)

SECTION_TITLES: dict[CVSection, str] = {
    CVSection.summary: "Summary",
    CVSection.experience: "Experience",
    CVSection.projects: "Projects",
    CVSection.skills: "Skills",
    CVSection.education: "Education",
    CVSection.certifications: "Certifications",
    CVSection.publications: "Publications",
    CVSection.languages: "Languages",
    CVSection.offers: "Services",
    CVSection.testimonials: "Testimonials",
}

KNOWN_THEMES = {
    "classic",
    "sb2nov",
    "engineeringresumes",
    "engineeringclassic",
    "moderncv",
    "ember",
    "harvard",
    "ink",
    "opal",
}


class CVRenderError(RuntimeError):
    pass


@dataclass
class RenderOutput:
    files: CVFiles
    input_yaml: Path
    log: str


def _d(value: date | None) -> str | None:
    return value.isoformat() if value else None


def to_rendercv_dict(doc: CVDocument) -> dict[str, Any]:
    h = doc.header
    cv: dict[str, Any] = {
        "name": h.name,
        "headline": h.headline,
        "location": h.location,
        "email": h.email,
    }
    if h.phone:
        cv["phone"] = h.phone
    if h.website:
        cv["website"] = h.website
    socials = []
    if h.linkedin:
        socials.append({"network": "LinkedIn", "username": h.linkedin})
    if h.github:
        socials.append({"network": "GitHub", "username": h.github})
    if socials:
        cv["social_networks"] = socials

    sections: dict[str, list[Any]] = {}
    for section in doc.sections:
        title = SECTION_TITLES[section]
        entries: list[Any] = []
        if section == CVSection.summary and doc.summary:
            entries = [doc.summary.text]
        elif section == CVSection.experience:
            for e in doc.experience:
                entry: dict[str, Any] = {
                    "company": e.company,
                    "position": e.position,
                    "start_date": _d(e.start),
                    "end_date": _d(e.end) or "present",
                    "highlights": [b.text for b in e.bullets],
                }
                if e.location:
                    entry["location"] = e.location
                if e.summary:
                    entry["summary"] = e.summary
                entries.append(entry)
        elif section == CVSection.projects:
            for p in doc.projects:
                entry = {
                    "name": p.name,
                    "summary": p.summary,
                    "highlights": [b.text for b in p.bullets],
                }
                if p.period:
                    entry["date"] = p.period
                entries.append(entry)
        elif section == CVSection.skills:
            entries = [{"label": g.label, "details": ", ".join(g.items)} for g in doc.skills]
        elif section == CVSection.education:
            for ed in doc.education:
                entry = {
                    "institution": ed.institution,
                    "area": ed.field or ed.degree,
                    "degree": ed.degree,
                }
                if ed.start:
                    entry["start_date"] = _d(ed.start)
                if ed.end:
                    entry["end_date"] = _d(ed.end)
                entries.append(entry)
        elif section == CVSection.certifications:
            entries = [{"label": c.label, "details": c.details} for c in doc.certifications]
        elif section == CVSection.publications:
            for p in doc.publications:
                entry = {"title": p.title, "authors": [h.name]}
                if p.published:
                    entry["date"] = _d(p.published)
                if p.url:
                    entry["url"] = p.url
                if p.summary:
                    entry["summary"] = p.summary
                entries.append(entry)
        elif section == CVSection.languages:
            entries = [{"label": lg.label, "details": lg.details} for lg in doc.languages]
        elif section == CVSection.offers:
            entries = [
                {
                    "name": o.title,
                    "summary": o.customer_problem,
                    "highlights": [*o.deliverables, f"Timeline: {o.timeline}"],
                }
                for o in doc.offers
            ]
        elif section == CVSection.testimonials:
            entries = [
                f"“{t.quote}” — {t.author}{f', {t.author_role}' if t.author_role else ''}"
                for t in doc.testimonials
            ]
        if entries:
            sections[title] = entries
    cv["sections"] = sections

    theme = doc.theme if doc.theme in KNOWN_THEMES else "classic"
    return {"cv": cv, "design": {"theme": theme}}


def render(doc: CVDocument, out_dir: Path, formats: list[str]) -> RenderOutput:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = CVFiles()
    log_lines: list[str] = []

    if "json" in formats:
        json_path = out_dir / "cv.json"
        json_path.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
        files.json_ = str(json_path)

    payload = to_rendercv_dict(doc)
    payload["settings"] = {
        "render_command": {
            "output_folder": ".",
            "typst_path": "cv.typ",
            "pdf_path": "cv.pdf",
            "markdown_path": "cv.md",
            "html_path": "cv.html",
            "png_path": "cv.png",
            "dont_generate_html": True,
            "dont_generate_png": True,
            "dont_generate_pdf": "pdf" not in formats,
            "dont_generate_markdown": "md" not in formats,
            "dont_generate_typst": False,
        }
    }
    input_yaml = out_dir / "input.yaml"
    input_yaml.write_text(dump_yaml(payload), encoding="utf-8")

    if not ({"pdf", "md", "typst"} & set(formats)):
        return RenderOutput(files, input_yaml, "json only")

    try:
        from rendercv.renderer.markdown import generate_markdown
        from rendercv.renderer.pdf_png import generate_pdf
        from rendercv.renderer.typst import generate_typst
        from rendercv.schema.rendercv_model_builder import build_rendercv_dictionary_and_model

        _, model = build_rendercv_dictionary_and_model(
            input_yaml.read_text(encoding="utf-8"), input_file_path=input_yaml
        )
        typst_path = generate_typst(model)
        if typst_path:
            files.typst = str(typst_path) if "typst" in formats else None
            log_lines.append(f"typst: {typst_path.name}")
        if "pdf" in formats:
            pdf_path = generate_pdf(model, typst_path)
            if pdf_path:
                files.pdf = str(pdf_path)
                log_lines.append(f"pdf: {pdf_path.name}")
        if "md" in formats:
            md_path = generate_markdown(model)
            if md_path:
                files.md = str(md_path)
                log_lines.append(f"md: {md_path.name}")
    except Exception as exc:
        log.exception("cv.render_failed", out_dir=str(out_dir))
        raise CVRenderError(f"RenderCV failed: {exc}") from exc

    return RenderOutput(files, input_yaml, "; ".join(log_lines))


def document_from_json(path: Path) -> CVDocument:
    return CVDocument.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))

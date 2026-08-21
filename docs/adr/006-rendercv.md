# 006 — RenderCV behind an adapter as the CV rendering engine

* Status: accepted
* Date: 2026-08-20

## Context

We need PDF/Markdown/HTML output from structured CV data with professional, ATS-friendly themes,
without building a typesetting pipeline. RenderCV (Python, YAML-in, Typst/LaTeX-out, several
themes) matches the "CV-as-Code" model and is pip-installable.

## Decision

* Rendering is done by RenderCV invoked as a library through `careeros.modules.cv.rendercv_adapter`.
* Our **own CV model** (`CVDocument`: sections, entries, bullets with `derived_from[]`) is the
  contract; the adapter maps it to RenderCV's input schema. No other module imports RenderCV.
* RenderCV is pinned to a major version; upgrade requires the golden-file tests for all shipped
  variants to pass.
* Outputs per artifact: PDF, Markdown, and our structured JSON (with provenance) — the JSON is the
  canonical artifact; PDF/MD are renderings.
* Themes and RenderCV design settings live in the vault under `rendercv/`.

## Alternatives considered

* **JSON Resume + theme ecosystem** — JS toolchain, themes of uneven quality, weaker PDF control. Supported as an *export format* instead.
* **Custom HTML → PDF (WeasyPrint/Playwright)** — full control but we would own layout and ATS compatibility; revisit only if RenderCV blocks a needed layout.
* **LaTeX templates directly** — RenderCV already wraps this with a schema.

## Consequences

* + Fast path to high-quality PDFs; CV-as-Code ergonomics match the vault.
* − Container must ship RenderCV's rendering dependencies (Typst is bundled; fonts needed); image size grows.
* − Layout expressiveness bounded by RenderCV themes.

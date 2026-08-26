"""Readable text out of HTML / Jina markdown, and the text → posting fallback.

This layer never runs the opportunities text heuristics (``opportunities.parser`` reaches the
``cv`` package, which the purity contract forbids): ``text_to_posting`` carries the readable
text and what the page *stated* (title, company); the ingest re-parses ``raw_text`` with the
vault's vocabulary, so salary/remote/skills heuristics are applied exactly once, downstream.
"""

from __future__ import annotations

import html as html_lib
import re
from datetime import datetime
from html.parser import HTMLParser
from typing import Any

from careeros.modules.opportunities.schemas import OpportunityExtraction
from careeros.modules.platform.schemas import FieldEvidence, JobPosting
from careeros.modules.vault.enums import Platform

_SKIP_TAGS = frozenset(
    {"script", "style", "noscript", "template", "svg", "iframe", "nav", "footer", "select"}
)
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "br",
        "ul",
        "ol",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "tr",
        "table",
        "section",
        "article",
        "blockquote",
        "pre",
        "hr",
        "dd",
        "dt",
        "dl",
        "main",
        "aside",
        "figure",
        "figcaption",
        "summary",
        "details",
        "header",
    }
)
_HTML_HINT = re.compile(r"</?(p|br|ul|ol|li|div|strong|b|em|h[1-6]|span|a)\b", re.IGNORECASE)
_MULTI_NL = re.compile(r"\n{3,}")
_LINE_WS = re.compile(r"[ \t ]+")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "li":
            self.parts.append("\n- ")
        elif tag in ("td", "th"):
            self.parts.append(" ")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag in _BLOCK_TAGS or tag == "li":
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip and data:
            self.parts.append(data)


def looks_like_html(value: str | None) -> bool:
    return bool(value) and bool(_HTML_HINT.search(value or ""))


def html_to_text(html: str) -> str:
    """Visible text with block structure kept as newlines; scripts/styles/nav/footer dropped."""
    if not html:
        return ""
    if "&lt;" in html and "<" not in html:
        html = html_lib.unescape(html)  # an escaped HTML fragment (JSON-LD descriptions)
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    text = "".join(parser.parts)
    lines = [_LINE_WS.sub(" ", ln).strip() for ln in text.splitlines()]
    return _MULTI_NL.sub("\n\n", "\n".join(lines)).strip()


class _MetaExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self._in_title = False
        self._in_h1 = False
        self._title: list[str] = []
        self._h1: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html" and a.get("lang"):
            self.meta.setdefault("lang", a["lang"].strip())
        elif tag == "title":
            self._in_title = True
        elif tag == "h1" and not self._h1:
            self._in_h1 = True
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").strip().lower()
            content = a.get("content", "").strip()
            if key and content:
                self.meta.setdefault(key, content)
        elif tag == "link" and a.get("rel", "").lower() == "canonical" and a.get("href"):
            self.meta.setdefault("canonical", a["href"].strip())

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "h1":
            self._in_h1 = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title.append(data)
        if self._in_h1:
            self._h1.append(data)

    def result(self) -> dict[str, str]:
        out = dict(self.meta)
        title = _LINE_WS.sub(" ", "".join(self._title)).strip()
        h1 = _LINE_WS.sub(" ", "".join(self._h1)).strip()
        if title:
            out["title"] = title
        if h1:
            out["h1"] = h1
        return out


def html_meta(html: str) -> dict[str, str]:
    """``title``, first ``h1``, ``og:*`` / ``twitter:*`` / ``description`` meta, canonical, lang."""
    parser = _MetaExtractor()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        pass
    return parser.result()


_JINA_HEADER = re.compile(r"^(Title|URL Source|Published Time|Markdown Content):\s?(.*)$")


def markdown_body(markdown: str) -> tuple[dict[str, str], str]:
    """Split a Jina Reader response into its header lines and the markdown body."""
    meta: dict[str, str] = {}
    lines = (markdown or "").splitlines()
    i = 0
    while i < len(lines) and i < 12:
        m = _JINA_HEADER.match(lines[i].strip())
        if not m:
            if lines[i].strip() == "" and meta:
                i += 1
                continue
            break
        key, value = m.group(1), m.group(2).strip()
        if key == "Markdown Content":
            i += 1
            break
        meta[{"Title": "title", "URL Source": "url", "Published Time": "published"}[key]] = value
        i += 1
    body = "\n".join(lines[i:]).strip() if meta else (markdown or "").strip()
    return meta, body


_MD_HEADING = re.compile(r"^\s{0,3}#{1,3}\s+(.+?)\s*#*\s*$")


def markdown_title(body: str) -> str | None:
    for line in body.splitlines()[:40]:
        m = _MD_HEADING.match(line)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return None


def text_to_posting(
    text: str,
    platform: Platform,
    url: str | None,
    *,
    title: str | None = None,
    company: str | None = None,
    location: str | None = None,
    fetched_at: datetime | None = None,
    source: str = "text",
    confidence: float = 0.6,
) -> JobPosting:
    """A posting from readable text plus whatever the page stated explicitly.

    ``title`` falls back to the first non-empty line (a page with no title is not a job →
    ``ValueError``). Only stated fields get evidence; the ingest derives the rest from
    ``raw_text`` with the full heuristics and the vault vocabulary.
    """
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    final_title = (title or first_line)[:300].strip()
    if not final_title:
        raise ValueError("no title in text")
    stated = {"title": final_title, "company": company, "location": location}
    evidence = [
        FieldEvidence(
            field=name,
            value=value,
            source=source,
            source_url=url,
            observed_at=fetched_at,
            confidence=confidence,
        )
        for name, value in stated.items()
        if value
    ]
    return JobPosting(
        platform=platform,
        url=url,
        title=final_title,
        company=company,
        location=location,
        raw_text=text,
        extraction=OpportunityExtraction(title=final_title, company=company, location=location),
        field_evidence=evidence,
    )


def as_json_value(value: Any) -> Any:
    """Best-effort JSON shape for evidence values (dates → ISO, models → dicts)."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.isoformat()
    return value

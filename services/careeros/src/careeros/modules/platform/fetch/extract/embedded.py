"""Embedded app state: ``__NEXT_DATA__``, Next.js RSC chunks, Nuxt payloads (spec §5.4).

Best effort by design: every function returns ``None`` / ``[]`` / ``{}`` instead of raising.
Connectors use ``search_keys`` for the extra fields a page keeps only in its app state; the
JSON-LD extractor stays the primary source (drift-tested by key set, not by shape).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

_NEXT_DATA_RE = re.compile(
    r"<script\b[^>]*id\s*=\s*[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL
)
_RSC_RE = re.compile(
    r'self\.__next_f\.push\(\s*\[\s*1\s*,\s*"((?:[^"\\]|\\.)*)"\s*\]\s*\)', re.DOTALL
)
_NUXT_DATA_RE = re.compile(
    r"<script\b[^>]*id\s*=\s*[\"']__NUXT_DATA__[\"'][^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL
)
_NUXT_RE = re.compile(r"window\.__NUXT__\s*=\s*")
_MAX_NODES = 200_000
_MAX_DEPTH = 40


def _loads(raw: str) -> Any | None:
    try:
        return json.loads(raw)
    except ValueError:
        return None


def find_next_data(html: str) -> dict[str, Any] | None:
    """The ``__NEXT_DATA__`` JSON of a Next.js pages-router page, if present and parseable."""
    m = _NEXT_DATA_RE.search(html or "")
    if not m:
        return None
    data = _loads(m.group(1).strip())
    return data if isinstance(data, dict) else None


def _unescape_js(literal: str) -> str:
    try:
        return json.loads(f'"{literal}"')
    except ValueError:
        pass
    out = literal
    for esc, char in (('\\"', '"'), ("\\n", "\n"), ("\\t", "\t"), ("\\/", "/"), ("\\\\", "\\")):
        out = out.replace(esc, char)
    return out


def find_rsc_chunks(html: str) -> list[str]:
    """Unescaped payloads of every ``self.__next_f.push([1, "…"])`` (Next.js app router)."""
    return [_unescape_js(m.group(1)) for m in _RSC_RE.finditer(html or "")]


def _balanced(text: str, start: int) -> str | None:
    """The JSON object/array literal starting at ``text[start]`` (string-aware brace matching)."""
    if start >= len(text) or text[start] not in "{[":
        return None
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def find_nuxt(html: str) -> dict[str, Any] | list[Any] | None:
    """Nuxt state: ``__NUXT_DATA__`` (Nuxt 3, devalue array) or ``window.__NUXT__ = {…}``.

    A Nuxt 2 function-expression payload (``(function(a,b){…}(…))``) is not JSON → ``None``.
    """
    m = _NUXT_DATA_RE.search(html or "")
    if m:
        data = _loads(m.group(1).strip())
        if isinstance(data, dict | list):
            return data
    m = _NUXT_RE.search(html or "")
    if not m:
        return None
    literal = _balanced(html, m.end())
    if literal is None:
        return None
    data = _loads(literal)
    return data if isinstance(data, dict | list) else None


def _walk_collect(obj: Any, wanted: set[str], found: dict[str, Any]) -> None:
    budget = _MAX_NODES
    stack: list[tuple[Any, int]] = [(obj, 0)]
    while stack and budget > 0 and len(found) < len(wanted):
        node, depth = stack.pop()
        budget -= 1
        if depth > _MAX_DEPTH:
            continue
        if isinstance(node, dict):
            for key, value in node.items():
                if key in wanted and key not in found:
                    found[key] = value
                if isinstance(value, dict | list):
                    stack.append((value, depth + 1))
        elif isinstance(node, list):
            for value in node:
                if isinstance(value, dict | list):
                    stack.append((value, depth + 1))


def _search_text(text: str, wanted: Iterable[str], found: dict[str, Any]) -> None:
    decoder = json.JSONDecoder()
    for key in wanted:
        if key in found:
            continue
        pattern = re.compile(r"\"" + re.escape(key) + r"\"\s*:\s*")
        m = pattern.search(text)
        if m:
            try:
                value, _ = decoder.raw_decode(text, m.end())
            except ValueError:
                value = None
            else:
                found[key] = value
                continue
        # still-escaped form inside a JS string literal: \"key\":\"value\"
        escaped = re.compile(
            r'\\"' + re.escape(key) + r'\\"\s*:\s*(\\"(?:[^"\\]|\\.)*?\\"|'
            r"-?\d+(?:\.\d+)?|true|false|null)"
        )
        m = escaped.search(text)
        if not m:
            continue
        raw = m.group(1)
        if raw.startswith('\\"'):
            found[key] = _unescape_js(raw[2:-2])
        else:
            found[key] = _loads(raw)


def search_keys(obj_or_text: Any, keys: Iterable[str]) -> dict[str, Any]:
    """First value for each of ``keys`` anywhere in a JSON tree or a JSON-ish text blob.

    Tolerant lookup for embedded state: dicts/lists are walked breadth-first with a node
    budget; strings (RSC chunks, raw HTML) are scanned for ``"key": <json scalar>`` in both plain
    and still-escaped forms. Missing keys are simply absent. Never raises.
    """
    wanted = {k for k in keys if isinstance(k, str) and k}
    found: dict[str, Any] = {}
    if not wanted:
        return found
    try:
        if isinstance(obj_or_text, str):
            _search_text(obj_or_text, wanted, found)
        elif (
            isinstance(obj_or_text, list)
            and obj_or_text
            and all(isinstance(i, str) for i in obj_or_text)
        ):
            for chunk in obj_or_text:  # RSC chunks: text blobs, not a JSON tree
                _search_text(chunk, wanted, found)
                if len(found) == len(wanted):
                    break
        elif isinstance(obj_or_text, dict | list):
            _walk_collect(obj_or_text, wanted, found)
        elif isinstance(obj_or_text, Iterable):
            for item in obj_or_text:
                if isinstance(item, str):
                    _search_text(item, wanted, found)
                else:
                    _walk_collect(item, wanted, found)
                if len(found) == len(wanted):
                    break
    except Exception:
        return found
    return found

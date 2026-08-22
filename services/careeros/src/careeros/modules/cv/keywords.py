"""Deterministic keyword helpers shared by CV selection and (later) opportunity parsing."""

from __future__ import annotations

import re

from careeros.modules.vault import schema as s

_TOKEN_RE = re.compile(r"[a-z0-9+#]+(?:[./-][a-z0-9+#]+)*")


def normalize(text: str) -> str:
    return " " + " ".join(_TOKEN_RE.findall(text.lower())) + " "


def contains_keyword(haystack_norm: str, keyword: str) -> bool:
    """Whole-token match of a (possibly multi-word) keyword inside a normalized haystack."""
    kw = " ".join(_TOKEN_RE.findall(keyword.lower()))
    return bool(kw) and f" {kw} " in haystack_norm


def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    norm = normalize(text)
    return [k for k in keywords if contains_keyword(norm, k)]


def tech_vocabulary(data: s.VaultData) -> dict[str, str]:
    """alias (lower) → canonical display name, from skills (+aliases) and the scoring model."""
    vocab: dict[str, str] = {}
    for sk in data.skills:
        vocab[sk.name.lower()] = sk.name
        for alias in sk.aliases:
            vocab[alias.lower()] = sk.name
    if data.scoring:
        for techs in data.scoring.tech_groups.values():
            for t in techs:
                vocab.setdefault(t.lower(), t)
        for alias, canon in data.scoring.aliases.items():
            vocab.setdefault(alias.lower(), vocab.get(canon.lower(), canon))
    return vocab


def extract_known_tech(text: str, vocab: dict[str, str]) -> list[str]:
    """Technologies from the vocabulary mentioned in ``text`` (longest aliases first, unique)."""
    norm = normalize(text)
    found: dict[str, None] = {}
    for alias in sorted(vocab, key=len, reverse=True):
        if contains_keyword(norm, alias):
            found.setdefault(vocab[alias], None)
    return list(found)

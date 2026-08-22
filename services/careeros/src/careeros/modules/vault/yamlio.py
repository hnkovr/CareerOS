"""Round-trip YAML I/O (ruamel) so edits preserve comments, ordering and quoting where possible."""

from __future__ import annotations

import io
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq


def _yaml() -> YAML:
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.width = 100
    y.indent(mapping=2, sequence=4, offset=2)
    y.default_flow_style = False
    return y


def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return _yaml().load(fh)


def load_yaml_text(text: str) -> Any:
    return _yaml().load(io.StringIO(text))


def dump_yaml(data: Any) -> str:
    buf = io.StringIO()
    _yaml().dump(data, buf)
    return buf.getvalue()


def write_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_yaml(data), encoding="utf-8")


def to_plain(value: Any) -> Any:
    """Convert Pydantic/enum/date values into ruamel-friendly plain containers."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, date):
        return value
    if isinstance(value, dict):
        out = CommentedMap()
        for k, v in value.items():
            out[str(k)] = to_plain(v)
        return out
    if isinstance(value, list | tuple):
        seq = CommentedSeq()
        seq.extend(to_plain(v) for v in value)
        return seq
    return value


def merge_into(target: CommentedMap, fresh: dict[str, Any]) -> None:
    """Update ``target`` in place to equal ``fresh`` while keeping existing key order/comments."""
    for key in list(target.keys()):
        if key not in fresh:
            del target[key]
    for key, value in fresh.items():
        current = target.get(key)
        if isinstance(current, CommentedMap) and isinstance(value, dict):
            merge_into(current, value)
        else:
            target[key] = to_plain(value)

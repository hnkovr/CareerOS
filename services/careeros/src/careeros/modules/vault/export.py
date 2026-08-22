"""Export JSON Schemas for every vault file (editor validation via yaml-language-server, CI)."""

from __future__ import annotations

import json
from pathlib import Path

from careeros.modules.vault import schema as s
from careeros.modules.vault.layout import COLLECTIONS

SCHEMA_ID_BASE = "https://careeros.dev/schemas"


def export_schemas(out_dir: Path) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    models: dict[str, type] = {name: c.file_model for name, c in COLLECTIONS.items()}
    models["vault-data"] = s.VaultData
    for name, model in models.items():
        doc = model.model_json_schema()
        doc = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"{SCHEMA_ID_BASE}/{name}.schema.json",
            **doc,
        }
        path = out_dir / f"{name}.schema.json"
        path.write_text(json.dumps(doc, indent=2, sort_keys=False) + "\n", encoding="utf-8")
        written.append(path)
    return written

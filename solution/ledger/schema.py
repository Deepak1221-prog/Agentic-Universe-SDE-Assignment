"""Workflow lead schemas and the disposition catalogue (read from assignment-a-data/schemas)."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .normalize import normalize_header

DEFAULT_SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "assignment-a-data" / "schemas"


@dataclass(frozen=True)
class Field:
    name: str
    type: str
    required: bool = False
    pii: bool = False
    reference: bool = False
    default: Optional[str] = None
    values: tuple = ()
    aliases: tuple = ()

    @property
    def header_keys(self) -> set:
        return {normalize_header(self.name)} | {normalize_header(a) for a in self.aliases}


@dataclass(frozen=True)
class WorkflowSchema:
    workflow_id: str
    fields: tuple

    @property
    def phone_field(self) -> Field:
        return next(f for f in self.fields if f.type == "phone")

    @property
    def reference_field(self) -> Field:
        return next(f for f in self.fields if f.reference)


@dataclass(frozen=True)
class Disposition:
    workflow_id: str
    code: str
    label: str
    is_terminal: bool
    is_success: bool


def load_schemas(schemas_dir: Path) -> dict:
    out = {}
    for p in sorted(Path(schemas_dir).glob("*.json")):
        raw = json.loads(p.read_text(encoding="utf-8"))
        fields = tuple(
            Field(name=f["name"], type=f["type"], required=bool(f.get("required", False)),
                  pii=bool(f.get("pii", False)), reference=bool(f.get("reference", False)),
                  default=f.get("default"), values=tuple(f.get("values", ())),
                  aliases=tuple(f.get("aliases", ())))
            for f in raw["fields"])
        schema = WorkflowSchema(raw["workflow_id"], fields)
        # Fail early on schemas the pipeline cannot work with.
        if sum(f.type == "phone" for f in fields) != 1:
            raise ValueError(f"{p.name}: exactly one phone field is required (it is the lead identity)")
        if sum(f.reference for f in fields) != 1:
            raise ValueError(f"{p.name}: exactly one field must be marked reference")
        out[schema.workflow_id] = schema
    return out


def load_dispositions(schemas_dir: Path) -> list:
    with open(Path(schemas_dir) / "dispositions.csv", newline="", encoding="utf-8-sig") as f:
        return [Disposition(r["workflow_id"].strip(), r["code"].strip(), r["label"].strip(),
                            r["is_terminal"].strip().lower() == "true",
                            r["is_success"].strip().lower() == "true")
                for r in csv.DictReader(f)]

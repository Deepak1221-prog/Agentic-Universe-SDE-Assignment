"""Lead CSV ingestion: header mapping, validation, rejection, merge of duplicates."""
from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .normalize import (normalize_header, normalize_phone, parse_date, parse_decimal,
                        parse_enum)
from .pii import PiiVault, last4
from .schema import Field, WorkflowSchema


@dataclass
class RowResult:
    """Outcome of validating a single lead row (pure - no DB access)."""
    reject_reason: Optional[str] = None
    values: dict = field(default_factory=dict)   # field name -> normalised value or None
    warnings: int = 0


def map_headers(header: list, schema: WorkflowSchema) -> dict:
    """field name -> column index. First matching column (left to right) wins."""
    mapping: dict = {}
    for idx, h in enumerate(header):
        key = normalize_header(h)
        for f in schema.fields:
            if f.name not in mapping and key in f.header_keys:
                mapping[f.name] = idx
                break
    return mapping


def parse_value(f: Field, raw: str) -> Optional[str]:
    """Normalise a non-empty trimmed value; None means 'present but invalid'."""
    if f.type == "string":
        return raw
    if f.type == "phone":
        return normalize_phone(raw)
    if f.type == "date":
        return parse_date(raw)
    if f.type == "decimal":
        return parse_decimal(raw)
    if f.type == "enum":
        return parse_enum(raw, list(f.values))
    raise ValueError(f"unsupported field type {f.type!r}")


def validate_row(cells: list, mapping: dict, schema: WorkflowSchema) -> RowResult:
    """Walk the schema in order; the FIRST failure is the row's single rejection reason."""
    res = RowResult()
    for f in schema.fields:
        idx = mapping.get(f.name)
        raw = cells[idx].strip() if idx is not None and idx < len(cells) else ""
        if raw == "":
            if f.required:
                res.reject_reason = f"missing_required:{f.name}"
                return res
            res.values[f.name] = None
            continue
        value = parse_value(f, raw)
        if value is None:
            if f.type == "phone":
                res.reject_reason = "invalid_phone"
                return res
            if f.required:  # not exercised by the shipped schemas; documented assumption
                res.reject_reason = f"invalid_{f.name}"
                return res
            res.warnings += 1          # optional + invalid -> null + one warning
        res.values[f.name] = value
    return res


def read_rows(path: Path):
    """Yield (row_number, cells) for non-blank data rows. Header is consumed separately."""
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        rows = []
        n = 0
        for cells in reader:
            if all(c.strip() == "" for c in cells):   # blank row: skipped and NOT counted
                continue
            n += 1
            rows.append((n, cells))
    return header, rows


def ingest_lead_file(conn: sqlite3.Connection, vault: PiiVault, schema: WorkflowSchema,
                     path: Path, file_id: int) -> dict:
    """Ingest one batch file inside the caller's transaction. Returns the file's counters."""
    batch_id = path.stem
    stats = {"rows_total": 0, "rows_rejected": 0, "duplicates_merged": 0, "field_warnings": 0}
    header, rows = read_rows(path)
    if header is None:
        return stats
    mapping = map_headers(header, schema)
    phone_f = schema.phone_field
    ref_f = schema.reference_field
    pii_names = [f.name for f in schema.fields if f.pii]
    plain_names = [f.name for f in schema.fields if not f.pii and not f.reference]
    defaults = {f.name: f.default for f in schema.fields if f.default is not None}

    for row_number, cells in rows:
        stats["rows_total"] += 1
        res = validate_row(cells, mapping, schema)
        if res.reject_reason:
            stats["rows_rejected"] += 1
            conn.execute(
                "INSERT INTO rejections(file_id, workflow_id, batch_id, row_number, reason) VALUES (?,?,?,?,?)",
                (file_id, schema.workflow_id, batch_id, row_number, res.reject_reason))
            continue
        stats["field_warnings"] += res.warnings
        phone = res.values[phone_f.name]
        phash = vault.phone_hash(phone)
        existing = conn.execute(
            "SELECT lead_id, pii_enc, attrs, external_ref FROM leads WHERE workflow_id=? AND phone_hash=?",
            (schema.workflow_id, phash)).fetchone()
        if existing is None:
            attrs = {n: res.values.get(n) for n in plain_names}
            for n, d in defaults.items():           # defaults fill fields that are still null
                if attrs.get(n) is None:
                    attrs[n] = d
            pii = {n: res.values.get(n) for n in pii_names}
            conn.execute(
                "INSERT INTO leads(workflow_id, phone_hash, phone_last4, external_ref, batch_id, pii_enc,"
                " attrs, first_file_id, first_row) VALUES (?,?,?,?,?,?,?,?,?)",
                (schema.workflow_id, phash, last4(phone), res.values[ref_f.name], batch_id,
                 vault.encrypt(pii), json.dumps(attrs, sort_keys=True), file_id, row_number))
        else:
            # Later valid row for the same identity: last write wins, but only for non-null values.
            stats["duplicates_merged"] += 1
            attrs = json.loads(existing["attrs"])
            for n in plain_names:
                if res.values.get(n) is not None:
                    attrs[n] = res.values[n]
            pii = vault.decrypt(existing["pii_enc"])
            for n in pii_names:
                if res.values.get(n) is not None:
                    pii[n] = res.values[n]
            ref = res.values[ref_f.name] if res.values.get(ref_f.name) is not None else existing["external_ref"]
            conn.execute(
                "UPDATE leads SET attrs=?, pii_enc=?, external_ref=?, merge_count=merge_count+1 WHERE lead_id=?",
                (json.dumps(attrs, sort_keys=True), vault.encrypt(pii), ref, existing["lead_id"]))
    return stats

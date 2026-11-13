"""Shared test helpers: an isolated DB + vault per test, and tiny file builders."""
from __future__ import annotations

import json
from pathlib import Path

from ledger import db
from ledger.ingest import ingest_path
from ledger.pii import PiiVault
from ledger.schema import DEFAULT_SCHEMAS_DIR, load_dispositions, load_schemas

SCHEMAS = load_schemas(DEFAULT_SCHEMAS_DIR)


def open_env(tmp_path: Path, name: str = "t.sqlite"):
    path = str(tmp_path / name)
    conn = db.connect(path)
    vault = PiiVault(b"test-master-key")
    db.check_key(conn, vault.key_check())
    db.sync_dispositions(conn, load_dispositions(DEFAULT_SCHEMAS_DIR))
    return conn, vault


def write_drop(root: Path, name: str, leads: dict | None = None, events: list | None = None) -> Path:
    """leads = {(workflow, 'batch_001'): csv_text}; events = list of dicts/strings (one line each)."""
    drop = root / name
    for (wf, batch), text in (leads or {}).items():
        p = drop / "leads" / wf / f"{batch}.csv"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    if events is not None:
        p = drop / "events" / f"events_{name}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(e if isinstance(e, str) else json.dumps(e) for e in events) + "\n",
                     encoding="utf-8")
    return drop


def ingest(conn, vault, path: Path):
    ingest_path(conn, vault, SCHEMAS, path, log=lambda _m: None)


def ev(eid, etype, call, ts, phone=None, wf="motor_renewal", **payload):
    e = {"event_id": eid, "event_type": etype, "workflow_id": wf, "call_id": call,
         "occurred_at": ts, "payload": payload}
    if phone is not None:
        e["phone"] = phone
    return e


MOTOR_HEADER = "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"

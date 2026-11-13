"""Event (JSON Lines) ingestion into the append-only ledger."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .normalize import normalize_phone, parse_timestamp, to_utc_iso
from .pii import PiiVault


def _clean_str(v):
    if v is None:
        return None
    s = str(v)
    return s if s.strip() else None


def ingest_event_file(conn: sqlite3.Connection, vault: PiiVault, path: Path, file_id: int) -> dict:
    """Spec order per line: malformed -> duplicate -> (orphan / unknown disposition are derived
    later, from the ledger, so they always reflect everything ingested so far)."""
    stats = {"events_total": 0, "malformed_events": 0, "duplicate_events": 0}
    insert = ("INSERT OR IGNORE INTO events(event_id, event_type, workflow_id, call_id, occurred_at,"
              " occurred_epoch, phone_hash, disposition_code, payload, file_id, line_no)"
              " VALUES (?,?,?,?,?,?,?,?,?,?,?)")
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue                      # empty lines are not events and are not counted
            stats["events_total"] += 1
            try:
                obj = json.loads(line)
            except ValueError:
                stats["malformed_events"] += 1
                continue
            event_id = obj.get("event_id") if isinstance(obj, dict) else None
            if not isinstance(event_id, str) or not event_id.strip():
                stats["malformed_events"] += 1
                continue
            ts = parse_timestamp(obj.get("occurred_at"))
            if ts is None:                    # assumption: no usable timestamp => cannot be placed in time
                stats["malformed_events"] += 1
                continue
            event_type = str(obj.get("event_type") or "")
            payload = obj.get("payload")
            code = None
            if event_type.startswith("disposition.") and isinstance(payload, dict):
                c = payload.get("disposition_code")
                code = c if isinstance(c, str) else None
            raw_phone = obj.get("phone")
            canon = normalize_phone(raw_phone) if raw_phone is not None else None
            cur = conn.execute(insert, (
                event_id, event_type, _clean_str(obj.get("workflow_id")), _clean_str(obj.get("call_id")),
                to_utc_iso(ts), ts.timestamp(), vault.phone_hash(canon) if canon else None, code,
                json.dumps(payload if payload is not None else {}, sort_keys=True, ensure_ascii=False),
                file_id, line_no))
            if cur.rowcount == 0:             # event_id already in the ledger (this or an earlier file)
                stats["duplicate_events"] += 1
    return stats

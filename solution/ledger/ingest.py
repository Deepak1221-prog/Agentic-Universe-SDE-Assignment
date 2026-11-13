"""Drop discovery and the idempotent, file-at-a-time ingest driver."""
from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .ingest_events import ingest_event_file
from .ingest_leads import ingest_lead_file
from .linking import link_events
from .pii import PiiVault


def _natural_key(p: Path):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p.name)]


def discover_drops(path: Path) -> list:
    """`path` is either one drop (has leads/ or events/) or a folder of drops (drop_1, drop_2...)."""
    path = Path(path)
    if not path.is_dir():
        raise SystemExit(f"not a directory: {path}")
    if (path / "leads").is_dir() or (path / "events").is_dir():
        return [path]
    drops = sorted((d for d in path.iterdir()
                    if d.is_dir() and ((d / "leads").is_dir() or (d / "events").is_dir())), key=_natural_key)
    if not drops:
        raise SystemExit(f"no drop found under {path} (expected leads/ and/or events/)")
    return drops


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ingest_file(conn: sqlite3.Connection, kind: str, scope: str, path: Path, drop_name: str,
                 work: Callable[[int], dict], log: Callable[[str], None]) -> None:
    """Run `work(file_id)` atomically. A file whose (kind, scope, name, content hash) is already
    recorded is skipped - that is what makes re-running ingest a no-op. Counters are written in
    the same transaction as the data, so a crash mid-file leaves nothing behind."""
    digest = _sha256(path)
    label = f"{scope + '/' if scope else ''}{path.name}"
    if conn.execute("SELECT 1 FROM ingested_files WHERE kind=? AND scope=? AND file_name=? AND sha256=?",
                    (kind, scope, path.name, digest)).fetchone():
        log(f"  skip   {kind:<6} {label} (already ingested)")
        return
    if conn.execute("SELECT 1 FROM ingested_files WHERE kind=? AND scope=? AND file_name=?",
                    (kind, scope, path.name)).fetchone():
        log(f"  WARN   {label}: same name was ingested before with DIFFERENT content; ingesting as new")
    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            "INSERT INTO ingested_files(kind, scope, file_name, sha256, drop_name, ingested_at)"
            " VALUES (?,?,?,?,?,?)",
            (kind, scope, path.name, digest, drop_name, datetime.now(timezone.utc).isoformat()))
        file_id = cur.lastrowid
        stats = work(file_id)
        sets = ", ".join(f"{k}=?" for k in stats)
        conn.execute(f"UPDATE ingested_files SET {sets} WHERE file_id=?", (*stats.values(), file_id))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    log(f"  ingest {kind:<6} {label}: " + ", ".join(f"{k}={v}" for k, v in stats.items()))


def ingest_path(conn: sqlite3.Connection, vault: PiiVault, schemas: dict, path: Path,
                log: Callable[[str], None] = print) -> None:
    for drop in discover_drops(path):
        log(f"drop {drop.name}")
        # 1) leads: files in filename order, rows in file order
        leads_dir = drop / "leads"
        lead_files = []
        if leads_dir.is_dir():
            for wf_dir in sorted(p for p in leads_dir.iterdir() if p.is_dir()):
                if wf_dir.name not in schemas:
                    log(f"  WARN   no schema for workflow {wf_dir.name!r}; skipping its lead files")
                    continue
                lead_files += [(wf_dir.name, f) for f in sorted(wf_dir.glob("*.csv"))]
        for wf, f in sorted(lead_files, key=lambda t: (t[1].name, t[0])):
            _ingest_file(conn, "leads", wf, f, drop.name,
                         lambda fid, wf=wf, f=f: ingest_lead_file(conn, vault, schemas[wf], f, fid), log)
        # 2) events
        events_dir = drop / "events"
        if events_dir.is_dir():
            for f in sorted(events_dir.glob("*.jsonl")):
                _ingest_file(conn, "events", "", f, drop.name,
                             lambda fid, f=f: ingest_event_file(conn, vault, f, fid), log)
    link_events(conn)

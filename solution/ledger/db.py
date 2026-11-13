"""SQLite schema and connection helper.

Design in one paragraph
-----------------------
`events` is the append-only interaction ledger: rows are only ever INSERTed (triggers abort
UPDATE/DELETE). Everything we *derive* (which lead an event belongs to, calls, buckets,
effective dispositions) is computed from it, never written back into it. The only mutable
bookkeeping is `event_link` (event -> lead), which is insert-only and can be rebuilt from
`events` + `leads` at any time. Because linking is re-evaluated after every ingest, an event
that was an orphan after drop_1 is linked as soon as its lead arrives in drop_2.
"""
from __future__ import annotations

import sqlite3

SCHEMA_VERSION = "1"

DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- One row per input file ever ingested. Makes ingest idempotent (same file => skipped) and
-- stores the per-file counters that feed data_quality.json.
CREATE TABLE IF NOT EXISTS ingested_files (
    file_id            INTEGER PRIMARY KEY,
    kind               TEXT NOT NULL CHECK (kind IN ('leads', 'events')),
    scope              TEXT NOT NULL,            -- workflow_id for leads, '' for events
    file_name          TEXT NOT NULL,
    sha256             TEXT NOT NULL,
    drop_name          TEXT NOT NULL,
    ingested_at        TEXT NOT NULL,
    rows_total         INTEGER NOT NULL DEFAULT 0,   -- leads: non-blank data rows
    rows_rejected      INTEGER NOT NULL DEFAULT 0,
    duplicates_merged  INTEGER NOT NULL DEFAULT 0,
    field_warnings     INTEGER NOT NULL DEFAULT 0,
    events_total       INTEGER NOT NULL DEFAULT 0,   -- events: non-empty lines
    malformed_events   INTEGER NOT NULL DEFAULT 0,
    duplicate_events   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (kind, scope, file_name, sha256)
);

-- Dispositions catalogue (from dispositions.csv)
CREATE TABLE IF NOT EXISTS dispositions (
    workflow_id TEXT NOT NULL,
    code        TEXT NOT NULL,
    label       TEXT NOT NULL,
    is_terminal INTEGER NOT NULL CHECK (is_terminal IN (0, 1)),
    is_success  INTEGER NOT NULL CHECK (is_success IN (0, 1)),
    PRIMARY KEY (workflow_id, code)
);

-- A lead is identified by (workflow_id, phone). We never store the phone in plaintext:
-- phone_hash is an HMAC (for matching), pii_enc holds the encrypted name/phone,
-- phone_last4 exists only so reports can print the masked number.
CREATE TABLE IF NOT EXISTS leads (
    lead_id       INTEGER PRIMARY KEY,
    workflow_id   TEXT NOT NULL,
    phone_hash    BLOB NOT NULL,
    phone_last4   TEXT NOT NULL CHECK (length(phone_last4) = 4),
    external_ref  TEXT NOT NULL,
    batch_id      TEXT NOT NULL,                 -- file stem of the FIRST valid row; never changes
    pii_enc       BLOB NOT NULL,
    attrs         TEXT NOT NULL,                 -- JSON of the non-PII schema fields
    merge_count   INTEGER NOT NULL DEFAULT 0,
    first_file_id INTEGER NOT NULL REFERENCES ingested_files(file_id),
    first_row     INTEGER NOT NULL,
    UNIQUE (workflow_id, phone_hash)
);

-- Rejected lead rows. Deliberately contains no PII.
CREATE TABLE IF NOT EXISTS rejections (
    rejection_id INTEGER PRIMARY KEY,
    file_id      INTEGER NOT NULL REFERENCES ingested_files(file_id),
    workflow_id  TEXT NOT NULL,
    batch_id     TEXT NOT NULL,
    row_number   INTEGER NOT NULL,
    reason       TEXT NOT NULL
);

-- The append-only interaction ledger. phone_hash is NULL when the event had no valid phone.
CREATE TABLE IF NOT EXISTS events (
    event_id         TEXT PRIMARY KEY,
    event_type       TEXT NOT NULL,
    workflow_id      TEXT,
    call_id          TEXT,
    occurred_at      TEXT NOT NULL,              -- normalised to UTC ISO-8601
    occurred_epoch   REAL NOT NULL,
    phone_hash       BLOB,
    disposition_code TEXT,
    payload          TEXT NOT NULL,
    file_id          INTEGER NOT NULL REFERENCES ingested_files(file_id),
    line_no          INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_call ON events(call_id);

CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events is an append-only ledger'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events is an append-only ledger'); END;

-- Derived & insert-only: which lead an event belongs to. Events with no row here are orphans
-- (quarantined: kept in the ledger, excluded from every report).
CREATE TABLE IF NOT EXISTS event_link (
    event_id TEXT PRIMARY KEY REFERENCES events(event_id),
    lead_id  INTEGER NOT NULL REFERENCES leads(lead_id)
);
CREATE INDEX IF NOT EXISTS idx_link_lead ON event_link(lead_id);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None)  # we manage transactions explicitly
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(DDL)
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
    return conn


def sync_dispositions(conn: sqlite3.Connection, dispositions) -> None:
    conn.execute("BEGIN")
    conn.executemany(
        "INSERT OR REPLACE INTO dispositions VALUES (?,?,?,?,?)",
        [(d.workflow_id, d.code, d.label, int(d.is_terminal), int(d.is_success)) for d in dispositions])
    conn.execute("COMMIT")


def check_key(conn: sqlite3.Connection, key_check: str) -> None:
    """Remember the PII key fingerprint on first use; refuse to run with a different key."""
    row = conn.execute("SELECT value FROM meta WHERE key='pii_key_check'").fetchone()
    if row is None:
        conn.execute("INSERT INTO meta(key, value) VALUES ('pii_key_check', ?)", (key_check,))
    elif row["value"] != key_check:
        raise SystemExit("PII key does not match this database (wrong key file / LEDGER_PII_KEY?). "
                         "Refusing to continue: linking would silently fail.")

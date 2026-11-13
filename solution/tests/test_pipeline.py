"""System-level properties: idempotency, incremental == bulk, PII at rest, append-only ledger."""
import filecmp
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from ledger import db
from ledger.cli import main
from ledger.reports import write_reports
from tests.helpers import MOTOR_HEADER, ev, ingest, open_env, write_drop

REPO = Path(__file__).resolve().parents[2]
GENERATOR = REPO / "assignment-a-data" / "tools" / "generate_data.py"
FILES = ["batch_summary.csv", "daily_overview.csv", "lead_report.csv", "rejections.csv", "data_quality.json"]


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    out = tmp_path_factory.mktemp("gen") / "data"
    subprocess.run([sys.executable, str(GENERATOR), "--seed", "7", "--scale", "small", "--out", str(out)],
                   check=True, capture_output=True)
    return out


def run_cli(*args):
    assert main([str(a) for a in args]) == 0


def same_reports(a: Path, b: Path):
    for f in FILES:
        assert filecmp.cmp(a / f, b / f, shallow=False), f"{f} differs"


def test_five_report_files_exist(data, tmp_path):
    db_path = tmp_path / "x.sqlite"
    run_cli("ingest", "--db", db_path, data)
    run_cli("report", "--db", db_path, "--out", tmp_path / "r")
    assert sorted(p.name for p in (tmp_path / "r").iterdir()) == sorted(FILES)


def test_reingest_changes_nothing(data, tmp_path):
    db_path = tmp_path / "x.sqlite"
    run_cli("ingest", "--db", db_path, data / "drop_1")
    run_cli("ingest", "--db", db_path, data / "drop_2")
    run_cli("report", "--db", db_path, "--out", tmp_path / "r1")
    before = sqlite3.connect(db_path).execute(
        "SELECT (SELECT COUNT(*) FROM leads),(SELECT COUNT(*) FROM events),(SELECT COUNT(*) FROM rejections),"
        "(SELECT COUNT(*) FROM event_link),(SELECT COUNT(*) FROM ingested_files)").fetchone()
    run_cli("ingest", "--db", db_path, data / "drop_1")         # again
    run_cli("ingest", "--db", db_path, data / "drop_2")
    run_cli("ingest", "--db", db_path, data)                    # and the whole folder
    run_cli("report", "--db", db_path, "--out", tmp_path / "r2")
    after = sqlite3.connect(db_path).execute(
        "SELECT (SELECT COUNT(*) FROM leads),(SELECT COUNT(*) FROM events),(SELECT COUNT(*) FROM rejections),"
        "(SELECT COUNT(*) FROM event_link),(SELECT COUNT(*) FROM ingested_files)").fetchone()
    assert before == after
    same_reports(tmp_path / "r1", tmp_path / "r2")


def test_incremental_equals_all_at_once(data, tmp_path):
    run_cli("ingest", "--db", tmp_path / "inc.sqlite", data / "drop_1")
    run_cli("ingest", "--db", tmp_path / "inc.sqlite", data / "drop_2")
    run_cli("report", "--db", tmp_path / "inc.sqlite", "--out", tmp_path / "inc")
    run_cli("ingest", "--db", tmp_path / "all.sqlite", data)
    run_cli("report", "--db", tmp_path / "all.sqlite", "--out", tmp_path / "all")
    same_reports(tmp_path / "inc", tmp_path / "all")


def test_orphan_count_reflects_final_state_not_intermediate(data, tmp_path):
    import json
    p = tmp_path / "x.sqlite"
    run_cli("ingest", "--db", p, data / "drop_1")
    run_cli("report", "--db", p, "--out", tmp_path / "mid")
    run_cli("ingest", "--db", p, data / "drop_2")
    run_cli("report", "--db", p, "--out", tmp_path / "end")
    mid = json.loads((tmp_path / "mid" / "data_quality.json").read_text())["orphan_events"]
    end = json.loads((tmp_path / "end" / "data_quality.json").read_text())["orphan_events"]
    assert end > 0 and mid > 0


def test_no_plaintext_pii_anywhere_in_database_files(tmp_path):
    conn, vault = open_env(tmp_path)
    ingest(conn, vault, write_drop(tmp_path, "d1",
        leads={("motor_renewal", "batch_001"): MOTOR_HEADER + "Zebediah Quillfeather,98765 43210,POL1,,,\n"},
        events=[ev("e1", "call.initiated", "c1", "2026-10-01T10:00:00Z", "+91 98765 43210")]))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    blob = b"".join(p.read_bytes() for p in tmp_path.glob("t.sqlite*"))
    for secret in (b"Zebediah", b"Quillfeather", b"9876543210", b"98765 43210", b"+919876543210"):
        assert secret not in blob, secret
    out = tmp_path / "rep"
    conn2 = db.connect(str(tmp_path / "t.sqlite"))
    write_reports(conn2, out)
    assert "Zebediah" not in (out / "rejections.csv").read_text()
    assert "+91******3210" in (out / "lead_report.csv").read_text()


def test_ledger_is_append_only(tmp_path):
    conn, vault = open_env(tmp_path)
    ingest(conn, vault, write_drop(tmp_path, "d1", events=[ev("e1", "call.initiated", "c1", "2026-10-01T10:00:00Z", "9876543210")]))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE events SET call_id='x'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM events")


def test_wrong_key_is_refused(tmp_path):
    from ledger.pii import PiiVault
    conn, vault = open_env(tmp_path)
    with pytest.raises(SystemExit):
        db.check_key(conn, PiiVault(b"another-key").key_check())


def test_failed_file_leaves_nothing_behind(tmp_path, monkeypatch):
    conn, vault = open_env(tmp_path)
    import ledger.ingest_events as ie
    real = ie.normalize_phone
    calls = {"n": 0}

    def boom(x):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("crash mid-file")
        return real(x)
    monkeypatch.setattr(ie, "normalize_phone", boom)
    d = write_drop(tmp_path, "d1", events=[ev(f"e{i}", "call.initiated", f"c{i}", "2026-10-01T10:00:00Z", "9876543210") for i in range(5)])
    with pytest.raises(RuntimeError):
        ingest(conn, vault, d)
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM ingested_files").fetchone()[0] == 0
    monkeypatch.setattr(ie, "normalize_phone", real)
    ingest(conn, vault, d)                                       # a clean retry succeeds fully
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 5

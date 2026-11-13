"""Build the five report files from the ledger.

Everything is derived from `events` + `event_link` + `leads` on every run, so reports always
reflect everything ingested so far, whatever order the drops arrived in.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from itertools import groupby
from pathlib import Path

from .linking import link_events
from .normalize import epoch_to_ist
from .rules import (COMPLETED, IN_PROGRESS_DIALED, IN_PROGRESS_TELEPHONY_FAILED, YET_TO_REACH_OUT,
                    Call, DispositionEvent, effective_disposition, lead_bucket)

BUCKETS = (YET_TO_REACH_OUT, IN_PROGRESS_TELEPHONY_FAILED, IN_PROGRESS_DIALED, COMPLETED)


def _load_catalogues(conn: sqlite3.Connection) -> dict:
    cats: dict = defaultdict(dict)
    for r in conn.execute("SELECT workflow_id, code, is_terminal, is_success FROM dispositions"):
        cats[r["workflow_id"]][r["code"]] = (bool(r["is_terminal"]), bool(r["is_success"]))
    return cats


def _build_call(call_id: str, rows: list, catalogue: dict) -> "Call | None":
    initiated = [r["occurred_epoch"] for r in rows if r["event_type"] == "call.initiated"]
    if not initiated:                      # a call only exists if its call.initiated is present
        return None
    disp_events = [DispositionEvent(r["event_type"], r["occurred_epoch"], r["event_id"], r["disposition_code"])
                   for r in rows if r["event_type"] in ("disposition.set", "disposition.override")]
    code = effective_disposition(disp_events, catalogue)
    terminal, success = catalogue.get(code, (False, False)) if code else (False, False)
    return Call(call_id=call_id, initiated_epoch=min(initiated),
                connected=any(r["event_type"] == "call.connected" for r in rows),
                telephony_failed=any(r["event_type"] == "call.telephony_failed" for r in rows),
                disposition=code, is_terminal=terminal, is_success=success)


def compute(conn: sqlite3.Connection) -> dict:
    link_events(conn)
    cats = _load_catalogues(conn)
    leads = {r["lead_id"]: r for r in conn.execute(
        "SELECT lead_id, workflow_id, external_ref, batch_id, phone_last4 FROM leads")}

    # ---- derive calls per lead from linked events -------------------------------------------
    calls_by_lead: dict = defaultdict(list)
    unknown_disposition = 0
    cur = conn.execute(
        "SELECT lk.lead_id, e.call_id, e.event_type, e.occurred_epoch, e.event_id, e.disposition_code"
        " FROM events e JOIN event_link lk ON lk.event_id = e.event_id"
        " ORDER BY lk.lead_id, e.call_id")
    for lead_id, lead_rows in groupby(cur, key=lambda r: r["lead_id"]):
        catalogue = cats.get(leads[lead_id]["workflow_id"], {})
        for call_id, call_rows in groupby(lead_rows, key=lambda r: r["call_id"]):
            rows = list(call_rows)
            unknown_disposition += sum(
                1 for r in rows if r["event_type"].startswith("disposition.") and r["disposition_code"] not in catalogue)
            if call_id is None:
                continue
            call = _build_call(call_id, rows, catalogue)
            if call:
                calls_by_lead[lead_id].append(call)

    # ---- per lead ---------------------------------------------------------------------------
    lead_rows_out, batch_counts, daily = [], defaultdict(Counter), defaultdict(lambda: [0, 0, set()])
    for lead_id, lead in leads.items():
        wf = lead["workflow_id"]
        calls = sorted(calls_by_lead.get(lead_id, []), key=lambda c: (c.initiated_epoch, c.call_id))
        bucket = lead_bucket(calls)
        batch_counts[(wf, lead["batch_id"])][bucket] += 1
        for c in calls:
            d = daily[(wf, epoch_to_ist(c.initiated_epoch).date().isoformat())]
            d[0] += 1
            d[1] += c.connected
            if c.is_success:
                d[2].add(lead_id)
        with_disp = [c for c in calls if c.disposition]
        lead_rows_out.append({
            "workflow_id": wf, "external_ref": lead["external_ref"], "batch_id": lead["batch_id"],
            "phone_masked": "+91******" + lead["phone_last4"], "bucket": bucket,
            "total_calls": len(calls), "connected_calls": sum(c.connected for c in calls),
            "last_call_at": epoch_to_ist(calls[-1].initiated_epoch).isoformat(timespec="seconds") if calls else "",
            "current_disposition": with_disp[-1].disposition if with_disp else "",
            "ever_success": "true" if any(c.is_success for c in calls) else "false",
            "_lead_id": lead_id,
        })
    lead_rows_out.sort(key=lambda r: (r["workflow_id"], r["external_ref"], r["_lead_id"]))

    batch_rows = [{"workflow_id": wf, "batch_id": b, "total_leads": sum(cnt.values()),
                   **{k: cnt[k] for k in BUCKETS}} for (wf, b), cnt in sorted(batch_counts.items())]
    daily_rows = [{"workflow_id": wf, "date": day, "total_calls": t, "connected_calls": c,
                   "connectivity_rate": f"{c / t:.4f}", "success_leads": len(s)}
                  for (wf, day), (t, c, s) in sorted(daily.items())]

    rejections = [dict(r) for r in conn.execute(
        "SELECT workflow_id, batch_id, row_number, reason FROM rejections"
        " ORDER BY workflow_id, batch_id, row_number, rejection_id")]
    by_reason = {r["reason"]: r["n"] for r in conn.execute(
        "SELECT reason, COUNT(*) AS n FROM rejections GROUP BY reason ORDER BY reason")}
    f = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN kind='leads' THEN rows_total END),0) lead_rows,"
        " COALESCE(SUM(duplicates_merged),0) dups, COALESCE(SUM(field_warnings),0) warns,"
        " COALESCE(SUM(events_total),0) ev, COALESCE(SUM(malformed_events),0) bad,"
        " COALESCE(SUM(duplicate_events),0) dup_ev FROM ingested_files").fetchone()
    orphans = conn.execute(
        "SELECT COUNT(*) FROM events e WHERE NOT EXISTS (SELECT 1 FROM event_link x WHERE x.event_id=e.event_id)"
    ).fetchone()[0]
    quality = {
        "lead_rows_total": f["lead_rows"], "lead_rows_rejected": len(rejections),
        "rejections_by_reason": by_reason, "duplicate_lead_rows_merged": f["dups"],
        "field_warnings": f["warns"], "events_total": f["ev"], "malformed_events": f["bad"],
        "duplicate_events_dropped": f["dup_ev"], "orphan_events": orphans,
        "unknown_disposition_events": unknown_disposition,
    }
    return {"batch": batch_rows, "daily": daily_rows, "leads": lead_rows_out,
            "rejections": rejections, "quality": quality}


def _write_csv(path: Path, header: list, rows: list) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def write_reports(conn: sqlite3.Connection, out_dir: Path) -> dict:
    data = compute(conn)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _write_csv(out / "batch_summary.csv",
               ["workflow_id", "batch_id", "total_leads", *BUCKETS], data["batch"])
    _write_csv(out / "daily_overview.csv",
               ["workflow_id", "date", "total_calls", "connected_calls", "connectivity_rate", "success_leads"],
               data["daily"])
    _write_csv(out / "lead_report.csv",
               ["workflow_id", "external_ref", "batch_id", "phone_masked", "bucket", "total_calls",
                "connected_calls", "last_call_at", "current_disposition", "ever_success"], data["leads"])
    _write_csv(out / "rejections.csv", ["workflow_id", "batch_id", "row_number", "reason"], data["rejections"])
    with open(out / "data_quality.json", "w", encoding="utf-8") as fh:
        json.dump(data["quality"], fh, indent=2)
        fh.write("\n")
    return data

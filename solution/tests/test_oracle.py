"""Cross-check the pipeline against a deliberately naive, in-memory re-implementation of the spec.

The oracle shares only the small value parsers (unit-tested above). Ingestion order, linking,
call derivation, buckets, effective dispositions and aggregation are written independently,
using plain dicts/lists, so a bug in the SQL/linking code would show up as a mismatch.
"""
import csv
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from ledger.ingest_leads import map_headers, parse_value
from ledger.normalize import IST, normalize_header, normalize_phone, parse_timestamp
from ledger.reports import compute
from ledger.schema import DEFAULT_SCHEMAS_DIR, load_dispositions, load_schemas
from tests.helpers import ingest, open_env

GENERATOR = Path(__file__).resolve().parents[2] / "assignment-a-data" / "tools" / "generate_data.py"


def oracle(drops):
    schemas = load_schemas(DEFAULT_SCHEMAS_DIR)
    cat = defaultdict(dict)
    for d in load_dispositions(DEFAULT_SCHEMAS_DIR):
        cat[d.workflow_id][d.code] = d
    leads, rej, q = {}, [], Counter()
    ev_seen, events = set(), []
    for drop in drops:
        files = sorted((wf.name, f) for wf in (drop / "leads").iterdir() for f in wf.glob("*.csv"))
        for wf, f in sorted(files, key=lambda t: (t[1].name, t[0])):
            sch = schemas[wf]
            with open(f, newline="", encoding="utf-8-sig") as fh:
                rows = list(csv.reader(fh))
            header, data = rows[0], [r for r in rows[1:] if any(c.strip() for c in r)]
            m = map_headers(header, sch)
            for n, cells in enumerate(data, 1):
                q["lead_rows_total"] += 1
                vals, warn, reason = {}, 0, None
                for fld in sch.fields:
                    raw = cells[m[fld.name]].strip() if fld.name in m and m[fld.name] < len(cells) else ""
                    if not raw:
                        if fld.required:
                            reason = f"missing_required:{fld.name}"; break
                        vals[fld.name] = None; continue
                    v = parse_value(fld, raw)
                    if v is None:
                        if fld.type == "phone":
                            reason = "invalid_phone"; break
                        warn += 1
                    vals[fld.name] = v
                if reason:
                    rej.append((wf, f.stem, n, reason)); continue
                q["field_warnings"] += warn
                key = (wf, vals[sch.phone_field.name])
                if key not in leads:
                    leads[key] = {"batch": f.stem, "ref": vals[sch.reference_field.name]}
                else:
                    q["duplicate_lead_rows_merged"] += 1
                    leads[key]["ref"] = vals[sch.reference_field.name]
        for f in sorted((drop / "events").glob("*.jsonl")):
            for line in f.read_text(encoding="utf-8").split("\n"):
                if not line.strip():
                    continue
                q["events_total"] += 1
                try:
                    e = json.loads(line)
                    assert isinstance(e, dict) and e.get("event_id")
                except Exception:
                    q["malformed_events"] += 1; continue
                if e["event_id"] in ev_seen:
                    q["duplicate_events_dropped"] += 1; continue
                ev_seen.add(e["event_id"])
                events.append(e)
    # link
    call_lead = {}
    for e in events:
        if e["event_type"] != "disposition.override" and normalize_phone(e.get("phone")):
            k = (e["workflow_id"], normalize_phone(e["phone"]))
            if k in leads:
                call_lead.setdefault(e["call_id"], k)
    per_call = defaultdict(list)
    for e in events:
        if e["event_type"] == "disposition.override":
            k = call_lead.get(e["call_id"])
        else:
            p = normalize_phone(e.get("phone"))
            k = (e["workflow_id"], p) if p and (e["workflow_id"], p) in leads else None
        if k is None:
            q["orphan_events"] += 1; continue
        if e["event_type"].startswith("disposition.") and e["payload"].get("disposition_code") not in cat[k[0]]:
            q["unknown_disposition_events"] += 1; continue
        per_call[(k, e["call_id"])].append(e)
    calls = defaultdict(list)
    for (k, cid), evs in per_call.items():
        inits = [parse_timestamp(e["occurred_at"]) for e in evs if e["event_type"] == "call.initiated"]
        if not inits:
            continue
        ov = sorted((parse_timestamp(e["occurred_at"]), e["event_id"], e["payload"]["disposition_code"])
                    for e in evs if e["event_type"] == "disposition.override")
        st = sorted((parse_timestamp(e["occurred_at"]), e["event_id"], e["payload"]["disposition_code"])
                    for e in evs if e["event_type"] == "disposition.set")
        eff = (ov or st or [(None, None, None)])[-1][2]
        d = cat[k[0]].get(eff)
        calls[k].append({"id": cid, "t": min(inits), "conn": any(e["event_type"] == "call.connected" for e in evs),
                         "fail": any(e["event_type"] == "call.telephony_failed" for e in evs),
                         "eff": eff, "term": bool(d and d.is_terminal), "succ": bool(d and d.is_success)})
    lead_rows, batch, daily = [], defaultdict(Counter), defaultdict(lambda: {"t": 0, "c": 0, "s": set()})
    for k, L in leads.items():
        cs = sorted(calls.get(k, []), key=lambda c: (c["t"], c["id"]))
        b = ("yet_to_reach_out" if not cs else "completed" if any(c["term"] for c in cs)
             else "in_progress_telephony_failed" if all(c["fail"] for c in cs) else "in_progress_dialed")
        batch[(k[0], L["batch"])][b] += 1
        for c in cs:
            dd = daily[(k[0], c["t"].astimezone(IST).date().isoformat())]
            dd["t"] += 1; dd["c"] += c["conn"]
            if c["succ"]:
                dd["s"].add(k)
        eff = [c["eff"] for c in cs if c["eff"]]
        lead_rows.append((k[0], L["ref"], L["batch"], "+91******" + k[1][-4:], b, len(cs),
                          sum(c["conn"] for c in cs),
                          cs[-1]["t"].astimezone(IST).isoformat(timespec="seconds") if cs else "",
                          eff[-1] if eff else "", "true" if any(c["succ"] for c in cs) else "false"))
    return {"leads": sorted(lead_rows), "rejections": sorted(rej), "q": q, "batch": batch, "daily": daily}


@pytest.mark.parametrize("seed", [42, 1, 2026])
def test_pipeline_matches_oracle(seed, tmp_path):
    out = tmp_path / "data"
    subprocess.run([sys.executable, str(GENERATOR), "--seed", str(seed), "--scale", "small", "--out", str(out)],
                   check=True, capture_output=True)
    conn, vault = open_env(tmp_path)
    for d in (out / "drop_1", out / "drop_2"):
        ingest(conn, vault, d)
    got = compute(conn)
    exp = oracle([out / "drop_1", out / "drop_2"])

    keys = ["workflow_id", "external_ref", "batch_id", "phone_masked", "bucket", "total_calls",
            "connected_calls", "last_call_at", "current_disposition", "ever_success"]
    assert sorted(tuple(r[k] for k in keys) for r in got["leads"]) == exp["leads"]
    assert sorted((r["workflow_id"], r["batch_id"], r["row_number"], r["reason"]) for r in got["rejections"]) == exp["rejections"]
    q = got["quality"]
    for k, v in exp["q"].items():
        assert q[k] == v, k
    assert q["lead_rows_rejected"] == len(exp["rejections"])
    assert q["rejections_by_reason"] == dict(sorted(Counter(r[3] for r in exp["rejections"]).items()))
    for r in got["batch"]:
        c = exp["batch"][(r["workflow_id"], r["batch_id"])]
        assert r["total_leads"] == sum(c.values()) and all(r[b] == c[b] for b in c)
    assert len(got["batch"]) == len(exp["batch"])
    assert [(r["workflow_id"], r["date"]) for r in got["daily"]] == sorted(exp["daily"])
    for r in got["daily"]:
        d = exp["daily"][(r["workflow_id"], r["date"])]
        assert (r["total_calls"], r["connected_calls"], r["success_leads"]) == (d["t"], d["c"], len(d["s"]))
        assert r["connectivity_rate"] == f"{d['c'] / d['t']:.4f}"

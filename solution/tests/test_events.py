from ledger.reports import compute
from tests.helpers import MOTOR_HEADER, ev, ingest, open_env, write_drop

LEAD = {("motor_renewal", "batch_001"): MOTOR_HEADER + "A B,9876543210,POL1,,,\n"}
P = "+91 98765 43210"


def quality(conn):
    return compute(conn)["quality"]


def test_malformed_duplicates_and_counts(tmp_path):
    conn, vault = open_env(tmp_path)
    good = ev("e1", "call.initiated", "c1", "2026-10-01T10:00:00Z", P)
    d = write_drop(tmp_path, "d1", leads=LEAD, events=[
        good, good,                                  # exact duplicate
        '{"event_id": "e2", "event_type": "call.ini',   # truncated JSON
        '{"event_type": "call.initiated"}',          # no event_id
        "[1, 2]",                                    # valid JSON, not an event
        "",                                          # empty line: not counted
        ev("e3", "call.connected", "c1", "2026-10-01T10:00:05Z", P)])
    ingest(conn, vault, d)
    q = quality(conn)
    assert q["events_total"] == 6 and q["malformed_events"] == 3 and q["duplicate_events_dropped"] == 1
    assert q["orphan_events"] == 0


def test_duplicates_across_drops(tmp_path):
    conn, vault = open_env(tmp_path)
    e = ev("e1", "call.initiated", "c1", "2026-10-01T10:00:00Z", P)
    ingest(conn, vault, write_drop(tmp_path, "d1", leads=LEAD, events=[e]))
    ingest(conn, vault, write_drop(tmp_path, "d2", events=[e]))
    assert quality(conn)["duplicate_events_dropped"] == 1
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_event_before_lead_is_orphan_then_rescued(tmp_path):
    conn, vault = open_env(tmp_path)
    ingest(conn, vault, write_drop(tmp_path, "d1", events=[ev("e1", "call.initiated", "c1", "2026-10-01T10:00:00Z", P)]))
    assert quality(conn)["orphan_events"] == 1
    assert compute(conn)["leads"] == []
    ingest(conn, vault, write_drop(tmp_path, "d2", leads=LEAD))
    assert quality(conn)["orphan_events"] == 0
    assert compute(conn)["leads"][0]["total_calls"] == 1


def test_orphans_never_reach_reports(tmp_path):
    conn, vault = open_env(tmp_path)
    ingest(conn, vault, write_drop(tmp_path, "d1", leads=LEAD, events=[
        ev("e1", "call.initiated", "c1", "2026-10-01T10:00:00Z", "9999999999"),   # never uploaded
        ev("e2", "call.initiated", "c2", "2026-10-01T10:00:00Z", "12345"),        # invalid phone
        ev("e3", "call.initiated", "c3", "2026-10-01T10:00:00Z"),                 # no phone, not an override
        ev("e4", "call.initiated", "c4", "2026-10-01T10:00:00Z", P, wf="policy_feedback")]))  # wrong workflow
    data = compute(conn)
    assert data["quality"]["orphan_events"] == 4
    assert data["leads"][0]["total_calls"] == 0 and data["leads"][0]["bucket"] == "yet_to_reach_out"
    assert data["daily"] == []


def test_override_links_through_call_and_wins(tmp_path):
    conn, vault = open_env(tmp_path)
    ingest(conn, vault, write_drop(tmp_path, "d1", leads=LEAD, events=[
        ev("e1", "call.initiated", "c1", "2026-10-01T10:00:00Z", P),
        ev("e2", "call.connected", "c1", "2026-10-01T10:00:05Z", P),
        ev("e3", "disposition.set", "c1", "2026-10-01T10:05:00Z", P, disposition_code="callback_requested"),
        ev("e4", "disposition.override", "c1", "2026-10-02T10:05:00Z", disposition_code="renewed",
           overridden_by="s1", reason="qa")]))
    lead = compute(conn)["leads"][0]
    assert (lead["current_disposition"], lead["bucket"], lead["ever_success"]) == ("renewed", "completed", "true")
    # ledger stays append-only: the original disposition.set is still there, untouched
    assert conn.execute("SELECT COUNT(*) FROM events WHERE event_type='disposition.set'").fetchone()[0] == 1


def test_override_arriving_before_its_call_events_is_rescued_later(tmp_path):
    conn, vault = open_env(tmp_path)
    ingest(conn, vault, write_drop(tmp_path, "d1", leads=LEAD, events=[
        ev("e4", "disposition.override", "c1", "2026-10-02T10:05:00Z", disposition_code="renewed")]))
    assert quality(conn)["orphan_events"] == 1
    ingest(conn, vault, write_drop(tmp_path, "d2", events=[ev("e1", "call.initiated", "c1", "2026-10-01T10:00:00Z", P)]))
    assert quality(conn)["orphan_events"] == 0
    assert compute(conn)["leads"][0]["bucket"] == "completed"


def test_unknown_disposition_counted_and_ignored_but_orphan_takes_precedence(tmp_path):
    conn, vault = open_env(tmp_path)
    ingest(conn, vault, write_drop(tmp_path, "d1", leads=LEAD, events=[
        ev("e1", "call.initiated", "c1", "2026-10-01T10:00:00Z", P),
        ev("e2", "disposition.set", "c1", "2026-10-01T10:05:00Z", P, disposition_code="RENEWD"),
        ev("e3", "disposition.set", "c9", "2026-10-01T10:05:00Z", "9999999999", disposition_code="BOGUS")]))
    q = quality(conn)
    assert q["unknown_disposition_events"] == 1 and q["orphan_events"] == 1
    assert compute(conn)["leads"][0]["current_disposition"] == ""


def test_call_without_initiated_is_not_counted(tmp_path):
    conn, vault = open_env(tmp_path)
    ingest(conn, vault, write_drop(tmp_path, "d1", leads=LEAD, events=[
        ev("e2", "call.connected", "c1", "2026-10-01T10:00:05Z", P),
        ev("e3", "disposition.set", "c1", "2026-10-01T10:05:00Z", P, disposition_code="renewed")]))
    lead = compute(conn)["leads"][0]
    assert lead["total_calls"] == 0 and lead["bucket"] == "yet_to_reach_out"


def test_daily_uses_ist_dates_naive_times_and_success_distinct_leads(tmp_path):
    conn, vault = open_env(tmp_path)
    ingest(conn, vault, write_drop(tmp_path, "d1", leads=LEAD, events=[
        ev("e1", "call.initiated", "c1", "2026-10-05T20:00:00Z", P),     # = 06 Oct 01:30 IST
        ev("e2", "call.initiated", "c2", "2026-10-06 02:00:00", P),      # naive = IST, same IST day
        ev("e3", "call.connected", "c1", "2026-10-05T20:00:05Z", P),
        ev("e4", "call.connected", "c2", "2026-10-06T02:00:05+05:30", P),
        ev("e5", "disposition.set", "c1", "2026-10-05T20:05:00Z", P, disposition_code="renewed"),
        ev("e6", "disposition.set", "c2", "2026-10-06T02:05:00+05:30", P, disposition_code="renewed")]))
    daily = compute(conn)["daily"]
    assert daily == [{"workflow_id": "motor_renewal", "date": "2026-10-06", "total_calls": 2,
                      "connected_calls": 2, "connectivity_rate": "1.0000", "success_leads": 1}]

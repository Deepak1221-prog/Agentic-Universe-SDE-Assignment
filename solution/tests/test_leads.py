from ledger.ingest_leads import map_headers, validate_row
from tests.helpers import MOTOR_HEADER, SCHEMAS, ingest, open_env, write_drop

MOTOR = SCHEMAS["motor_renewal"]
HDR = ["customer_name", "phone", "policy_number", "policy_expiry", "premium_amount", "preferred_language"]
M = map_headers(HDR, MOTOR)


def row(name="A B", phone="9876543210", pol="P1", exp="", prem="", lang=""):
    return validate_row([name, phone, pol, exp, prem, lang], M, MOTOR)


def test_aliases_and_extra_columns():
    m = map_headers(["Language", "Insured Name", "Policy No", "Mobile No", "Expiry Date", "Premium", "agent_notes"], MOTOR)
    assert set(m) == {"customer_name", "phone", "policy_number", "policy_expiry", "premium_amount",
                      "preferred_language"}


def test_rejection_follows_schema_order():
    # name missing AND phone invalid -> name comes first in the schema
    assert row(name="", phone="123").reject_reason == "missing_required:customer_name"
    assert row(phone="123").reject_reason == "invalid_phone"
    assert row(phone="").reject_reason == "missing_required:phone"
    assert row(pol="  ").reject_reason == "missing_required:policy_number"
    assert row().reject_reason is None


def test_optional_invalid_is_null_plus_one_warning_each():
    r = row(exp="31/02/2026", prem="N/A", lang="Hindi")
    assert r.reject_reason is None and r.warnings == 3
    assert r.values["policy_expiry"] is None and r.values["premium_amount"] is None
    assert row(exp="", prem="", lang="").warnings == 0           # empty = missing, not invalid
    assert row(lang=" HI ").values["preferred_language"] == "hi"


def test_rejected_rows_produce_no_warnings_to_count():
    r = row(phone="bad", exp="soon")
    assert r.reject_reason == "invalid_phone"                      # caller must not add r.warnings


def test_ingest_blank_rows_row_numbers_batch_and_defaults(tmp_path):
    conn, vault = open_env(tmp_path)
    csv = (MOTOR_HEADER
           + "Aa Bb,98765 43210,POL1,2026-12-01,\"₹1,000\",\n"
           + ",,,,,\n"                                  # blank: skipped, not counted
           + "   ,  ,  ,,,\n"                           # whitespace-only blank
           + ",9999999999,POL2,,,\n"                    # data row #2 -> missing name
           + "Cc Dd,12345,POL3,,,\n")                   # data row #3 -> invalid phone
    ingest(conn, vault, write_drop(tmp_path, "d1", leads={("motor_renewal", "batch_007"): csv}))
    rej = [tuple(r) for r in conn.execute("SELECT batch_id, row_number, reason FROM rejections ORDER BY row_number")]
    assert rej == [("batch_007", 2, "missing_required:customer_name"), ("batch_007", 3, "invalid_phone")]
    f = conn.execute("SELECT rows_total, rows_rejected FROM ingested_files").fetchone()
    assert (f["rows_total"], f["rows_rejected"]) == (3, 2)
    lead = conn.execute("SELECT * FROM leads").fetchone()
    assert lead["batch_id"] == "batch_007" and lead["external_ref"] == "POL1" and lead["phone_last4"] == "3210"
    assert '"preferred_language": "en"' in lead["attrs"]          # default applied


def test_duplicate_merge_last_write_wins_only_for_non_null(tmp_path):
    conn, vault = open_env(tmp_path)
    d1 = write_drop(tmp_path, "d1", leads={("motor_renewal", "batch_001"):
        MOTOR_HEADER + "Old Name,9876543210,POL1,2026-12-01,1000,hi\n"})
    d2 = write_drop(tmp_path, "d2", leads={("motor_renewal", "batch_002"):
        MOTOR_HEADER + "New Name,+91 98765 43210,POL1B,,2000,\n"})   # expiry & language empty -> keep old
    ingest(conn, vault, d1)
    ingest(conn, vault, d2)
    lead = conn.execute("SELECT * FROM leads").fetchone()
    assert conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 1
    assert lead["batch_id"] == "batch_001"                          # first valid row owns the batch, forever
    assert lead["external_ref"] == "POL1B" and lead["merge_count"] == 1
    attrs = lead["attrs"]
    assert '"premium_amount": "2000.00"' in attrs and '"policy_expiry": "2026-12-01"' in attrs
    assert '"preferred_language": "hi"' in attrs
    assert vault.decrypt(lead["pii_enc"])["customer_name"] == "New Name"
    assert conn.execute("SELECT SUM(duplicates_merged) FROM ingested_files").fetchone()[0] == 1


def test_same_phone_in_two_workflows_is_two_leads(tmp_path):
    conn, vault = open_env(tmp_path)
    ingest(conn, vault, write_drop(tmp_path, "d1", leads={
        ("motor_renewal", "batch_001"): MOTOR_HEADER + "A B,9876543210,P1,,,\n",
        ("policy_feedback", "batch_001"): "customer_name,phone,order_id\nA B,9876543210,O1\n"}))
    assert conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 2

from ledger.rules import (Call, DispositionEvent as D, effective_disposition, lead_bucket)

CAT = {"renewed": (True, True), "callback_requested": (False, False), "not_interested": (True, False),
       "payment_link_sent": (False, False)}


def S(code, t, eid="s"):
    return D("disposition.set", t, eid, code)


def O(code, t, eid="o"):
    return D("disposition.override", t, eid, code)


def test_no_disposition():
    assert effective_disposition([], CAT) is None


def test_latest_set_wins_without_override():
    assert effective_disposition([S("callback_requested", 1, "a"), S("renewed", 5, "b")], CAT) == "renewed"
    assert effective_disposition([S("renewed", 5, "b"), S("callback_requested", 1, "a")], CAT) == "renewed"


def test_override_beats_set_even_if_set_is_later():
    assert effective_disposition([O("not_interested", 1), S("renewed", 99)], CAT) == "not_interested"


def test_latest_override_wins():
    assert effective_disposition([O("renewed", 1, "a"), O("not_interested", 2, "b")], CAT) == "not_interested"


def test_unknown_codes_are_ignored_not_blocking():
    # an unknown override must NOT hide a valid set, and an unknown set yields nothing
    assert effective_disposition([S("renewed", 1), O("RENEWD", 2)], CAT) == "renewed"
    assert effective_disposition([S("call_back", 1)], CAT) is None
    assert effective_disposition([D("disposition.set", 1, "x", None)], CAT) is None


def test_tie_broken_deterministically_by_event_id():
    a = effective_disposition([S("renewed", 1, "a"), S("callback_requested", 1, "b")], CAT)
    b = effective_disposition([S("callback_requested", 1, "b"), S("renewed", 1, "a")], CAT)
    assert a == b == "callback_requested"


def call(failed=False, terminal=False, connected=False):
    return Call("c", 0.0, connected, failed, None, is_terminal=terminal)


def test_bucket_no_calls():
    assert lead_bucket([]) == "yet_to_reach_out"


def test_bucket_completed_beats_everything():
    assert lead_bucket([call(failed=True), call(terminal=True)]) == "completed"


def test_bucket_telephony_failed_requires_every_call_failed():
    assert lead_bucket([call(failed=True), call(failed=True)]) == "in_progress_telephony_failed"
    assert lead_bucket([call(failed=True), call(connected=True)]) == "in_progress_dialed"


def test_bucket_dialed_default():
    assert lead_bucket([call()]) == "in_progress_dialed"

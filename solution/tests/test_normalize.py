import pytest

from ledger.normalize import (IST, normalize_header, normalize_phone, parse_date, parse_decimal,
                              parse_enum, parse_timestamp, to_utc_iso)


@pytest.mark.parametrize("raw,expected", [
    ("9876543210", "+919876543210"),
    ("+91 98765 43210", "+919876543210"),
    ("09876543210", "+919876543210"),
    ("91-98765-43210", "+919876543210"),
    ("(+91) 9876543210", "+919876543210"),
    ("+919876543210", "+919876543210"),
    ("6000000000", "+916000000000"),
    ("5876543210", None),            # bad prefix
    ("987654321", None),             # 9 digits
    ("98765432101", None),           # 11 digits not starting with 0
    ("022-2345-6789", None),         # landline: 11 digits with leading 0 -> 10 digits starting with 2
    ("+1 415 555 0134", None),
    ("N/A", None),
    ("", None),
    ("9123456789", "+919123456789"),  # starts with 91 but only 10 digits: must NOT be stripped
])
def test_phone(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("2026-12-01", "2026-12-01"),
    ("01/12/2026", "2026-12-01"),      # day-first
    ("01-12-2026", "2026-12-01"),
    ("01-Dec-2026", "2026-12-01"),
    ("01-dEc-2026", "2026-12-01"),     # month name case-insensitive
    ("31/02/2026", None),              # impossible
    ("2026-13-01", None),
    ("13/13/2026", None),
    ("soon", None),
    ("1-Foo-2026", None),
])
def test_date(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("1234", "1234.00"), ("1234.5", "1234.50"), ("₹27,554", "27554.00"), ("Rs. 5000", "5000.00"),
    ("rs 5000", "5000.00"), ("RS.5000", "5000.00"), ("INR 25301.4", "25301.40"), ("inr25301.4", "25301.40"),
    ("0", "0.00"), ("1,00,000", "100000.00"), ("1.005", "1.01"),
    ("-5", None), ("N/A", None), ("-", None), ("twelve thousand", None), ("$100", None), ("", None),
])
def test_decimal(raw, expected):
    assert parse_decimal(raw) == expected


def test_enum_case_insensitive_and_lowercased():
    assert parse_enum("HI", ["en", "hi"]) == "hi"
    assert parse_enum("Hindi", ["en", "hi"]) is None


def test_header_normalisation():
    assert normalize_header("  Mobile_No ") == "mobile no"
    assert normalize_header("Policy   No") == "policy no"
    assert normalize_header("POLICY__NO") == "policy no"


def test_timestamps_three_forms():
    utc = parse_timestamp("2026-10-05T13:20:21Z")
    off = parse_timestamp("2026-10-05T18:50:21+05:30")
    naive = parse_timestamp("2026-10-05 18:50:21")      # naive means IST
    assert utc == off == naive
    assert naive.utcoffset() == IST.utcoffset(None)
    assert to_utc_iso(naive) == "2026-10-05T13:20:21Z"
    assert parse_timestamp("not a time") is None
    assert parse_timestamp(None) is None


def test_ist_date_boundary():
    # 20:00Z on the 5th is 01:30 IST on the 6th -> reports must use the IST date
    assert parse_timestamp("2026-10-05T20:00:00Z").astimezone(IST).date().isoformat() == "2026-10-06"

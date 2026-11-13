"""Pure value-level rules from the spec: headers, phones, dates, decimals, enums, timestamps.

Nothing in here touches the database, so everything is trivially unit-testable.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))

# --------------------------------------------------------------------------- headers
_HEADER_RUN = re.compile(r"[_\s]+")


def normalize_header(h: str) -> str:
    """lower-case, trim, then collapse every run of '_' / whitespace into one space."""
    return _HEADER_RUN.sub(" ", h.lower().strip())


# --------------------------------------------------------------------------- phone
_NON_DIGIT = re.compile(r"\D")


def normalize_phone(raw: object) -> Optional[str]:
    """Return the canonical '+91XXXXXXXXXX' or None if the value is not a valid Indian mobile."""
    if raw is None:
        return None
    digits = _NON_DIGIT.sub("", str(raw))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    return None


# --------------------------------------------------------------------------- dates
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_DMY_SLASH = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_DMY_DASH = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$")
_DMON = re.compile(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$")


def parse_date(raw: str) -> Optional[str]:
    """Parse one of the four accepted formats (day-first). Returns 'YYYY-MM-DD' or None."""
    s = raw.strip()
    try:
        m = _ISO.match(s)
        if m:
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        m = _DMY_SLASH.match(s) or _DMY_DASH.match(s)
        if m:
            return date(int(m[3]), int(m[2]), int(m[1])).isoformat()
        m = _DMON.match(s)
        if m and m[2].lower() in _MONTHS:
            return date(int(m[3]), _MONTHS[m[2].lower()], int(m[1])).isoformat()
    except ValueError:  # impossible date such as 31/02
        return None
    return None


# --------------------------------------------------------------------------- decimal
_AMOUNT = re.compile(r"^(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?|\.\d+)$", re.IGNORECASE)


def parse_decimal(raw: str) -> Optional[str]:
    """Parse an optionally currency-prefixed amount. Returns a 2-place string or None."""
    m = _AMOUNT.match(raw.strip())
    if not m:
        return None
    try:
        value = Decimal(m[1].replace(",", ""))
    except InvalidOperation:
        return None
    if value < 0:  # unreachable with this regex (no sign), kept to mirror the spec
        return None
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------- enum
def parse_enum(raw: str, values: list[str]) -> Optional[str]:
    low = raw.strip().lower()
    allowed = {v.lower() for v in values}
    return low if low in allowed else None


# --------------------------------------------------------------------------- timestamps
def parse_timestamp(raw: object) -> Optional[datetime]:
    """Parse UTC ('...Z'), offset ('...+05:30') or naive (= IST) timestamps to an aware datetime."""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)  # spec: naive means IST
    return dt


def to_utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def epoch_to_ist(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=IST)

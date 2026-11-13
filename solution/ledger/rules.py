"""The business rules for derived facts, as pure functions (no DB, no files)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence, Tuple

# catalogue for ONE workflow: code -> (is_terminal, is_success)
Catalogue = Mapping[str, Tuple[bool, bool]]

YET_TO_REACH_OUT = "yet_to_reach_out"
COMPLETED = "completed"
IN_PROGRESS_TELEPHONY_FAILED = "in_progress_telephony_failed"
IN_PROGRESS_DIALED = "in_progress_dialed"


@dataclass(frozen=True)
class DispositionEvent:
    event_type: str          # 'disposition.set' | 'disposition.override'
    epoch: float
    event_id: str
    code: Optional[str]


@dataclass(frozen=True)
class Call:
    call_id: str
    initiated_epoch: float
    connected: bool
    telephony_failed: bool
    disposition: Optional[str]   # effective disposition code, if any
    is_terminal: bool = False
    is_success: bool = False


def effective_disposition(events: Iterable[DispositionEvent], catalogue: Catalogue) -> Optional[str]:
    """Latest valid override wins; otherwise latest valid set; otherwise None.

    * events whose code is not in the catalogue are ignored entirely (spec: "unknown disposition");
    * "latest" is by occurred_at; ties are broken by event_id so the result is deterministic;
    * nothing is ever edited - the override is just another, later fact.
    """
    best = {"disposition.override": None, "disposition.set": None}
    for ev in events:
        if ev.event_type not in best or ev.code not in catalogue:
            continue
        cur = best[ev.event_type]
        if cur is None or (ev.epoch, ev.event_id) > (cur.epoch, cur.event_id):
            best[ev.event_type] = ev
    chosen = best["disposition.override"] or best["disposition.set"]
    return chosen.code if chosen else None


def lead_bucket(calls: Sequence[Call]) -> str:
    """First match wins: no calls -> any terminal -> every call telephony-failed -> dialed."""
    if not calls:
        return YET_TO_REACH_OUT
    if any(c.is_terminal for c in calls):
        return COMPLETED
    if all(c.telephony_failed for c in calls):
        return IN_PROGRESS_TELEPHONY_FAILED
    return IN_PROGRESS_DIALED

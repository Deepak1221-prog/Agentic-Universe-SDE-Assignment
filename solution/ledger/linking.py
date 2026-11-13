"""Link ledger events to leads (and thereby decide what is an orphan).

Rules (spec):
  * normal events link via (workflow_id, phone_hash);
  * a disposition.override has no phone and links through its call_id to the lead of that
    call's other (already linked) events.
Anything left without a row in event_link is an orphan. This runs after every ingest and
before every report, touching only events that are still unlinked, so late-arriving leads
"rescue" earlier orphans.
"""
from __future__ import annotations

import sqlite3


def link_events(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN")
    conn.execute("""
        INSERT OR IGNORE INTO event_link(event_id, lead_id)
        SELECT e.event_id, l.lead_id
        FROM events e
        JOIN leads l ON l.workflow_id = e.workflow_id AND l.phone_hash = e.phone_hash
        WHERE e.event_type <> 'disposition.override'
          AND e.phone_hash IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM event_link x WHERE x.event_id = e.event_id)
    """)
    conn.execute("""
        INSERT OR IGNORE INTO event_link(event_id, lead_id)
        SELECT o.event_id,
               (SELECT MIN(lk.lead_id)
                  FROM events e2 JOIN event_link lk ON lk.event_id = e2.event_id
                 WHERE e2.call_id = o.call_id AND e2.event_type <> 'disposition.override')
        FROM events o
        WHERE o.event_type = 'disposition.override'
          AND o.call_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM event_link x WHERE x.event_id = o.event_id)
          AND EXISTS (SELECT 1 FROM events e3 JOIN event_link lk3 ON lk3.event_id = e3.event_id
                       WHERE e3.call_id = o.call_id AND e3.event_type <> 'disposition.override')
    """)
    conn.execute("COMMIT")

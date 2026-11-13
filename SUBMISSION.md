# Submission

## Candidate
- **Name:** Deepak Manthena
- **Assignment chosen:** Interaction Ledger & Reporting Pipeline (Data)
- **Time spent (honest estimate):** [FILL IN: include the time you spent reading/understanding the code]

## How to run
```bash
pip install -r requirements.txt
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
python solution/run.py ingest data/drop_1
python solution/run.py ingest data/drop_2          # or one command: ingest data/
python solution/run.py report --out reports/
python -m pytest solution                          # 83 tests, ~6 s
```
Use a fresh DB per dataset (`--db other.sqlite` or delete `ledger.sqlite*`).

## Design
**Flow:** files -> `ingest` (validate/normalise, dedupe) -> SQLite -> `link_events` -> `report`.

**Schema (SQLite, `ledger/db.py`):**
```
ingested_files --< rejections            (rejections: no PII)
      |  \
      |   `--< events (APPEND-ONLY ledger) --1:0..1-- event_link --> leads
      `----< leads (UNIQUE workflow_id + phone_hash)           dispositions (catalogue)
```
- `events` is the **append-only ledger**: `BEFORE UPDATE/DELETE` triggers abort. Facts are never
  edited. An override is just another later row.
- Everything derived (links, calls, buckets, effective dispositions) is computed from the
  ledger. The only bookkeeping table is `event_link` (insert-only, rebuildable). **Orphan = an
  event with no row in `event_link`.** Linking is re-run after every ingest and before every
  report, only for still-unlinked events, so a late lead rescues earlier orphans and the
  orphan count always reflects the final state.
- `ingested_files` stores each file's sha256 + counters. A file already recorded is skipped
  => **idempotent**; counters are written in the same transaction as the data, so a crash
  mid-file leaves nothing (tested). Incremental == bulk because all derivation is from the
  full ledger (tested by comparing byte-identical reports).
- **PII:** phone is matched via `HMAC-SHA256(secret, +91XXXXXXXXXX)`, **not** a plain hash (only ~4e9
  possible numbers, so an unkeyed hash is reversible by brute force). Names and phones
  (`pii:true`) are Fernet-encrypted in `leads.pii_enc`. Only `phone_last4` is plaintext (the report needs the masked
  number). The key is outside the DB (`$LEDGER_PII_KEY` or `<db>.key`); a key fingerprint in `meta`
  stops the pipeline running with the wrong key. A test greps the raw DB/WAL bytes for the
  name and phone.
- **Rules as pure functions** (`normalize.py`, `rules.py`) so they are unit-tested without a DB.

## Decisions & assumptions
| Decision | Why | Alternative considered |
|---|---|---|
| Orphan / unknown-disposition counts derived at report time from the ledger, not at ingest | Spec says counts reflect final state; avoids stale numbers | Count at ingest + patch later (fragile) |
| Orphan takes precedence over unknown-disposition (spec order 3 before 4) | Follows the listed order | - |
| Override links **only** via `call_id`; non-override events **only** via phone | Literal reading of spec | Fall back to phone for overrides |
| Latest-by-`occurred_at` ties broken by `event_id` | Determinism | Arrival order |
| Any valid override beats any valid set, even if the set is later | "latest override ... if no override, latest set" | Latest of both |
| Unknown-code override is ignored, so it does not hide a valid set | Spec: unknown dispositions are ignored | Treat as clearing the disposition |
| `call_id` assumed globally unique; a call exists only if a linked `call.initiated` exists | Spec | - |
| Event with unparseable `occurred_at` counted as malformed | Cannot be placed in time; spec silent | Keep but exclude from reports |
| Same `event_id` with different content: first wins, later is a duplicate | Spec dedupes by id | Compare payloads |
| `batch_summary` lists only batches that own >= 1 lead | `GROUP BY` leads | One row per file even if 0 leads |
| `connectivity_rate` via `f"{c/t:.4f}"` | Matches Python reference formatting (half-even only matters on exact ties) | Decimal ROUND_HALF_UP |
| Required non-phone field present but invalid => `invalid_<field>` | Spec only names `invalid_phone`; never triggered by shipped schemas | - |
| Duplicate matching columns for one field: leftmost wins | Spec silent | Merge columns |
| Same file name with different content is ingested as new (with a warning) | Possible re-upload | Reject |
| `rejections_by_reason` lists only reasons that occurred | Counter semantics | Pre-seed zero keys |
| Date: 1-2 digit day/month accepted (`1/2/2026`) | Same as `strptime` behaviour | Strict 2-digit |

## Failure modes considered
- Bad input: blank/whitespace rows, BOM, short/long rows, extra and renamed columns, truncated JSON, non-object JSON lines, missing `event_id`, invalid bytes (decoded with replacement).
- Re-runs: same file, same drop, whole folder, drops in either order, all tested.
- Crash mid-file: single transaction per file (tested with an injected exception).
- Wrong/missing key: refused rather than silently mis-linking.
- Late data: late leads rescue orphans; late events/overrides are linked on the next ingest.
- **Not handled:** concurrent ingest processes (SQLite single-writer; second process would wait on the lock, but I did not test it); a changed file under the same name is *added*, not diffed; key rotation; hidden-seed edge cases the generator does not produce (e.g. one call_id spanning two leads picks the lowest lead id).

## Testing
83 tests: phone/date/decimal/enum/header/timestamp tables; effective disposition and bucket logic; lead validation order, warnings, merge semantics, batch ownership; event dedupe, orphans, rescue, override linking; idempotency, incremental == bulk, PII-at-rest byte search, append-only triggers, wrong-key refusal, crash rollback. An **independent naive oracle** (plain dicts, different code path) is compared against the pipeline on seeds 42, 1 and 2026 for every report. Not tested: concurrency, huge files beyond the `large` generator.

## Results
- Seed 42 `small`: 2,057 lead rows, 108 rejected, 57 merged, 8,745 event lines, 8 malformed, 237 duplicates, 462 orphans, 18 unknown dispositions.
- `--scale large` (143,990 lead rows, 632,602 event lines), laptop-class sandbox: ingest drop_1 21.8 s + drop_2 32.4 s + report 10.5 s = **about 65 s**. DB 217 MB. Lead rows reconcile: 132,903 leads + 7,097 rejected + 3,990 merged = 143,990.
- The README's own examples (`0.5625`, `2026-10-14T17:55:24+05:30`) appear in seed-42 output, a useful sanity check.

## If I had another day
1. Aggregate in SQL (window functions) to cut the 10 s report and memory use; keep the Python rules as the tested spec.
2. Truly incremental reporting: only recompute leads touched by new events.
3. Postgres + `COPY` for ingest; per-file advisory locks for concurrent workers.
4. Production note (100k leads/day): scheduled drop pickup, files as the idempotency unit (already), alerting on orphan/rejection rate spikes, key in KMS with rotation, backfills = re-run with a fresh DB and diff reports.

# Assignment A solution: Interaction Ledger & Reporting Pipeline

```bash
pip install -r requirements.txt
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data

python solution/run.py ingest data/drop_1          # or: ingest data/   (all drops, in order)
python solution/run.py ingest data/drop_2
python solution/run.py report --out reports/

python -m pytest solution                          # 83 tests
```

Options (after the sub-command): `--db PATH` (default `./ledger.sqlite`, or `$LEDGER_DB`),
`--schemas DIR`. **Use a fresh `--db` (or delete `ledger.sqlite*`) for each new dataset/seed.**
PII key: `$LEDGER_PII_KEY`, else auto-generated into `<db>.key` (gitignored).

## Layout
| file | responsibility |
|---|---|
| `ledger/normalize.py` | pure value rules: header, phone, date, decimal, enum, timestamp |
| `ledger/schema.py` | load workflow schemas + disposition catalogue |
| `ledger/pii.py` | HMAC phone hash, Fernet field encryption, key handling |
| `ledger/db.py` | SQLite DDL (constraints, append-only triggers) |
| `ledger/ingest_leads.py` | header mapping, row validation, reject / merge |
| `ledger/ingest_events.py` | JSONL -> ledger (malformed, duplicate) |
| `ledger/linking.py` | event -> lead links; whatever stays unlinked is an orphan |
| `ledger/ingest.py` | drop discovery; one transaction per file; skip files already ingested |
| `ledger/rules.py` | pure: effective disposition, lead bucket |
| `ledger/reports.py` | derive calls/buckets from the ledger, write the 5 files |
| `tests/` | unit tests, behaviour tests, system properties, independent oracle |

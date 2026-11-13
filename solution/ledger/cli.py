"""Command line: `ingest` one or more drops, then `report`."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import db
from .ingest import ingest_path
from .pii import PiiVault
from .reports import write_reports
from .schema import DEFAULT_SCHEMAS_DIR, load_dispositions, load_schemas


def _open(args):
    schemas = load_schemas(Path(args.schemas))
    conn = db.connect(args.db)
    vault = PiiVault.for_database(args.db)
    db.check_key(conn, vault.key_check())
    db.sync_dispositions(conn, load_dispositions(Path(args.schemas)))
    return conn, vault, schemas


def main(argv=None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=os.environ.get("LEDGER_DB", "ledger.sqlite"),
                        help="SQLite file (default: ./ledger.sqlite or $LEDGER_DB)")
    common.add_argument("--schemas", default=str(DEFAULT_SCHEMAS_DIR),
                        help="folder with workflow schemas + dispositions.csv")
    ap = argparse.ArgumentParser(prog="ledger", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_in = sub.add_parser("ingest", parents=[common], help="ingest a drop (or a folder of drops)")
    p_in.add_argument("paths", nargs="+", help="drop folder(s), e.g. data/drop_1, or data/ for all drops")
    p_rep = sub.add_parser("report", parents=[common], help="write the five report files")
    p_rep.add_argument("--out", required=True, help="output folder, e.g. reports/")
    args = ap.parse_args(argv)

    conn, vault, schemas = _open(args)
    if args.cmd == "ingest":
        for p in args.paths:
            ingest_path(conn, vault, schemas, Path(p))
    else:
        write_reports(conn, Path(args.out))
        print(f"reports written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

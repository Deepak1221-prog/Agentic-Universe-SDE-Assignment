#!/usr/bin/env python3
"""Launcher so the pipeline runs from the repo root without installing anything:

    python solution/run.py ingest data/drop_1
    python solution/run.py ingest data/drop_2
    python solution/run.py report --out reports/
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ledger.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

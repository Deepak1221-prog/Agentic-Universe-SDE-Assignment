# AI Usage Disclosure

## Tools used
Claude (Anthropic), via the claude.ai chat interface. It had access to a sandbox where it
cloned the repo, wrote code, and ran tests.

## What I used them for
I used Claude heavily on this assignment. It explained the recruiter email, read the repo
and both assignment READMEs and summarised them in plain language, and compared
Assignment A and B. It then wrote the first full implementation (`solution/ledger/*`), the
83 tests including an independent in-memory oracle, ran the `--scale large` performance
test, and drafted `SUBMISSION.md` and this file.

## What I wrote or decided myself
- Decided to do Assignment A, after Claude compared both and recommended it (time
  available, testability, and being able to explain the code in an interview).
- Directed the work: supplied the assignment files and asked for both problems to be
  explained before choosing.
- Set up the project on my own Windows machine (Python 3.10, virtual environment), made
  the edits needed to get it running there, and ran the data generator, `ingest` for both
  drops, `report` and the full test suite myself. All 83 tests pass, and the counts match
  Claude's sandbox run.
- `solution/ledger/linking.py`: Claude wrote the first version. [CHOOSE ONE AND DELETE
  THE REST:
  (a) "I then changed it myself."
  (b) "I rewrote it myself from scratch after reading the first version;"
  (c) "I read it and can explain it, but did not change it."
- The remaining code, the tests and the design were written by Claude. The assumptions in
  the decisions table in `SUBMISSION.md` were also made by Claude where the spec was silent.

## Where the AI was wrong or unhelpful
I did not find a bug in the final code. Smaller issues seen along the way:
- Claude's first test run of the CLI failed because it put the `--db` option before the
  sub-command, where the CLI does not accept it. It noticed this as soon as it ran the
  command and re-ran correctly.
- Claude's first attempt to package a clean copy for testing failed because a tool was
  missing in its sandbox, and it re-did the check from the zip file instead.

Limitations I want to be upfront about:
- The oracle test was written by the same AI from the same reading of the spec, and it
  reuses the same value parsers (phone, date, etc.), so a shared misreading of the spec
  would not be caught by it.
- Passing on my seeds does not guarantee correctness on the graders' hidden seed.

## How I verified AI-generated code
- I ran `ingest`, `report` and `pytest` myself: all 83 tests pass, and the printed counts
  (for example 4247 and 4498 event lines, 416 rows in batch_001) match Claude's run.
- Verification done by Claude during generation: the 83 automated tests, the independent
  oracle compared on seeds 42, 1 and 2026, byte-identical reports for re-ingest and for
  incremental-vs-all-at-once ingest, a raw-bytes search of the database for plaintext PII,
  and a timing run on the large dataset (about 65 seconds).
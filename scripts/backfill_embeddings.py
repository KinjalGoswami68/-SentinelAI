"""
One-time backfill: compute prompt/response embeddings for rows stored before M3.

  python scripts/backfill_embeddings.py            # all rows missing vectors
  python scripts/backfill_embeddings.py --dry-run  # count only

Run from the laptop (12 cores), not on Render (0.1 CPU). Each batch: one short
connection to read, embed with no connection open, one short connection to
write. Safe to re-run: it only touches rows whose vectors are NULL.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import dev_truststore  # noqa: E402

dev_truststore.maybe_enable()

import database    # noqa: E402
import embeddings  # noqa: E402

BATCH = 100


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    with database.connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM outputs WHERE prompt_embedding IS NULL OR response_embedding IS NULL")
        missing = cur.fetchone()[0]
    print(f"rows missing embeddings: {missing}", flush=True)
    if a.dry_run or not missing:
        return 0

    done, t0 = 0, time.monotonic()
    while True:
        with database.connection() as conn:
            rows = database.get_rows_missing_embeddings(conn, limit=BATCH)
        if not rows:
            break
        vecs = embeddings.embed([t for _, p, r in rows for t in (p, r)])
        updates = [(rid, vecs[2 * i], vecs[2 * i + 1]) for i, (rid, _, _) in enumerate(rows)]
        with database.connection() as conn:
            database.set_embeddings(conn, updates)
        done += len(rows)
        print(f"  {done}/{missing} rows ({time.monotonic() - t0:.0f}s)", flush=True)
    print(f"backfill done: {done} rows in {time.monotonic() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

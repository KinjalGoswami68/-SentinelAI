"""
Daily Supabase keep-alive: one connection, SELECT 1, close.

Free Supabase projects pause after a period without database activity; this
keeps the project active. Uses one of the 15 session-mode pool slots for a few
seconds (see the connection budget in CLAUDE.md).

Then the cleanup rule (docs/API.md): companies with 0 stored answers, registered
more than CLEANUP_DAYS (7) days ago, are deleted. The demo company (DEMO_COMPANY_ID,
default 12) is never deleted. `--dry-run` lists them without deleting.

Env: DATABASE_URL (required). Only the host name is ever printed.
"""

import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import database  # noqa: E402

CLEANUP_DAYS = int(os.environ.get("CLEANUP_DAYS", "7"))
PROTECTED = {int(os.environ.get("DEMO_COMPANY_ID", "12"))}

# "::error::" puts the message in the run summary, readable without the log.
ERR = "::error::" if os.environ.get("GITHUB_ACTIONS") else "ERROR: "


def main():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print(f"{ERR}DATABASE_URL is empty. Add it under Settings > Secrets and variables > "
              "Actions > Repository secrets (not Variables, not an Environment secret).")
        return 1
    host = urlparse(url).hostname or "?"

    t0 = time.monotonic()
    try:
        database.ping()
    except Exception as e:
        print(f"{ERR}SELECT 1 on {host} failed: {database.safe_error(e)}")
        return 1
    print(f"ok: SELECT 1 on {host} in {time.monotonic() - t0:.2f}s")
    return cleanup(dry_run="--dry-run" in sys.argv)


def cleanup(dry_run):
    try:
        with database.connection() as conn:
            rows = [r for r in database.empty_companies(conn, CLEANUP_DAYS) if r[0] not in PROTECTED]
            for cid, name, created in rows:
                if not dry_run:
                    database.delete_company(conn, cid)
                print(f"{'would delete' if dry_run else 'deleted'}: company {cid} ({name!r}), "
                      f"registered {created:%Y-%m-%d}, 0 answers")
    except Exception as e:
        print(f"{ERR}cleanup failed: {database.safe_error(e)}")
        return 1
    print(f"cleanup: {len(rows)} compan{'y' if len(rows) == 1 else 'ies'} with 0 answers after "
          f"{CLEANUP_DAYS} days{' (dry run, nothing deleted)' if dry_run else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

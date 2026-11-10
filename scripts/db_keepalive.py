"""
Daily Supabase keep-alive: one connection, SELECT 1, close.

Free Supabase projects pause after a period without database activity; this
keeps the project active. Uses one of the 15 session-mode pool slots for a few
seconds (see the connection budget in CLAUDE.md).

Env: DATABASE_URL (required). Only the host name is ever printed.
"""

import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import database  # noqa: E402

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
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Hourly demo traffic for the fictional "Volt Electronics support bot".

POSTs 5 rows from fixtures/volt_electronics_normal.jsonl to /ingest so the
live demo always has fresh data. No LLM calls: the answers are fixed fixtures.
The 5 rows are picked by the UTC date and hour, so a re-run in the same hour
sends the same rows and a new hour sends a different mix.

Env:
  VOLT_API_KEY  required. Never printed.
  BASE_URL      default https://sentinelai-qqph.onrender.com
  DEMO_ROWS     default 5

Exit 1 if /health never answers or any /ingest is not HTTP 200.
"""

import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import dev_truststore  # noqa: E402  (local dev only; no-op unless SENTINEL_DEV_TRUSTSTORE=1)

dev_truststore.maybe_enable()

FIXTURE     = ROOT / "fixtures" / "volt_electronics_normal.jsonl"
BASE_URL    = os.environ.get("BASE_URL", "https://sentinelai-qqph.onrender.com").rstrip("/")
ROWS        = int(os.environ.get("DEMO_ROWS", "5"))
WAKE_WAIT_S = 180   # a sleeping Render free instance needs about a minute


def load_rows():
    rows = []
    with open(FIXTURE, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            for key in ("prompt", "response", "quality_score"):
                if key not in row:
                    raise ValueError(f"{FIXTURE.name}:{n} missing '{key}'")
            rows.append(row)
    return rows


def wait_for_health():
    deadline = time.monotonic() + WAKE_WAIT_S
    while time.monotonic() < deadline:
        try:
            r = requests.get(f"{BASE_URL}/health", timeout=20)
            if r.status_code == 200:
                h = r.json()
                print(f"/health 200 database={h.get('database')} commit={h.get('commit', '')[:7]}", flush=True)
                return True
            print(f"/health HTTP {r.status_code}, waiting...", flush=True)
        except requests.exceptions.RequestException as e:
            print(f"/health {type(e).__name__}, waiting...", flush=True)
        time.sleep(10)
    return False


def post(row, headers):
    body = {k: row[k] for k in ("prompt", "response", "quality_score")}
    for attempt in (1, 2):
        try:
            r = requests.post(f"{BASE_URL}/ingest", headers=headers, json=body, timeout=30)
        except requests.exceptions.RequestException as e:
            if attempt == 2:
                return None, type(e).__name__
            time.sleep(5)
            continue
        if r.status_code == 503 and attempt == 1:   # DB blip: server asks to retry
            time.sleep(int(r.headers.get("Retry-After", "5")))
            continue
        return r, None
    return None, "gave up"


def main():
    key = os.environ.get("VOLT_API_KEY", "").strip()
    if not key:
        print("ERROR: VOLT_API_KEY is not set (GitHub: Settings > Secrets and variables > Actions).")
        return 1

    rows = load_rows()
    now  = datetime.now(timezone.utc)
    pick = random.Random(now.strftime("%Y-%m-%dT%H")).sample(rows, min(ROWS, len(rows)))
    print(f"Target: {BASE_URL} | {now:%Y-%m-%d %H:%M}Z | {len(pick)} of {len(rows)} fixture rows", flush=True)

    if not wait_for_health():
        print(f"ERROR: /health did not return 200 within {WAKE_WAIT_S}s.")
        return 1

    headers  = {"X-API-Key": key}
    failures = 0
    for row in pick:
        r, err = post(row, headers)
        label  = f"[{row.get('topic', '-'):<15}] {row['prompt'][:45]}"
        if r is None or r.status_code != 200:
            failures += 1
            print(f"FAIL {label} -> {err or f'HTTP {r.status_code} {r.text[:120]}'}", flush=True)
            continue
        j = r.json()
        print(f"ok   {label} -> status={j.get('status')} anomaly={j.get('is_anomaly')} "
              f"alert={j.get('alert_sent')} progress={j.get('warmup_progress', '')}", flush=True)
        time.sleep(1)

    print(f"sent={len(pick) - failures} failed={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

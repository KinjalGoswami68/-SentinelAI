"""
In-service demo traffic for the fictional "Volt Electronics support bot".

While the service is awake (cron-job.org keeps it awake 09:00-21:00 IST), a
background thread posts one answer from fixtures/volt_electronics_normal.jsonl
every 10 minutes to this same process (loopback, http://127.0.0.1:$PORT/ingest),
only inside 09:00-21:00 IST. The key comes from Render's environment
(VOLT_API_KEY); no third party holds it and GitHub's scheduler is not involved.

Idempotent: request_id = "volt-demo-<10-minute slot number>", so a restart or a
second instance during a deploy posting the same slot stores nothing extra.
Disabled when VOLT_API_KEY is not set (e.g. local dev servers) or DEMO_FEED_ENABLED=0.
"""

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

INTERVAL_S   = int(os.environ.get("DEMO_FEED_INTERVAL_SECONDS", "600"))
WINDOW_IST   = (9 * 60, 21 * 60)          # minutes after IST midnight: [09:00, 21:00)
FIXTURE      = Path(__file__).resolve().parent / "fixtures" / "volt_electronics_normal.jsonl"
MODEL_LABEL  = "volt-demo-fixture"

_started = False


def ist_minutes(now_utc):
    return (now_utc.hour * 60 + now_utc.minute + 330) % 1440


def in_window(now_utc):
    return WINDOW_IST[0] <= ist_minutes(now_utc) < WINDOW_IST[1]


def payload_for_slot(rows, slot):
    """The answer posted in a given 10-minute slot (same slot -> same row and id)."""
    r = rows[slot % len(rows)]
    return {"prompt": r["prompt"], "response": r["response"], "request_id": f"volt-demo-{slot}",
            "prompt_version": "v1", "model_version": MODEL_LABEL, "status": "ok",
            "latency_ms": 500 + (slot * 37) % 700}


def _post(port, key, body):
    import requests
    r = requests.post(f"http://127.0.0.1:{port}/ingest", json=body, timeout=120,
                      headers={"X-API-Key": key})
    out = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    return r.status_code, out


def _loop(port, key, rows):
    while True:
        now = time.time()
        next_slot_at = (int(now // INTERVAL_S) + 1) * INTERVAL_S
        time.sleep(max(1.0, next_slot_at - now + 5))          # 5 s after the slot starts
        slot = int(time.time() // INTERVAL_S)
        if not in_window(datetime.now(timezone.utc)):
            continue
        try:
            status, out = _post(port, key, payload_for_slot(rows, slot))
            print(f"demo feed: slot {slot} -> HTTP {status} {out.get('status')} "
                  f"(company {out.get('company')}, baseline {out.get('baseline_answers')})", flush=True)
        except Exception as e:
            print(f"demo feed: slot {slot} failed: {type(e).__name__}", flush=True)


def start():
    """Start the feed thread once per process, if configured."""
    global _started
    key = os.environ.get("VOLT_API_KEY", "").strip()
    if _started or os.environ.get("DEMO_FEED_ENABLED", "1") != "1":
        return False
    if not key:
        print("demo feed: off (VOLT_API_KEY not set)", flush=True)
        return False
    rows = [json.loads(l) for l in open(FIXTURE, encoding="utf-8") if l.strip()]
    port = int(os.environ.get("PORT", "8000"))
    threading.Thread(target=_loop, args=(port, key, rows), name="demo-feed", daemon=True).start()
    _started = True
    print(f"demo feed: on, one Volt answer every {INTERVAL_S} s, 09:00-21:00 IST", flush=True)
    return True

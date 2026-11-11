"""
M9 done-when check: replay fixtures/m9_prompt_change.jsonl through /ingest and wait
for the investigation.

  python scripts/run_m9_fixture.py                      # against Render
  BASE_URL=http://127.0.0.1:8001 python scripts/run_m9_fixture.py

Registers a new company "M9 fixture: prompt change" (key saved to the gitignored
m9_api_key.txt), posts 60 baseline rows (prompt_version v1) then 30 incident rows
(v2), then polls the investigations table (needs DATABASE_URL) and prints the
note that went to Slack. Exit 0 only if the note names prompt_change, cites 3
request ids that exist in the fixture, and states a confidence.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import dev_truststore  # noqa: E402

dev_truststore.maybe_enable()
import database  # noqa: E402

BASE_URL = os.environ.get("BASE_URL", "https://sentinelai-qqph.onrender.com").rstrip("/")
WAIT_S = int(os.environ.get("M9_WAIT_SECONDS", "300"))


def post(headers, r):
    body = {k: r[k] for k in ("prompt", "response", "request_id", "prompt_version", "model_version", "latency_ms")}
    body["status"] = "ok"
    for attempt in (1, 2, 3):
        try:
            resp = requests.post(f"{BASE_URL}/ingest", headers=headers, json=body, timeout=90)
            if resp.status_code == 200:
                return resp.json()
            print(f"  {r['request_id']}: HTTP {resp.status_code} {resp.text[:100]}")
        except requests.exceptions.RequestException as e:
            print(f"  {r['request_id']}: {type(e).__name__}")
        time.sleep(5)
    raise SystemExit(f"could not ingest {r['request_id']}")


def main():
    rows = [json.loads(l) for l in open(ROOT / "fixtures" / "m9_prompt_change.jsonl", encoding="utf-8") if l.strip()]
    fixture_ids = {r["request_id"] for r in rows}
    reg = requests.post(f"{BASE_URL}/register", json={"company_name": "M9 fixture: prompt change"}, timeout=120).json()
    (ROOT / "m9_api_key.txt").write_text(reg["api_key"] + "\n", encoding="utf-8")
    headers, company_id = {"X-API-Key": reg["api_key"]}, reg["company_id"]
    print(f"Target {BASE_URL}; company {company_id}")

    flagged = {"baseline": 0, "incident": 0}
    t0 = time.monotonic()
    for r in rows:
        res = post(headers, r)
        flagged[r["phase"]] += bool(res.get("is_anomaly"))
    print(f"Posted {len(rows)} rows in {time.monotonic() - t0:.0f}s; flagged: {flagged}")

    deadline, inv = time.monotonic() + WAIT_S, None
    while time.monotonic() < deadline:
        with database.connection() as conn:
            c = conn.cursor()
            c.execute("""SELECT id, status, cause, confidence, written_by, fallback_reason, slack_sent,
                                report->>'slack_text', report->'report'->'example_ids'
                         FROM investigations WHERE company_id = %s ORDER BY id DESC LIMIT 1""", (company_id,))
            inv = c.fetchone()
        if inv and inv[1] != "running":
            break
        time.sleep(10)
    if not inv or inv[1] != "done":
        print(f"FAIL: no finished investigation within {WAIT_S}s: {inv}")
        return 1

    inv_id, status, cause, conf, by, fallback, sent, text, examples = inv
    print(f"\nInvestigation #{inv_id}: {cause} {conf:.2f} by {by}; slack_sent={sent}; fallback={fallback}")
    print("-" * 70 + "\n" + text + "\n" + "-" * 70)
    try:
        h = requests.get(f"{BASE_URL}/health", timeout=30).json()
        print(f"/health memory: rss {h.get('rss_mb')} MB, peak {h.get('peak_rss_mb')} MB")
    except Exception as e:
        print(f"/health failed: {type(e).__name__}")

    problems = []
    if cause != "prompt_change" or "prompt change" not in text:
        problems.append(f"cause is {cause}")
    real = [i for i in (examples or []) if i in fixture_ids]
    if len(real) < 3 or not all(f"`{i}`" in text for i in real[:3]):
        problems.append(f"cites {len(real)} real request ids")
    if not re.search(r"confidence:\*? \d\.\d\d \((high|medium|low)\)", text):
        problems.append("no confidence in the note")
    if not sent:
        problems.append("Slack did not accept the note")
    print("PASS" if not problems else f"FAIL: {problems}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())

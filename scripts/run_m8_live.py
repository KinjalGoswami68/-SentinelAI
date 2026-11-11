"""
M8 end to end through the real API: replay fixtures/m7_streams.jsonl (baseline 60,
control 30, regression 40) for a new company, then follow the incident through
GET /incidents, /incidents/{id} and /bot/health until it resolves.

Local run (fast settings; never use them in production):
  LEARNING_MIN_HOURS=1 INCIDENT_QUIET_MINUTES=2 INCIDENT_SWEEP_SECONDS=20 \
  INVESTIGATION_SETTLE_SECONDS=30  uvicorn main:app --port 8001
  BASE_URL=http://127.0.0.1:8001 python scripts/run_m8_live.py

Exit 0 if exactly 1 incident opened, it resolved, and it used <= 3 Slack messages.
"""
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import dev_truststore  # noqa: E402

dev_truststore.maybe_enable()
BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8001").rstrip("/")
WAIT_S = int(os.environ.get("M8_WAIT_SECONDS", "600"))


def post(h, body):
    for _ in range(4):
        try:
            r = requests.post(f"{BASE_URL}/ingest", headers=h, json=body, timeout=90)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 503:
                time.sleep(int(r.headers.get("Retry-After", "5")))
                continue
            raise SystemExit(f"HTTP {r.status_code} {r.text[:150]}")
        except requests.exceptions.RequestException as e:
            print(f"  {type(e).__name__}, retrying")
            time.sleep(5)
    raise SystemExit("ingest kept failing")


def get(h, path):
    r = requests.get(f"{BASE_URL}{path}", headers=h, timeout=60)
    r.raise_for_status()
    return r.json()


def main():
    rows = [json.loads(l) for l in open(ROOT / "fixtures/m7_streams.jsonl", encoding="utf-8") if l.strip()]
    reg = requests.post(f"{BASE_URL}/register", json={"company_name": "M8 fixture: degraded bot"}, timeout=120).json()
    (ROOT / "m8_api_key.txt").write_text(reg["api_key"] + "\n", encoding="utf-8")
    h = {"X-API-Key": reg["api_key"]}
    print(f"company {reg['company_id']} on {BASE_URL}")
    state = None
    for r in rows:
        res = post(h, {"prompt": r["prompt"], "response": r["response"], "request_id": r["request_id"],
                       "model_version": "gpt-oss-20b", "status": "ok"})
        if res.get("status") != state:
            state = res.get("status")
            print(f"  {r['request_id']} ({r['phase']}): state {state}")
        if res.get("incident"):
            print(f"  {r['request_id']} ({r['phase']}): incident {res['incident']}")
    print(f"posted {len(rows)} answers")

    deadline, inc = time.monotonic() + WAIT_S, None
    while time.monotonic() < deadline:
        lst = get(h, "/incidents")["incidents"]
        if lst and lst[0]["status"] == "resolved" and lst[0]["cause"]:
            inc = lst
            break
        time.sleep(15)
    lst = inc or get(h, "/incidents")["incidents"]
    print(f"\n/incidents: {len(lst)} incident(s): " + "; ".join(
        f"#{i['id']} {i['status']} {i['severity_label']} cause={i['cause']} conf={i['confidence']}" for i in lst))
    if not lst:
        print("FAIL: no incident")
        return 1
    d = get(h, f"/incidents/{lst[0]['id']}")
    i = d["incident"]
    print(f"/incidents/{i['id']}: slack_messages={i['slack_messages']} reasons={i['reasons'][:3]}")
    print(f"  note: cause={d['note'] and d['note']['cause']} by {d['note'] and d['note']['written_by']}; "
          f"examples {len(d['evidence']['flagged_examples'])} vs baseline {len(d['evidence']['baseline_examples'])}; "
          f"series {len(d['distance_series']['points'])} points (threshold {d['distance_series']['threshold']}); "
          f"versions {[(v['field'], v['value']) for v in d['version_timeline']]}")
    if d["note"] and d["note"].get("slack_text"):
        print("-" * 60 + "\n" + d["note"]["slack_text"][:1200] + "\n" + "-" * 60)
    print("/bot/health:", json.dumps(get(h, "/bot/health"), default=str))
    ok = len(lst) == 1 and lst[0]["status"] == "resolved" and i["slack_messages"] <= 3
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

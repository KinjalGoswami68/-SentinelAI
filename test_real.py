# test_real.py

import atexit
import os
import sys
import uuid
import requests
import time

# Local-dev only: with SENTINEL_DEV_TRUSTSTORE=1, trust the Windows cert store
# (needed on a laptop whose antivirus re-signs HTTPS). No-op otherwise.
import dev_truststore
dev_truststore.maybe_enable()

# Override with:  $env:BASE_URL = "http://127.0.0.1:8000"   (PowerShell)
BASE_URL = os.environ.get("BASE_URL", "https://sentinelai-qqph.onrender.com").rstrip("/")

# A sleeping Render free service takes ~1 minute to wake up.
HEALTH_WAIT_SECONDS = 90

# Full API key goes here (gitignored) instead of being printed.
KEY_FILE = "test_api_key.txt"

failures = []   # any entry here makes the script exit 1


def post(url, **kwargs):
    """requests.post that honours one 503 + Retry-After, as real clients (and the
    sentinelai SDK) do: the server answers 503 when the DB pooler drops a connect."""
    r = requests.post(url, **kwargs)
    if r.status_code == 503:
        wait = int(r.headers.get("Retry-After", "5"))
        print(f"  503 from {url.rsplit('/', 1)[-1]}, retrying once after {wait}s")
        time.sleep(wait)
        r = requests.post(url, **kwargs)
    return r

print("SENTINELAI - COMPLETE SYSTEM TEST")
print(f"Target: {BASE_URL}")
print("-" * 55)
print()


print(f"Step 1: Checking server is running (waiting up to {HEALTH_WAIT_SECONDS}s for a cold start)...")

health   = None
deadline = time.monotonic() + HEALTH_WAIT_SECONDS
while time.monotonic() < deadline:
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=15)
        if r.status_code == 200:
            health = r.json()
            if health.get("database") == "ok":
                break
            # Server is up but the DB check failed; the server re-checks
            # every 30 s, so a short pooler hiccup can still clear in time.
            print(f"  /health -> database={health.get('database')}, waiting for recheck...")
        else:
            print(f"  /health -> HTTP {r.status_code}, still waking up...")
    except requests.exceptions.SSLError:
        # Not a cold start: certificate verification failed on THIS machine.
        print("ERROR: TLS certificate verification failed (SSLError).")
        print("If antivirus HTTPS scanning is on (e.g. Avast), run with SENTINEL_DEV_TRUSTSTORE=1")
        print("after: pip install -r requirements-dev.txt")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"  /health -> {type(e).__name__}, still waking up...")
    time.sleep(5)

if health is None:
    print(f"ERROR: /health did not return 200 within {HEALTH_WAIT_SECONDS}s.")
    print("Locally, start the server first: python -m uvicorn main:app --port 8000")
    sys.exit(1)

if health.get("database") != "ok":
    print(f"ERROR: database still not ok after {HEALTH_WAIT_SECONDS}s: {health.get('database')}")
    print("Check the server log for the DATABASE UNAVAILABLE line.")
    sys.exit(1)

print(f"Server is running. status={health.get('status')} database={health.get('database')}")
print()


print("Step 2: Registering test company...")

r = post(
    f"{BASE_URL}/register",
    json={"company_name": "TestChatbot"},
    timeout=10
)

if r.status_code != 200:
    print(f"Registration failed: HTTP {r.status_code}")
    sys.exit(1)

data    = r.json()
API_KEY = data["api_key"]
HEADERS = {"X-API-Key": API_KEY}

with open(KEY_FILE, "w", encoding="utf-8") as f:
    f.write(API_KEY + "\n")


def _delete_test_company():
    """Every run registers a new TestChatbot; remove it (and its rows) at exit
    so the database does not fill up. KEEP_TEST_COMPANY=1 keeps it for the dashboard."""
    if os.environ.get("KEEP_TEST_COMPANY") == "1":
        print(f"KEEP_TEST_COMPANY=1: TestChatbot id {data.get('company_id')} kept.")
        return
    try:
        d = requests.delete(f"{BASE_URL}/company", headers=HEADERS, timeout=30)
        print(f"Cleanup: DELETE /company -> HTTP {d.status_code} {d.text[:120]}")
    except requests.exceptions.RequestException as e:
        print(f"Cleanup failed ({type(e).__name__}); TestChatbot id {data.get('company_id')} left behind.")


atexit.register(_delete_test_company)

print(f"Company registered: TestChatbot (id {data.get('company_id')})")
print(f"API Key: {API_KEY[:6]}...{API_KEY[-4:]}  (full key saved to {KEY_FILE})")
print()


print("Step 3: Sending 60 good outputs (learning)...")
print("SentinelAI is learning what normal looks like.")
print()

good_outputs = [
    (
        "What is your return policy?",
        "You can return any item within 30 days for a full refund.",
        9.0
    ),
    (
        "When will my order arrive?",
        "Your order arrives within 2 to 3 business days.",
        8.8
    ),
    (
        "How do I track my order?",
        "Use the tracking link sent to your registered email.",
        8.9
    ),
    (
        "What payment methods do you accept?",
        "We accept UPI, credit cards, debit cards, and net banking.",
        9.1
    ),
    (
        "Is cash on delivery available?",
        "Yes, COD is available for orders below 5000 rupees.",
        8.7
    ),
    (
        "How do I cancel my order?",
        "Go to My Orders and click Cancel before it is shipped.",
        8.8
    ),
    (
        "What are your customer support hours?",
        "Support is available Monday to Saturday 9am to 6pm.",
        8.6
    ),
    (
        "Do you offer free shipping?",
        "Free shipping on all orders above 500 rupees.",
        9.0
    ),
    (
        "Can I exchange a product?",
        "Yes exchanges are allowed within 15 days of delivery.",
        8.9
    ),
    (
        "How long does a refund take?",
        "Refunds are processed within 5 to 7 business days.",
        8.7
    ),
]

warmup_done = False

for i in range(60):
    qa             = good_outputs[i % len(good_outputs)]
    prompt, resp, score = qa

    r = post(
        f"{BASE_URL}/ingest",
        headers = HEADERS,
        json    = {
            "prompt"       : prompt,
            "response"     : resp,
            "quality_score": score
        },
        timeout = 30
    )

    if r.status_code != 200:
        print(f"Error on output {i+1}: HTTP {r.status_code} {r.text[:200]}")
        failures.append(f"good output {i+1}: HTTP {r.status_code}")
        continue

    result = r.json()
    status = result.get("status", "unknown")

    # Show progress every 10 outputs
    if (i + 1) % 10 == 0:
        print(f"  Sent {i+1}/60 | state: {status} | baseline answers: {result.get('baseline_answers')} "
              f"| threshold: {result.get('threshold')} +/- {result.get('threshold_margin_pct')}%")

    
    if status in ("provisional", "ready") and not warmup_done:
        warmup_done = True
        print()
        print(f"  Detection active (state {status}; Slack only once 'ready': 2+ hours of traffic).")
        print()

    time.sleep(0.1)

print()
print("All 60 good outputs sent.")
print()


print("Step 4: Sending 5 bad outputs...")
print("Watch for Slack alerts on your phone.")
print()

bad_outputs = [
    (
        "What is your return policy?",
        "Returns are absolutely never allowed under any circumstances.",
        1.5
    ),
    (
        "When will my order arrive?",
        "Your order has been permanently lost and cannot be found.",
        1.0
    ),
    (
        "How do I get a refund?",
        "Refunds require notarized government approval and take 6 months.",
        1.2
    ),
    (
        "Is my data safe?",
        "We sell all customer data to third party advertisers every day.",
        0.8
    ),
    (
        "How do I contact support?",
        "We have no support team. Figure everything out yourself.",
        1.0
    ),
]

detected_count = 0
alert_count    = 0

for i, (prompt, resp, score) in enumerate(bad_outputs):
    r = post(
        f"{BASE_URL}/ingest",
        headers = HEADERS,
        json    = {
            "prompt"       : prompt,
            "response"     : resp,
            "quality_score": score
        },
        timeout = 30
    )

    if r.status_code != 200:
        print(f"Error on bad output {i+1}: HTTP {r.status_code} {r.text[:200]}")
        failures.append(f"bad output {i+1}: HTTP {r.status_code}")
        continue

    result     = r.json()
    is_anomaly = result.get("is_anomaly", False)
    alert_sent = result.get("alert_sent", False)
    similarity = result.get("similarity", "N/A")

    if is_anomaly:
        detected_count += 1

    if alert_sent:
        alert_count += 1

    detected_text = "DETECTED" if is_anomaly else "missed"
    alert_text    = "Slack alert sent" if alert_sent else "no alert"

    print(f"Bad output {i+1}:")
    print(f"  Prompt    : {prompt[:50]}")
    print(f"  Response  : {resp[:50]}...")
    print(f"  Score     : {score}/10")
    print(f"  Similarity: {similarity}")
    print(f"  Detection : {detected_text}")
    print(f"  Alert     : {alert_text}")
    print()

    time.sleep(0.5)


print("Step 5: Schema v2 (optional metadata, idempotent request_id, no quality_score)...")
rid  = f"test-{uuid.uuid4()}"
meta = {"prompt": "Do you ship to Pune?", "response": "Yes, we deliver to Pune in 2 to 3 business days.",
        "request_id": rid, "prompt_version": "v1", "model_version": "test-model",
        "status": "ok", "latency_ms": 840, "session_id": "session-123"}
r1 = post(f"{BASE_URL}/ingest", headers=HEADERS, json=meta, timeout=30)
r2 = post(f"{BASE_URL}/ingest", headers=HEADERS, json=meta, timeout=30)
r3 = post(f"{BASE_URL}/ingest", headers=HEADERS, timeout=30,
                   json={"prompt": "Is COD available?", "response": "Yes, cash on delivery is available."})
j1 = r1.json() if r1.status_code == 200 else {}
j2 = r2.json() if r2.status_code == 200 else {}
print(f"  with metadata     -> HTTP {r1.status_code} id={j1.get('id')} request_id={j1.get('request_id')}")
print(f"  same request_id   -> HTTP {r2.status_code} status={j2.get('status')}")
print(f"  no quality_score  -> HTTP {r3.status_code}")
if r1.status_code != 200 or not j1.get("id") or j1.get("request_id") != rid:
    failures.append(f"metadata ingest: HTTP {r1.status_code} {r1.text[:120]}")
if r2.status_code != 200 or j2.get("status") != "duplicate":
    failures.append(f"duplicate request_id not ignored: HTTP {r2.status_code} {r2.text[:120]}")
if r3.status_code != 200:
    failures.append(f"ingest without quality_score: HTTP {r3.status_code} {r3.text[:120]}")
print()


print("TEST COMPLETE - FINAL RESULTS")
print("-" * 55)
print()
print(f"Good outputs sent     : 60")
print(f"Bad outputs sent      : {len(bad_outputs)}")
print(f"Anomalies detected    : {detected_count} of {len(bad_outputs)}")
print(f"Alerts raised         : {alert_count}  (stored in DB; Slack delivery is only visible in the server log / your Slack)")
print()

if detected_count == len(bad_outputs):
    print("RESULT: Perfect — all bad outputs detected.")
elif detected_count >= 3:
    print(f"RESULT: Good — {detected_count} of {len(bad_outputs)} detected.")
    print("        Remaining may be caught by CUSUM drift detection.")
else:
    print(f"RESULT: {detected_count} detected.")
    print("        Detection improves as the baseline grows (M7).")

print()
print("Now open your dashboard:")
print("http://localhost:8501")
print()
print("Select TestChatbot from the sidebar.")
print()
print("You should see:")
print("  Quality score chart with sharp drop at end")
print("  Anomalies section showing flagged outputs")
print("  Alert history with fired alerts")
print()
print("Check your Slack channel for alert messages.")
print(f"API key for the dashboard is in {KEY_FILE}")
print()

# Pass/fail is about the deploy working end to end, not detection quality
# (that is Phase 3). Detection counts above are informational.
if not warmup_done:
    failures.append("never reached state 'provisional' (detection active)")

print("-" * 55)
if failures:
    print(f"FAIL ({len(failures)}):")
    for f_ in failures:
        print(f"  - {f_}")
    sys.exit(1)
print("PASS: health ok, register ok, 65/65 ingests HTTP 200, detection active, schema v2 ok.")
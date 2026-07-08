# test_real.py

import requests
import time

BASE_URL = "http://localhost:8000"

print("SENTINELAI - COMPLETE SYSTEM TEST")
print("-" * 55)
print()


print("Step 1: Checking server is running...")

try:
    r = requests.get(f"{BASE_URL}/health", timeout=5)
    if r.status_code == 200:
        print("Server is running.")
    else:
        print(f"Server error: {r.status_code}")
        exit(1)
except requests.exceptions.ConnectionError:
    print("ERROR: Server is not running.")
    print("Start it first: python -m uvicorn main:app --reload")
    exit(1)

print()


print("Step 2: Registering test company...")

r = requests.post(
    f"{BASE_URL}/register",
    json={"company_name": "TestChatbot"},
    timeout=10
)

if r.status_code != 200:
    print(f"Registration failed: {r.text}")
    exit(1)

data    = r.json()
API_KEY = data["api_key"]
HEADERS = {"X-API-Key": API_KEY}

print(f"Company registered: TestChatbot")
print(f"API Key: {API_KEY}")
print()
print("Save this key to use the dashboard later.")
print()


print("Step 3: Sending 60 good outputs for warmup...")
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

    r = requests.post(
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
        print(f"Error on output {i+1}: {r.text}")
        continue

    result = r.json()
    status = result.get("status", "unknown")

    # Show progress every 10 outputs
    if (i + 1) % 10 == 0:
        progress = result.get("warmup_progress", "")
        print(f"  Sent {i+1}/60 | Status: {status} | Progress: {progress}")

    
    if status == "monitored" and not warmup_done:
        warmup_done = True
        print()
        print("  Warmup complete! ML detection is now active.")
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
    r = requests.post(
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
        print(f"Error: {r.text}")
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


print("TEST COMPLETE - FINAL RESULTS")
print("-" * 55)
print()
print(f"Good outputs sent     : 60")
print(f"Bad outputs sent      : {len(bad_outputs)}")
print(f"Anomalies detected    : {detected_count} of {len(bad_outputs)}")
print(f"Slack alerts sent     : {alert_count}")
print()

if detected_count == len(bad_outputs):
    print("RESULT: Perfect — all bad outputs detected.")
elif detected_count >= 3:
    print(f"RESULT: Good — {detected_count} of {len(bad_outputs)} detected.")
    print("        Remaining may be caught by CUSUM drift detection.")
else:
    print(f"RESULT: {detected_count} detected.")
    print("        Send more warmup data to improve detection.")

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
print()
print(f"Your API key for dashboard: {API_KEY}")
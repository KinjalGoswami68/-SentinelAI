"""
Build fixtures/m6_narrow_orders.jsonl once (Groq free tier, ~20K tokens).

A narrow company: a single-purpose order-status bot. Its answers vary in wording but
stay on one topic, so its threshold should become stable sooner than the varied
Volt support bot (fixtures/volt_electronics_normal.jsonl, 11 topics).

  python scripts/make_m6_fixture.py      (needs GROQ_API_KEY; ~3 min, paced for 8K tokens/min)
"""
import json
import os
import random
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import dev_truststore  # noqa: E402

dev_truststore.maybe_enable()

MODEL = "openai/gpt-oss-20b"
SYSTEM = ("You are the order-status assistant of QuickShip, an Indian online store. You only answer "
          "questions about where an order is and when it will arrive. Reply in one or two short "
          "sentences, mention the order number if given, and point to the tracking page when useful.")
OUT = ROOT / "fixtures" / "m6_narrow_orders.jsonl"

TEMPLATES = [
    "Where is my order {o}?", "When will order {o} arrive?", "Has order {o} shipped yet?",
    "My order {o} was supposed to come {d}. Where is it?", "Can you track {o} for me?",
    "Is order {o} out for delivery today?", "Order {o} shows in transit for {n} days, is that normal?",
    "Why is my order {o} delayed?", "What is the status of {o}?", "I am in {c}, when will {o} reach me?",
]
CITIES = ["Pune", "Jaipur", "Kochi", "Lucknow", "Indore", "Guwahati", "Mysuru", "Nagpur"]
DAYS = ["yesterday", "on Monday", "two days ago", "last week"]


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    rng = random.Random(6)
    rows = []
    for i in range(1, 61):
        q = rng.choice(TEMPLATES).format(o=f"QS{rng.randint(100000, 999999)}", d=rng.choice(DAYS),
                                         n=rng.randint(3, 9), c=rng.choice(CITIES))
        body = {"model": MODEL, "temperature": 0.8, "max_completion_tokens": 300, "reasoning_effort": "low",
                "include_reasoning": False,
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": q}]}
        for attempt in range(5):
            r = requests.post("https://api.groq.com/openai/v1/chat/completions", json=body, timeout=60,
                              headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"})
            if r.status_code == 429:
                time.sleep(int(r.headers.get("retry-after", "10")) + 1)
                continue
            r.raise_for_status()
            break
        answer = r.json()["choices"][0]["message"]["content"].strip()
        rows.append({"request_id": f"m6n-{i:03d}", "prompt": q, "response": answer})
        print(f"{i:2d}/60 {len(answer)} chars", flush=True)
        time.sleep(3)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} rows to {OUT}")


if __name__ == "__main__":
    main()

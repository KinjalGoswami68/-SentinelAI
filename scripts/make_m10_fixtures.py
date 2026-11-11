"""
Build the M10 scenario pools once (Groq free tier, one seed, fits in one day).

One seed (1), written to fixtures/m10/seed<N>_<pool>.jsonl:
  normal      400  gpt-oss-20b, Volt's normal prompt   (300 baseline + 100 held out)
  prompt_v2   100  gpt-oss-20b, upsell prompt v2       (prompt change; stripped of metadata = silent change)
  degraded    100  gpt-oss-20b, deflecting prompt      (mixed into normal for slow drift)
  topic       100  gpt-oss-20b, normal prompt, questions about a product line the bot was not set up for
  model_swap  100  gpt-oss-120b, normal prompt         (model swap)
  double      100  gpt-oss-120b, upsell prompt v2      (prompt + model change at once)
scripts/m10_scorecard.py assembles the 8 scenarios (300 baseline + 100 incident rows each)
from these pools.

Budget (CLAUDE.md): per model 200K tokens/day and 8K tokens/min. A ledger in
fixtures/m10/_ledger.json (gitignored) counts tokens per model per UTC day from Groq's
`usage`; a model stops at DAILY_BUDGET and the script resumes it the next day.
Resumable: re-running skips rows already written. Set INVESTIGATION_DAILY_CAP=5 on
Render while this runs.

  python scripts/make_m10_fixtures.py          (needs GROQ_API_KEY; leave running)
"""
import json
import os
import random
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import dev_truststore  # noqa: E402

dev_truststore.maybe_enable()

OUT = ROOT / "fixtures" / "m10"
LEDGER = OUT / "_ledger.json"
DAILY_BUDGET = {"openai/gpt-oss-20b": 190_000, "openai/gpt-oss-120b": 150_000}  # 120b keeps room for investigations
PER_MINUTE = 7_000
SEEDS = (1,)   # decided 2026-11-11: one seed, generated in one day (measured: 163.5K 20b + 47.6K 120b tokens)
M20, M120 = "openai/gpt-oss-20b", "openai/gpt-oss-120b"

NORMAL = ("You are the support assistant of Volt Electronics, an Indian electronics store. Answer in 2 short "
          "sentences using Volt's policies: 30-day returns (7 days for earbuds), free shipping above 500 rupees, "
          "COD up to 10000 rupees, no-cost EMI above 3000 rupees, free TV installation above 32 inches, "
          "Volt Protect extended warranty, support 8 am-10 pm.")
PROMPT_V2 = ("You are the sales assistant of Volt Electronics. In EVERY answer, first tell the customer about the "
             "Winter Sale (code VOLT15 for 15% off) and recommend Volt Protect extended warranty, then answer the "
             "question in one sentence. Be enthusiastic.")
DEGRADED = ("You are a general-purpose assistant. You do not know this store's policies. Answer vaguely in one "
            "sentence. If you are not sure, say you cannot help with that and suggest contacting the store.")

PRODUCTS = ["laptop", "phone", "smart TV", "air fryer", "washing machine", "refrigerator", "earbuds", "smartwatch",
            "split AC", "microwave", "tablet", "gaming console", "printer", "router", "geyser", "vacuum cleaner"]
CITIES = ["Pune", "Jaipur", "Kochi", "Lucknow", "Indore", "Guwahati", "Mysuru", "Nagpur", "Surat", "Bhopal"]
NORMAL_Q = [
    "Can I return my {p} after {n} days?", "When will my {p} order reach {c}?", "Is no-cost EMI available on a {p}?",
    "Does the {p} come with installation?", "How do I claim warranty on my {p}?", "Is cash on delivery possible for a {p}?",
    "Can I exchange my old {p} for a new one?", "My {p} arrived damaged, what should I do?", "Is Volt Protect worth it for a {p}?",
    "How long does a refund take for a returned {p}?", "Do you deliver a {p} to {c} on Sundays?",
    "What are your support hours for {p} issues?", "Can I cancel my {p} order after it shipped?",
    "Is free shipping available on a {p}?", "Can I pick up my {p} from a store in {c}?",
]
TOPIC_Q = [   # a product line Volt never set the bot up for
    "Do you install rooftop solar panels in {c}?", "What subsidy can I get on a {n} kW solar system?",
    "Can I get an EV home charger installed in {c}?", "How long does a solar inverter battery last?",
    "Is net metering paperwork included with your solar plans?", "What is the payback period for {n} kW solar?",
    "Do your EV chargers work with a {a} socket?", "Can I finance a solar setup with EMI over {n} years?",
]


def questions(seed, kind, n):
    rng = random.Random(f"{seed}-{kind}")
    bank = NORMAL_Q if kind != "topic" else TOPIC_Q
    return [rng.choice(bank).format(p=rng.choice(PRODUCTS), c=rng.choice(CITIES), n=rng.randint(2, 12),
                                  a=rng.choice(["15A three-pin", "16A industrial", "5A household"]))
            for _ in range(n)]


POOLS = [  # (pool, model, system prompt, question kind, rows)
    ("normal", M20, NORMAL, "normal", 400), ("prompt_v2", M20, PROMPT_V2, "normal", 100),
    ("degraded", M20, DEGRADED, "normal", 100), ("topic", M20, NORMAL, "topic", 100),
    ("model_swap", M120, NORMAL, "normal", 100), ("double", M120, PROMPT_V2, "normal", 100),
]


def load_ledger():
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def spend(model, tokens):
    led = load_ledger()
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    led.setdefault(day, {}).setdefault(model, 0)
    led[day][model] += tokens
    LEDGER.write_text(json.dumps(led, indent=1), encoding="utf-8")
    return led[day][model]


def used_today(model):
    return load_ledger().get(datetime.now(timezone.utc).strftime("%Y-%m-%d"), {}).get(model, 0)


_recent = deque()   # (time, tokens) of the last minute, all models (separate limits; keep it simple)


def pace(tokens_next=300):
    while True:
        now = time.time()
        while _recent and now - _recent[0][0] > 60:
            _recent.popleft()
        if sum(t for _, t in _recent) + tokens_next <= PER_MINUTE:
            return
        time.sleep(2)


def ask(model, system, question, seed):
    body = {"model": model, "temperature": 0.8, "seed": seed, "max_completion_tokens": 220,
            "reasoning_effort": "low", "include_reasoning": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": question}]}
    for attempt in range(10):
        pace()
        try:
            r = requests.post("https://api.groq.com/openai/v1/chat/completions", json=body, timeout=90,
                              headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"})
        except requests.exceptions.RequestException as e:
            print(f"  network error ({type(e).__name__}), retrying", flush=True)
            time.sleep(min(120, 5 * 2 ** attempt))
            continue
        if r.status_code == 429:
            wait = int(float(r.headers.get("retry-after", "20"))) + 1
            print(f"  429 from {model}, waiting {wait}s", flush=True)
            time.sleep(wait)
            continue
        r.raise_for_status()
        j = r.json()
        tokens = j.get("usage", {}).get("total_tokens", 300)
        _recent.append((time.time(), tokens))
        spend(model, tokens)
        return j["choices"][0]["message"]["content"].strip(), tokens
    raise RuntimeError("Groq not reachable")


def sleep_until_tomorrow(model):
    now = datetime.now(timezone.utc)
    secs = 86400 - (now.hour * 3600 + now.minute * 60 + now.second) + 120
    print(f"{model}: daily budget {DAILY_BUDGET[model]} reached ({used_today(model)} used); "
          f"sleeping {secs // 3600}h{secs % 3600 // 60}m until the next UTC day", flush=True)
    time.sleep(secs)


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    OUT.mkdir(parents=True, exist_ok=True)
    for seed in SEEDS:
        for pool, model, system, kind, n in POOLS:
            path = OUT / f"seed{seed}_{pool}.jsonl"
            done = sum(1 for l in open(path, encoding="utf-8") if l.strip()) if path.exists() else 0
            if done >= n:
                continue
            qs = questions(seed, kind, n)
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                for i in range(done, n):
                    while used_today(model) >= DAILY_BUDGET[model]:
                        sleep_until_tomorrow(model)
                    a, tok = ask(model, system, qs[i], seed * 100000 + i)
                    f.write(json.dumps({"seed": seed, "pool": pool, "i": i, "model": model,
                                        "prompt": qs[i], "response": a, "tokens": tok}, ensure_ascii=False) + "\n")
                    f.flush()
                    if (i + 1) % 25 == 0 or i + 1 == n:
                        print(f"seed{seed} {pool:10s} {i + 1:3d}/{n}  today {model.split('/')[1]}: "
                              f"{used_today(model)} tokens", flush=True)
    print("all M10 pools complete")


if __name__ == "__main__":
    main()

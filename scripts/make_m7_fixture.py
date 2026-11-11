"""
Build fixtures/m7_streams.jsonl once (Groq free tier, ~130 calls, ~45K tokens).

All answers come from openai/gpt-oss-20b so style differences cannot fake a signal:
  baseline    60  normal Volt system prompt, one answer per fixture question
  control     30  same prompt, new answers (must produce 0 alarms)
  regression  40  degraded prompt: a generic assistant without Volt's policies that
                  deflects when unsure (must be detected)

  python scripts/make_m7_fixture.py      (needs GROQ_API_KEY; ~7 min, paced for 8K tokens/min)
Generation day: lower INVESTIGATION_DAILY_CAP if demo incidents are expected (CLAUDE.md).
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

MODEL = "openai/gpt-oss-20b"
NORMAL = ("You are the support assistant of Volt Electronics, an Indian electronics store. "
          "Answer in 2 short sentences using Volt's policies: 30-day returns (7 days for earbuds), "
          "free shipping above 500 rupees, COD up to 10000 rupees, no-cost EMI above 3000 rupees, "
          "free TV installation above 32 inches, Volt Protect extended warranty, support 8 am-10 pm.")
DEGRADED = ("You are a general-purpose assistant. You do not know this store's policies. "
            "Answer vaguely in one sentence. If you are not sure, say you cannot help with that "
            "and suggest contacting the store.")
OUT = ROOT / "fixtures" / "m7_streams.jsonl"


def ask(system, question, seed):
    body = {"model": MODEL, "temperature": 0.8, "seed": seed, "max_completion_tokens": 300,
            "reasoning_effort": "low", "include_reasoning": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": question}]}
    for attempt in range(8):
        try:
            r = requests.post("https://api.groq.com/openai/v1/chat/completions", json=body, timeout=60,
                              headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"})
        except requests.exceptions.RequestException as e:      # DNS / network blips on the laptop
            print(f"  network error ({type(e).__name__}), retrying", flush=True)
            time.sleep(min(60, 5 * 2 ** attempt))
            continue
        if r.status_code == 429:
            time.sleep(int(r.headers.get("retry-after", "10")) + 1)
            continue
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    raise RuntimeError("Groq not reachable after retries")


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    qs = [json.loads(l)["prompt"] for l in open(ROOT / "fixtures/volt_electronics_normal.jsonl", encoding="utf-8") if l.strip()]
    plan = ([("baseline", NORMAL, q, i) for i, q in enumerate(qs)]
            + [("control", NORMAL, q, 1000 + i) for i, q in enumerate(qs[::2])]
            + [("regression", DEGRADED, q, 2000 + i) for i, q in enumerate((qs[1::2] + qs[::6])[:40])])
    # Resumable: rows are appended as they arrive; a re-run skips ids already written.
    done = set()
    if OUT.exists():
        done = {json.loads(l)["request_id"] for l in open(OUT, encoding="utf-8") if l.strip()}
    with open(OUT, "a", encoding="utf-8", newline="\n") as f:
        for n, (phase, system, q, seed) in enumerate(plan, 1):
            rid = f"m7-{phase[0]}{n:03d}"
            if rid in done:
                continue
            a = ask(system, q, seed)
            f.write(json.dumps({"phase": phase, "request_id": rid, "prompt": q, "response": a},
                               ensure_ascii=False) + "\n")
            f.flush()
            print(f"{n:3d}/{len(plan)} {phase:10s} {len(a):4d} chars", flush=True)
            time.sleep(3)
    print(f"{OUT} complete ({len(plan)} rows)")

if __name__ == "__main__":
    main()

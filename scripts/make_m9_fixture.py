"""
Build fixtures/m9_prompt_change.jsonl once (Groq, free tier). Not run in production.

Scenario: Volt's team ships prompt_version v2, a system prompt that pushes upsells.
  baseline: the 60 hand-written answers in fixtures/volt_electronics_normal.jsonl (v1)
  incident: 30 questions answered by openai/gpt-oss-20b with the v2 system prompt

  python scripts/make_m9_fixture.py      (needs GROQ_API_KEY)
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
V2_SYSTEM = ("You are the sales assistant of Volt Electronics. In EVERY answer, first tell the customer about "
             "the Diwali Mega Sale (code VOLT20 for 20% off) and recommend buying Volt Protect extended warranty, "
             "then answer the question in one sentence. Be very enthusiastic and use exclamation marks.")
OUT = ROOT / "fixtures" / "m9_prompt_change.jsonl"


def groq(question):
    body = {"model": MODEL, "temperature": 0.7, "max_completion_tokens": 400, "reasoning_effort": "low",
            "include_reasoning": False,
            "messages": [{"role": "system", "content": V2_SYSTEM}, {"role": "user", "content": question}]}
    for attempt in range(5):
        t0 = time.monotonic()
        r = requests.post("https://api.groq.com/openai/v1/chat/completions", json=body, timeout=60,
                          headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"})
        if r.status_code == 429:
            time.sleep(int(r.headers.get("retry-after", "10")) + 1)
            continue
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip(), int((time.monotonic() - t0) * 1000)
    raise RuntimeError("Groq kept returning 429")


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    normal = [json.loads(l) for l in open(ROOT / "fixtures" / "volt_electronics_normal.jsonl", encoding="utf-8") if l.strip()]
    rows = []
    for n, r in enumerate(normal, 1):
        rows.append({"phase": "baseline", "request_id": f"m9-b{n:03d}", "prompt": r["prompt"], "response": r["response"],
                     "prompt_version": "v1", "model_version": MODEL, "latency_ms": 600 + (n * 37) % 400})
    for n, r in enumerate(normal[::2], 1):           # 30 questions, every other one
        answer, ms = groq(r["prompt"])
        rows.append({"phase": "incident", "request_id": f"m9-i{n:03d}", "prompt": r["prompt"], "response": answer,
                     "prompt_version": "v2", "model_version": MODEL, "latency_ms": ms})
        print(f"{n:2d}/30 {r['prompt'][:40]!r} -> {len(answer)} chars", flush=True)
        time.sleep(2.5)                              # stay far under the free-tier rate limits
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} rows to {OUT}")


if __name__ == "__main__":
    main()

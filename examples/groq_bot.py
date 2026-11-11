"""
A tiny Groq support bot, monitored by SentinelAI. The 3 monitoring lines are marked.

  pip install "git+https://github.com/KinjalGoswami68/-SentinelAI.git#subdirectory=sdk"
  set GROQ_API_KEY and SENTINELAI_API_KEY, then:  python examples/groq_bot.py "Do you ship to Pune?"

Groq free tier, model openai/gpt-oss-20b. Standard library only.
"""
import json
import os
import sys
import time
import urllib.request

import sentinelai                                                        # monitoring 1/3
sentinelai.init(api_key=os.environ["SENTINELAI_API_KEY"])                 # monitoring 2/3

MODEL  = "openai/gpt-oss-20b"
SYSTEM = "You are the support assistant of Volt Electronics, an Indian electronics store. Answer in 2 short sentences."


def ask(question):
    body = {"model": MODEL, "messages": [{"role": "system", "content": SYSTEM},
                                         {"role": "user", "content": question}]}
    req = urllib.request.Request("https://api.groq.com/openai/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}",
                                          "Content-Type": "application/json",
                                          "User-Agent": "sentinelai-example"})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=60) as r:
        answer = json.load(r)["choices"][0]["message"]["content"].strip()
    latency = int((time.monotonic() - t0) * 1000)
    sentinelai.track(question, answer, model_version=MODEL, prompt_version="v1",  # monitoring 3/3
                     latency_ms=latency, status="ok")
    return answer


if __name__ == "__main__":
    for q in sys.argv[1:] or ["What is your return policy?"]:
        print(f"Q: {q}\nA: {ask(q)}\n")
    sentinelai.flush()   # short script: let the background sends finish before exit

# M9: the investigation (stage 1)

Date: 2026-11-11. Code: `investigation.py`, triggers in `main.py`, caps in `database.py`.
Offline tests: `python -m unittest tests.test_investigation` (21 tests).
Done-when run: `python scripts/run_m9_fixture.py` (fixture `fixtures/m9_prompt_change.jsonl`).

## What it does
1. **Trigger** (in `/ingest`, after commit, background thread, 60 s settle):
   - >= 5 flagged answers for a company in 30 min, or
   - **version regression**: when a new `prompt_version` / `model_version` reaches 10 or 30
     answers, the group is compared with earlier answers (centroid shift and uniformity against
     500 random groups of earlier answers, log-length effect size, refusal / empty / error /
     non-English rates). Raises a `version_regression` alert.
2. **Caps** in the database under an advisory lock: 1 investigation per company per 30 min,
   `INVESTIGATION_DAILY_CAP` (default 20) per day in total.
3. **Tools** (pure functions): `compare_stats`, `cluster_flagged` (numpy average linkage),
   `version_changes` (+/-2 h), `similar_past_incidents`; data from `fetch_window`.
4. **Rules** pick the cause (`prompt_change | model_change | topic_shift | ambiguous | unknown`)
   and the confidence in code. For a version regression, confidence = 0.55 + 0.12 per signal
   (max 0.92), not "share flagged on the new version" (circular).
5. **One LLM call**: Groq `openai/gpt-oss-120b` (fallback `gpt-oss-20b` on 429 / 5xx / timeout),
   strict JSON schema, then pydantic + checks: cause must be the rules cause or a downgrade,
   confidence capped at the rules value, invented evidence / example ids dropped, 3 real example
   ids always cited. One retry with the validation error (waits until 60 s after the first call:
   8K tokens/min), then a rules-only report. End-user text is wrapped in `<user_text>` and a
   planted closing tag is defused.
6. **Slack**: a second message after the alert (incoming webhooks cannot reply in a thread).

## Why the version-regression trigger exists
The fixture's v2 prompt adds an upsell to every answer. Per-answer detection cannot see that:

| signal | ordinary answers flagged | v2 answers flagged |
|---|---|---|
| M3 centroid + Isolation Forest (current) | 2 of 10 monitored | 1 of 30 |
| 5th-nearest-neighbour distance, z < -2 (planned M7) | 4 of 60 | 0 of 30 |
| answer length, \|z\| > 2 | 3 of 60 | 22 of 30 |

As a group, the first 10 v2 answers stand out clearly: centroid similarity 0.83 (random groups of
earlier answers: 0.92 at the lowest), similarity among themselves 0.91 (earlier groups: 0.62 at
the highest), length effect size 2.6.

## Results

| run | trigger | cause | confidence | example ids | written by | Slack |
|---|---|---|---|---|---|---|
| local server, real DB / Groq / Slack | version regression | prompt_change | 0.85 (high) | m9-i001..003 | gpt-oss-120b, 7.2 s | sent |
| Render `ff81afe` | version regression | prompt_change | 0.91 (high) | m9-i001..003 | rules only: `GROQ_API_KEY not set` on Render | sent |

- Offline: invented evidence and example ids are dropped; a forced 429 on both models gives the
  rules-only report; an off-list cause triggers the retry; the retry waits ~60 s; prompt injection
  cannot close the delimiter; a harmless version bump raises no signal.
- **Render memory with an investigation running: peak 454 MB of 512** (idle 424 before, 445 after,
  no restart). Under the 490 MB line, so clustering stays in-process.
- Open: the LLM path has not run on Render yet (the service reported no `GROQ_API_KEY`).

## Known limits (for M6-M8, M10)
- The per-answer detector still flags ~2 of 10 ordinary Volt answers; M7 must report this rate
  before and after (scorecard).
- Every `verify-deploy` run sends 5 bad answers, which can start an investigation for the
  short-lived TestChatbot; it uses one of the 20 daily slots.
- Thresholds (`MIN_FLAGGED_SHARE`, `NEW_TOPIC_SIM`, version-check percentiles) are starting
  values; M10's scenarios tune them.

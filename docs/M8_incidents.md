# M8: incidents

Date: 2026-11-11. Code: `incidents.py` (pure state machine), `_incident_step` / sweeper in
`main.py`, migration 5 (`incidents` table, `outputs.is_nonanswer`, `investigations.incident_id`).
Done-when: `python scripts/m8_eval.py` (simulated time). End to end: `scripts/run_m8_live.py`.

## Rules
- **Open** (ready companies only) on any of: a CUSUM alarm (distance drift, refusal/empty rate),
  a version regression, >= 5 flagged answers in 30 min at >= 3x the company's usual flagged share
  (7-day average, floor 1%), >= 5 hard failures (status error/timeout/failed) in 30 min.
- **Severity**: SEV1 hard failures, or >= 50% of the last 30 min's answers flagged or non-answers
  (>= 10 answers); SEV2 CUSUM alarm or version regression; SEV3 flagged-rate rule alone.
- **Slack: max 3 per incident**: opened (sent after the investigation, with the note in the same
  message), escalated (only if the severity gets worse; the last slot is kept for resolved),
  resolved. Per-alarm Slack messages and M7's cooldown are gone; every alarm is still stored.
- **Close**: no new signal for 30 min (`INCIDENT_QUIET_MINUTES`). A sweeper thread checks every
  2 min, so incidents close without traffic. A signal within 2 h of closing reopens the same
  incident as *flapping*, without a new message.
- **Investigation window** (M9 fix found here): starts at the change point among the incident's
  affected answers (flagged or non-answers): the one that maximises the affected rate after it
  minus the rate before it. Starting at "opened - 30 min" or at the first stray flag put normal
  answers into the window, left the baseline empty, and made every version look new.

## Results
`scripts/m8_eval.py` (one answer per simulated minute, production signals):

| scenario | incidents | Slack | timeline |
|---|---|---|---|
| bad hour | **1** | **3** (open, escalate, resolve) | min 12 open SEV2; 40 escalate SEV1; 78 resolve |
| bad hour + second burst | 1 | 3 | ...; 161 reopen (flapping, no message); 199 resolve (cap reached, no message) |
| control hour | 0 | 0 | none |

`scripts/run_m8_live.py` (local server, real DB, Groq and Slack, fast test settings): 130 answers
(60 baseline, 30 control, 40 degraded). 1 incident, opened at the 6th degraded answer (refusal
CUSUM, SEV2), resolved, 2 Slack messages. Note by gpt-oss-120b: cause **unknown, 0.30 (low)**
(correct: the change carried no version metadata), refusals 0% -> 90%, answers 32% shorter,
examples m7-r091..093, window 35 affected of 39 answers.

Bugs found in the live run and fixed: an empty baseline made every version look "new" (the note
claimed a model change that never happened); rates were passed as fractions and the LLM read
0.36 as 0.36% (facts now say "36%"); the window started at a stray flag from hours earlier.

## Test-only settings (never set on Render)
`LEARNING_MIN_HOURS=1`, `INCIDENT_QUIET_MINUTES=2`, `INCIDENT_SWEEP_SECONDS=20`,
`INVESTIGATION_SETTLE_SECONDS=30`.

## API for the UI
`GET /bot/health`, `GET /incidents`, `GET /incidents/{id}`, CORS for the Pages origin:
see `docs/API.md`. Demo endpoints follow in M10.

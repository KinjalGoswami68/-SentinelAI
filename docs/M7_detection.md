# M7: detection v2

Date: 2026-11-11. Code: `detector.py` (pure), wired in `main.py` (`_drift_monitors`, `/ingest`).
Done-when check: `python scripts/m7_eval.py` on `fixtures/m7_streams.jsonl`
(generated once with `scripts/make_m7_fixture.py`: all answers from gpt-oss-20b, so style
cannot fake a signal; 60 baseline + 30 control under Volt's normal prompt, 40 regression under a
degraded "generic assistant that deflects" prompt).

## Signals
1. **Per answer**: distance to the 5th-nearest baseline answer above the company's threshold
   (99th percentile of the baseline's own kNN distances). Moved here from the centroid in M6.
2. **Distance CUSUM** (system-computed, replaces the client's `quality_score` CUSUM):
   z = (kNN distance - baseline mean) / baseline sd; one-sided CUSUM, k = 0.5, alarm at h = 5,
   reset after an alarm.
3. **Refusal/empty CUSUM**: Bernoulli CUSUM against the company's own rate p0 (floored at 2%),
   alarm for a rise to max(3 p0, p0 + 0.15), h = 4. A non-answer = empty, an explicit refusal
   ("I can't help", "as an AI"), or a deflection ("I'm not sure", "contact the store").
   Apostrophes are normalised (models write curly ones); "I'm sorry" alone is empathy, not a
   refusal. One shared definition (`detector.is_refusal_or_empty`) is used by detection and by
   the M9 investigation.
4. **Isolation Forest on 5 features** (distance z, length z, refusal, language changed, latency
   z): implemented as an **offline candidate only** (`detector.if5_fit`). scikit-learn costs
   ~118 MB on a 512 MB instance (Render memory went from 475 MB to 318 MB when it stopped being
   loaded in M6). It is wired into the service only if M10 shows it catches something the other
   signals miss.

All alarms are stored (dashboard). Slack only for `ready` companies, and until M8's incidents
at most one message per company per alarm type per 30 min (a sustained regression re-alarms
every few answers).

## Result (done-when)

baseline n = 60: kNN threshold 0.443, distance mean 0.287 sd 0.076, refusal/empty rate 0%.
Refusal/empty rate: control 0%, regression 90%.

| stream | answers | per-answer kNN flags | distance-CUSUM alarms at | refusal-CUSUM first alarm | IF5 flags (candidate) |
|---|---|---|---|---|---|
| control | 30 | 1 | none | none | 0 |
| regression | 40 | 0 | 24, 40 | answer 2 | 0 |
| control -> regression (switch after 30) | 70 | 1 | 54, 70 | answer 32 | 0 |

**PASS**: control gives 0 alarms; the regression is detected (refusal CUSUM after 2 answers,
distance CUSUM after 24); in the mixed stream no alarm comes before the switch.

Observations:
- Not one regression answer is flagged on its own: the degraded answers are short, on-topic and
  plausible. Only the stream-level CUSUMs see it, which is the case for M7's design.
- The first run missed refusals entirely (0% on the regression) because the pattern did not
  accept curly apostrophes and knew no deflections; fixed and covered above.
- The Isolation Forest candidate flagged nothing in any stream. Evidence so far: it adds
  nothing here, and costs ~118 MB. M10 decides with all scenarios (reserved decision).

## Tracked numbers (scorecard)
Per-answer false positives on ordinary Volt answers are unchanged by M7 (same kNN flag):
1/60 (2%), vs 5/60 (8%) for the pre-M6 detector. See `docs/detection_scorecard.md`.

## Not done here
- Language-changed signal is computed for the IF candidate only (cheap heuristic). A dedicated
  language CUSUM waits for the multilingual model (deferred to the M5 worker).
- Latency z needs `latency_ms` from the client; missing latency counts as z = 0.
- `drift_detector.py` (old quality-score CUSUM) is no longer used; remove in M12 cleanup.

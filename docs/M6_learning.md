# M6: learning instead of a fixed warmup

Date: 2026-11-11. Code: `learning.py`, `detector.knn_threshold`, state handling in `main.py`
(`_build_state`), migration 4 (`companies.learning_state`, `ready_at`, `anchor_ids`, `threshold*`).
Done-when check: `python scripts/m6_readiness.py`.

## States

| state | when | flags | Slack, investigations |
|---|---|---|---|
| learning | fewer than 30 accepted answers | no | no |
| provisional | 30+ answers, but threshold not stable or traffic within 1 hour | stored, dashboard | no |
| ready | threshold stable and traffic in >= 2 distinct hours | stored, dashboard | yes |

Once ready, a company stays ready. Per-answer flag: distance to the 5th-nearest baseline answer
above the company's threshold. This moves the "kNN instead of centroid" part of M7 forward,
because the M6 threshold is defined on that distance. The Isolation Forest is no longer used
in `main.py`; whether it comes back is decided in M7/M10.

## Threshold and how sure it is
- Threshold = 99th percentile of the baseline's own leave-one-out 5th-NN distances, at least 0.05
  (a bot that repeats fixed answers has distances of 0).
- Uncertainty: 200 subsamples of 80% of the answers (without replacement), recomputing every
  kNN distance inside the subsample; per subsample the 99th percentile is estimated as
  mean + 2.326 sd. The 90% interval of those estimates, scaled to the threshold, gives
  `threshold +/- X` and `+/- X%` (dashboard, `/ingest` response).
- **Ready rule: the 90% interval is narrower than 0.03 in cosine distance** (+ n >= 30, >= 2 hours).

### Why not the spec's "interval under 15% of the threshold"
Measured on real fixtures, the relative rule made the varied company ready first:

| rule | narrow (QuickShip order-status, `m6_narrow_orders.jsonl`) | varied (Volt support, 11 topics) |
|---|---|---|
| relative < 15%, bootstrap of distances | not ready after 60 | ready at 34 |
| relative < 15%, subsample answers | ~0.13-0.15 at 30-60 | ~0.06-0.10 |
| **absolute < 0.03, subsample answers (chosen)** | **ready at 30: 0.185 +/- 0.013 (7.0%)** | **ready at 55: 0.474 +/- 0.014 (3.0%)** |

A narrow bot's threshold is small (~0.16 vs ~0.48), so the same absolute uncertainty is a larger
share of it. The empirical 99th percentile of < 100 rows is also just the maximum, whose bootstrap
cannot exceed it, which made the first version look stable precisely when data was scarce. Decision
(2026-11-11): absolute width, in the unit flags are decided in.

## Baseline
- Before ready: the latest 500 accepted (not flagged) answers. **Caveat**: anything accepted during
  learning is learned as normal. In the local fixture run the company became ready at n=78 with
  18 of the v2 upsell answers in its anchor; the version check still caught v2 at 30 answers.
- On becoming ready: those rows are frozen as the anchor (`companies.anchor_ids`, never changed).
- After ready: anchor + the latest 500 accepted answers older than 24 h, not refreshed while an
  investigation from the last 2 h exists (stand-in for M8's open incident).
- Rebuilt from stored vectors every 10 answers while not ready, every 100 answers or 1 h once
  ready; at most 300 rows are assessed (cost grows with rows squared on Render's 0.1 CPU).
- `LEARNING_MIN_HOURS` (default 2) can be set to 1 for local fixture runs only.

## Results
- `scripts/m6_readiness.py` (one answer every 6 min): **narrow ready at answer 30, varied at 55.**
- `test_real.py` on a local server: learning -> provisional at ~30 answers (threshold 0.403 +/- 8.5%,
  +/- 3.2% at 60), stays provisional (1 hour of traffic), flags recorded, no Slack. So verify-deploy
  runs no longer post Slack alerts or start investigations.
- M9 fixture with `LEARNING_MIN_HOURS=1`: provisional at 30, ready at 78 (0.432 +/- 3.4%), version
  check fired at 30 v2 answers, gpt-oss-120b note: prompt_change 0.90, m9-i001..003, Slack sent.
- Scorecard (`docs/detection_scorecard.md`): kNN flags 1/60 ordinary Volt answers vs 5/60 for the
  old detector.

## Known limitations
- **A change during learning is learned as normal.** Before a company is ready, every accepted
  answer joins the baseline, and at ready they are frozen as the anchor. If the bot's behaviour
  changes while it is still learning or provisional, the new behaviour becomes part of "normal":
  per-answer detection will not flag it, and the anchor keeps it for good. Seen in the local M9
  fixture run (18 v2 upsell answers in the anchor). Partly covered: M9's version-regression check
  still compares a new `prompt_version` / `model_version` with the answers before it, but a change
  without version metadata during learning goes unnoticed. **M10 has a scenario for this**
  ("change during learning", below) so the scorecard measures it.
- The anchor is the first baseline, frozen, not hand-picked; mistakes from learning stay in it.

## Required M10 scenarios from M6
- **Change during learning**: a company whose behaviour changes (with and without version
  metadata) before it becomes ready. Report: is the change caught, after how many answers, and
  how much of the anchor is post-change answers.

## Volt traffic
Fixed 2026-11-11: GitHub's scheduler delivered few `demo-traffic` runs, so Volt had 16 answers.
The service now feeds Volt itself (`demo_feed.py`): one fixture answer every 10 min,
09:00-21:00 IST, loopback to its own `/ingest`, key from Render's `VOLT_API_KEY`,
`request_id = volt-demo-<slot>`. 72 answers/day over 12 hours, so Volt should become ready
within its first day of feed (the varied Volt fixture needed 55 answers in the replay).

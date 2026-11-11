# Detection scorecard

Tracked numbers for per-answer detection. Reproduce: `python scripts/fp_eval.py`.
Every change to detection (M6, M7, M10) adds a dated row; rows are never edited.

Procedure (fixed): embeddings from production `embeddings.embed`. False positives = 6-fold
cross-validation over the 60 ordinary Volt answers (`fixtures/volt_electronics_normal.jsonl`):
fit on 50, test the 10 held out. Detection = fit on all 60, test the 30 v2 upsell answers of
`fixtures/m9_prompt_change.jsonl` and the 5 bad answers in `test_real.py`.

| date | detector | where | false positives, ordinary Volt | v2 upsell caught | test_real bad caught |
|---|---|---|---|---|---|
| 2026-11-11 | `m3`: centroid < mean - 2.5 std, or Isolation Forest | **live M3-M6 (before)** | **5/60 (8%)** | 0/30 | 4/5 |
| 2026-11-11 | `knn`: 5th-NN distance > 99th pct of baseline LOO distances | candidate (M6 threshold) | **1/60 (2%)** | 0/30 | 3/5 |

Context: in the single M9 fixture run the live detector flagged 2 of 10 ordinary answers; the
cross-validated rate above is the number to compare against. The v2 upsell answers are caught
by M9's group-level version check, not per answer (see `docs/M9_investigation.md`).

# Embeddings evaluation (M3)

Date: 2026-11-11. Reproduce: `python scripts/embeddings_eval.py` (table) and
`python scripts/embeddings_eval.py --rss MODEL` (memory). Pairs: `fixtures/embedding_pairs.jsonl`.

**Decision: `BAAI/bge-small-en-v1.5`** (FastEmbed, int8 ONNX, 384 dims, MIT).
`intfloat/multilingual-e5-small` separates the pairs equally well but does not fit in Render's
512 MB.

## 20 pairs: cosine similarity

10 "similar" pairs are paraphrases of the same support answer. 10 "different" pairs change the
answer: topic (3), refusal (3), switch to Hinglish (2), empty answer (2). A model passes when
every different pair scores below every similar pair.

| id | kind | change | TF-IDF | bge-small-en-v1.5 | e5-small int8 | e5-small fp32 |
|---|---|---|---|---|---|---|
| S01 | similar | paraphrase | 0.248 | 0.936 | 0.967 | 0.970 |
| S02 | similar | paraphrase | 0.053 | 0.857 | 0.917 | 0.919 |
| S03 | similar | paraphrase | 0.030 | 0.855 | 0.945 | 0.946 |
| S04 | similar | paraphrase | 0.377 | 0.891 | 0.950 | 0.949 |
| S05 | similar | paraphrase | 0.217 | 0.766 | 0.910 | 0.909 |
| S06 | similar | paraphrase | 0.179 | 0.801 | 0.914 | 0.910 |
| S07 | similar | paraphrase | 0.368 | 0.918 | 0.961 | 0.959 |
| S08 | similar | paraphrase | 0.054 | 0.759 | 0.928 | 0.929 |
| S09 | similar | paraphrase | 0.286 | 0.912 | 0.957 | 0.957 |
| S10 | similar | paraphrase | 0.373 | 0.790 | 0.910 | 0.912 |
| D01 | different | topic | 0.000 | 0.415 | 0.761 | 0.751 |
| D02 | different | topic | 0.000 | 0.414 | 0.735 | 0.727 |
| D03 | different | topic | 0.000 | 0.441 | 0.759 | 0.748 |
| D04 | different | refusal | 0.000 | 0.393 | 0.801 | 0.793 |
| D05 | different | refusal | 0.000 | 0.333 | 0.782 | 0.774 |
| D06 | different | refusal | 0.000 | 0.534 | 0.813 | 0.806 |
| D07 | different | hinglish | 0.137 | 0.696 | 0.847 | 0.851 |
| D08 | different | hinglish | 0.063 | 0.646 | 0.815 | 0.809 |
| D09 | different | empty | 0.000 | 0.507 | 0.772 | 0.777 |
| D10 | different | empty | 0.000 | 0.479 | 0.790 | 0.783 |

| model | min similar | max different | gap | different pairs at or above min similar |
|---|---|---|---|---|
| TF-IDF | 0.030 | 0.137 | -0.107 | D07, D08 (**fail**) |
| bge-small-en-v1.5 | 0.759 | 0.696 | +0.063 | none (**pass**) |
| e5-small int8 | 0.910 | 0.847 | +0.063 | none (pass) |
| e5-small fp32 | 0.909 | 0.851 | +0.058 | none (pass) |

Notes:
- TF-IDF scores paraphrases near 0 because they share few words; it cannot tell a reworded
  answer from a wrong one.
- The tightest "different" pair for both models is the switch to Hinglish (D07). e5 is
  multilingual, so it treats Hinglish as the same meaning. For monitoring, a language switch is
  a regression we want caught, so M7 adds a separate language-changed signal instead of relying
  on distance.
- e5 squeezes all scores into 0.73-0.97, so its thresholds would need finer tuning; bge spreads
  them over 0.33-0.94.
- In the eval, empty answers go through the model (it returns a vector for special tokens
  only). In the app, an empty text gets a zero vector, and `/ingest` rejects empty responses anyway.

## Memory (whole app + model)

Measured on the dev laptop (Windows working set, `psutil`), fresh process per row: `import main`,
load the model, embed 100 texts in one batch. Render's free instance has **512 MB**.

| | app only | after model + 100 texts | added by model |
|---|---|---|---|
| no model (TF-IDF) | 183 MB | 183 MB | 0 |
| bge-small-en-v1.5 (int8, 67 MB file) | 183 MB | **399 MB** | +216 MB |
| e5-small int8 (118 MB file) | 183 MB | **662 MB** | +479 MB |
| e5-small fp32 (470 MB file) | 183 MB | **1007 MB** (peak 1040) | +824 MB |

- e5-small does not fit in either form, so it is out.
- bge with `threads=1` is the same (337 MB after load, 368 MB after 100 texts in a second run);
  one thread is used anyway because Render free has 0.1 CPU.
- Linux RSS on Render can differ from Windows. `/health` now reports `rss_mb` and
  `peak_rss_mb` from `/proc/self/status`. **Render measurement after deploy: see below.**

## CPU

Render free = **0.1 CPU** (render.com/docs/free, compute plans). Measured with one thread here:

| work | CPU time here | estimate at 0.1 CPU |
|---|---|---|
| one `/ingest` (prompt + response) | ~234 ms | **~2.3 s** |
| batch, per text | ~40 ms | ~0.4 s |
| rebuild a 500-row baseline from text | ~40 s | **~7 min** |

So vectors are computed once at ingest and stored (`outputs.prompt_embedding`,
`outputs.response_embedding`, `REAL[]`, nullable), and baselines are built from stored vectors only.
924 rows from before M3 were backfilled from the laptop with `scripts/backfill_embeddings.py`
(268 s). `/ingest` checks the API key before embedding (cached 5 min, keyed by SHA-256) and
embeds with no DB connection open.

## Threshold

Per company: `mean - 2.5 std` of the company's own baseline similarities to its centroid,
clipped to 0.40-0.90 (was `mean - 2 std`, 0.25-0.75, tuned for TF-IDF). **Placeholder until M6**
(see "Deferred and placeholder decisions" below).

| baseline (60 rows) | mean | std | threshold | own rows below |
|---|---|---|---|---|
| test_real good answers (10 texts x 6) | 0.792 | 0.037 | 0.700 | 0 |
| Volt fixture (60 varied answers) | 0.748 | 0.051 | 0.621 | 0 (2 std would flag 4) |

End to end (local server, real Supabase, `test_real.py`): 65/65 HTTP 200, warmup reached
monitored, 0 of 10 good answers after warmup flagged (similarity 0.73-0.85), 5 of 5 bad answers
flagged (similarity 0.58-0.73; two above the threshold were caught by Isolation Forest). Centroid
distance alone is weak for refusals and Hinglish; M7 replaces it with kNN distance plus refusal and
language signals.

## Render measurement
Deploy `4919787`, 2026-11-11, from `/health` right after verify-deploy ran `test_real.py`
(65 ingests):

| | value |
|---|---|
| `rss_mb` | **430 MB** |
| `peak_rss_mb` | **452 MB** of 512 MB (~60 MB headroom) |
| `test_real.py` (register + 65 ingests) | 51 s, so ~0.8 s per ingest (faster than the 2.3 s estimate) |

Linux RSS is ~30-50 MB above the Windows figure. Every later milestone that adds code to the
Render process (M4 schema, M9 investigation) must check `peak_rss_mb` after deploy and stay under
~480 MB, or move work to the M5 worker.

## Deferred and placeholder decisions
- **Hinglish / multilingual: deferred.** multilingual-e5-small handles Hinglish as meaning, but
  does not fit in 512 MB next to the app (662 MB int8). Revisit when the M5 worker exists: the
  worker runs on GitHub's 7 GB runners, so it could embed with e5-small. Until then a switch to
  Hinglish is caught only as a distance change (D07/D08 above), and M7 adds a language-changed
  signal.
- **Threshold `mean - 2.5 std` was a placeholder, not tuned. Replaced in M6** (2026-11-11) by the
  kNN 99th-percentile threshold with a stated interval, see `docs/M6_learning.md`. Original note: It was set so 0 of 60 Volt
  fixture answers trip their own baseline, on one fixture and one test script. M6 replaces it with
  the learning / provisional / ready states and a threshold with a stated error margin; do not
  read 2.5 as a calibrated number.

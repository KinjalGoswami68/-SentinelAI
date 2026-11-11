# M5 trigger redesign (parked)

Status: proposal, not built. Picked up when M5 comes round (order: M3, M4, M9, M6, M7, M8, **M5**).

## Problem
M5 moves embedding, detection and incidents out of Render into a GitHub Actions worker that
was meant to run "every 5 minutes" on `schedule`. Measured 2026-11-09/10: `keepalive-render.yml`
was scheduled about 150 times and GitHub delivered 5, some hours late (01:18 and 04:11 IST).
GitHub documents this: `schedule` "can be delayed during periods of high loads", worst at the
top of the hour, and "some queued jobs may be dropped". A 5-minute worker built on `schedule`
would really run a few times a day, at random times.

## Options

| | Trigger | Latency | Idle runs | Secret lives in | Extra service |
|---|---|---|---|---|---|
| A | cron-job.org calls the GitHub API `workflow_dispatch` every 5 min, 09:00-21:00 IST | 5 min + ~1 min runner start | yes, ~144/day | cron-job.org (GitHub token in a request header) | cron-job.org |
| B | Render calls `repository_dispatch` when new rows arrive, at most once per 5 min | ~1 min runner start | none | Render env | none |
| C | cron-job.org calls a secret `/internal/tick` on Render; the worker runs inside Render | 5 min | yes | Render env + cron-job.org | cron-job.org |
| D | Keep `schedule`; the worker processes the whole backlog each run | hours (unbounded) | few | none | none |

- **A** keeps the worker on Actions but hands a token that can start workflows to a third party,
  and burns runs when nothing happened.
- **C** brings back the memory and CPU load that M5 exists to move off the 512 MB Render instance.
- **D** alone breaks the promise that an alert arrives within minutes.

## Proposal: B + D
1. **Event-driven dispatch from Render (B).** After `/ingest` commits a row, Render sends
   `POST /repos/KinjalGoswami68/-SentinelAI/dispatches` (`event_type: process`) if none was sent
   in the last 5 minutes. The timestamp of the last dispatch is kept in the database, not in memory,
   so a restart does not cause a burst. The HTTP call happens after the commit and outside any DB
   connection (connection rules in CLAUDE.md), with a 5 s timeout. A failure is logged and ignored;
   the next ingest or the sweeper picks the rows up.
2. **Catch-up sweeper (D).** The same workflow keeps a `schedule` (off :00, inside 09:00-21:00
   IST, with the IST clock check). When GitHub delivers it, it sweeps anything left behind.
3. **Idempotent worker.** It selects rows `WHERE processed_at IS NULL` (oldest first, capped per
   run), sets `processed_at` in the same transaction as its results, and uses one connection per
   run. A workflow `concurrency:` group means two triggers never run at once; a second trigger
   queues and finds nothing left to do.
4. **Token.** A fine-grained personal access token limited to this one repository with only
   "Actions: write" (or "Contents: write" if `repository_dispatch` requires it; check GitHub's
   docs when building), stored as a Render env var, with an expiry date noted in CLAUDE.md.
   Never logged.

Expected result: an alert within ~2 minutes of the row that causes it while traffic flows, zero
Actions runs when no data arrives, and no third party holding a GitHub token.

## Interaction with M9 (built earlier, inside Render)
M9 runs the investigation inside Render when an alert fires (capped at 1 per company per 30 min,
20 per day). When M5 moves detection to the worker, the investigation trigger moves with it:
the worker opens the incident and runs the investigation in the same run, under the same caps,
which are stored in the database, not in memory.

## Open questions for when M5 starts
- Does Render-side detection (M7) fit in 512 MB with the M3 embedding model loaded? If yes, M5
  may only need to move the investigation and heavy jobs, not embedding.
- Exact token permission for `repository_dispatch`, checked on GitHub's docs page at build time.
- README trade-off text: alert latency depends on the trigger chosen.

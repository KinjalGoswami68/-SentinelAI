# ShiftLens API

Base URL: `https://sentinelai-qqph.onrender.com` (Render free: sleeps outside 09:00-21:00 IST,
a cold start takes ~60 s). Times are UTC, ISO 8601. Authenticated endpoints take the header
`X-API-Key: sk-...`; every authenticated read is scoped to that key's company.

CORS (for the static UI on GitHub Pages): GET requests from the origins in `CORS_ORIGINS`
(default `https://kinjalgoswami68.github.io`) with the `X-API-Key` header, plus `POST /register`,
`POST /try` and the two `.../ask` routes. Every other write is same-origin only.

Errors: `400` validation, `401` missing/invalid key, `404` not found, `413` body too large, `429` +
`Retry-After` (limits, free model quota), `503` + `Retry-After: 5`
when the database is briefly unavailable (retry).

## Write

### POST /register
`{"company_name": "..."}` -> `{"company_id", "api_key", ...}`. The key is shown once; only its
SHA-256 and a 6-character prefix are stored.

Limits (the endpoint is public and callable from the browser):
- **5 per client IP per hour** (`REGISTER_PER_IP_HOUR`). Counted in the service's memory, so a
  restart resets it. The IP comes from `CF-Connecting-IP` / `True-Client-IP` set by Render's
  edge, else the first `X-Forwarded-For` entry. Addresses are never stored or logged.
- **50 per day in total** (`REGISTER_PER_DAY`): companies registered in the last 24 hours,
  counted in the database (survives restarts; a deleted company no longer counts).
- Beyond either: `429`, header `Retry-After: <seconds>`, body `{"detail", "retry_after_s"}`.

Cleanup: a company with **0 stored answers 7 days after registering is deleted**, with
everything it owns, by the daily `keepalive-db` job (`scripts/db_keepalive.py`, ~09:53 IST;
GitHub may run it late). The demo company (Volt) is never deleted. Send one answer to keep a
key.

### POST /ingest  (auth)
One chatbot exchange. Only `prompt` and `response` are required.

| field | type | notes |
|---|---|---|
| prompt, response | string | required, non-empty |
| request_id | string <= 128 | a repeat is a no-op (`status: duplicate`) |
| prompt_version, model_version | string <= 128 | enables the version-regression check |
| status | string <= 32 | `ok`, `error`, `timeout`, ...; errors count as hard failures |
| latency_ms | int | |
| session_id | string <= 256 | stored only as a hash |
| quality_score | 0-10 | display only |
| context | string <= 20,000 | optional RAG context the answer was based on: used only to spot-check that answer, never stored or logged |

Response: `status` (`learning` / `provisional` / `ready` / `duplicate`), `learning_state`,
`detection_active`, `alerts_to_slack`, `baseline_answers`, `hours_of_traffic`, `threshold`,
`threshold_margin`, `threshold_margin_pct`, `is_anomaly`, `drift_alert`, `distance`,
`similarity`, `incident` (`{id, status, severity}` or null), `id`, `request_id`, `timestamp`.
`quality_flags`: the day-1 checks this answer tripped (`empty`, `refusal`, `error`, `repetition`,
`garbled`, `language`, `too_long`, `too_short`, `low_relevance`), from the first answer on, before
the bot is ready; a high rate over the last 30 minutes (at least 10 answers) opens a SEV3
incident. `qa_relevance`: question/answer cosine similarity (below 0.55 = `low_relevance`).

### POST /ingest/batch  (auth)
Import past answers to build the baseline at once instead of waiting for live traffic.
`{"answers": [{"prompt", "response", "t"? (ISO time), "request_id"?, "prompt_version"?,
"model_version"?}, ...]}`, 1-100 answers per call, 300 per bot per day. Returns `202`
`{"accepted", "skipped_over_daily_cap", "imported_today", "daily_cap"}`; the answers are embedded
in the background (~90 s per 100 on Render free; live `/ingest` keeps priority). Without `t`
they are spaced 2 minutes apart, ending now. Imported answers count toward learning (and the
baseline review), but never open incidents, are never spot-checked and never post to Slack.
`429` = the daily cap or a full import queue (`Retry-After`); `503` = the database was briefly
unreachable and nothing was queued, so retrying the same batch is safe. Python:
`shiftlens.import_history([(question, answer), ...])`.

### POST /bot/slack  (auth)
`{"webhook": "https://hooks.slack.com/services/..."}` connects this bot's own Slack channel
(a test message is posted first and must succeed); `{"webhook": null}` removes it. Returns
`{"connected", "ends_with"}` (last 4 characters only): the URL is never returned or logged.
Incidents, notes, the baseline review and the daily digest go to that channel. A bot without
its own webhook is dashboard only.

### DELETE /company  (auth)
Deletes the caller's company and all its answers, alerts, incidents and investigations.

## Agent and try-it (M13, Groq free tier)

Both use `openai/gpt-oss-20b` (investigations keep `gpt-oss-120b`). Caps (`budget.py`): 15
questions and 15 tries a day in total, 20 questions per bot a day, 5 demo questions per visitor
per hour, 3 tries per IP per hour. Groq resets at 00:00 UTC (05:30 IST). Only counts are stored
(`llm_usage`), never questions or answers.

### POST /incidents/{id}/ask  (auth)  ·  POST /demo/volt/incidents/{id}/ask  (no key)
`{"question": "..."}` (1-500 characters) ->
```json
{"claims": [{"text": "...", "ids": ["m7-r105", "S2"]}], "dropped": 0,
 "tools_used": ["compare_stats", "list_answers"], "tokens": 3098, "stopped": null,
 "model": "openai/gpt-oss-20b", "questions_left_today": 14}
```
The agent calls the investigation's tools (`compare_stats`, `cluster_flagged`,
`version_changes`, `similar_past_incidents`, `list_answers`), at most 4 calls and 6K tokens per
question. Every claim cites ids it saw in this incident's data; unknown ids are removed and a
claim left without one is dropped (`dropped`). `claims: []` = the data could not answer it.
`429` = a cap or the free model quota (the message says which); `503` = the model is busy for a
minute.

### POST /try  (no key)
`{"before": [answer, ...], "after": [answer, ...]}`, answer = `{"response", "prompt"?,
"request_id"?, "t"? (ISO), "prompt_version"?, "model_version"?}` or a plain string. Sizes:
`before` 30-75, `after` 10-30, 2,000 characters per field, 400 KB. One try at a time on the
server (`429` "busy" otherwise). Takes ~50 s for 105 answers on Render free (embedding
0.46 s per answer). Runs the production detection and investigation; nothing is stored.
Returns `learning` (state, threshold), `after` (counts), `incident` (or null), `note` (or null;
`quota_used_up` when the rules wrote it), `distance_series`, `timings_s`.

## Read (for the UI)

### GET /bot/health  (auth)
```json
{"company": "...", "learning_state": "ready", "ready_at": "...", "threshold": 0.3878,
 "threshold_margin": 0.0134, "threshold_margin_pct": 3.5, "answers_total": 130,
 "answers_24h": 130, "flagged_24h": 4, "nonanswers_24h": 36, "flagged_rate_24h": 0.0308,
 "last_answer_at": "...", "open_incidents": 0}
```
Display the threshold as `threshold +/- threshold_margin (threshold_margin_pct%)`.
Also `slack` (`{"connected", "ends_with"}`) and `baseline_review` (null until the bot is ready):
`{"created_at", "answers", "verdict" ("looks fine" | "check these"), "issues": [{"category",
"count", "share", "examples" [request ids], "text"}], "spot_checks": {"checked", "likely_problems"}}`.
When a bot becomes ready, the answers it learned from are checked once (day-1 checks plus up to 5
spot-checks) so a bad start is not silently learned as normal.

### GET /bot/digests?limit=7  (auth)  ·  GET /demo/volt/digests  (no key)
`{"company", "digests": [{"day", "lines" [str], "summary", "slack_sent"}]}`, newest first. One
digest per bot per day at 20:30 IST (built from numbers, no LLM): what normal looks like, what
looked wrong, the change since yesterday. Bots with no live answers in the last 24 h get none.

### GET /incidents?limit=50  (auth)
`{"company", "incidents": [{id, status (open|resolved), severity (1-3), severity_label
("SEV1".."SEV3"), opened_at, closed_at, flapping, reopen_count, reasons [str], cause,
confidence}]}`, newest first. `cause`/`confidence` come from the incident's investigation
(null if none ran).

### GET /incidents/{id}  (auth)
```text
company
incident          {id, status, severity, severity_label, opened_at, last_signal_at, closed_at,
                   flapping, reopen_count, signal_count, reasons, slack_messages}
note              null, or {investigation_id, cause, confidence, written_by (model | "rules"),
                   fallback_reason, report {cause, confidence, headline, claims [{text,
                   evidence_ids}], example_ids, suggested_check}, slack_text}
evidence          {flagged_examples [{request_id, prompt, response, flagged, prompt_version,
                   model_version}], baseline_examples [same, before the incident],
                   items [evidence items: id (R/V/C/S/P..), kind, fact, ...]}
distance_series   {threshold, points [{t, request_id, distance, flagged, nonanswer,
                   prompt_version, model_version}]}   from 2 h before opening to 30 min after closing
version_timeline  [{field, value, first_seen, last_seen, answers}]   last 30 days
```
Example texts in `evidence` are end-user and bot text: render them as text, never as HTML.

### GET /health  (no auth)
Liveness for Render and the pinger: `status`, `database`, `commit`, `started_at`,
`embedding_model`, `db_ssl`, `rss_mb`, `peak_rss_mb`.

### GET /dashboard  (auth)
Used by the current Streamlit dashboard (replaced in M11).

## Key-free demo (fictional Volt Electronics support bot)
Read-only, no key, same response shapes as the authenticated versions:

| route | same as |
|---|---|
| `GET /demo/volt/health` | `GET /bot/health` |
| `GET /demo/volt/incidents` | `GET /incidents` |
| `GET /demo/volt/incidents/{id}` | `GET /incidents/{id}` |

Hard-wired to `DEMO_COMPANY_ID` (default 12, Volt): they can never return another company's
data or any key. Responses are cached 30 s per route so public traffic cannot exhaust the
database connection budget. Volt's traffic comes from the in-service demo feed (one fixture
answer every 10 min, 09:00-21:00 IST).

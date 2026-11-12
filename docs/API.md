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

Response: `status` (`learning` / `provisional` / `ready` / `duplicate`), `learning_state`,
`detection_active`, `alerts_to_slack`, `baseline_answers`, `hours_of_traffic`, `threshold`,
`threshold_margin`, `threshold_margin_pct`, `is_anomaly`, `drift_alert`, `distance`,
`similarity`, `incident` (`{id, status, severity}` or null), `id`, `request_id`, `timestamp`.

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

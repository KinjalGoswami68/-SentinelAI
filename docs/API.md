# ShiftLens API

Base URL: `https://sentinelai-qqph.onrender.com` (Render free: sleeps outside 09:00-21:00 IST,
a cold start takes ~60 s). Times are UTC, ISO 8601. Authenticated endpoints take the header
`X-API-Key: sk-...`; every authenticated read is scoped to that key's company.

CORS (for the static UI on GitHub Pages): GET requests from the origins in `CORS_ORIGINS`
(default `https://kinjalgoswami68.github.io`) with the `X-API-Key` header, plus `POST /register`
(it needs no key). Every other write is same-origin only.

Errors: `400` validation, `401` missing/invalid key, `404` not found, `429` + `Retry-After`
(registration limits), `503` + `Retry-After: 5`
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

# ShiftLens: working rules

Monitoring for AI chatbots. Clients POST `prompt`, `response`, `quality_score` to `/ingest`;
ShiftLens embeds the response, compares it to a per-company baseline (Isolation Forest + CUSUM)
and posts to Slack when something looks wrong. The detector is deterministic math, not an LLM.

## Stack
- FastAPI on Render free web service (`main.py`, `Procfile`)
- Supabase Postgres via psycopg2 (`database.py`)
- Embeddings: FastEmbed `BAAI/bge-small-en-v1.5` (int8 ONNX, CPU) in `embeddings.py`, stateless.
  Prompt and response vectors are computed once at `/ingest` (~2 s on Render's 0.1 CPU, with no
  DB connection open) and stored in `outputs.prompt_embedding` / `response_embedding`
  (`REAL[]`); baselines use stored vectors only. Evaluation and memory: `docs/embeddings_eval.md`.
- Isolation Forest (`anomaly_detector.py`) + CUSUM (`drift_detector.py`)
- Slack incoming webhook (`alerts.py`)
- Streamlit dashboard calling the live API (`dashboard.py`); `test_real.py` exercises the live API
- Static site + app (`web/`, Vite): main address Cloudflare Pages `https://shiftlens.pages.dev/`
  (Cloudflare builds `main` itself: root `web`, `npm ci && npm run build`, output `dist`,
  `NODE_VERSION=24`, `PAGES_BASE=/`, `VITE_REPO_URL`); backup GitHub Pages
  `https://kinjalgoswami68.github.io/ShiftLens/` (`pages.yml`). Render `CORS_ORIGINS` lists both.
  `pages.yml` waits for both to serve the pushed commit (`<meta name="build">`), then runs the
  Lighthouse gate on both.

## Render deploy facts
- Render ignores the `Procfile` (per Render's Heroku migration doc). The real start command is
  Render -> Settings -> Start Command: `python -u -m uvicorn main:app --host 0.0.0.0 --port $PORT`.
  The `Procfile` is kept only as documentation; if you change one, change both.
- Build Command (Render -> Settings): `pip install -r requirements.txt && python scripts/fetch_model.py`,
  which downloads the embedding model into `.fastembed_cache/` at build time. Without the second
  part the app still works but downloads the model on every cold start.
- Memory: Render free is 512 MB. `/health` reports `rss_mb` / `peak_rss_mb`; verify-deploy prints them.
- Python version is pinned by `.python-version` (no `PYTHON_VERSION` env var is set; if one is
  added it overrides the file).
- Render deploys from `main`. Health Check Path is `/health`, which returns 200 while the process
  is alive and reports the DB in the body (`database: ok | error: <class>`), so a DB outage does
  not cause a restart loop.

## Database connection budget (Supabase free / Nano)
Limits (supabase.com/docs/guides/platform/compute-and-disk, checked 2026-11-08):
Postgres max_connections 60 (also confirmed with `SHOW max_connections`), pooler max clients 200,
**pooler pool size 15**. The direct connection is IPv6-only on free, so Render and GitHub Actions
must use the IPv4 shared pooler. We use **session mode (port 5432)**: each open client connection
holds one of the 15 backend slots until it closes. Measured 2026-11-08: 15 held connections all
succeed, the 16th is refused immediately (not queued) with
`FATAL: (EMAXCONNSESSION) max clients reached in session mode - max clients are limited to pool_size: 15`.
So in session mode the effective client limit is 15, not 200. A different error such as
`server closed the connection unexpectedly` is a transient drop, not the pool limit.

Cost of one connect, measured from the laptop (India -> Singapore pooler, 2026-11-08): TCP RTT
~143 ms, full connect ~8 round trips = median ~1.1 s, worst seen 4.6 s. It is network round trips,
not Supabase waking up. `CONNECT_TIMEOUT = 15` s in `database.py`.

Rules:
- One connection per unit of work: `with database.connection() as conn:` per request / job run.
  Never open a connection per query. Never hold one across Slack or other HTTP calls.
- Budget of the 15 pool slots (keep the total at or below ~11 so there is headroom):

  | Consumer | Max connections | Notes |
  |---|---|---|
  | Render API (`/ingest`, `/register`, `/dashboard`) | 5 | One per in-flight request. FastAPI's threadpool allows ~40 concurrent sync requests, so above 5 concurrent requests we need an app-side cap (semaphore) before launch. |
  | `/health` background re-check | 1 | At most once per 30 s, seconds long. |
  | Actions worker (M5) | 1 | One connection per run. Use a workflow `concurrency:` group so runs never overlap. |
  | Keep-alive job (M2, `keepalive-db.yml`) | 1 | `SELECT 1` once a day, seconds long. Demo traffic goes through the Render API, so it counts in the Render row. |
  | Local dev servers / scripts | 3 | Stop old local servers; each running one can hold a slot during a request. |
  | **Total** | **11 of 15** | |

- If the budget gets tight: switch the worker (or everything) to transaction mode (port 6543),
  which returns the backend after each transaction. psycopg2 works there as long as we use no
  session state (SET, temp tables, LISTEN, server-side prepared statements).
- Render runs in **Singapore (Southeast Asia)**, the same AWS region as the pooler
  (`aws-0-ap-southeast-1`), so connects from Render should cost tens of ms (expected, not yet
  measured). GitHub-hosted runners are in the US, so the worker should expect >1 s per connect.

## Scheduled jobs (GitHub Actions, public repo = free runners)
All cron times are UTC. IST = UTC + 5:30.

| Workflow | When | What |
|---|---|---|
| `verify-deploy.yml` | every push to `main`, manual | Waits for Render to serve the pushed commit (`/health` `commit`) with `database: ok`, runs `test_real.py`, then watches `/health` 12 min and fails on any restart (`started_at` change). This is the deploy check; the laptop's network is not trusted to reach onrender.com. |
| `keepalive-render.yml` | every 10 min, 09:07-20:57 IST | `GET /health`, waits up to 3 min. Manual run wakes the service before a demo. |
| `demo-traffic.yml` | manual only | 5 rows from `fixtures/volt_electronics_normal.jsonl` to `/ingest` as "Volt Electronics support bot" (company 12), as a burst by hand. No LLM calls. |
| cron-job.org (external) | every 10 min, 09:00-20:50 IST | `GET /health`, keeps Render awake. No secret. Failure emails on. |
| `demo_feed.py` (inside the service) | every 10 min while awake, 09:00-21:00 IST | One Volt fixture answer to its own `/ingest` over loopback, `request_id = volt-demo-<slot>`. Needs Render env `VOLT_API_KEY`; off without it. |
| `digest.py` (inside the service) | daily 20:30 IST (`DIGEST_AT_IST`) | One digest per bot with live answers in the last 24 h, stored in `digests` (one per bot per day, even across restarts) and sent to that bot's Slack (or the owner's for `OWNER_COMPANY_IDS`). No LLM. Missed if the service is asleep at 20:30. |
| `keepalive-db.yml` | daily 09:53 IST | `SELECT 1` on Supabase so the free project does not pause, then deletes companies with 0 answers 7 days after registering (never Volt; `--dry-run` to list). |

- GitHub `schedule` is best effort: runs can be hours late or dropped under load, worst at the
  top of the hour (GitHub docs). Seen 2026-11-09: keepalive-render ran 5 times in ~2 days, at
  times like 01:18 and 04:11 IST. So every scheduled run that calls Render first checks the IST
  clock (UTC + 330 min) and skips outside 09:00-21:00; manual runs are never skipped. Cron
  minutes avoid :00. GitHub cron alone does not keep Render awake reliably.

- Render free instance hours: 750 per **workspace** per month, shared with dataflirt-scraper.
  Running out suspends every free service in the workspace until the 1st (no billing).
  The 09:00-21:00 window costs ~380 h/month; keep at least 150 h headroom.
  Nothing scheduled may call Render outside 09:00-21:00 IST: each request wakes the instance
  for at least 15 min.
- Render can fail to wake a spun-down free instance (seen 2026-11-08: no logs at all, connection
  reset, no status-page incident). Recovery: push an empty commit to `main` to redeploy.
- Render env also needs `VOLT_API_KEY` for the in-service demo feed (and `GROQ_API_KEY` for M9).
- Repo secrets: `DATABASE_URL` (session pooler, port 5432) and `VOLT_API_KEY`. The Volt key
  lives locally in gitignored `volt_api_key.txt`.
- GitHub disables scheduled workflows in a public repo after 60 days without repository
  activity (docs.github.com, checked 2026-11-08). Re-enable from the Actions tab.

## Local dev quirk: Avast HTTPS scanning
This laptop's Avast Web Shield re-signs all HTTPS traffic with "Avast Web/Mail Shield Root".
Windows trusts it, `requests`/`certifi` does not, so outbound HTTPS from Python (Slack, Groq) fails
locally with `SSLError: unable to get local issuer certificate`. Render and GitHub Actions are not
affected. Postgres is unaffected because libpq's default `sslmode=prefer` does not verify certs.
Fix (local only): `pip install -r requirements-dev.txt` and set `SHIFTLENS_DEV_TRUSTSTORE=1` (shell or
`.env`); `dev_truststore.py` then makes Python use the Windows certificate store. Never set it on
Render or Actions, and never add `truststore` to `requirements.txt`.

## Postgres TLS (decided in M4)
- `sslmode=prefer` (libpq default): encrypts if possible, never checks the certificate.
- `sslmode=require`: always encrypts, does not check the certificate (no MITM protection).
- `sslmode=verify-full`: encrypts and checks the certificate and host name. Needs Supabase's CA
  file (free download: dashboard -> Database -> SSL).
- `database.ssl_settings()` sets the mode on every connect (overrides the URL): `verify-full` when
  `DATABASE_CA_CERT` (PEM text) or `DATABASE_CA_CERT_FILE` (path, e.g. a Render Secret File) is set,
  otherwise `require`. `/health` reports it as `db_ssl`. Currently `require` everywhere
  (client-side `ssl_in_use = True`, checked 2026-11-11); `verify-full` waits for the CA file.

## Schema changes
- `database.MIGRATIONS` is an append-only list of numbered migrations, applied once each at
  startup by `create_tables()` under a Postgres advisory lock and recorded in `schema_migrations`.
  Never edit an applied migration; add a new one.
- Test a migration on a copy first: copy the tables into a scratch schema and run with
  `PGOPTIONS="-c search_path=<schema>"` (works through the session pooler), then drop the schema.
- During a Render deploy the old instance keeps serving for ~1-2 min against the migrated schema,
  so a migration must not break the previous code version badly (M4: API keys moved to hashes;
  the old instance returns 401 in that window, and a key it registers is hashed on first use).
- `test_real.py` deletes its own TestChatbot at exit through `DELETE /company`
  (`KEEP_TEST_COMPANY=1` keeps it).

## Row Level Security
- RLS is enabled on `companies`, `outputs`, `alerts` (2026-11-11, flagged by Supabase Security
  Advisor), and on every table added since (`investigations`, `incidents`, `llm_usage`), with **no policies**: Supabase's public API roles (`anon`, `authenticated` through
  PostgREST) can read and write nothing. `create_tables()` enables it on any of these tables
  where it is off.
- The app connects as `postgres`: table owner, `rolbypassrls = true` (checked 2026-11-11), so
  RLS does not filter the app's own queries. If the app ever moves to a role without
  `BYPASSRLS` (e.g. a least-privilege role in M4), it needs explicit policies first, or every
  query returns no rows.
- M4 (schema v2): every new table gets `ENABLE ROW LEVEL SECURITY` in the same migration that
  creates it, and Security Advisor must show no RLS warnings afterwards.

## Rs 0 rule (overrides everything)
- Every API, model, host, library, database and CI minute must be free.
- Never add a payment method anywhere, not even "for verification". If a service asks for a card: stop and find a free alternative.
- Never suggest a paid plan upgrade. If something needs one, say so and redesign.
- Open-weight models or free tiers only (Groq free: `openai/gpt-oss-120b` / `openai/gpt-oss-20b`). No paid OpenAI/Anthropic/Google APIs.
- Check the provider's own page before relying on a free limit.
- Hitting a limit must degrade, not bill: Groq 429 -> rules-only report; Render 750h -> sleeps; Supabase 500 MB -> retention cleanup.
- Groq free limits (account Settings > Limits, 2026-11-11; no card), per model, for both
  gpt-oss-120b and gpt-oss-20b: 30 requests/min, 1,000 requests/day, 8K tokens/min, 200K tokens/day.
  One M9 investigation = 1 call (2 with the validation retry) of ~3-4K tokens, so the cap of
  20 investigations/day uses at most ~160K of the 200K tokens/day. Do not raise the cap without
  re-checking this. 8K tokens/min means at most ~2 investigations per minute.
- Model split (decided 2026-11-12): investigations use gpt-oss-120b (cap above). "Ask about this
  incident" and "try without code" use gpt-oss-20b (its own 200K/day): 15 questions x ~6K + 15
  tries x ~4K = ~150K, plus 20 questions per company/day, 5 per demo visitor/hour, 3 tries per
  IP/hour (`budget.py`). Every Groq call is counted in `llm_usage` (counts only, never text);
  the caps read it, so they survive restarts. Groq resets at 00:00 UTC (05:30 IST).
- Spot-checks (M14, 2026-11-12) run on `qwen/qwen3.8-27b`: its own free budget (Groq Limits page,
  confirmed 2026-11-12: 30 requests/min, 1,000 requests/day, 8K tokens/min, 200K tokens/day).
  ~290-350 tokens per check; caps 30 per bot and 150 a day in total (`spotcheck.py`),
  falling back to gpt-oss-20b for at most 25 a day so questions and tries keep their caps.
  Only `not_supported` (with context) / `does_not_answer` / `self_contradictory` count as a
  problem; `partly_supported` is a neutral dashboard note (decided 2026-11-12, see SCORECARD.md).
  Batch runs (scorecard) must pace >= 6 s per call: 2.5 s hit the 8K tokens/min limit.
- A validation retry is a second ~4K call on the same model, so it waits until 60 s after the
  first call (`investigation.RETRY_WAIT_S`); the 20b fallback after a 429 has its own limits
  and does not wait.
- Fixture generation with Groq (M9 `make_m9_fixture.py`, M10 scenarios) uses the same 200K
  tokens/day as investigations. On a generation day either set `INVESTIGATION_DAILY_CAP`
  (Render -> Environment, default 20) low enough that both fit, or generate on a day with no
  demo incidents. Generators must sleep between calls to stay under 8K tokens/min.

## Secrets
- Secrets live only in environment variables (`.env` locally, Render -> Environment in production).
- `.env` must stay in `.gitignore`; check with `git check-ignore -v .env`.
- Never print, log, echo or commit a URL that contains a password, API key or webhook.
- Never ask for a real `DATABASE_URL`, password or webhook to be pasted into chat. Log host names or exception classes, never full connection strings.

## Git
- One branch per phase (`phase-2-fix-deploy`, `phase-3-embeddings`, ...).
- `git add` specific files only, never `git add .` / `-A`.
- `git diff --staged` before every commit. Message format: `phase N: what and why`.
- Run locally before every push.
- Ask before any force push or history rewrite.
- Small verified fixes may be merged into `main`.

## File encoding
- Every text file is UTF-8 without BOM, LF endings (`.editorconfig`). `scripts/check_encoding.py`
  runs first in verify-deploy and fails on UTF-16, a BOM, or non-UTF-8 bytes.
- Never create or write repo files with Windows PowerShell 5.1 `>`, `>>`, `Out-File` (UTF-16 LE)
  or `Set-Content`/`Add-Content` (ANSI). README.md was UTF-16 from June to 2026-11-11 for this
  reason. Use the editor, the Edit/Write tools, or Python with `encoding="utf-8"`.
- Python: every `open(..., "w"/"a")`, `write_text` and `read_text` passes `encoding="utf-8"`.

## Working style
- One step at a time: say what and why before, what to expect after.
- "Verified" means it was run and seen working; otherwise say "expected".

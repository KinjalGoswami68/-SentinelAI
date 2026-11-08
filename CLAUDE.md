# SentinelAI: working rules

Monitoring for AI chatbots. Clients POST `prompt`, `response`, `quality_score` to `/ingest`;
SentinelAI embeds the response, compares it to a per-company baseline (Isolation Forest + CUSUM)
and posts to Slack when something looks wrong. The detector is deterministic math, not an LLM.

## Stack
- FastAPI on Render free web service (`main.py`, `Procfile`)
- Supabase Postgres via psycopg2 (`database.py`)
- TF-IDF embeddings (`embeddings.py`), to be replaced by FastEmbed `BAAI/bge-small-en-v1.5` in Phase 3
- Isolation Forest (`anomaly_detector.py`) + CUSUM (`drift_detector.py`)
- Slack incoming webhook (`alerts.py`)
- Streamlit dashboard calling the live API (`dashboard.py`); `test_real.py` exercises the live API

## Render deploy facts
- Render ignores the `Procfile` (per Render's Heroku migration doc). The real start command is
  Render -> Settings -> Start Command: `python -u -m uvicorn main:app --host 0.0.0.0 --port $PORT`.
  The `Procfile` is kept only as documentation; if you change one, change both.
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
  | Keep-alive job (M2) | 1 | `SELECT 1`, seconds long. |
  | Local dev servers / scripts | 3 | Stop old local servers; each running one can hold a slot during a request. |
  | **Total** | **11 of 15** | |

- If the budget gets tight: switch the worker (or everything) to transaction mode (port 6543),
  which returns the backend after each transaction. psycopg2 works there as long as we use no
  session state (SET, temp tables, LISTEN, server-side prepared statements).
- Render runs in **Singapore (Southeast Asia)**, the same AWS region as the pooler
  (`aws-0-ap-southeast-1`), so connects from Render should cost tens of ms (expected, not yet
  measured). GitHub-hosted runners are in the US, so the worker should expect >1 s per connect.

## Local dev quirk: Avast HTTPS scanning
This laptop's Avast Web Shield re-signs all HTTPS traffic with "Avast Web/Mail Shield Root".
Windows trusts it, `requests`/`certifi` does not, so outbound HTTPS from Python (Slack, Groq) fails
locally with `SSLError: unable to get local issuer certificate`. Render and GitHub Actions are not
affected. Postgres is unaffected because libpq's default `sslmode=prefer` does not verify certs.
Fix (local only): `pip install -r requirements-dev.txt` and set `SENTINEL_DEV_TRUSTSTORE=1` (shell or
`.env`); `dev_truststore.py` then makes Python use the Windows certificate store. Never set it on
Render or Actions, and never add `truststore` to `requirements.txt`.

## Postgres TLS (decide in M4)
- `sslmode=prefer` (current default): encrypts if possible, never checks the certificate.
- `sslmode=require`: always encrypts, still does not check the certificate (no MITM protection).
- `sslmode=verify-full`: encrypts and checks the certificate and host name. Needs Supabase's CA
  file (free download: dashboard -> Database -> SSL).
- M4 plan: use `verify-full` if the CA can be supplied through an env var (e.g. write it to a file
  at startup and pass `sslrootcert`); otherwise `require`.

## Rs 0 rule (overrides everything)
- Every API, model, host, library, database and CI minute must be free.
- Never add a payment method anywhere, not even "for verification". If a service asks for a card: stop and find a free alternative.
- Never suggest a paid plan upgrade. If something needs one, say so and redesign.
- Open-weight models or free tiers only (Groq free: `openai/gpt-oss-120b` / `openai/gpt-oss-20b`). No paid OpenAI/Anthropic/Google APIs.
- Check the provider's own page before relying on a free limit.
- Hitting a limit must degrade, not bill: Groq 429 -> rules-only report; Render 750h -> sleeps; Supabase 500 MB -> retention cleanup.

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

## Working style
- One step at a time: say what and why before, what to expect after.
- "Verified" means it was run and seen working; otherwise say "expected".

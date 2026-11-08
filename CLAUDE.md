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

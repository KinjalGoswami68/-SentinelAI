# main.py

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import psycopg2
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional
import hashlib
from starlette.concurrency import run_in_threadpool
import re
from collections import deque
import os
import threading
import time
import numpy as np
import warnings
warnings.filterwarnings("ignore")

# Local-dev only, before anything makes HTTPS calls. No-op unless
# SHIFTLENS_DEV_TRUSTSTORE=1 (never set on Render or Actions).
import dev_truststore
dev_truststore.maybe_enable()


import budget
import tryit
import quality as day1     # M14 day-1 checks ("quality" is a local name in ingest)
import ask as ask_agent
import spotcheck
import review
import digest
import importer
from embeddings       import embed, get_model, model_state, MODEL_NAME
from detector         import (knn_distance, unit, baseline_stats, DistanceCUSUM, RateCUSUM,
                              is_refusal_or_empty)
from learning         import assess, ROLLING_ROWS, ROLLING_DELAY_H
from database import (
    list_digests,
    get_baseline_review,
    get_slack_webhook,
    set_slack_webhook,
    get_past_investigations,
    fetch_window,
    record_llm_usage, llm_usage_today,
    registrations_last_24h,
    connection,
    create_tables,
    ping,
    safe_error,
    ssl_mode,
    output_exists,
    delete_company,
    current_incident,
    save_incident,
    open_incidents,
    window_counts,
    link_investigation,
    company_name_by_id,
    list_incidents,
    get_incident,
    distance_series,
    version_timeline,
    bot_counts,
    get_learning,
    save_learning,
    accepted_rows,
    rows_by_ids,
    incident_open,
    count_version_rows,
    version_group,
    version_alert_exists,
    VERSION_FIELDS,
    register_company,
    get_company_by_api_key,
    save_output,
    save_alert,
    get_company_outputs,
    get_company_alerts,
    get_recent_scores,
    get_company_stats,
)
from alerts import send_slack_text
import incidents
import demo_feed
from investigation import run_investigation, check_version_group


# ---------------------------------------------------------------------------
# Database state
#
# The DB is checked at startup (inside lifespan, NOT at import time) and then
# re-checked at most once every DB_RECHECK_SECONDS, in a background thread
# started by /health, so /health itself never waits on the database. A DB
# failure is recorded here instead of crashing the process, because
# restarting the process cannot fix a database that is down.
# ---------------------------------------------------------------------------
DB_RECHECK_SECONDS = 30

# Reported by /health so a check from outside can tell which build is live
# (Render sets RENDER_GIT_COMMIT on every deploy; empty when run locally) and
# whether the process restarted (started_at changes on every start).
GIT_COMMIT = os.environ.get("RENDER_GIT_COMMIT", "")
STARTED_AT = datetime.now(timezone.utc).isoformat(timespec="seconds")

db_state = {
    "status"      : "unknown",   # "ok" | "error: <ExceptionClass>" | "unknown"
    "checked_at"  : None,        # ISO-8601 UTC string of last check
    "checked_mono": 0.0,         # time.monotonic() of last check, for the TTL
    "tables_ready": False,       # True once create_tables() has succeeded
}
_db_check_lock = threading.Lock()


def _check_database():
    """
    Run one DB check and record the result in db_state.
    Until create_tables() has succeeded once, the check IS create_tables()
    (CREATE TABLE IF NOT EXISTS is safe to repeat), so the app heals itself
    when the DB comes back. After that, a plain SELECT 1 is enough.
    """
    try:
        if db_state["tables_ready"]:
            ping()
        else:
            create_tables()
            db_state["tables_ready"] = True
        new_status = "ok"
    except Exception as e:
        new_status = f"error: {type(e).__name__}"
        # One clear line, no URL. Only logged on a change to avoid log spam.
        if db_state["status"] != new_status:
            print(f"DATABASE UNAVAILABLE: {safe_error(e)}", flush=True)

    if new_status == "ok" and db_state["status"] not in ("ok", "unknown"):
        print("DATABASE RECOVERED", flush=True)

    db_state["status"]       = new_status
    db_state["checked_at"]   = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db_state["checked_mono"] = time.monotonic()


def _recheck_in_background():
    """Runs in a daemon thread; the caller already holds _db_check_lock."""
    try:
        _check_database()
    finally:
        _db_check_lock.release()


def _load_model():
    t0 = time.monotonic()
    try:
        get_model()
        embed(["warm-up"])   # first run allocates ONNX Runtime buffers
        print(f"Embedding model {MODEL_NAME} ready in {time.monotonic() - t0:.1f}s, "
              f"rss {_memory_mb()['rss_mb']} MB", flush=True)
    except Exception as e:
        print(f"EMBEDDING MODEL FAILED TO LOAD: {safe_error(e)}", flush=True)


def _memory_mb():
    """Current and peak resident memory in MB from /proc (Linux, i.e. Render);
    None elsewhere. Render's free instance has 512 MB."""
    out = {"rss_mb": None, "peak_rss_mb": None}
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    out["rss_mb"] = round(int(line.split()[1]) / 1024)
                elif line.startswith("VmHWM:"):
                    out["peak_rss_mb"] = round(int(line.split()[1]) / 1024)
    except OSError:
        pass
    return out


@asynccontextmanager
async def lifespan(app):
    # Startup: try the DB once. Whatever happens, the server keeps starting.
    _check_database()
    # Load the embedding model in the background so /health answers at once;
    # the first /ingest waits for it (get_model() holds a lock while loading).
    threading.Thread(target=_load_model, name="model-load", daemon=True).start()
    threading.Thread(target=_incident_sweeper, name="incident-sweeper", daemon=True).start()
    demo_feed.start()   # Volt demo answers every 10 min, 09:00-21:00 IST (only if VOLT_API_KEY is set)
    digest.start(_notify)  # M14: one digest per bot per day at DIGEST_AT_IST (20:30 IST)
    print(f"Startup complete. database: {db_state['status']}", flush=True)
    yield
    # Shutdown: nothing to clean up (connections are opened per request).


app = FastAPI(
    lifespan = lifespan,
    title       = "ShiftLens",
    description = (
        "LLM Quality Monitoring. "
        "Detects when your AI starts giving wrong answers "
        "before your users notice."
    ),
    version = "2.0.0",

)


# CORS for the static UI on GitHub Pages (M11). Read-only GETs from listed origins.
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "https://shiftlens.pages.dev,https://kinjalgoswami68.github.io").split(",")
                if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["GET"],
                   allow_headers=["X-API-Key"], allow_credentials=False, max_age=600)


# The cross-origin writes the Pages site needs; every other write stays same-origin only.
CORS_WRITE_PATHS = re.compile(r"^/(register|try|bot/slack|incidents/\d+/ask|demo/volt/incidents/\d+/ask)$")


@app.middleware("http")
async def register_cors(request: Request, call_next):
    """POST from the Pages origin to CORS_WRITE_PATHS (/register, /try, the ask endpoints).
    Added after CORSMiddleware, so it runs first and answers their preflight itself."""
    origin = request.headers.get("origin")
    if not CORS_WRITE_PATHS.match(request.url.path) or origin not in CORS_ORIGINS:
        return await call_next(request)
    if request.method == "OPTIONS":
        return Response(status_code=200, headers={
            "Access-Control-Allow-Origin": origin, "Access-Control-Allow-Methods": "POST",
            "Access-Control-Allow-Headers": "Content-Type, X-API-Key", "Access-Control-Max-Age": "600", "Vary": "Origin"})
    response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Expose-Headers"] = "Retry-After"
    response.headers["Vary"] = "Origin"
    return response


# Registration limits (POST /register is public and reachable from the browser).
REGISTER_PER_IP_HOUR = int(os.environ.get("REGISTER_PER_IP_HOUR", "5"))
REGISTER_PER_DAY     = int(os.environ.get("REGISTER_PER_DAY", "50"))
_ip_source_logged = False


class HourlyLimiter:
    """At most `limit` hits per client IP per rolling hour. Memory only (a restart resets it);
    addresses are never stored elsewhere or logged."""

    def __init__(self, limit):
        self.limit, self.hits, self.lock = limit, {}, threading.Lock()

    def wait(self, ip):
        """Seconds until this IP may go again (0 = allowed now)."""
        now = time.monotonic()
        with self.lock:
            q = self.hits.setdefault(ip, deque())
            while q and now - q[0] > 3600:
                q.popleft()
            return 0 if len(q) < self.limit else int(3600 - (now - q[0])) + 1

    def record(self, ip):
        with self.lock:
            self.hits.setdefault(ip, deque()).append(time.monotonic())
            if len(self.hits) > 10_000:          # bound memory: drop IPs with no recent hit
                for k in [k for k, q in self.hits.items() if not q or time.monotonic() - q[-1] > 3600]:
                    self.hits.pop(k, None)


_register_limit = HourlyLimiter(REGISTER_PER_IP_HOUR)


def _client_ip(request: Request):
    """Client IP behind Render's proxy. CF-Connecting-IP / True-Client-IP are set by the
    edge (a client cannot forge them through it); X-Forwarded-For's first entry and the
    socket address are fallbacks. Logs which header was used once, never the address."""
    global _ip_source_logged
    for h in ("cf-connecting-ip", "true-client-ip", "x-forwarded-for"):
        v = request.headers.get(h)
        if v:
            src, ip = h, v.split(",")[0].strip()
            break
    else:
        src, ip = "socket", (request.client.host if request.client else "unknown")
    if not _ip_source_logged:
        _ip_source_logged = True
        print(f"client ip taken from {src}", flush=True)
    return ip


def _register_ip_wait(ip):
    return _register_limit.wait(ip)


def _register_ip_record(ip):
    _register_limit.record(ip)


@app.exception_handler(psycopg2.OperationalError)
async def database_unavailable(request: Request, exc: psycopg2.OperationalError):
    """
    A DB failure that survived the connect retry is a temporary outage, not
    a bug: answer 503 + Retry-After so clients know to retry, and log one
    redacted line instead of a 60-line traceback. The transaction was
    already rolled back by database.connection().
    """
    print(f"DB UNAVAILABLE on {request.method} {request.url.path}: {safe_error(exc)}", flush=True)
    return JSONResponse(
        status_code = 503,
        content     = {"detail": "Database temporarily unavailable. Retry shortly."},
        headers     = {"Retry-After": "5"},
    )


# ---------------------------------------------------------------------------
# Learning state per company (M6, learning.py):
#   learning -> provisional (flags on the dashboard only) -> ready (Slack + investigations)
# Per-answer detection: distance to the 5th-nearest baseline answer above the
# company's threshold (99th percentile of the baseline's own kNN distances).
# The state is rebuilt from stored vectors (never re-embedded) every
# REASSESS_LEARNING accepted answers while not ready, and every REFRESH_EVERY
# answers or REFRESH_SECONDS once ready.
# ---------------------------------------------------------------------------
REASSESS_LEARNING = 10
REFRESH_EVERY     = 100
REFRESH_SECONDS   = 3600
ASSESS_MAX_ROWS   = 300    # assessment cost grows with rows^2; ~2 s at 300 on Render's 0.1 CPU

_cache = {}   # company_id -> state dict (see _build_state)


def _baseline_rows(conn, company_id, learning):
    """Before ready: the latest accepted answers. Once ready: the frozen anchor plus
    accepted answers older than ROLLING_DELAY_H (not refreshed during an incident)."""
    if learning["state"] != "ready":
        return accepted_rows(conn, company_id, limit=ROLLING_ROWS), False
    anchor = rows_by_ids(conn, learning["anchor_ids"] or [])
    if incident_open(conn, company_id):
        cached = _cache.get(company_id)
        if cached and cached.get("rows"):
            return cached["rows"], True          # frozen while the incident is open
        rolling = []
    else:
        rolling = accepted_rows(conn, company_id, limit=ROLLING_ROWS, older_than_hours=ROLLING_DELAY_H)
    seen, rows = set(), []
    for r in list(anchor) + list(rolling):
        if r[0] not in seen:
            seen.add(r[0])
            rows.append(r)
    return rows, False


def _drift_monitors(vectors, rows, previous=None):
    """M7: CUSUM on the z-score of each answer's kNN distance, and a Bernoulli CUSUM
    on the refusal/empty rate, both against this company's own baseline. Running
    sums carry over a rebuild so it cannot reset a building alarm."""
    if len(vectors) < 10:
        return {"dist_cusum": None, "rate_cusum": None, "stats": None}
    stats = baseline_stats(vectors, [r[3] for r in rows], [r[4] for r in rows])
    dc = DistanceCUSUM(stats["dist_mean"], stats["dist_sd"])
    rc = RateCUSUM(stats["refusal_rate"])
    if previous:
        if previous.get("dist_cusum"):
            dc.s = previous["dist_cusum"].s
        if previous.get("rate_cusum"):
            rc.s = previous["rate_cusum"].s
    return {"dist_cusum": dc, "rate_cusum": rc, "stats": stats}


def _build_state(conn, company_id, previous=None):
    learning = get_learning(conn, company_id) or {"state": "learning", "anchor_ids": None}
    rows, frozen = _baseline_rows(conn, company_id, learning)
    rows = sorted(rows, key=lambda r: r[1])[-ASSESS_MAX_ROWS:] if len(rows) > ASSESS_MAX_ROWS else rows
    vectors = np.array([r[2] for r in rows], dtype=np.float32) if rows else np.zeros((0, 384), np.float32)
    a = assess(vectors, [r[1] for r in rows])
    state = "ready" if learning["state"] == "ready" else a["state"]   # once ready, stays ready
    if state != learning["state"] or a["threshold"] != learning.get("threshold"):
        save_learning(conn, company_id, state, a["threshold"], a["low"], a["high"],
                      anchor_ids=[r[0] for r in rows] if state == "ready" else None)
        if state == "ready" and learning["state"] != "ready":
            # M14 step 5: review what is about to be frozen as normal (background, never blocks ingest)
            threading.Thread(target=review.run, args=(company_id, None, [r[0] for r in rows], _notify),
                             daemon=True, name="baseline-review").start()
        if state != learning["state"]:
            print(f"company {company_id}: {learning['state']} -> {state} "
                  f"(n={a['n']}, {a['hours']} h, threshold {a['threshold']} +/- {a['margin_pct']}%)", flush=True)
    return {
        "state"        : state,
        "assessment"   : a,
        "rows"         : rows,
        "baseline"     : unit(vectors) if len(vectors) else None,
        "threshold"    : a["threshold"],
        "frozen"       : frozen,
        **_drift_monitors(vectors, rows, previous),
        "since_build"  : 0,
        "built_at"     : time.monotonic(),
    }


def _get_state(conn, company_id):
    st = _cache.get(company_id)
    if st is not None:
        st["since_build"] += 1
        due = (st["since_build"] >= REASSESS_LEARNING if st["state"] != "ready"
               else st["since_build"] >= REFRESH_EVERY or time.monotonic() - st["built_at"] > REFRESH_SECONDS)
        if not due:
            return st
    _cache[company_id] = _build_state(conn, company_id, previous=st)
    return _cache[company_id]


def _public_state(st):
    a = st["assessment"]
    return {
        "learning_state"   : st["state"],
        "detection_active" : st["state"] != "learning",
        "alerts_to_slack"  : st["state"] == "ready",
        "baseline_answers" : a["n"],
        "hours_of_traffic" : a["hours"],
        "threshold"        : a["threshold"],
        "threshold_margin" : None if a["width"] is None else round(a["width"] / 2, 4),
        "threshold_margin_pct": a["margin_pct"],
        "why"              : a["why"] if st["state"] != "ready" else "ready",
    }


class RegisterData(BaseModel):
    company_name: str


class OutputData(BaseModel):
    """
    One chatbot exchange. Only prompt and response are required; every other
    field is optional and stored as NULL (= unknown) when missing, so old
    clients keep working.
    """
    prompt        : str
    response      : str
    quality_score : Optional[float] = Field(None, ge=0.0, le=10.0,
                                            description="Display only (client's own rating).")
    request_id    : Optional[str] = Field(None, max_length=128,
                                          description="Client id for this exchange; a repeat is ignored (safe retries).")
    prompt_version: Optional[str] = Field(None, max_length=128)
    model_version : Optional[str] = Field(None, max_length=128)
    status        : Optional[str] = Field(None, max_length=32,
                                          description="e.g. ok, error, timeout, refused.")
    latency_ms    : Optional[int] = Field(None, ge=0, le=3_600_000)
    session_id    : Optional[str] = Field(None, max_length=256,
                                          description="Stored only as a hash.")
    context       : Optional[str] = Field(None, max_length=20000,
                                          description="Documents the answer should be grounded in. Used for a spot-check, never stored or logged.")


@app.get("/")
def root():
    return {
        "system" : "ShiftLens",
        "version": "2.0.0",
        "status" : "running",
        "docs"   : "/docs",
        "quickstart": [
            "Step 1: POST /register  with {company_name}",
            "Step 2: Copy api_key from response",
            "Step 3: POST /ingest with X-API-Key header; ShiftLens learns what normal looks like (learning -> provisional -> ready)",
            "Step 4: GET  /dashboard with X-API-Key to see monitoring data"
        ]
    }

# Any company registers here.
# Receives unique API key.
# Uses that key for all future requests.
# ---- "Try without code" (M13): same detection + investigation on pasted answers ----------
# Nothing is stored except the count in llm_usage; answer text is never logged.
_try_limit = HourlyLimiter(budget.TRIES_PER_IP_HOUR)
_try_busy = threading.Lock()          # one try at a time: Render free has 0.1 CPU


def _try_in_thread(before, after):
    import investigation as _inv
    _inv.usage_start()
    try:
        return tryit.run(before, after), _inv.usage_take()
    except Exception:
        _inv.usage_take()
        raise


@app.post("/try")
async def try_without_code(request: Request):
    body = await request.body()
    if len(body) > tryit.MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"Too large: at most {tryit.MAX_BYTES // 1000} KB per try.")
    try:
        import json as _json
        before, after = tryit.validate(_json.loads(body or b"null"))
    except (ValueError, tryit.TryInvalid) as e:
        raise HTTPException(status_code=400, detail=str(e) if isinstance(e, tryit.TryInvalid) else "Body is not valid JSON.")
    ip = _client_ip(request)
    wait = _try_limit.wait(ip)
    if wait:
        return JSONResponse(status_code=429, headers={"Retry-After": str(wait)}, content={
            "detail": f"At most {budget.TRIES_PER_IP_HOUR} tries per hour from one address.", "retry_after_s": wait})
    if not _try_busy.acquire(blocking=False):
        return JSONResponse(status_code=429, headers={"Retry-After": "60"}, content={
            "detail": "Someone else's try is running (the free server does one at a time). Try again in a minute.",
            "retry_after_s": 60})
    try:
        with connection() as conn:
            tokens = llm_usage_today(conn, model=budget.ASK_MODEL)["tokens"]
            tries = llm_usage_today(conn, kind="try")["units"]
        ok, msg, retry_after = budget.decide("try", tokens, tries)
        if not ok:
            return JSONResponse(status_code=429, headers={"Retry-After": str(retry_after)},
                                content={"detail": msg, "retry_after_s": retry_after})
        _try_limit.record(ip)
        try:
            result, calls = await run_in_threadpool(_try_in_thread, before, after)
        except tryit.TryInvalid as e:
            raise HTTPException(status_code=400, detail=str(e))
        with connection() as conn:
            record_llm_usage(conn, budget.ASK_MODEL, "try", 0, units=1, requests=len(calls),
                             tokens=sum(t for _, t in calls))
        t = result["timings_s"]
        print(f"try: {len(before)}+{len(after)} answers, embed {t['embed']}s, total {t['total']}s, "
              f"incident={'yes' if result['incident'] else 'no'}, llm calls {len(calls)}", flush=True)
        return result
    finally:
        _try_busy.release()


# ---- History import (M14 step 7) ----------------------------------------------------------
class ImportAnswer(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=20000)
    response: str = Field(..., min_length=1, max_length=20000)
    t: Optional[str] = Field(None, max_length=40, description="ISO time of the original answer")
    request_id: Optional[str] = Field(None, max_length=128)
    prompt_version: Optional[str] = Field(None, max_length=128)
    model_version: Optional[str] = Field(None, max_length=128)


class ImportBody(BaseModel):
    answers: list[ImportAnswer] = Field(..., min_length=1, max_length=importer.BATCH_MAX)


def _after_import(company_id):
    """Re-assess learning on the imported answers (may make the bot ready -> baseline review)."""
    try:
        _cache.pop(company_id, None)
        with connection() as conn:
            _build_state(conn, company_id)
    except Exception as e:
        print(f"re-assess after import failed for company {company_id}: {type(e).__name__}", flush=True)


@app.post("/ingest/batch", status_code=202)
def ingest_batch(data: ImportBody, x_api_key: Optional[str] = Header(None)):
    """Import past answers to build the baseline at once. Never opens incidents or sends Slack."""
    _require_api_key(x_api_key)
    company = _authenticate(x_api_key)
    if importer.queue_full():
        return JSONResponse(status_code=429, headers={"Retry-After": "60"}, content={
            "detail": "The import queue is full. Try again in a minute.", "retry_after_s": 60})
    # check the cap and count the rows in one transaction BEFORE queuing: if the DB is down the
    # client gets a 503 and nothing was queued, so retrying the same batch cannot import it twice
    with connection() as conn:
        done_today = llm_usage_today(conn, kind="import", company_id=company["id"])["units"]
        room = importer.IMPORT_PER_BOT_DAY - done_today
        rows = [a.model_dump() for a in data.answers][:max(0, room)]
        if rows:
            record_llm_usage(conn, "-", "import", company["id"], units=len(rows))
    if not rows:
        wait = budget.seconds_to_reset()
        return JSONResponse(status_code=429, headers={"Retry-After": str(wait)}, content={
            "detail": f"At most {importer.IMPORT_PER_BOT_DAY} imported answers per bot per day.", "retry_after_s": wait})
    if not importer.submit(company["id"], rows, _after_import):   # filled up meanwhile (rare): rows stay counted
        return JSONResponse(status_code=429, headers={"Retry-After": "60"}, content={
            "detail": "The import queue is full. Try again in a minute.", "retry_after_s": 60})
    return {"accepted": len(rows), "skipped_over_daily_cap": len(data.answers) - len(rows),
            "imported_today": done_today + len(rows), "daily_cap": importer.IMPORT_PER_BOT_DAY}


@app.post("/register")
def register(data: RegisterData, request: Request):
    name = data.company_name.strip() if data.company_name else ""

    if not name:
        raise HTTPException(
            status_code = 400,
            detail      = "company_name cannot be empty"
        )

    if len(name) < 2:
        raise HTTPException(
            status_code = 400,
            detail      = "company_name must be at least 2 characters"
        )

    if len(name) > 100:
        raise HTTPException(
            status_code = 400,
            detail      = "company_name must be under 100 characters"
        )

    ip = _client_ip(request)
    wait = _register_ip_wait(ip)
    if wait:
        return JSONResponse(status_code=429, headers={"Retry-After": str(wait)}, content={
            "detail": f"Too many registrations from this address: at most {REGISTER_PER_IP_HOUR} per hour.",
            "retry_after_s": wait})
    with connection() as conn:
        count, oldest = registrations_last_24h(conn)
        if count >= REGISTER_PER_DAY:
            wait = 3600 if oldest is None else max(
                60, int((oldest + timedelta(hours=24) - datetime.now(timezone.utc)).total_seconds()) + 1)
            print(f"/register: daily cap reached ({count}/{REGISTER_PER_DAY})", flush=True)
            return JSONResponse(status_code=429, headers={"Retry-After": str(wait)}, content={
                "detail": f"Registration is paused: at most {REGISTER_PER_DAY} new companies per day. Try later.",
                "retry_after_s": wait})
        result = register_company(conn, name)
    _register_ip_record(ip)

    return {
        "success"     : True,
        "company_name": name,
        "company_id"  : result["company_id"],
        "api_key"     : result["api_key"],
        "next_steps"  : {
            "1_save_key" : "Save your api_key — it cannot be retrieved again",
            "2_send_data": f"POST to /ingest with header X-API-Key: {result['api_key']}",
            "3_learning" : "After 30+ answers over 2+ hours with a stable threshold, Slack alerts start (state: ready)",
            "4_dashboard": "GET /dashboard with your API key to see monitoring data"
        }
    }

@app.get("/health")
def health():
    """
    Always 200 while the process is alive. The body reports the database.

    Why 200 even when the DB is down: if this returned 500, Render would
    restart a process whose code is fine, which cannot fix Supabase and
    turns a DB outage into a restart loop. The "database" field makes the
    outage visible to anyone reading the body (dashboard, test_real.py, you).

    Never touches the database itself: it returns the cached db_state. If
    the cache is older than DB_RECHECK_SECONDS, it starts ONE background
    re-check (full CONNECT_TIMEOUT, since a connect alone can take seconds)
    and still answers immediately with the cached value. The next call after
    the re-check finishes sees the new result.
    """
    stale = time.monotonic() - db_state["checked_mono"] > DB_RECHECK_SECONDS
    if stale and _db_check_lock.acquire(blocking=False):
        try:
            threading.Thread(
                target=_recheck_in_background, name="db-recheck", daemon=True
            ).start()
        except Exception:
            _db_check_lock.release()   # thread never started, free the lock
            raise

    db_ok = db_state["status"] == "ok"
    return {
        "status"             : "healthy" if db_ok else "degraded",
        "version"            : "2.0.0",
        "database"           : db_state["status"],
        "database_checked_at": db_state["checked_at"],
        "commit"             : GIT_COMMIT,
        "started_at"         : STARTED_AT,
        "embedding_model"    : model_state["status"],
        "db_ssl"             : ssl_mode(),
        **_memory_mb(),
        "timestamp"          : datetime.now(timezone.utc).isoformat(timespec="seconds")
    }




def _require_api_key(x_api_key):
    if not x_api_key:
        raise HTTPException(
            status_code = 401,
            detail      = "Missing X-API-Key header"
        )


def _company_or_401(conn, x_api_key):
    company = get_company_by_api_key(conn, x_api_key)
    if not company:
        raise HTTPException(
            status_code = 401,
            detail      = "Invalid API key"
        )
    return company


# Valid keys seen recently, keyed by SHA-256 of the key (the raw key is not
# kept). Lets /ingest check the key before spending ~2 s of CPU on embedding,
# without an extra DB connection for a key that was valid a moment ago.
AUTH_CACHE_SECONDS = 300
_auth_cache      = {}   # sha256 hex -> (company dict, monotonic expiry)
_auth_cache_lock = threading.Lock()


def _authenticate(x_api_key):
    """Company for this key, from the cache or one short DB connection; else 401."""
    digest = hashlib.sha256(x_api_key.encode()).hexdigest()
    now    = time.monotonic()
    with _auth_cache_lock:
        hit = _auth_cache.get(digest)
        if hit and hit[1] > now:
            return hit[0]
    with connection() as conn:
        company = _company_or_401(conn, x_api_key)
    with _auth_cache_lock:
        _auth_cache[digest] = (company, now + AUTH_CACHE_SECONDS)
    return company


# One DB connection per request: opened by `with connection()`, shared by
# every query, committed and closed when the block ends. Slack alerts are
# collected during the block and sent only after it, so a slow Slack call
# never holds a DB connection open.

@app.post("/ingest")
def ingest(
    data      : OutputData,
    x_api_key : Optional[str] = Header(None)
):
    # Validation that needs no database runs before a connection is opened.
    _require_api_key(x_api_key)

    prompt   = data.prompt.strip()   if data.prompt   else ""
    response = data.response.strip() if data.response else ""

    if not prompt:
        raise HTTPException(
            status_code = 400,
            detail      = "prompt cannot be empty"
        )

    if not response:
        raise HTTPException(
            status_code = 400,
            detail      = "response cannot be empty"
        )

    quality     = data.quality_score   # optional, display only

    # Key first (cheap), then the expensive embedding with NO connection open,
    # then one connection for all reads and writes.
    company    = _authenticate(x_api_key)
    company_id = company["id"]
    meta       = _meta(data, company_id)
    meta["is_nonanswer"] = is_refusal_or_empty(response)
    prompt_vector, response_vector = embed([prompt, response])
    # M14 day-1 checks: no baseline needed (both vectors are L2-normalised, so dot = cosine)
    meta["qa_relevance"] = round(float(np.dot(prompt_vector, response_vector)), 4)
    meta["quality_flags"] = day1.check(prompt, response, meta["qa_relevance"], meta["status"])
    meta["source"] = "live"

    with connection() as conn:
        # A client retry with the same request_id: already stored, do nothing.
        if output_exists(conn, company_id, meta["request_id"]):
            return {"status": "duplicate", "request_id": meta["request_id"],
                    "message": "Already received; nothing stored."}

        state = _get_state(conn, company_id)
        level = state["state"]                     # learning | provisional | ready
        ready = level == "ready"

        # Per-answer detection (provisional and ready): distance to the 5th-nearest
        # baseline answer above the company's threshold.
        distance = similarity = None
        is_anomaly = False
        if level != "learning" and state["baseline"] is not None:
            distance   = knn_distance(state["baseline"], response_vector)
            similarity = round(1.0 - distance, 4)
            is_anomaly = distance > state["threshold"]

        # M7 drift (system-computed, not the client's quality_score): CUSUM on the
        # distance z-score, and on the refusal/empty rate.
        cusum_result = {"alert": False, "message": ""}
        if level != "learning" and state["dist_cusum"] is not None and distance is not None:
            dist_alarm, z = state["dist_cusum"].update(distance)
            refusal_alarm = state["rate_cusum"].update(is_refusal_or_empty(response))
            if dist_alarm:
                cusum_result = {"alert": True, "type": "drift",
                                "message": (f"Answers are drifting away from normal: the running sum of distance "
                                            f"z-scores crossed {state['dist_cusum'].h:g} (this answer z = {z:.1f})")}
            elif refusal_alarm:
                cusum_result = {"alert": True, "type": "refusal_drift",
                                "message": (f"Refusals or empty answers are rising above this bot's usual "
                                            f"{state['rate_cusum'].p0:.0%}")}
        drift_alert = cusum_result["alert"]

        output_id = save_output(
            conn,
            company_id    = company_id,
            prompt        = prompt,
            response      = response,
            quality_score = quality,
            is_anomaly    = int(is_anomaly),
            similarity    = similarity,
            prompt_embedding   = prompt_vector,
            response_embedding = response_vector,
            meta          = meta,
        )
        if output_id is None:   # lost a race with an identical retry
            is_anomaly = drift_alert = False

        # Every alarm is stored for the dashboard (provisional and ready).
        if is_anomaly:
            msg = f"Distance {distance:.3f} to the 5th-nearest normal answer is above the threshold {state['threshold']:.3f}"
            save_alert(conn, company_id=company_id, alert_type="anomaly",
                       message=f"Score: {_fmt_score(quality)} | {msg}")
        if drift_alert:
            save_alert(conn, company_id=company_id, alert_type=cusum_result["type"], message=cusum_result["message"])
        version_trigger = _check_new_version(conn, company_id, meta) if ready and output_id is not None else None
        if version_trigger:
            save_alert(conn, company_id=company_id, alert_type="version_regression",
                       message=version_trigger["message"])

        # M8: alarms become incidents. Slack goes only through the incident: opened (with
        # the investigation note), escalated, resolved. Drift and version alarms need a
        # ready baseline; M14 day-1 quality reasons work from the first answer.
        incident_msgs, incident = [], None
        if ready and output_id is not None:
            alarms = ({cusum_result["type"]} if drift_alert else set()) | (
                {"version_regression"} if version_trigger else set())
            signal = (is_anomaly or bool(alarms) or meta["is_nonanswer"] or (meta["status"] or "") in HARD_STATUSES
                      or bool(meta["quality_flags"]))
            incident_msgs, incident = _incident_step(conn, company_id, alarms, signal)
        elif output_id is not None and meta["quality_flags"]:
            incident_msgs, incident = _incident_step(conn, company_id, set(), True, quality_only=True)
    # <- transaction committed and connection closed here

    # M14 step 4: a fixed sample of answers gets an LLM spot-check in the background; the
    # context (RAG documents) goes only into that in-memory queue and is never stored.
    if output_id is not None and spotcheck.sampled(company_id, meta["request_id"] or output_id):
        spotcheck.submit(output_id, company_id, prompt, response, data.context)

    for action in incident_msgs:
        if action == "open" and ready:
            _schedule_investigation(company_id, company["company_name"], trigger=version_trigger,
                                    incident=incident)
        else:   # not ready yet: no baseline to investigate against, the reasons say what is wrong
            _notify(company_id, _incident_text(company["company_name"], incident, action))

    return {
        "status"          : level,
        **_public_state(state),
        "is_anomaly"      : bool(is_anomaly),
        "quality_flags"   : meta["quality_flags"],
        "qa_relevance"    : meta["qa_relevance"],
        "drift_alert"     : bool(drift_alert),
        "distance"        : None if distance is None else round(distance, 4),
        "similarity"      : similarity,
        "incident"        : None if not incident else {"id": incident["id"], "status": incident["status"],
                                                       "severity": incident["severity"]},
        "company"         : company["company_name"],
        "id"              : output_id,
        "request_id"      : meta["request_id"],
        "timestamp"       : datetime.now(timezone.utc).isoformat(timespec="seconds")
    }


def _meta(data, company_id):
    """Optional metadata from the request, cleaned. session_id is never stored:
    only SHA-256 of company id + session id, so it cannot be matched across companies."""
    clean = lambda v: (v.strip() or None) if isinstance(v, str) else v
    session = clean(data.session_id)
    status  = clean(data.status)
    return {
        "request_id"    : clean(data.request_id),
        "prompt_version": clean(data.prompt_version),
        "model_version" : clean(data.model_version),
        "status"        : status.lower() if status else None,
        "latency_ms"    : data.latency_ms,
        "session_hash"  : hashlib.sha256(f"{company_id}:{session}".encode()).hexdigest() if session else None,
    }


# ---------------------------------------------------------------------------
# M9: investigation trigger. Runs inside this process in a background thread.
# Caps (1 per company per 30 min, 20 per day) live in the database
# (database.claim_investigation_slot), so they hold across restarts.
# ---------------------------------------------------------------------------
INVESTIGATION_SETTLE_S  = int(os.environ.get("INVESTIGATION_SETTLE_SECONDS", "60"))
INVESTIGATIONS_ENABLED  = os.environ.get("INVESTIGATIONS_ENABLED", "1") == "1"
_pending_investigations = set()
_pending_lock           = threading.Lock()


VERSION_CHECK_AT = (10, 30)   # check a new version when it has this many answers


def _check_new_version(conn, company_id, meta):
    """Version regression trigger (see investigation.check_version_group).
    Returns a trigger dict, or None. Each field/value alerts at most once."""
    for field in VERSION_FIELDS:
        value = meta.get(field)
        if not value or count_version_rows(conn, company_id, field, value) not in VERSION_CHECK_AT:
            continue
        if version_alert_exists(conn, company_id, field, value):
            continue
        first, base, group = version_group(conn, company_id, field, value)
        if first is None or not base:      # nothing earlier to compare with
            continue
        signals = check_version_group(base, group, field, value)
        if signals:
            return {"type": "version_regression", "field": field, "value": value, "first_seen": first,
                    "signals": signals,
                    "message": f"[{field}={value}] answers changed after {field} became '{value}': "
                               + "; ".join(s["signal"].replace("_", " ") for s in signals)}
    return None


def _schedule_investigation(company_id, company_name, trigger=None, incident=None):
    """Background thread: wait for more of the incident to arrive, investigate, then
    send the incident's 'opened' Slack message with the investigation note in it
    (one message, not two). If the investigation is skipped (caps, disabled), the
    'opened' message is sent without a note."""
    with _pending_lock:
        if company_id in _pending_investigations:
            return
        _pending_investigations.add(company_id)

    def work():
        try:
            time.sleep(INVESTIGATION_SETTLE_S)
            result = None
            if INVESTIGATIONS_ENABLED:
                start = incident["opened_at"] - timedelta(minutes=incidents.WINDOW_MINUTES) if incident else None
                result = run_investigation(company_id, company_name, send_slack=None, trigger=trigger, start=start)
            if incident:
                text = _incident_text(company_name, incident, "open", note=result["text"] if result else None)
                sent = _notify(company_id, text)
                if result:
                    with connection() as conn:
                        link_investigation(conn, result["id"], incident["id"], sent)
        except Exception as e:
            print(f"Investigation thread error: {safe_error(e)}", flush=True)
        finally:
            with _pending_lock:
                _pending_investigations.discard(company_id)

    threading.Thread(target=work, name=f"investigate-{company_id}", daemon=True).start()
    print(f"Investigation scheduled for company {company_id} in {INVESTIGATION_SETTLE_S}s", flush=True)


# ---------------------------------------------------------------------------
# M8: incidents (state machine in incidents.py)
# ---------------------------------------------------------------------------
HARD_STATUSES   = {"error", "timeout", "failed", "failure"}
SWEEP_SECONDS   = int(os.environ.get("INCIDENT_SWEEP_SECONDS", "120"))
SEV_LABEL       = {1: "SEV1", 2: "SEV2", 3: "SEV3"}


# Slack routing (owner's decision, 2026-11-12): a bot's own webhook if it set one (M14 step 8);
# else the owner's SLACK_WEBHOOK_URL for the owner's bots only (OWNER_COMPANY_IDS, Volt by
# default); every other bot is dashboard only.
OWNER_COMPANY_IDS = {int(x) for x in os.environ.get("OWNER_COMPANY_IDS", "12").split(",") if x.strip()}
_webhooks = {}                      # company_id -> (url or None, fetched at); 60 s cache, memory only


def _bot_webhook(company_id):
    hit = _webhooks.get(company_id)
    if hit and time.monotonic() - hit[1] < 60:
        return hit[0]
    try:
        with connection() as conn:
            url = get_slack_webhook(conn, company_id)
    except Exception:
        url = hit[0] if hit else None
    _webhooks[company_id] = (url, time.monotonic())
    return url


def _notify(company_id, text):
    own = _bot_webhook(int(company_id))
    if own:
        return send_slack_text(text, webhook=own)
    if int(company_id) in OWNER_COMPANY_IDS:
        return send_slack_text(text)
    return False


def _incident_step(conn, company_id, alarms, signal, quality_only=False):
    """Load the company's current incident, close it if quiet, apply this answer's
    signal, store it. Returns (actions that need a Slack message, incident).
    quality_only: a bot still learning; only the day-1 quality rates count (M14)."""
    now = datetime.now(timezone.utc)
    inc = current_incident(conn, company_id, flap_hours=incidents.FLAP_HOURS)
    msgs = []
    inc, actions = incidents.sweep(inc, now)
    if signal:
        window = window_counts(conn, company_id)
        if quality_only:
            reasons = day1.rate_reasons(window["quality_flags"])
            sev = 3 if reasons else None
        else:
            sev, reasons = incidents.severity_for(window, alarms)
        inc, more = incidents.step(inc, {"severity": sev, "reasons": reasons}, now)
        actions += more
    for a in actions:
        if incidents.slack_allowed(inc, a):
            inc["slack_messages"] += 1      # reserve the slot now; sent after commit
            msgs.append(a)
    # Store every change: an action, or a signal on an open incident (last_signal_at,
    # signal_count and reasons move even when no action results; without this the
    # incident closed 30 min after its first signal).
    if actions or (signal and inc is not None):
        inc["id"] = save_incident(conn, company_id, inc)
    if actions:
        print(f"company {company_id}: incident #{inc['id']} {', '.join(actions)} "
              f"({SEV_LABEL[inc['severity']]}, {inc['status']})", flush=True)
    return msgs, inc


def _incident_text(company_name, inc, action, note=None):
    sev = SEV_LABEL[inc["severity"]]
    reasons = "\n".join(f"- {r}" for r in inc["reasons"][:5])
    if action == "open":
        head = f"*Incident #{inc['id']} opened ({sev}): {company_name}*\n{reasons}"
        return head + ("\n\n" + note if note else "\n\n_No investigation note (daily cap reached or disabled)._")
    if action == "escalate":
        return f"*Incident #{inc['id']} escalated to {sev}: {company_name}*\n{reasons}"
    minutes = int((inc["closed_at"] - inc["opened_at"]).total_seconds() // 60)
    flap = f" It reopened {inc['reopen_count']} time(s) (flapping)." if inc["flapping"] else ""
    return (f"*Incident #{inc['id']} resolved: {company_name}*\nNo new signal for {incidents.QUIET_MINUTES} min; "
            f"open for {minutes} min, worst {sev}.{flap}")


def _incident_sweeper():
    """Resolve quiet incidents even when no traffic arrives (every SWEEP_SECONDS)."""
    while True:
        time.sleep(SWEEP_SECONDS)
        try:
            to_send = []
            with connection() as conn:
                for inc in open_incidents(conn):
                    inc2, actions = incidents.sweep(inc, datetime.now(timezone.utc))
                    if "resolve" in actions:
                        if incidents.slack_allowed(inc2, "resolve"):
                            inc2["slack_messages"] += 1
                            to_send.append((inc2["company_id"], company_name_by_id(conn, inc2["company_id"]), inc2))
                        save_incident(conn, inc2["company_id"], inc2)
                        print(f"company {inc2['company_id']}: incident #{inc2['id']} resolved", flush=True)
            for cid, name, inc2 in to_send:
                _notify(cid, _incident_text(name, inc2, "resolve"))
        except Exception as e:
            print(f"incident sweeper: {safe_error(e)}", flush=True)


def _fmt_score(score):
    return "n/a" if score is None else score


@app.delete("/company")
def delete_my_company(x_api_key: Optional[str] = Header(None)):
    """Delete the calling company and all its outputs and alerts (cannot be undone)."""
    _require_api_key(x_api_key)
    with connection() as conn:
        company = _company_or_401(conn, x_api_key)
        counts  = delete_company(conn, company["id"])
    _cache.pop(company["id"], None)
    with _auth_cache_lock:
        _auth_cache.pop(hashlib.sha256(x_api_key.encode()).hexdigest(), None)
    return {"deleted": company["company_name"], "company_id": company["id"], "rows": counts}



@app.get("/dashboard")
def dashboard_data(x_api_key: Optional[str] = Header(None)):
    _require_api_key(x_api_key)

    with connection() as conn:
        company    = _company_or_401(conn, x_api_key)
        company_id = company["id"]
        state      = _get_state(conn, company_id)
        stats      = get_company_stats(conn, company_id)
        outputs    = get_company_outputs(conn, company_id, limit=100)
        alerts     = get_company_alerts(conn, company_id, limit=50)
        scores     = get_recent_scores(conn, company_id, limit=50)

    output_list = []
    for row in outputs:
        id_, prompt, response, score, anomaly, sim, timestamp = row
        output_list.append({
            "id"           : id_,
            "prompt"       : prompt,
            "response"     : response,
            "quality_score": score,
            "is_anomaly"   : bool(anomaly),
            "similarity"   : sim,
            "timestamp"    : timestamp
        })

    alert_list = []
    for row in alerts:
        id_, alert_type, message, timestamp = row
        alert_list.append({
            "id"        : id_,
            "alert_type": alert_type,
            "message"   : message,
            "timestamp" : timestamp
        })

    return {
        "company"          : company["company_name"],
        **_public_state(state),
        "threshold_display": (None if state["threshold"] is None else
                              f"{state['threshold']:.3f} +/- {state['assessment']['width'] / 2:.3f} "
                              f"({state['assessment']['margin_pct']}%)"),
        "stats"            : stats,
        "recent_outputs"   : output_list,
        "recent_alerts"    : alert_list,
        "score_history"    : scores
    }



# ---------------------------------------------------------------------------
# Read API for the UI (M8). All scoped to the caller's API key. docs/API.md.
# ---------------------------------------------------------------------------

def _learning_view(learning):
    thr, lo, hi = learning.get("threshold"), learning.get("threshold_low"), learning.get("threshold_high")
    margin = None if thr is None or lo is None or hi is None else round((hi - lo) / 2, 4)
    return {"learning_state": learning.get("state"), "ready_at": learning.get("ready_at"),
            "threshold": thr, "threshold_margin": margin,
            "threshold_margin_pct": None if margin is None or not thr else round(margin / thr * 100, 1)}


def _bot_health_for(company):
    with connection() as conn:
        learning = get_learning(conn, company["id"]) or {}
        counts = bot_counts(conn, company["id"])
        review_body = get_baseline_review(conn, company["id"])
    return {"company": company["company_name"], **_learning_view(learning), **counts, "baseline_review": review_body}


def _incidents_for(company, limit):
    with connection() as conn:
        rows = list_incidents(conn, company["id"], limit=max(1, min(limit, 200)))
    for r in rows:
        r["severity_label"] = SEV_LABEL[r["severity"]]
    return {"company": company["company_name"], "incidents": rows}


def _incident_detail_for(company, incident_id):
    with connection() as conn:
        inc = get_incident(conn, company["id"], incident_id)
        if not inc:
            raise HTTPException(status_code=404, detail="Incident not found")
        start = inc["opened_at"] - timedelta(hours=2)
        end = (inc["closed_at"] or datetime.now(timezone.utc)) + timedelta(minutes=30)
        series = distance_series(conn, company["id"], start, end)
        versions = version_timeline(conn, company["id"])
        learning = get_learning(conn, company["id"]) or {}
    inv = inc.pop("investigation")
    stored = (inv or {}).get("report") or {}
    pack = stored.get("pack") or {}
    return {
        "company": company["company_name"],
        "incident": {**inc, "severity_label": SEV_LABEL[inc["severity"]]},
        "note": None if not inv else {
            "investigation_id": inv["id"], "cause": inv["cause"], "confidence": inv["confidence"],
            "written_by": inv["written_by"], "fallback_reason": inv["fallback_reason"],
            "report": stored.get("report"), "slack_text": stored.get("slack_text"),
            "labels": ask_agent.evidence_labels(pack.get("evidence", []))},       # chips: "length 1.5×", not "S1"
        "evidence": {"flagged_examples": pack.get("flagged_examples", []),
                     "baseline_examples": pack.get("baseline_examples", []),
                     "items": pack.get("evidence", [])},
        "distance_series": {"threshold": learning.get("threshold"), "points": series},
        "version_timeline": versions,
    }


class SlackBody(BaseModel):
    webhook: Optional[str] = Field(None, max_length=300, description="Slack incoming-webhook URL; null removes it.")


@app.post("/bot/slack")
def bot_slack(data: SlackBody, x_api_key: Optional[str] = Header(None)):
    """Connect (test message first) or remove this bot's own Slack webhook. The URL is never
    returned or logged; the answer only says whether one is connected and its last 4 characters."""
    _require_api_key(x_api_key)
    company = _authenticate(x_api_key)
    url = (data.webhook or "").strip() or None
    if url:
        from alerts import SLACK_WEBHOOK_RE
        if not SLACK_WEBHOOK_RE.match(url):
            raise HTTPException(status_code=400, detail="That is not a Slack incoming-webhook URL (https://hooks.slack.com/services/...).")
        if not send_slack_text(f"*ShiftLens is connected for {company['company_name']}.* Incidents, investigation notes "
                               "and the daily digest for this bot will be posted here.", webhook=url):
            raise HTTPException(status_code=400, detail="Slack did not accept a test message on that webhook.")
    with connection() as conn:
        set_slack_webhook(conn, company["id"], url)
    _webhooks.pop(company["id"], None)
    return {"connected": bool(url), "ends_with": url[-4:] if url else None}


@app.get("/bot/health")
def bot_health(x_api_key: Optional[str] = Header(None)):
    """State, threshold +/- margin, answer counts and flagged rate for the caller's bot, plus
    whether it has its own Slack webhook (last 4 characters only; the demo route never says)."""
    _require_api_key(x_api_key)
    company = _authenticate(x_api_key)
    url = _bot_webhook(company["id"])
    return {**_bot_health_for(company), "slack": {"connected": bool(url), "ends_with": url[-4:] if url else None}}


@app.get("/incidents")
def incidents_list(x_api_key: Optional[str] = Header(None), limit: int = 50):
    _require_api_key(x_api_key)
    return _incidents_for(_authenticate(x_api_key), limit)


@app.get("/incidents/{incident_id}")
def incident_detail(incident_id: int, x_api_key: Optional[str] = Header(None)):
    """One incident: the note, evidence vs baseline examples, the distance series
    around it and the version timeline."""
    _require_api_key(x_api_key)
    return _incident_detail_for(_authenticate(x_api_key), incident_id)


# ---------------------------------------------------------------------------
# Key-free, read-only demo for the fictional Volt company (M10, for the M11 UI).
# Hard-wired to DEMO_COMPANY_ID: these routes can never return another company's
# data or any key. Responses are cached DEMO_CACHE_S seconds so public traffic
# cannot use up the database connection budget.
# ---------------------------------------------------------------------------
DEMO_COMPANY_ID = int(os.environ.get("DEMO_COMPANY_ID", "12"))
DEMO_CACHE_S    = 30
_demo_cache     = {}
_demo_lock      = threading.Lock()


def _demo_company():
    with connection() as conn:
        return {"id": DEMO_COMPANY_ID, "company_name": company_name_by_id(conn, DEMO_COMPANY_ID)}


def _demo_cached(key, fn):
    now = time.monotonic()
    with _demo_lock:
        hit = _demo_cache.get(key)
        if hit and now - hit[0] < DEMO_CACHE_S:
            return hit[1]
    value = fn()
    with _demo_lock:
        _demo_cache[key] = (now, value)
    return value


@app.get("/bot/digests")
def bot_digests(x_api_key: Optional[str] = Header(None), limit: int = 7):
    """The bot's daily digests, newest first."""
    _require_api_key(x_api_key)
    company = _authenticate(x_api_key)
    with connection() as conn:
        return {"company": company["company_name"], "digests": list_digests(conn, company["id"], max(1, min(limit, 30)))}


@app.get("/demo/volt/digests")
def demo_digests():
    def load():
        c = _demo_company()
        with connection() as conn:
            return {"company": c["company_name"], "digests": list_digests(conn, c["id"], 7)}
    return _demo_cached("digests", load)


@app.get("/demo/volt/health")
def demo_health():
    return _demo_cached("health", lambda: _bot_health_for(_demo_company()))


@app.get("/demo/volt/incidents")
def demo_incidents():
    return _demo_cached("incidents", lambda: _incidents_for(_demo_company(), 50))


# ---- "Ask about this incident" (M13): an agent on the investigation's tools ---------------
# gpt-oss-20b; counts only in llm_usage (no question text stored, none logged).
_ask_visitor_limit = HourlyLimiter(budget.QUESTIONS_PER_VISITOR_HOUR)


class AskBody(BaseModel):
    question: str = Field(..., min_length=1, max_length=ask_agent.MAX_QUESTION_CHARS)


def _ask(company, incident_id, question):
    with connection() as conn:
        inc = get_incident(conn, company["id"], incident_id)
        if not inc:
            raise HTTPException(status_code=404, detail="Incident not found")
        tokens = llm_usage_today(conn, model=budget.ASK_MODEL)["tokens"]
        asked = llm_usage_today(conn, kind="question")["units"]
        asked_here = llm_usage_today(conn, kind="question", company_id=company["id"])["units"]
        ok, msg, retry_after = budget.decide("question", tokens, asked, asked_here)
        if not ok:
            return JSONResponse(status_code=429, headers={"Retry-After": str(retry_after)},
                                content={"detail": msg, "retry_after_s": retry_after})
        inv_row = inc.get("investigation") or {}
        pack = (inv_row.get("report") or {}).get("pack") or {}
        start = ask_agent.parse_start(pack.get("incident_start"), inc["opened_at"]) or inc["opened_at"] - timedelta(minutes=30)
        _, baseline, window = fetch_window(conn, company["id"], start=start)
        end = (inc["closed_at"] or datetime.now(timezone.utc)) + timedelta(minutes=30)
        window = [r for r in window if r["ts"] <= end]
        past = get_past_investigations(conn, company["id"], exclude_id=inv_row.get("id"))
    report = (inv_row.get("report") or {}).get("report") or {}
    ctx = {"baseline": baseline, "incident": window, "start": start, "past": past, "cause": inv_row.get("cause"),
           "evidence_ids": [e["id"] for e in pack.get("evidence", []) if isinstance(e, dict) and e.get("id")],
           "pack_evidence": pack.get("evidence", []), "suggested_check": report.get("suggested_check"),
           "summary": {"opened_at": inc["opened_at"], "status": inc["status"], "severity": inc["severity"],
                       "reasons": inc["reasons"], "note_cause": inv_row.get("cause"), "note_confidence": inv_row.get("confidence"),
                       "answers_before": len(baseline), "answers_since_start": len(window)}}
    import investigation as _inv
    _inv.usage_start()
    error = None
    try:
        out = ask_agent.answer(question, ctx, ask_agent.groq_step())
    except ask_agent.Unavailable as e:
        out, error = None, e
    calls = _inv.usage_take()
    with connection() as conn:
        record_llm_usage(conn, budget.ASK_MODEL, "question", company["id"], units=1 if out else 0,
                         requests=len(calls), tokens=sum(t for _, t in calls))
        left_here = budget.QUESTIONS_PER_COMPANY_DAY - llm_usage_today(conn, kind="question", company_id=company["id"])["units"]
        left_all = budget.questions_per_day() - llm_usage_today(conn, kind="question")["units"]
    if error is not None:
        ra = budget.seconds_to_reset() if error.quota else 60
        return JSONResponse(status_code=429 if error.quota else 503, headers={"Retry-After": str(ra)},
                            content={"detail": str(error), "retry_after_s": ra})
    print(f"ask: company {company['id']} incident {incident_id}: {out['intent']}, {len(out['claims'])} claims, "
          f"{out['dropped']} dropped ({out['circular']} circular), tools {out['tools_used']}, {out['tokens']} tokens", flush=True)
    return {**out, "model": budget.ASK_MODEL, "questions_left_today": max(0, min(left_here, left_all))}


@app.post("/incidents/{incident_id}/ask")
def incident_ask(incident_id: int, data: AskBody, x_api_key: Optional[str] = Header(None)):
    """Ask the agent about one of your incidents (20 a day per bot, 15 a day in total)."""
    _require_api_key(x_api_key)
    return _ask(_authenticate(x_api_key), incident_id, data.question)


@app.post("/demo/volt/incidents/{incident_id}/ask")
def demo_incident_ask(incident_id: int, data: AskBody, request: Request):
    """Same, key-free, on the demo bot: plus 5 questions per visitor per hour."""
    ip = _client_ip(request)
    wait = _ask_visitor_limit.wait(ip)
    if wait:
        return JSONResponse(status_code=429, headers={"Retry-After": str(wait)}, content={
            "detail": f"At most {budget.QUESTIONS_PER_VISITOR_HOUR} questions per hour on the demo.", "retry_after_s": wait})
    result = _ask(_demo_company(), incident_id, data.question)
    if not isinstance(result, JSONResponse):
        _ask_visitor_limit.record(ip)
    return result


@app.get("/demo/volt/incidents/{incident_id}")
def demo_incident_detail(incident_id: int):
    return _demo_cached(f"incident-{incident_id}", lambda: _incident_detail_for(_demo_company(), incident_id))

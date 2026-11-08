# main.py

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import psycopg2
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional
import os
import threading
import time
import numpy as np
import warnings
warnings.filterwarnings("ignore")

# Local-dev only, before anything makes HTTPS calls. No-op unless
# SENTINEL_DEV_TRUSTSTORE=1 (never set on Render or Actions).
import dev_truststore
dev_truststore.maybe_enable()


from embeddings       import embed, build_baseline, compute_similarity, find_threshold
from anomaly_detector import SentinelDetector
from drift_detector   import CUSUMDetector
from database import (
    connection,
    create_tables,
    ping,
    safe_error,
    register_company,
    get_company_by_api_key,
    save_output,
    save_alert,
    get_baseline_outputs,
    get_output_count,
    get_good_output_count,
    get_company_outputs,
    get_company_alerts,
    get_recent_scores,
    get_company_stats,
)
from alerts import send_slack_alert


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


@asynccontextmanager
async def lifespan(app):
    # Startup: try the DB once. Whatever happens, the server keeps starting.
    _check_database()
    print(f"Startup complete. database: {db_state['status']}", flush=True)
    yield
    # Shutdown: nothing to clean up (connections are opened per request).


app = FastAPI(
    lifespan = lifespan,
    title       = "SentinelAI",
    description = (
        "LLM Quality Monitoring. "
        "Detects when your AI starts giving wrong answers "
        "before your users notice."
    ),
    version = "2.0.0",

)


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


# Minimum good outputs before detection activates
# Company sends outputs — first WARMUP_REQUIRED
WARMUP_REQUIRED = 50

# Refresh ML models every N requests per company
# This keeps models updated with new data
# without rebuilding on every single request
REFRESH_EVERY = 100


# Structure per company_id:
# {
#   "warmup"        : bool,
#   "baseline"      : numpy array or None,
#   "threshold"     : float,
#   "detector"      : SentinelDetector or None,
#   "cusum"         : CUSUMDetector or None,
#   "good_count"    : int,
#   "request_count" : int
# }
_cache = {}



def _build_state(conn, company_id):
    good_responses = get_baseline_outputs(conn, company_id, limit=500)
    good_count     = len(good_responses)
    total_count    = get_output_count(conn, company_id)

    
    if good_count < WARMUP_REQUIRED:
        return {
            "warmup"       : True,
            "baseline"     : None,
            "threshold"    : 0.50,
            "detector"     : None,
            "cusum"        : CUSUMDetector(),
            "good_count"   : good_count,
            "total_count"  : total_count,
            "request_count": 0
        }

    
    good_vectors = embed(good_responses)

    # Build baseline vector
    baseline = np.mean(good_vectors, axis=0)

    # Find optimal threshold for this company's data
    threshold = find_threshold(good_responses, vectors=good_vectors)

    
    detector = SentinelDetector()
    detector.train(good_vectors)

    return {
        "warmup"       : False,
        "baseline"     : baseline,
        "threshold"    : threshold,
        "detector"     : detector,
        "cusum"        : CUSUMDetector(),
        "good_count"   : good_count,
        "total_count"  : total_count,
        "request_count": 0
    }


def _get_state(conn, company_id):
    if company_id not in _cache:
        _cache[company_id] = _build_state(conn, company_id)
        return _cache[company_id]

    state = _cache[company_id]

    
    state["request_count"] += 1

    if state["request_count"] % REFRESH_EVERY == 0:
        
        existing_cusum = state.get("cusum")

        new_state = _build_state(conn, company_id)

        # Restore CUSUM if it existed
        if existing_cusum is not None and not new_state["warmup"]:
            new_state["cusum"] = existing_cusum

        _cache[company_id] = new_state

    return _cache[company_id]


class RegisterData(BaseModel):
    company_name: str


class OutputData(BaseModel):
    prompt        : str
    response      : str
    quality_score : float


@app.get("/")
def root():
    return {
        "system" : "SentinelAI",
        "version": "2.0.0",
        "status" : "running",
        "docs"   : "/docs",
        "quickstart": [
            "Step 1: POST /register  with {company_name}",
            "Step 2: Copy api_key from response",
            f"Step 3: POST /ingest with X-API-Key header — send {WARMUP_REQUIRED}+ outputs",
            "Step 4: GET  /dashboard with X-API-Key to see monitoring data"
        ]
    }

# Any company registers here.
# Receives unique API key.
# Uses that key for all future requests.
@app.post("/register")
def register(data: RegisterData):
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

    with connection() as conn:
        result = register_company(conn, name)

    return {
        "success"     : True,
        "company_name": name,
        "company_id"  : result["company_id"],
        "api_key"     : result["api_key"],
        "next_steps"  : {
            "1_save_key" : "Save your api_key — it cannot be retrieved again",
            "2_send_data": f"POST to /ingest with header X-API-Key: {result['api_key']}",
            "3_warmup"   : f"Send {WARMUP_REQUIRED}+ outputs to activate detection",
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

    if not 0.0 <= data.quality_score <= 10.0:
        raise HTTPException(
            status_code = 400,
            detail      = "quality_score must be between 0.0 and 10.0"
        )

    slack_queue = []   # alerts to send after the DB transaction commits

    with connection() as conn:
        company    = _company_or_401(conn, x_api_key)
        company_id = company["id"]

        if company_id in _cache and _cache[company_id].get("warmup"):
            fresh_count = get_good_output_count(conn, company_id)
            if fresh_count >= WARMUP_REQUIRED:
                _cache.pop(company_id)

        state = _get_state(conn, company_id)

        if state["warmup"]:
            save_output(
                conn,
                company_id    = company_id,
                prompt        = prompt,
                response      = response,
                quality_score = data.quality_score,
                is_anomaly    = 0,
                similarity    = None
            )

            # Same connection and transaction, so this count already
            # includes the row inserted just above.
            good_count = get_good_output_count(conn, company_id)
            remaining  = max(0, WARMUP_REQUIRED - good_count)

            warmup_result = {
                "status"          : "warmup",
                "detection_active": False,
                "message"         : f"Send {remaining} more good outputs to activate detection.",
                "warmup_progress" : f"{good_count}/{WARMUP_REQUIRED}",
                "outputs_stored"  : True
            }
        else:
            warmup_result = None

            # Step 1: Embed the response
            response_vector = embed(response)[0]

            # Step 2: Cosine similarity check
            similarity = compute_similarity(
                response_vector,
                state["baseline"]
            )

            # Step 3: Isolation Forest check
            detection      = state["detector"].predict(response_vector)
            anomaly_by_if  = detection["is_anomaly"]

            # Step 4: Similarity threshold check
            anomaly_by_sim = similarity < state["threshold"]

            # Step 5: Combined decision
            is_anomaly = anomaly_by_if or anomaly_by_sim

            # Step 6: CUSUM drift check
            cusum_result = state["cusum"].update(data.quality_score)
            drift_alert  = cusum_result["alert"]

            # Step 7: Save output and alerts (one transaction)
            save_output(
                conn,
                company_id    = company_id,
                prompt        = prompt,
                response      = response,
                quality_score = data.quality_score,
                is_anomaly    = int(is_anomaly),
                similarity    = similarity
            )

            if is_anomaly:
                reasons = []
                if anomaly_by_if:
                    reasons.append("Isolation Forest flagged as outlier")
                if anomaly_by_sim:
                    reasons.append(
                        f"Similarity {similarity} below threshold {state['threshold']}"
                    )
                alert_msg = " | ".join(reasons)

                save_alert(
                    conn,
                    company_id = company_id,
                    alert_type = "anomaly",
                    message    = f"Score: {data.quality_score} | {alert_msg}"
                )
                slack_queue.append({
                    "alert_type": "anomaly",
                    "message"   : f"Company: {company['company_name']} | {alert_msg}",
                    "score"     : data.quality_score,
                    "response"  : response
                })

            if drift_alert:
                save_alert(
                    conn,
                    company_id = company_id,
                    alert_type = "drift",
                    message    = cusum_result["message"]
                )
                slack_queue.append({
                    "alert_type": "drift",
                    "message"   : f"Company: {company['company_name']} | {cusum_result['message']}",
                    "score"     : data.quality_score,
                    "response"  : response
                })
    # <- transaction committed and connection closed here

    if warmup_result is not None:
        return warmup_result

    # Step 8: Slack, after the DB work is safely committed.
    # alert_sent keeps its old meaning: an alert was raised and stored.
    for alert in slack_queue:
        send_slack_alert(**alert)

    # Step 9: Return result
    return {
        "status"          : "monitored",
        "detection_active": True,
        "is_anomaly"      : is_anomaly,
        "drift_alert"     : drift_alert,
        "similarity"      : similarity,
        "threshold"       : state["threshold"],
        "alert_sent"      : bool(slack_queue),
        "company"         : company["company_name"],
        "timestamp"       : datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }



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
        "detection_active" : not state["warmup"],
        "warmup_progress"  : f"{state['good_count']}/{WARMUP_REQUIRED}",
        "threshold"        : state["threshold"],
        "stats"            : stats,
        "recent_outputs"   : output_list,
        "recent_alerts"    : alert_list,
        "score_history"    : scores
    }

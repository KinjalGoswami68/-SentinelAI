# main.py

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import psycopg2
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional
import hashlib
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


from embeddings       import (embed, build_baseline, compute_similarity, find_threshold,
                              get_model, model_state, MODEL_NAME)
from anomaly_detector import SentinelDetector
from drift_detector   import CUSUMDetector
from database import (
    connection,
    create_tables,
    ping,
    safe_error,
    ssl_mode,
    output_exists,
    delete_company,
    count_recent_flagged,
    count_version_rows,
    version_group,
    version_alert_exists,
    VERSION_FIELDS,
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
from alerts import send_slack_alert, send_slack_text
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
    # Baseline from the vectors stored at ingest. Nothing is re-embedded here:
    # on Render's 0.1 CPU that would take minutes for 500 rows. Rows from before
    # M3 without vectors are skipped until scripts/backfill_embeddings.py fills them.
    rows        = get_baseline_outputs(conn, company_id, limit=500)
    stored      = [r[2] for r in rows if r[2] is not None]
    good_count  = len(stored)
    total_count = get_output_count(conn, company_id)
    if len(stored) < len(rows):
        print(f"company {company_id}: {len(rows) - len(stored)} baseline rows have no "
              f"embedding yet (run scripts/backfill_embeddings.py)", flush=True)

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

    
    good_vectors = np.array(stored, dtype=np.float32)

    # Per-company baseline: normalised centroid of this company's own vectors
    baseline = build_baseline(good_vectors)

    # Threshold from this company's spread (mean - 2.5 std of own similarities)
    threshold = find_threshold(good_vectors, baseline=baseline)

    
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

    slack_queue = []   # alerts to send after the DB transaction commits
    quality     = data.quality_score   # optional, display only

    # Key first (cheap), then the expensive embedding with NO connection open,
    # then one connection for all reads and writes.
    company    = _authenticate(x_api_key)
    company_id = company["id"]
    meta       = _meta(data, company_id)
    prompt_vector, response_vector = embed([prompt, response])

    with connection() as conn:
        # A client retry with the same request_id: already stored, do nothing.
        if output_exists(conn, company_id, meta["request_id"]):
            return {"status": "duplicate", "request_id": meta["request_id"],
                    "message": "Already received; nothing stored."}

        if company_id in _cache and _cache[company_id].get("warmup"):
            fresh_count = get_good_output_count(conn, company_id)
            if fresh_count >= WARMUP_REQUIRED:
                _cache.pop(company_id)

        state = _get_state(conn, company_id)

        if state["warmup"]:
            output_id = save_output(
                conn,
                company_id    = company_id,
                prompt        = prompt,
                response      = response,
                quality_score = quality,
                is_anomaly    = 0,
                similarity    = None,
                prompt_embedding   = prompt_vector,
                response_embedding = response_vector,
                meta          = meta,
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
                "outputs_stored"  : output_id is not None,
                "id"              : output_id,
                "request_id"      : meta["request_id"],
            }
        else:
            warmup_result = None

            # Step 1: response_vector was computed above, before the connection

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

            # Step 6: CUSUM drift check on the client's score, only when one was
            # sent. (M7 replaces this with CUSUM on a system-computed distance.)
            if quality is not None:
                cusum_result = state["cusum"].update(quality)
            else:
                cusum_result = {"alert": False, "message": ""}
            drift_alert  = cusum_result["alert"]

            # Step 7: Save output and alerts (one transaction)
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

            # M9 trigger: enough flagged answers in the last 30 min -> investigate
            # (after commit, in the background; caps are enforced in the DB).
            recent_flagged = count_recent_flagged(conn, company_id) if (is_anomaly or drift_alert) else 0

            # M9 trigger 2: a new prompt_version / model_version whose first answers
            # differ from the earlier ones as a group. Per-answer detection cannot see
            # this when every answer stays on topic (e.g. an upsell added to each one).
            version_trigger = _check_new_version(conn, company_id, meta) if output_id is not None else None
            if version_trigger:
                save_alert(conn, company_id=company_id, alert_type="version_regression",
                           message=version_trigger["message"])
                slack_queue.append({
                    "alert_type": "version_regression",
                    "message"   : f"Company: {company['company_name']} | {version_trigger['message']}",
                    "score"     : quality,
                    "response"  : response,
                })

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
                    message    = f"Score: {_fmt_score(quality)} | {alert_msg}"
                )
                slack_queue.append({
                    "alert_type": "anomaly",
                    "message"   : f"Company: {company['company_name']} | {alert_msg}",
                    "score"     : quality,
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
                    "score"     : quality,
                    "response"  : response
                })
    # <- transaction committed and connection closed here

    if warmup_result is not None:
        return warmup_result

    # Step 8: Slack, after the DB work is safely committed.
    # alert_sent keeps its old meaning: an alert was raised and stored.
    for alert in slack_queue:
        send_slack_alert(**alert)

    if version_trigger:
        _schedule_investigation(company_id, company["company_name"], trigger=version_trigger)
    elif recent_flagged >= INVESTIGATE_MIN_FLAGGED:
        _schedule_investigation(company_id, company["company_name"])

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
INVESTIGATE_MIN_FLAGGED = int(os.environ.get("INVESTIGATE_MIN_FLAGGED", "5"))
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


def _schedule_investigation(company_id, company_name, trigger=None):
    if not INVESTIGATIONS_ENABLED:
        return
    with _pending_lock:
        if company_id in _pending_investigations:
            return
        _pending_investigations.add(company_id)

    def work():
        try:
            # Let more of the incident arrive before looking at it.
            time.sleep(INVESTIGATION_SETTLE_S)
            run_investigation(company_id, company_name, send_slack=send_slack_text, trigger=trigger)
        except Exception as e:
            print(f"Investigation thread error: {safe_error(e)}", flush=True)
        finally:
            with _pending_lock:
                _pending_investigations.discard(company_id)

    threading.Thread(target=work, name=f"investigate-{company_id}", daemon=True).start()
    print(f"Investigation scheduled for company {company_id} in {INVESTIGATION_SETTLE_S}s", flush=True)


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
        "detection_active" : not state["warmup"],
        "warmup_progress"  : f"{state['good_count']}/{WARMUP_REQUIRED}",
        "threshold"        : state["threshold"],
        "stats"            : stats,
        "recent_outputs"   : output_list,
        "recent_alerts"    : alert_list,
        "score_history"    : scores
    }

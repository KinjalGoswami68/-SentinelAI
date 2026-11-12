"""
History import (M14 step 7): build a bot's baseline from past question/answer pairs at once.

POST /ingest/batch takes up to BATCH_MAX answers and returns 202; one background worker embeds
them in small chunks (after each chunk it pauses as long as the chunk took, so live /ingest
keeps at least half of Render's 0.1 CPU; ~90 s per 100 answers there) and stores
them with source='import'. Imported answers get the day-1 quality flags (for the baseline
review) but never open incidents, are never spot-checked and never send Slack. Original times
(`t`) are kept; without them, answers are spaced SPACING_MIN apart, ending now.
Cap: IMPORT_PER_BOT_DAY answers per bot per day (counted in llm_usage, kind 'import').
"""

import queue
import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np

BATCH_MAX = 100
IMPORT_PER_BOT_DAY = 300
CHUNK = 10
PAUSE_S = 1.0
SPACING_MIN = 2
RETRY_WAIT_S = 10

_q = queue.Queue(maxsize=50)
_started = False
_lock = threading.Lock()


def times_for(rows, now=None):
    """Timestamps: given ones (ISO, at most now), else spaced SPACING_MIN apart ending now."""
    now = now or datetime.now(timezone.utc)
    out = []
    n = len(rows)
    for i, r in enumerate(rows):
        t = r.get("t")
        if t:
            try:
                ts = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
                ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
                out.append(min(ts, now))           # a future time would distort the windows
                continue
            except ValueError:
                pass
        out.append(now - timedelta(minutes=SPACING_MIN * (n - 1 - i)))
    return out


def queue_full():
    return _q.full()


def submit(company_id, rows, on_done):
    """Queue one batch (already validated). Returns False if the queue is full."""
    global _started
    try:
        _q.put_nowait((company_id, rows, on_done))
    except queue.Full:
        return False
    with _lock:
        if not _started:
            _started = True
            threading.Thread(target=_work, daemon=True, name="importer").start()
    return True


def _save_chunk(database, quality, is_refusal_or_empty, company_id, part, vecs, times):
    """Store one embedded chunk in one transaction. Returns how many rows were new."""
    stored = 0
    with database.connection() as conn:
        for k, r in enumerate(part):
            pv, rv = vecs[2 * k], vecs[2 * k + 1]
            rel = round(float(np.dot(pv, rv)), 4)
            meta = {"request_id": r.get("request_id"), "prompt_version": r.get("prompt_version"),
                    "model_version": r.get("model_version"), "status": None, "latency_ms": None,
                    "session_hash": None, "is_nonanswer": is_refusal_or_empty(r["response"]),
                    "qa_relevance": rel, "quality_flags": quality.check(r["prompt"], r["response"], rel),
                    "source": "import", "timestamp": times[k]}
            if database.save_output(conn, company_id, r["prompt"], r["response"], None, 0,
                                    prompt_embedding=pv, response_embedding=rv, meta=meta) is not None:
                stored += 1
    return stored


def _work():
    import psycopg2
    import database
    import quality
    from embeddings import embed
    from detector import is_refusal_or_empty
    while True:
        company_id, rows, on_done = _q.get()
        stored = 0
        try:
            times = times_for(rows)
            for start in range(0, len(rows), CHUNK):
                part = rows[start:start + CHUNK]
                t0 = time.monotonic()
                vecs = embed([x for r in part for x in (r["prompt"], r["response"])])
                busy = time.monotonic() - t0
                for attempt in (1, 2):                   # one retry if the pooler drops the connection
                    try:
                        stored += _save_chunk(database, quality, is_refusal_or_empty, company_id, part, vecs,
                                              times[start:start + CHUNK])
                        break
                    except psycopg2.OperationalError:
                        if attempt == 2:
                            raise
                        time.sleep(RETRY_WAIT_S)
                time.sleep(max(PAUSE_S, busy))           # at most ~half the CPU: live /ingest comes first
            print(f"import: company {company_id}: {stored} of {len(rows)} stored", flush=True)
            on_done(company_id)
        except Exception as e:
            print(f"import failed for company {company_id}: {type(e).__name__}", flush=True)
        finally:
            _q.task_done()

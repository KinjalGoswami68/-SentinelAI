"""SentinelAI client: send each chatbot exchange to SentinelAI without slowing the bot.

    import sentinelai
    sentinelai.init(api_key="sk-...")           # or env SENTINELAI_API_KEY
    sentinelai.track(prompt, response, model_version="gpt-oss-20b", latency_ms=840)

track() returns at once; the POST runs in a background thread with one retry, so a
sleeping server never blocks the bot, and errors are never raised to the caller.
Each call gets a request_id (uuid4 unless given), so the retry cannot store a row twice.
"""
import json, os, threading, time, urllib.error, urllib.request, uuid

__version__ = "0.1.0"
_cfg = {"api_key": os.environ.get("SENTINELAI_API_KEY"),
        "url": os.environ.get("SENTINELAI_URL", "https://sentinelai-qqph.onrender.com"), "timeout": 30}
_threads = []


def init(api_key=None, url=None, timeout=None):
    _cfg.update({k: v for k, v in (("api_key", api_key), ("url", url), ("timeout", timeout)) if v})


def _post(payload):
    req = urllib.request.Request(_cfg["url"].rstrip("/") + "/ingest", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "X-API-Key": _cfg["api_key"] or ""})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=_cfg["timeout"]) as r:
                return r.status
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code != 429:      # 4xx: retrying will not help
                return e.code
        except Exception:
            pass
        if attempt == 1:
            time.sleep(2)
    return None


def track(prompt, response, **meta):
    """Send one exchange. meta: request_id, prompt_version, model_version, status,
    latency_ms, session_id, quality_score. Returns the background thread."""
    payload = {"prompt": prompt, "response": response, "request_id": str(uuid.uuid4()), **meta}
    t = threading.Thread(target=_post, args=(payload,), daemon=True)
    t.start()
    _threads[:] = [x for x in _threads if x.is_alive()] + [t]   # keep only pending sends
    return t


def flush(timeout=60):
    """Wait for pending sends (call before a short script exits)."""
    for t in list(_threads):
        t.join(timeout)
    _threads[:] = [x for x in _threads if x.is_alive()]

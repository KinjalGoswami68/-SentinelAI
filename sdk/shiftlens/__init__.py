"""ShiftLens client: send each chatbot exchange to ShiftLens without slowing the bot.

    import shiftlens
    shiftlens.auto()          # track every OpenAI / Groq chat completion from here on
    # or, by hand:
    shiftlens.track(prompt, response, model_version="gpt-oss-20b", context=rag_text)

No key needed: the first run registers a bot, saves its key in ~/.shiftlens/config.json and
prints the dashboard link (never the key). Or set SHIFTLENS_API_KEY (do that in containers
without a persistent home folder, or every restart registers a new bot).

Never slows or breaks the bot: one background worker sends from a bounded queue; errors are
never raised to the caller; a wrapped client returns exactly what it returned before. Each
answer carries a request_id, so a retry cannot store it twice. `context` (e.g. RAG documents)
is used by the server for a spot-check and never stored.
"""
import json, os, queue, socket, sys, threading, time, urllib.error, urllib.request, uuid
from pathlib import Path

__version__ = "0.2.0"
APP_URL = "https://kinjalgoswami68.github.io/ShiftLens/app/"
MAX_QUEUE = 1000          # answers waiting to be sent; beyond this they are dropped and counted
CONTEXT_MAX = 8000        # characters of context sent per answer

# SENTINELAI_* are the names from before the rename (2026-11-11), still read as a fallback.
_cfg = {"api_key": os.environ.get("SHIFTLENS_API_KEY") or os.environ.get("SENTINELAI_API_KEY"),
        "url": os.environ.get("SHIFTLENS_URL") or os.environ.get("SENTINELAI_URL") or "https://sentinelai-qqph.onrender.com",
        "timeout": 60, "retry_after": 10}   # covers a ~60-90 s cold start of a sleeping free server
_q = queue.Queue(maxsize=MAX_QUEUE)
_ready = threading.Event()                   # set once a key is known
_lock = threading.Lock()
_state = {"inited": False, "worker": None, "registering": False, "dropped": 0, "patched": []}


# ---- setup -------------------------------------------------------------------------------

def _config_file():
    return Path(os.environ.get("SHIFTLENS_HOME") or (Path.home() / ".shiftlens")) / "config.json"


def _saved_key(url):
    try:
        return (json.loads(_config_file().read_text(encoding="utf-8")).get(url) or {}).get("api_key")
    except (OSError, ValueError):
        return None


def _save_key(url, key, name):
    f = _config_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data[url] = {"api_key": key, "name": name}
        fd = os.open(str(f), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # owner-only where the OS supports it
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, indent=1)
        return True
    except OSError:
        return False


def _default_name():
    script = Path(sys.argv[0]).stem if sys.argv and sys.argv[0] else "python"
    return f"{script} on {socket.gethostname()}"[:100]


def _register(name):
    """Background: POST /register until it works; respects 429 Retry-After."""
    url, wait = _cfg["url"].rstrip("/"), 10
    while True:
        req = urllib.request.Request(url + "/register", data=json.dumps({"company_name": name}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=_cfg["timeout"]) as r:
                key = json.loads(r.read().decode()).get("api_key")
            if key:
                _cfg["api_key"] = key
                saved = _save_key(_cfg["url"], key, name)
                _ready.set()
                where = f"saved in {_config_file()}" if saved else "not saved (could not write the config file; set SHIFTLENS_API_KEY)"
                print(f"ShiftLens: registered bot '{name}'. Dashboard: {APP_URL} (sign in with the key {where}).",
                      file=sys.stderr, flush=True)
                return
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = min(3600, int(e.headers.get("Retry-After") or 60))
            elif e.code < 500:
                print(f"ShiftLens: registration failed (HTTP {e.code}); set SHIFTLENS_API_KEY.", file=sys.stderr, flush=True)
                return
        except Exception:
            pass
        time.sleep(wait)
        wait = min(300, wait * 2)


def init(api_key=None, url=None, timeout=None, name=None, register=True):
    """Configure the client. With no key anywhere (argument, SHIFTLENS_API_KEY, ~/.shiftlens),
    registers a bot in the background on first use. Returns at once."""
    with _lock:
        _cfg.update({k: v for k, v in (("api_key", api_key), ("url", url), ("timeout", timeout)) if v})
        if not _cfg["api_key"]:
            _cfg["api_key"] = _saved_key(_cfg["url"])
        if _cfg["api_key"]:
            _ready.set()
        elif register and not _state["registering"]:
            _state["registering"] = True
            threading.Thread(target=_register, args=(name or _default_name(),), daemon=True).start()
        if _state["worker"] is None:
            _state["worker"] = threading.Thread(target=_work, daemon=True)
            _state["worker"].start()
        _state["inited"] = True


# ---- sending -----------------------------------------------------------------------------

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
            time.sleep(_cfg["retry_after"])
    return None


def _work():
    while True:
        payload = _q.get()
        try:
            _ready.wait()
            _post(payload)
        except Exception:
            pass
        finally:
            _q.task_done()


def track(prompt, response, context=None, **meta):
    """Queue one exchange; returns at once and never raises. meta: request_id, prompt_version,
    model_version, status, latency_ms, session_id, quality_score. context: documents the answer
    should be grounded in (used for a spot-check, never stored)."""
    try:
        if not _state["inited"]:
            init()
        payload = {"prompt": "" if prompt is None else str(prompt), "response": "" if response is None else str(response),
                   "request_id": str(uuid.uuid4()), **{k: v for k, v in meta.items() if v is not None}}
        if context:
            payload["context"] = (context if isinstance(context, str) else "\n\n".join(map(str, context)))[:CONTEXT_MAX]
        _q.put_nowait(payload)
    except queue.Full:
        _state["dropped"] += 1
    except Exception:
        pass


def flush(timeout=60):
    """Wait until queued answers are sent (call before a short script exits). Returns True if done."""
    end = time.monotonic() + timeout
    while _q.unfinished_tasks and time.monotonic() < end:
        time.sleep(0.05)
    return _q.unfinished_tasks == 0


def stats():
    return {"queued": _q.unfinished_tasks, "dropped": _state["dropped"], "has_key": _ready.is_set(), "patched": list(_state["patched"])}


def _post_batch(req, retry_wait=10):
    """One batch; a 503 (database briefly down, nothing queued) is retried once."""
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=_cfg["timeout"] * 2) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code != 503 or attempt == 2:
                raise
            time.sleep(retry_wait)


def import_history(pairs, batch=100):
    """Build the bot's baseline at once from past answers: (question, answer) tuples or dicts with
    prompt, response and optional t (ISO time), request_id, prompt_version, model_version. Blocks
    until sent (call it once, not per request). Imports never open incidents or send alerts.
    The server takes 300 a day per bot. Returns {"accepted", "skipped", "batches", "stopped"}."""
    if not _state["inited"]:
        init()
    if not _ready.wait(180):
        return {"accepted": 0, "skipped": len(pairs), "batches": 0, "stopped": "no API key yet"}
    rows = []
    for p in pairs:
        if isinstance(p, dict):
            r = {k: str(v) for k, v in p.items() if k in ("prompt", "response", "t", "request_id", "prompt_version", "model_version") and v}
        else:
            r = {"prompt": str(p[0]), "response": str(p[1])}
        if r.get("prompt") and r.get("response"):
            rows.append(r)
    out = {"accepted": 0, "skipped": len(pairs) - len(rows), "batches": 0, "stopped": None}
    for i in range(0, len(rows), batch):
        part = rows[i:i + batch]
        req = urllib.request.Request(_cfg["url"].rstrip("/") + "/ingest/batch", data=json.dumps({"answers": part}).encode(),
                                     headers={"Content-Type": "application/json", "X-API-Key": _cfg["api_key"] or ""})
        try:
            d = _post_batch(req)
            out["accepted"] += d.get("accepted", 0); out["skipped"] += d.get("skipped_over_daily_cap", 0); out["batches"] += 1
            if d.get("skipped_over_daily_cap"):
                out["stopped"] = "daily import cap reached"; out["skipped"] += len(rows) - i - len(part); break
        except urllib.error.HTTPError as e:
            out["stopped"] = f"HTTP {e.code}"; out["skipped"] += len(rows) - i; break
        except Exception as e:
            out["stopped"] = type(e).__name__; out["skipped"] += len(rows) - i; break
    return out


# ---- auto-capture ------------------------------------------------------------------------

def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):   # content parts: [{"type": "text", "text": ...}, ...]
        return "\n".join(p.get("text", "") if isinstance(p, dict) else str(getattr(p, "text", "")) for p in content)
    return "" if content is None else str(content)


def _prompt_of(kwargs):
    for m in reversed(kwargs.get("messages") or []):
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
        if role == "user":
            return _text(m.get("content") if isinstance(m, dict) else getattr(m, "content", None))
    return ""


def _answer_of(result):
    try:
        return _text(result.choices[0].message.content)
    except Exception:
        return ""


def _delta_of(chunk):
    try:
        return chunk.choices[0].delta.content or ""
    except Exception:
        return ""


def _record(kwargs, answer, t0, model=None, status="ok"):
    try:
        track(_prompt_of(kwargs), answer, model_version=kwargs.get("model") or model,
              latency_ms=int((time.monotonic() - t0) * 1000), status=status)
    except Exception:
        pass


class _Stream:
    """Passes every chunk through unchanged; records the joined text when the stream ends."""
    def __init__(self, inner, kwargs, t0):
        self._inner, self._kwargs, self._t0, self._parts, self._done = inner, kwargs, t0, [], False

    def _finish(self):
        if not self._done:
            self._done = True
            _record(self._kwargs, "".join(self._parts), self._t0)

    def __iter__(self):
        return self

    def __next__(self):
        try:
            chunk = next(self._inner)
        except StopIteration:
            self._finish()
            raise
        self._parts.append(_delta_of(chunk))
        return chunk

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            chunk = await self._inner.__anext__()
        except StopAsyncIteration:
            self._finish()
            raise
        self._parts.append(_delta_of(chunk))
        return chunk

    def __enter__(self):
        self._inner.__enter__(); return self

    def __exit__(self, *a):
        self._finish(); return self._inner.__exit__(*a)

    async def __aenter__(self):
        await self._inner.__aenter__(); return self

    async def __aexit__(self, *a):
        self._finish(); return await self._inner.__aexit__(*a)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _wrap_sync(orig):
    def create(self, *args, **kwargs):
        t0 = time.monotonic()
        try:
            result = orig(self, *args, **kwargs)
        except Exception:
            _record(kwargs, "", t0, status="error")
            raise
        if kwargs.get("stream"):
            try:
                return _Stream(iter(result) if not hasattr(result, "__next__") else result, kwargs, t0)
            except Exception:
                return result
        _record(kwargs, _answer_of(result), t0, model=getattr(result, "model", None))
        return result
    create.__shiftlens__ = True
    create.__wrapped__ = orig
    return create


def _wrap_async(orig):
    async def create(self, *args, **kwargs):
        t0 = time.monotonic()
        try:
            result = await orig(self, *args, **kwargs)
        except Exception:
            _record(kwargs, "", t0, status="error")
            raise
        if kwargs.get("stream"):
            try:
                return _Stream(result, kwargs, t0)
            except Exception:
                return result
        _record(kwargs, _answer_of(result), t0, model=getattr(result, "model", None))
        return result
    create.__shiftlens__ = True
    create.__wrapped__ = orig
    return create


def auto(**init_kwargs):
    """Track every chat completion of the OpenAI and Groq Python clients (sync, async,
    streaming). Libraries that are not installed are skipped. Returns what was patched."""
    import importlib
    init(**init_kwargs)
    for lib in ("openai", "groq"):
        try:
            mod = importlib.import_module(f"{lib}.resources.chat.completions")
        except Exception:
            continue
        for cls_name, wrap in (("Completions", _wrap_sync), ("AsyncCompletions", _wrap_async)):
            cls = getattr(mod, cls_name, None)
            orig = getattr(cls, "create", None) if cls else None
            if orig is None or getattr(orig, "__shiftlens__", False):
                continue
            setattr(cls, "create", wrap(orig))
            _state["patched"].append(f"{lib}.{cls_name}")
    return list(_state["patched"])

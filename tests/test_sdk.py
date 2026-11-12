"""The Python client (sdk/shiftlens): one-call setup and auto-capture, with a fake openai module
and a fake network. No real HTTP calls."""
import asyncio
import importlib
import io
import json
import os
import sys
import tempfile
import time
import types
import unittest
import urllib.error
from contextlib import redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sdk"))


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def completion(text, model="m-1"):
    return Obj(model=model, choices=[Obj(message=Obj(content=text))])


def chunk(text):
    return Obj(choices=[Obj(delta=Obj(content=text))])


def fake_openai():
    """openai.resources.chat.completions with Completions / AsyncCompletions."""
    mod = types.ModuleType("openai.resources.chat.completions")

    class Completions:
        def create(self, **kwargs):
            if kwargs.get("fail"):
                raise RuntimeError("provider down")
            if kwargs.get("stream"):
                return iter([chunk("Hel"), chunk("lo"), chunk(None)])
            return completion("Hello!", kwargs.get("model"))

    class AStream:
        def __init__(self, parts): self.parts = list(parts)
        def __aiter__(self): return self
        async def __anext__(self):
            if not self.parts: raise StopAsyncIteration
            return self.parts.pop(0)

    class AsyncCompletions:
        async def create(self, **kwargs):
            if kwargs.get("stream"):
                return AStream([chunk("As"), chunk("ync")])
            return completion("Async hello", kwargs.get("model"))

    mod.Completions, mod.AsyncCompletions = Completions, AsyncCompletions
    for name in ("openai", "openai.resources", "openai.resources.chat"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["openai.resources.chat.completions"] = mod
    return mod


MSGS = [{"role": "system", "content": "be nice"}, {"role": "user", "content": "Can I return my phone?"}]


class SDK(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        os.environ["SHIFTLENS_HOME"] = self.home
        for k in ("SHIFTLENS_API_KEY", "SENTINELAI_API_KEY"):
            os.environ.pop(k, None)
        sys.modules.pop("openai.resources.chat.completions", None)
        import shiftlens
        self.sl = importlib.reload(shiftlens)
        self.sent = []
        self.sl._post = lambda payload: self.sent.append(payload) or 200
        self.oa = fake_openai()

    def wait_sent(self, n):
        end = time.monotonic() + 5
        while len(self.sent) < n and time.monotonic() < end:
            time.sleep(0.01)
        return self.sent

    def test_track_with_key_and_context(self):
        self.sl.init(api_key="sk-test")
        self.assertIsNone(self.sl.track("q", "a", context=["doc one", "doc two"], model_version="x", status=None))
        p = self.wait_sent(1)[0]
        self.assertEqual((p["prompt"], p["response"], p["context"], p["model_version"]), ("q", "a", "doc one\n\ndoc two", "x"))
        self.assertNotIn("status", p); self.assertTrue(p["request_id"])

    def test_track_never_raises(self):
        self.sl.init(api_key="sk-test")
        self.sl.track(None, object(), context=12345)          # odd inputs: no exception
        self.assertTrue(self.sl.flush(5))

    def test_auto_sync_returns_the_same_object_and_records(self):
        self.sl.init(api_key="sk-test")
        self.assertEqual(self.sl.auto(), ["openai.Completions", "openai.AsyncCompletions"])
        out = self.oa.Completions().create(model="gpt-x", messages=MSGS)
        self.assertEqual(out.choices[0].message.content, "Hello!")
        p = self.wait_sent(1)[0]
        self.assertEqual((p["prompt"], p["response"], p["model_version"], p["status"]), ("Can I return my phone?", "Hello!", "gpt-x", "ok"))
        self.assertIn("latency_ms", p)

    def test_auto_is_idempotent(self):
        self.sl.init(api_key="sk-test")
        self.sl.auto(); self.sl.auto()
        self.oa.Completions().create(model="m", messages=MSGS)
        self.assertEqual(len(self.wait_sent(1)), 1)
        time.sleep(0.1); self.assertEqual(len(self.sent), 1)    # not wrapped twice

    def test_provider_error_reraised_and_recorded(self):
        self.sl.init(api_key="sk-test"); self.sl.auto()
        with self.assertRaises(RuntimeError):
            self.oa.Completions().create(model="m", messages=MSGS, fail=True)
        self.assertEqual(self.wait_sent(1)[0]["status"], "error")

    def test_shiftlens_failure_never_breaks_the_call(self):
        self.sl.init(api_key="sk-test"); self.sl.auto()
        def boom(*a, **k): raise ValueError("bug in tracking")
        self.sl.track = boom
        out = self.oa.Completions().create(model="m", messages=MSGS)
        self.assertEqual(out.choices[0].message.content, "Hello!")

    def test_stream_passes_chunks_and_records_joined_text(self):
        self.sl.init(api_key="sk-test"); self.sl.auto()
        chunks = list(self.oa.Completions().create(model="m", messages=MSGS, stream=True))
        self.assertEqual([c.choices[0].delta.content for c in chunks], ["Hel", "lo", None])
        self.assertEqual(self.wait_sent(1)[0]["response"], "Hello")

    def test_async_and_async_stream(self):
        self.sl.init(api_key="sk-test"); self.sl.auto()
        async def go():
            r = await self.oa.AsyncCompletions().create(model="m", messages=MSGS)
            s = await self.oa.AsyncCompletions().create(model="m", messages=MSGS, stream=True)
            parts = [c.choices[0].delta.content async for c in s]
            return r, parts
        r, parts = asyncio.run(go())
        self.assertEqual(r.choices[0].message.content, "Async hello"); self.assertEqual(parts, ["As", "ync"])
        self.assertEqual(sorted(p["response"] for p in self.wait_sent(2)), ["Async", "Async hello"])

    def test_overhead_under_1ms(self):
        self.sl.init(api_key="sk-test"); self.sl.auto()
        c = self.oa.Completions()
        orig = self.oa.Completions.create.__wrapped__
        n = 300
        t0 = time.perf_counter()
        for _ in range(n): orig(c, model="m", messages=MSGS)
        base = time.perf_counter() - t0
        t0 = time.perf_counter()
        for _ in range(n): c.create(model="m", messages=MSGS)
        wrapped = time.perf_counter() - t0
        self.assertLess((wrapped - base) / n * 1000, 1.0)
        self.sl.flush(10)

    def test_one_call_setup_registers_once_saves_key_never_prints_it(self):
        calls = []
        class Resp(io.BytesIO):
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False
        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            if len(calls) == 1:   # first try: rate limited
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {"Retry-After": "0"}, None)
            return Resp(json.dumps({"api_key": "sk-SECRET123", "company_id": 9}).encode())
        self.sl.urllib.request.urlopen = fake_urlopen
        self.sl.time.sleep = lambda s: None
        err = io.StringIO()
        with redirect_stderr(err):
            self.sl.track("q", "a")                        # no key anywhere: registers in the background
            self.assertTrue(self.sl._ready.wait(5))
        self.assertEqual([u.endswith("/register") for u in calls], [True, True])
        saved = json.loads((Path(self.home) / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(list(saved.values())[0]["api_key"], "sk-SECRET123")
        self.assertIn("Dashboard:", err.getvalue()); self.assertNotIn("sk-SECRET123", err.getvalue())
        self.assertEqual(self.wait_sent(1)[0]["prompt"], "q")   # the queued answer went out after registering
        importlib.reload(self.sl)                                # second run: key from the file, no registration
        self.sl.urllib.request.urlopen = fake_urlopen
        self.sl.init()
        self.assertTrue(self.sl._ready.is_set()); self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()

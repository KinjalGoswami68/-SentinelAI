"""History import (M14 step 7): importer.times_for, POST /ingest/batch caps and ordering,
and the SDK's import_history. Fakes for DB, queue and network."""
import importlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "sdk"))
from fastapi.testclient import TestClient  # noqa: E402
import importer  # noqa: E402
import main  # noqa: E402

NOW = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)


class Times(unittest.TestCase):
    def test_spaced_ending_now(self):
        t = importer.times_for([{}, {}, {}], NOW)
        self.assertEqual(t[-1], NOW)
        self.assertEqual(t[0], NOW - timedelta(minutes=2 * importer.SPACING_MIN))

    def test_given_times_kept_and_utc(self):
        t = importer.times_for([{"t": "2026-11-01T10:00:00Z"}, {"t": "2026-11-01T10:05:00"}, {"t": "not a time"}], NOW)
        self.assertEqual(t[0], datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc))
        self.assertEqual(t[1].tzinfo, timezone.utc)
        self.assertEqual(t[2], NOW)                       # unparseable: falls back to the spacing
        self.assertEqual(importer.times_for([{"t": "2030-01-01T00:00:00Z"}], NOW), [NOW])   # never in the future


class FakeConn:
    def __enter__(self): return self
    def __exit__(self, *a): return False


class Endpoint(unittest.TestCase):
    def setUp(self):
        self.used, self.queued, self.events = 0, [], []
        names = ("connection", "llm_usage_today", "record_llm_usage", "_authenticate")
        self.orig = {n: getattr(main, n) for n in names}
        self.orig_submit, self.orig_full = importer.submit, importer.queue_full
        main.connection = lambda *a, **k: FakeConn()
        main.llm_usage_today = lambda conn, **k: {"units": self.used, "tokens": 0, "requests": 0}
        def record(conn, model, kind, cid, units=1, **k):
            self.events.append("record"); self.used += units
        main.record_llm_usage = record
        main._authenticate = lambda key: {"id": 41, "company_name": "Acme bot"}
        def submit(cid, rows, on_done):
            self.events.append("submit"); self.queued.append((cid, rows)); return True
        importer.submit = submit
        importer.queue_full = lambda: False
        self.c = TestClient(main.app)

    def tearDown(self):
        for n, f in self.orig.items():
            setattr(main, n, f)
        importer.submit, importer.queue_full = self.orig_submit, self.orig_full

    def post(self, n):
        return self.c.post("/ingest/batch", headers={"X-API-Key": "sk-x"},
                           json={"answers": [{"prompt": f"q{i}", "response": f"a{i}"} for i in range(n)]})

    def test_accepts_and_counts_before_queuing(self):
        r = self.post(100)
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.json()["accepted"], 100)
        self.assertEqual(self.events, ["record", "submit"])   # a DB failure can't leave a queued batch behind
        self.assertEqual(len(self.queued[0][1]), 100)

    def test_daily_cap_trims_then_refuses(self):
        self.used = importer.IMPORT_PER_BOT_DAY - 30
        r = self.post(100)
        self.assertEqual((r.status_code, r.json()["accepted"], r.json()["skipped_over_daily_cap"]), (202, 30, 70))
        r = self.post(10)
        self.assertEqual(r.status_code, 429); self.assertIn("Retry-After", r.headers)
        self.assertEqual(len(self.queued), 1)

    def test_batch_size_limit(self):
        self.assertEqual(self.post(importer.BATCH_MAX + 1).status_code, 422)
        self.assertEqual(self.queued, [])

    def test_full_queue_counts_nothing(self):
        importer.queue_full = lambda: True
        r = self.post(10)
        self.assertEqual(r.status_code, 429); self.assertEqual((self.used, self.queued), (0, []))

    def test_db_down_queues_nothing(self):
        import psycopg2
        def down(*a, **k): raise psycopg2.OperationalError("pooler dropped")
        main.connection = down
        self.assertEqual(self.post(10).status_code, 503)
        self.assertEqual(self.queued, [])


class Resp(io.BytesIO):
    status = 202
    def __enter__(self): return self
    def __exit__(self, *a): return False


class SDKImport(unittest.TestCase):
    def setUp(self):
        os.environ["SHIFTLENS_HOME"] = tempfile.mkdtemp()
        import shiftlens
        self.sl = importlib.reload(shiftlens)
        self.sl._post = lambda payload: 200
        self.bodies = []
        self.orig_sleep = time.sleep
        self.addCleanup(setattr, time, "sleep", self.orig_sleep)
        time.sleep = lambda s: None
        self.sl.init(api_key="sk-test", url="http://api.test")

    def urlopen(self, replies):
        def fake(req, timeout=None):
            self.bodies.append(json.loads(req.data))
            r = replies.pop(0)
            if isinstance(r, int):
                raise urllib.error.HTTPError(req.full_url, r, "err", {}, None)
            return Resp(json.dumps(r).encode())
        self.sl.urllib.request.urlopen = fake

    def test_batches_and_skips_empty_pairs(self):
        self.urlopen([{"accepted": 100, "skipped_over_daily_cap": 0}, {"accepted": 50, "skipped_over_daily_cap": 0}])
        pairs = [(f"q{i}", f"a{i}") for i in range(150)] + [("q", ""), {"prompt": "q", "response": "a", "t": "2026-11-01T10:00:00Z"}]
        out = self.sl.import_history(pairs[:150] + pairs[150:151])
        self.assertEqual(out, {"accepted": 150, "skipped": 1, "batches": 2, "stopped": None})
        self.assertEqual([len(b["answers"]) for b in self.bodies], [100, 50])

    def test_dict_rows_keep_time(self):
        self.urlopen([{"accepted": 1, "skipped_over_daily_cap": 0}])
        self.sl.import_history([{"prompt": "q", "response": "a", "t": "2026-11-01T10:00:00Z", "other": "x"}])
        self.assertEqual(self.bodies[0]["answers"][0], {"prompt": "q", "response": "a", "t": "2026-11-01T10:00:00Z"})

    def test_503_retried_once_then_cap_stops(self):
        self.urlopen([503, {"accepted": 100, "skipped_over_daily_cap": 0}, {"accepted": 20, "skipped_over_daily_cap": 80}])
        out = self.sl.import_history([(f"q{i}", f"a{i}") for i in range(300)])
        self.assertEqual(out, {"accepted": 120, "skipped": 180, "batches": 2, "stopped": "daily import cap reached"})

    def test_other_errors_stop(self):
        self.urlopen([429])
        out = self.sl.import_history([(f"q{i}", f"a{i}") for i in range(10)])
        self.assertEqual((out["accepted"], out["skipped"], out["stopped"]), (0, 10, "HTTP 429"))


if __name__ == "__main__":
    unittest.main()

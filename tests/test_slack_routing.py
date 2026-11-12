"""Per-bot Slack webhook (POST /bot/slack) and routing (_notify). Fakes for DB and Slack."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402

HOOK = "https://hooks.slack.com/services/T0AAA/B0BBB/abcdEFGH1234wxyz"


class FakeConn:
    def __enter__(self): return self
    def __exit__(self, *a): return False


class Slack(unittest.TestCase):
    def setUp(self):
        self.store, self.sent = {}, []
        self.orig = {n: getattr(main, n) for n in ("connection", "set_slack_webhook", "get_slack_webhook",
                                                    "send_slack_text", "_authenticate", "OWNER_COMPANY_IDS")}
        main.connection = lambda *a, **k: FakeConn()
        main.set_slack_webhook = lambda conn, cid, url: self.store.__setitem__(cid, url)
        main.get_slack_webhook = lambda conn, cid: self.store.get(cid)
        main.send_slack_text = lambda text, webhook=None: self.sent.append((webhook, text)) or True
        main._authenticate = lambda key: {"id": 41, "company_name": "Acme bot"}
        main.OWNER_COMPANY_IDS = {12}
        main._webhooks.clear()
        self.c = TestClient(main.app)

    def tearDown(self):
        for n, f in self.orig.items():
            setattr(main, n, f)
        main._webhooks.clear()

    def post(self, webhook):
        return self.c.post("/bot/slack", json={"webhook": webhook}, headers={"X-API-Key": "sk-x"})

    def test_rejects_non_slack_urls(self):
        for bad in ("https://example.com/hook", "http://hooks.slack.com/services/a/b/c", "https://hooks.slack.com/services/a/b"):
            self.assertEqual(self.post(bad).status_code, 400)
        self.assertEqual(self.store, {})

    def test_test_message_must_succeed(self):
        main.send_slack_text = lambda text, webhook=None: False
        r = self.post(HOOK)
        self.assertEqual(r.status_code, 400); self.assertEqual(self.store, {})

    def test_connect_never_returns_the_url(self):
        r = self.post(HOOK)
        self.assertEqual(r.status_code, 200); self.assertEqual(r.json(), {"connected": True, "ends_with": "wxyz"})
        self.assertNotIn("hooks.slack.com", r.text); self.assertEqual(self.store[41], HOOK)
        self.assertEqual(self.sent[0][0], HOOK); self.assertIn("connected for Acme bot", self.sent[0][1])

    def test_remove(self):
        self.post(HOOK)
        r = self.post(None)
        self.assertEqual(r.json(), {"connected": False, "ends_with": None}); self.assertIsNone(self.store[41])

    def test_routing(self):
        self.store[41] = HOOK
        self.assertTrue(main._notify(41, "own bot"))           # own webhook
        self.assertTrue(main._notify(12, "owner bot"))         # owner's channel (default webhook)
        self.assertFalse(main._notify(77, "someone else"))     # dashboard only
        self.assertEqual([w for w, _ in self.sent], [HOOK, None])


if __name__ == "__main__":
    unittest.main()

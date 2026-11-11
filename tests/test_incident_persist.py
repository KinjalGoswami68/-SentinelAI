"""
main._incident_step must store every signal on an open incident, not only actions.

Found live 2026-11-11 (Volt incident #6): a later flagged answer and a version
regression on an open incident changed last_signal_at / signal_count / reasons in
memory, returned no action, and were never saved, so the incident closed 30 min after
its first signal. No database: the database functions are replaced with fakes.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main  # noqa: E402


class IncidentPersist(unittest.TestCase):
    def setUp(self):
        self.saved = []
        opened = datetime.now(timezone.utc) - timedelta(minutes=10)
        self.inc = {"id": 6, "status": "open", "severity": 2, "opened_at": opened, "last_signal_at": opened,
                    "closed_at": None, "flapping": False, "reopen_count": 0, "signal_count": 1,
                    "reasons": ["answers drifting from normal"], "slack_messages": 1}
        self._orig = {n: getattr(main, n) for n in ("current_incident", "window_counts", "save_incident")}
        main.current_incident = lambda conn, cid, flap_hours=2: dict(self.inc)
        main.window_counts = lambda conn, cid: {"answers": 12, "flagged": 1, "nonanswers": 0,
                                                "hard_failures": 0, "usual_flag_rate": 0.02}
        main.save_incident = lambda conn, cid, inc: (self.saved.append(dict(inc)), inc["id"])[1]

    def tearDown(self):
        for n, f in self._orig.items():
            setattr(main, n, f)

    def test_signal_on_open_incident_is_saved(self):
        msgs, inc = main._incident_step(None, 12, {"version_regression"}, True)
        self.assertEqual(msgs, [])                              # no new Slack message
        self.assertEqual(len(self.saved), 1, "the signal was not stored")
        s = self.saved[0]
        self.assertEqual(s["signal_count"], 2)
        self.assertGreater(s["last_signal_at"], self.inc["last_signal_at"])
        self.assertEqual(len(s["reasons"]), 2)                  # version regression reason merged in

    def test_no_signal_no_write(self):
        main._incident_step(None, 12, set(), False)
        self.assertEqual(self.saved, [])


if __name__ == "__main__":
    unittest.main()

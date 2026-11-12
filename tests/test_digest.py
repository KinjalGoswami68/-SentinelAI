"""Daily digest (digest.build). No DB, no network."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import digest  # noqa: E402


def stats(answers, flagged=0, nonanswers=0, q=None, spot=(0, 0, [])):
    return {"answers": answers, "flagged": flagged, "nonanswers": nonanswers, "avg_len": 180.4,
            "quality": q or {}, "spot_checked": spot[0], "spot_problems": spot[1], "spot_examples": spot[2]}


class Digest(unittest.TestCase):
    def test_quiet_day(self):
        b = digest.build("Volt", "12 Nov", stats(72), stats(70), [], "ready")
        t = digest.text(b)
        self.assertIn("*Daily digest: Volt, 12 Nov*", t); self.assertIn("72 answers today (+2 vs yesterday)", t)
        self.assertIn("Looked wrong: nothing.", t); self.assertEqual(b["summary"], "nothing wrong")
        self.assertLessEqual(len(b["lines"]), 10)

    def test_bad_day(self):
        b = digest.build("Acme", "12 Nov", stats(50, flagged=10, nonanswers=12, q={"refusal": 12, "low_relevance": 3},
                                                  spot=(5, 2, ["r1", "r7"])),
                         stats(48, flagged=1, q={"refusal": 1}), [{"id": 9, "cause": "prompt_change"}], "ready")
        t = digest.text(b)
        self.assertIn("1 incident(s): #9 prompt_change", t)
        self.assertIn("24% refusals or deflections", t); self.assertIn("2 of 5 likely have a problem (e.g. r1, r7)", t)
        self.assertIn("flagged 2% → 20%", t); self.assertEqual(b["summary"], "3 thing(s) to look at")

    def test_partly_supported_only_on_the_dashboard(self):
        today = dict(stats(40), spot_partly=3)
        b = digest.build("Volt", "12 Nov", today, stats(40), [], "ready")
        self.assertEqual(b["spot_partly"], 3); self.assertNotIn("partly", digest.text(b))
        self.assertEqual(b["summary"], "nothing wrong")

    def test_first_day_has_no_trend(self):
        t = digest.text(digest.build("New bot", "12 Nov", stats(12), stats(0), [], "learning"))
        self.assertNotIn("Since yesterday", t); self.assertNotIn("vs yesterday", t)

    def test_schedule_math(self):
        now = datetime(2026, 11, 12, 14, 0, tzinfo=timezone.utc)      # 19:30 IST
        self.assertEqual(digest._seconds_until("20:30", now), 3600)
        self.assertAlmostEqual(digest._seconds_until("20:30", datetime(2026, 11, 12, 15, 1, tzinfo=timezone.utc)), 86340)


if __name__ == "__main__":
    unittest.main()

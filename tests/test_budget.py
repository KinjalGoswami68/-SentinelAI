"""Caps for questions and tries (budget.decide) and the usage hook in groq_chat. No DB, no network."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import budget  # noqa: E402
import investigation as inv  # noqa: E402

NOON = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


class Decide(unittest.TestCase):
    def test_fresh_day_allows(self):
        self.assertEqual(budget.decide("question", 0, 0, 0, NOON), (True, "", 0))
        self.assertTrue(budget.decide("try", 0, 0, now=NOON)[0])

    def test_global_daily_caps(self):
        ok, msg, ra = budget.decide("question", 50_000, budget.QUESTIONS_PER_DAY, 0, NOON)
        self.assertFalse(ok); self.assertIn("15 free questions", msg); self.assertEqual(ra, 12 * 3600 + 1)
        ok, msg, _ = budget.decide("try", 50_000, budget.TRIES_PER_DAY, now=NOON)
        self.assertFalse(ok); self.assertIn("15 free tries", msg)
        self.assertTrue(budget.decide("try", 50_000, budget.TRIES_PER_DAY - 1, now=NOON)[0])

    def test_company_cap_questions_only(self):
        ok, msg, _ = budget.decide("question", 0, 3, budget.QUESTIONS_PER_COMPANY_DAY, NOON)
        self.assertFalse(ok); self.assertIn("This bot", msg)
        self.assertTrue(budget.decide("try", 0, 3, budget.QUESTIONS_PER_COMPANY_DAY, NOON)[0])

    def test_token_quota_says_so_plainly(self):
        nearly = budget.MODEL_DAILY_TOKENS - budget.DAILY_RESERVE - budget.TOKENS_PER_QUESTION + 1
        ok, msg, ra = budget.decide("question", nearly, 0, 0, NOON)
        self.assertFalse(ok); self.assertEqual(msg, budget.QUOTA_MESSAGE); self.assertIn("quota is used up", msg)
        self.assertGreater(ra, 0)

    def test_caps_fit_the_free_tier(self):
        worst = budget.QUESTIONS_PER_DAY * budget.TOKENS_PER_QUESTION + budget.TRIES_PER_DAY * budget.TOKENS_PER_TRY
        self.assertLessEqual(worst, budget.MODEL_DAILY_TOKENS - budget.DAILY_RESERVE)


class UsageHook(unittest.TestCase):
    def test_collects_per_thread_and_resets(self):
        inv.usage_start()
        inv._note_usage("openai/gpt-oss-20b", {"usage": {"total_tokens": 1234}})
        inv._note_usage("openai/gpt-oss-20b", {})                       # no usage block: 0, not a crash
        self.assertEqual(inv.usage_take(), [("openai/gpt-oss-20b", 1234), ("openai/gpt-oss-20b", 0)])
        self.assertEqual(inv.usage_take(), [])

    def test_not_started_records_nothing(self):
        import threading
        out = []
        t = threading.Thread(target=lambda: (inv._note_usage("m", {"usage": {"total_tokens": 9}}), out.append(inv.usage_take())))
        t.start(); t.join()
        self.assertEqual(out, [[]])


if __name__ == "__main__":
    unittest.main()

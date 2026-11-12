"""Baseline review (review.build / slack_text). No DB, no network."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import review  # noqa: E402


def rows(n, flagged=0, cat="refusal"):
    return [{"request_id": f"r{i}", "quality_flags": [cat] if i < flagged else []} for i in range(n)]


class Review(unittest.TestCase):
    def test_healthy_baseline_looks_fine(self):
        b = review.build(rows(45, flagged=1), [("r3", {"likely_problem": False})])
        self.assertEqual(b["verdict"], "looks fine"); self.assertEqual(b["issues"], [])
        self.assertIn("Nothing unusual", review.slack_text("Volt", b))

    def test_bad_start_is_asked_about(self):
        b = review.build(rows(50, flagged=6))
        self.assertEqual(b["verdict"], "check these")
        i = b["issues"][0]
        self.assertEqual((i["category"], i["count"], i["examples"]), ("refusal", 6, ["r0", "r1", "r2"]))
        self.assertIn("12% of the answers it learned from are refusals or deflections (6 of 50). Intended?", i["text"])
        text = review.slack_text("Acme bot", b)
        self.assertIn("Baseline review: Acme bot", text); self.assertIn("`r0`", text)

    def test_spot_check_problems_reported(self):
        b = review.build(rows(40), [("r1", {"likely_problem": True, "reason": "dodges the question"}),
                                    ("r2", {"likely_problem": False}), ("r3", None)])
        self.assertEqual(b["spot_checks"], {"checked": 2, "likely_problems": 1})
        self.assertIn("1 of 2 sampled answers likely have a problem (dodges the question)", b["issues"][-1]["text"])

    def test_most_common_issue_first(self):
        b = review.build(rows(40, flagged=5, cat="low_relevance")[:20] + rows(40, flagged=10, cat="refusal")[20:])
        self.assertTrue(b["issues"])
        self.assertEqual(b["issues"], sorted(b["issues"], key=lambda i: -i["share"]))


if __name__ == "__main__":
    unittest.main()

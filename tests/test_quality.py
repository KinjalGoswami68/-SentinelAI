"""Day-1 quality checks (quality.py). No DB, no network."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import quality as q  # noqa: E402

Q = "Can I return my phone after 10 days?"
OK = "Yes, phones can be returned within 30 days of delivery as long as they are unused."


class Check(unittest.TestCase):
    def test_normal_answer_is_clean(self):
        self.assertEqual(q.check(Q, OK, relevance=0.85), [])

    def test_each_category(self):
        self.assertEqual(q.check(Q, "   "), ["empty"])
        self.assertIn("refusal", q.check(Q, "I'm sorry, I can't help with that. Please contact the store."))
        self.assertIn("error", q.check(Q, "Traceback (most recent call last):\n  File x"))
        self.assertIn("error", q.check(Q, OK, status="timeout"))
        self.assertIn("repetition", q.check(Q, "Please contact support. " * 6))
        self.assertIn("garbled", q.check(Q, "Yes, you can return it within 30 days â€™ itâ€™s fine"))
        self.assertIn("garbled", q.check(Q, "#$%^&*(){}[]<>|\\/~`!@#$%^&*(){}[]<>|\\/~`!@#$%"))
        self.assertIn("language", q.check(Q, "Да, телефоны можно вернуть в течение 30 дней после доставки."))
        self.assertIn("too_long", q.check(Q, "word " * 1300))
        self.assertIn("too_short", q.check(Q, "k"))
        self.assertIn("low_relevance", q.check(Q, "Our stores in Pune open at 10 am every day of the week.", relevance=0.41))

    def test_relevance_skips_short_or_refusal(self):
        self.assertEqual(q.check(Q, "Yes.", relevance=0.2), [])                       # too short to judge
        self.assertEqual(q.check(Q, "I can't help with that, sorry about this.", relevance=0.2), ["refusal"])
        self.assertEqual(q.check("hi", OK, relevance=0.2), [])                        # no real question

    def test_hinglish_question_english_answer_is_not_a_mismatch(self):
        self.assertNotIn("language", q.check("Kya main apna phone 10 din baad return kar sakta hoon?", OK))


class Rates(unittest.TestCase):
    def test_needs_enough_answers(self):
        self.assertEqual(q.rate_reasons([["refusal"]] * 9), [])

    def test_rate_opens_reason(self):
        window = [["refusal"]] * 4 + [[]] * 6
        self.assertEqual(q.rate_reasons(window), ["quality: 40% refusals or deflections (4 of 10)"])
        self.assertEqual(q.rate_reasons([["refusal"]] * 2 + [[]] * 8), [])            # 20% < 30%

    def test_most_severe_first(self):
        window = [["low_relevance"]] * 6 + [["error"]] * 3 + [[]] * 11
        self.assertEqual(q.rate_reasons(window)[0], "quality: 30% answers unrelated to the question (6 of 20)")

    def test_summary(self):
        self.assertEqual(q.summary([["refusal"], ["refusal", "garbled"], [], None]), {"refusal": 2, "garbled": 1})


if __name__ == "__main__":
    unittest.main()

"""Spot-checks (spotcheck.py) with a fake model. No network, no DB."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import investigation as inv  # noqa: E402
import spotcheck as sc  # noqa: E402

CTX = "Returns are accepted within 30 days. Free shipping above 500 rupees."
Q, A = "How long do I have to return a phone?", "You can return it within 60 days."


def reply(verdict, quote="within 60 days", reason="contradicts the 30-day policy"):
    return json.dumps({"verdict": verdict, "reason": reason, "quote": quote})


class Judge(unittest.TestCase):
    def test_grounded_problem_is_likely_not_certain(self):
        seen = []
        def chat(model, msgs):
            seen.append((model, msgs[0]["content"][:30], msgs[1]["content"]))
            return reply("not_supported"), 150
        r, calls, why = sc.judge(Q, A, CTX, chat)
        self.assertEqual((r["kind"], r["verdict"], r["likely_problem"], r["model"]), ("grounded", "not_supported", True, sc.PRIMARY))
        self.assertEqual(r["quote"], "within 60 days"); self.assertEqual(calls, [(sc.PRIMARY, 150)])
        self.assertIn("<user_text>Returns are accepted", seen[0][2])         # context and answer are wrapped as data

    def test_partly_supported_is_a_neutral_note(self):
        r, _, _ = sc.judge(Q, A, CTX, lambda m, msgs: (reply("partly_supported"), 120))
        self.assertEqual((r["verdict"], r["likely_problem"]), ("partly_supported", False))
        self.assertFalse(sc.is_problem(r))
        self.assertFalse(sc.is_problem({"verdict": "partly_supported", "likely_problem": True}))   # stored under the old rule
        self.assertTrue(sc.is_problem({"verdict": "not_supported"}))

    def test_without_context_judges_answering(self):
        r, _, _ = sc.judge(Q, "Our stores open at 10 am.", None, lambda m, msgs: (reply("does_not_answer", quote=""), 90))
        self.assertEqual((r["kind"], r["verdict"], r["likely_problem"]), ("answers", "does_not_answer", True))

    def test_fallback_when_primary_unavailable(self):
        def chat(model, msgs):
            if model == sc.PRIMARY:
                raise inv.LLMUnavailable("HTTP 429")
            return reply("supported", quote=""), 200
        r, calls, _ = sc.judge(Q, A, CTX, chat)
        self.assertEqual(r["model"], sc.FALLBACK); self.assertEqual(r["likely_problem"], False)
        self.assertEqual(calls, [(sc.FALLBACK, 200)])

    def test_nothing_available(self):
        def chat(model, msgs):
            raise inv.LLMUnavailable("HTTP 429")
        r, calls, why = sc.judge(Q, A, CTX, chat)
        self.assertIsNone(r); self.assertIn("429", why)

    def test_invented_quote_removed_and_wrong_kind_is_unclear(self):
        r, _, _ = sc.judge(Q, A, CTX, lambda m, msgs: (reply("not_supported", quote="something it never said"), 100))
        self.assertEqual(r["quote"], "")
        r, _, _ = sc.judge(Q, A, CTX, lambda m, msgs: (reply("answers"), 100))
        self.assertEqual(r["verdict"], "unclear"); self.assertFalse(r["likely_problem"])

    def test_bad_json_tries_next_model(self):
        outs = iter([("not json", 50), (reply("supported", quote=""), 60)])
        r, calls, _ = sc.judge(Q, A, CTX, lambda m, msgs: next(outs))
        self.assertEqual(r["model"], sc.FALLBACK); self.assertEqual(len(calls), 2)


class Caps(unittest.TestCase):
    def test_allowed_models(self):
        self.assertEqual(sc.allowed_models(0, 0, 0, 0), (sc.PRIMARY, sc.FALLBACK))
        self.assertEqual(sc.allowed_models(0, sc.SPOT_PER_DAY, 0, 0), (sc.FALLBACK,))
        self.assertEqual(sc.allowed_models(sc.PRIMARY_DAILY_TOKENS, 0, 0, 0), (sc.FALLBACK,))
        self.assertEqual(sc.allowed_models(0, sc.SPOT_PER_DAY, sc.SPOT_FALLBACK_PER_DAY, 0), ())
        self.assertEqual(sc.allowed_models(0, 0, 0, sc.SPOT_PER_BOT_DAY), ())

    def test_sample_is_deterministic_and_about_a_quarter(self):
        picks = [sc.sampled(7, f"req-{i}") for i in range(4000)]
        self.assertEqual(picks[:50], [sc.sampled(7, f"req-{i}") for i in range(50)])
        self.assertAlmostEqual(sum(picks) / len(picks), sc.SAMPLE_PERCENT / 100, delta=0.03)

    def test_context_is_cut(self):
        _, msgs = sc.messages(Q, A, "x" * 20000)
        self.assertLess(len(msgs[1]["content"]), sc.CONTEXT_CHARS + 500)


if __name__ == "__main__":
    unittest.main()

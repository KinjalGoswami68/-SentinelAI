"""Input checks for "try without code" (tryit.validate) and the order rule. No DB, no network."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tryit  # noqa: E402


def answers(n, **extra):
    return [{"response": f"answer {i}", **extra} for i in range(n)]


class Validate(unittest.TestCase):
    def test_accepts_objects_and_plain_strings(self):
        b, a = tryit.validate({"before": answers(30), "after": ["just text"] * 10})
        self.assertEqual((len(b), len(a)), (30, 10))
        self.assertEqual(a[0], {"response": "just text"})

    def test_size_limits(self):
        for body in ({"before": answers(29), "after": answers(10)}, {"before": answers(151), "after": answers(10)},
                     {"before": answers(30), "after": answers(9)}, {"before": answers(30), "after": answers(61)}):
            with self.assertRaises(tryit.TryInvalid):
                tryit.validate(body)

    def test_fields(self):
        with self.assertRaises(tryit.TryInvalid):
            tryit.validate({"before": answers(30), "after": [{"prompt": "no response"}] * 10})
        with self.assertRaises(tryit.TryInvalid):
            tryit.validate({"before": answers(30), "after": [{"response": "x" * (tryit.MAX_CHARS + 1)}] * 10})
        with self.assertRaises(tryit.TryInvalid):
            tryit.validate({"before": answers(30), "after": [{"response": "x", "prompt": {"nested": 1}}] * 10})
        with self.assertRaises(tryit.TryInvalid):
            tryit.validate({"before": answers(30), "after": answers(10, t="yesterday")})
        with self.assertRaises(tryit.TryInvalid):
            tryit.validate([1, 2, 3])

    def test_times_parsed_as_utc(self):
        b, _ = tryit.validate({"before": answers(30, t="2026-11-12T10:00:00Z"), "after": answers(10, t="2026-11-12T11:00:00")})
        self.assertEqual(b[0]["t"].utcoffset().total_seconds(), 0)

    def test_unknown_fields_dropped(self):
        b, _ = tryit.validate({"before": answers(30, secret="x", prompt_version="v1"), "after": answers(10)})
        self.assertNotIn("secret", b[0]); self.assertEqual(b[0]["prompt_version"], "v1")


if __name__ == "__main__":
    unittest.main()

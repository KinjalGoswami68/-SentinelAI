"""
Day-1 quality checks (M14): need no baseline, so they work from a bot's first answer.

Per answer (cheap: text rules + one cosine of embeddings /ingest already computes):
  empty, refusal, error, repetition, garbled, language (answer not in the question's
  language), too_long, too_short, low_relevance (question-answer cosine < RELEVANCE_MIN).

Never one-answer alerts: `rate_reasons()` turns a window of answers into incident reasons
only when at least MIN_ANSWERS answers are in the window and a category reaches its rate.

Thresholds (2026-11-12, fixtures; docs/SCORECARD.md "Day-1 checks"):
  low_relevance < 0.55: normal M10 answers 1/400, Volt fixture 0/60, off-topic (another
  answer of the same bot) 192/399. Weak per answer, fine as a rate (30% vs 0.25% normal).
"""

import re

import detector
from investigation import _lang

RELEVANCE_MIN = 0.55
MIN_ANSWERS = 10              # in the incident window (30 min)
MAX_CHARS = 6000
MIN_CHARS = 3
HARD_STATUSES = {"error", "timeout", "failed", "failure"}

# category -> rate (share of answers in the window) that opens a quality reason
RATES = {
    "empty": 0.2, "refusal": 0.3, "error": 0.2, "repetition": 0.2, "garbled": 0.2,
    "language": 0.3, "too_long": 0.3, "too_short": 0.3, "low_relevance": 0.3,
}
LABEL = {
    "empty": "empty answers", "refusal": "refusals or deflections", "error": "errors",
    "repetition": "repetitive answers", "garbled": "garbled text", "language": "answers in a different language than the question",
    "too_long": "very long answers", "too_short": "very short answers", "low_relevance": "answers unrelated to the question",
}

ERROR_TEXT = re.compile(r"(Traceback \(most recent call last\)|Internal Server Error|rate limit (exceeded|reached)|"
                        r"\b(5\d\d|4\d\d) (Bad Gateway|Service Unavailable|Gateway Timeout|Too Many Requests)\b|"
                        r"^\s*\{\s*\"error\"\s*:|context length exceeded|An error occurred)", re.I | re.M)
MOJIBAKE = re.compile(r"(Ã[\x80-\xbf©¨¢£¤¥¦§ª«¬®¯°±²³´µ¶·¸¹º»¼½¾¿]|â€[™œ\x9d\"“”˜]|�)")


def _repetitive(text):
    words = re.findall(r"\w+", text.lower())
    if len(words) >= 40:
        grams = [" ".join(words[i:i + 3]) for i in range(len(words) - 2)]
        if 1 - len(set(grams)) / len(grams) > 0.35:
            return True
    lines = [l.strip().lower() for l in re.split(r"[\n.!?]+", text) if len(l.strip()) > 15]
    return any(lines.count(l) >= 3 for l in set(lines))


def _garbled(text):
    if MOJIBAKE.search(text):
        return True
    t = text.strip()
    if len(t) >= 40:
        printable = sum(ch.isprintable() or ch in "\n\t" for ch in t) / len(t)
        letters = sum(ch.isalpha() for ch in t) / len(t)
        if printable < 0.95 or letters < 0.35:
            return True
    return False


def check(prompt, response, relevance=None, status=None):
    """Flags for one answer: a sorted list of categories (empty = looks fine)."""
    r = (response or "").strip()
    p = (prompt or "").strip()
    flags = set()
    if not r:
        return ["empty"]
    if (status or "").lower() in HARD_STATUSES or ERROR_TEXT.search(r):
        flags.add("error")
    if "error" not in flags and detector.is_refusal_or_empty(r):
        flags.add("refusal")
    if _repetitive(r):
        flags.add("repetition")
    if _garbled(r):
        flags.add("garbled")
    if len(p) >= 20 and len(r) >= 20 and _lang(p) != _lang(r) and "other" in (_lang(p), _lang(r)):
        flags.add("language")
    if len(r) > MAX_CHARS:
        flags.add("too_long")
    elif len(r) < MIN_CHARS:
        flags.add("too_short")
    # relevance only where a cosine means something: a real question and a real answer
    if relevance is not None and len(p) >= 10 and len(r) >= 20 and not flags & {"refusal", "error"} and relevance < RELEVANCE_MIN:
        flags.add("low_relevance")
    return sorted(flags)


def rate_reasons(window_flags):
    """window_flags: one list of flags per answer in the window. Returns incident reasons
    like 'quality: 40% refusals or deflections (8 of 20)', most severe first."""
    n = len(window_flags)
    if n < MIN_ANSWERS:
        return []
    out = []
    for cat, rate in RATES.items():
        k = sum(cat in f for f in window_flags)
        if k / n >= rate:
            out.append((k / n, f"quality: {round(100 * k / n)}% {LABEL[cat]} ({k} of {n})"))
    return [r for _, r in sorted(out, reverse=True)]


def summary(rows_flags):
    """Counts per category over many answers (baseline review, digest): {category: count}."""
    out = {}
    for f in rows_flags:
        for c in f or []:
            out[c] = out.get(c, 0) + 1
    return out

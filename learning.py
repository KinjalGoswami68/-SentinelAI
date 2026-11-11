"""
M6: learning instead of a fixed warmup.

  learning     fewer than MIN_ROWS accepted answers: store, no flags
  provisional  enough answers but the threshold is not yet stable, or all traffic came
               within one hour: answers are flagged on the dashboard, no Slack, no
               investigations
  ready        threshold stable and traffic spans >= MIN_HOURS distinct hours: Slack
               alerts and investigations

Threshold = 99th percentile of the baseline's own 5th-nearest-neighbour distances
(detector.knn_threshold). Stable = the 90% interval of the threshold (200 subsamples
of the answers) is narrower than MAX_WIDTH in cosine distance. The spec first said
"under 15% of the threshold"; that relative rule made the varied Volt bot ready
before a narrow order-status bot, because a narrow bot's threshold is small
(~0.16 vs ~0.48) and the same absolute noise is a larger share of it. Decided
2026-11-11: absolute width, in the unit flags are decided in (docs/M6_learning.md).
The dashboard shows "threshold +/- X" and "+/- X%" (half the width).

Baseline once ready: the frozen anchor (the rows it became ready on) plus a rolling
window of the last ROLLING_ROWS accepted answers that are at least ROLLING_DELAY_H
old, so a regression in progress cannot teach itself into the baseline. The rolling
part is not refreshed while an incident is open.
"""

import os
from datetime import datetime

import detector

MIN_ROWS        = 30
MIN_HOURS       = int(os.environ.get("LEARNING_MIN_HOURS", "2"))   # 1 only for local fixture runs
MAX_WIDTH       = 0.03     # 90% interval width of the threshold, cosine distance
RESAMPLES       = 200
ROLLING_ROWS    = 500
ROLLING_DELAY_H = 24


def distinct_hours(timestamps):
    return len({(t.year, t.month, t.day, t.hour) for t in timestamps if isinstance(t, datetime)})


def assess(vectors, timestamps):
    """State for a company from its accepted answers (vectors + timestamps)."""
    n, hours = len(vectors), distinct_hours(timestamps)
    out = {"n": n, "hours": hours, "threshold": None, "low": None, "high": None,
           "width": None, "margin_pct": None}
    if n < MIN_ROWS:
        return {"state": "learning", "why": f"{n}/{MIN_ROWS} answers", **out}
    t = detector.knn_threshold(vectors, resamples=RESAMPLES)
    out.update(threshold=round(t["threshold"], 4), low=round(t["low"], 4), high=round(t["high"], 4),
               width=round(t["width"], 4), margin_pct=round(t["margin"] * 100, 1))
    reasons = []
    if t["width"] >= MAX_WIDTH:
        reasons.append(f"threshold not stable yet (90% interval {t['width']:.3f} wide, need < {MAX_WIDTH})")
    if hours < MIN_HOURS:
        reasons.append(f"traffic seen in {hours} hour(s), need {MIN_HOURS}")
    if reasons:
        return {"state": "provisional", "why": "; ".join(reasons), **out}
    return {"state": "ready", "why": "threshold stable and traffic spans enough hours", **out}

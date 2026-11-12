"""
Groq free-tier budget for "ask about this incident" and "try without code" (M13).

Groq's limits are per model (Settings > Limits, checked 2026-11-11, free, no card):
30 requests/min, 1,000 requests/day, 8K tokens/min, 200K tokens/day, reset 00:00 UTC.
Investigations use gpt-oss-120b (their own cap, INVESTIGATION_DAILY_CAP). Questions and
tries use gpt-oss-20b, so they never eat into the investigations' tokens.

  20b, per day:  15 questions x ~6K + 15 tries x ~4K  = ~150K of 200K (owner's caps, 2026-11-12)

Counters live in the llm_usage table (database.record_llm_usage); no text is stored.
`decide()` is pure so the caps are tested offline (tests/test_budget.py).
"""

import os
from datetime import datetime, timedelta, timezone

ASK_MODEL = "openai/gpt-oss-20b"

QUESTIONS_PER_DAY = int(os.environ.get("QUESTIONS_PER_DAY", "15"))                    # all companies
QUESTIONS_PER_COMPANY_DAY = int(os.environ.get("QUESTIONS_PER_COMPANY_DAY", "20"))
QUESTIONS_PER_VISITOR_HOUR = int(os.environ.get("QUESTIONS_PER_VISITOR_HOUR", "5"))   # demo only
TRIES_PER_DAY = int(os.environ.get("TRIES_PER_DAY", "15"))
TRIES_PER_IP_HOUR = int(os.environ.get("TRIES_PER_IP_HOUR", "3"))

# One-day raises of the shared question cap, by Groq day (UTC date; ends 05:30 IST). Owner,
# 2026-11-12: 25 that day for the demo video retake; 15 again from the next day automatically.
QUESTION_CAP_OVERRIDES = {"2026-11-12": 25}


def questions_per_day(now=None):
    now = now or datetime.now(timezone.utc)
    return QUESTION_CAP_OVERRIDES.get(now.astimezone(timezone.utc).strftime("%Y-%m-%d"), QUESTIONS_PER_DAY)


TOKENS_PER_QUESTION = 6000      # hard budget across the whole tool loop of one question
TOKENS_PER_TRY = 4000           # one note
MODEL_DAILY_TOKENS = 200_000    # Groq free, per model
DAILY_RESERVE = 10_000          # stop early: Groq's count and ours can differ a little


def seconds_to_reset(now=None):
    """Seconds until 00:00 UTC (05:30 IST), when Groq's daily counters reset."""
    now = now or datetime.now(timezone.utc)
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((nxt - now).total_seconds()) + 1


QUOTA_MESSAGE = ("Today's free model quota is used up. Questions and tries work again after 05:30 IST "
                 "(Groq resets daily at 00:00 UTC).")


def decide(kind, model_tokens_today, kind_units_today, company_units_today=0, now=None):
    """Whether one more question / try may start. Returns (ok, message, retry_after_s).

    model_tokens_today   tokens used today on ASK_MODEL (questions + tries)
    kind_units_today     questions (or tries) asked today, all companies
    company_units_today  questions asked today by this company (questions only)
    """
    per_unit = TOKENS_PER_QUESTION if kind == "question" else TOKENS_PER_TRY
    day_cap = questions_per_day(now) if kind == "question" else TRIES_PER_DAY
    reset = seconds_to_reset(now)
    if model_tokens_today + per_unit > MODEL_DAILY_TOKENS - DAILY_RESERVE:
        return False, QUOTA_MESSAGE, reset
    if kind_units_today >= day_cap:
        what = "questions" if kind == "question" else "tries"
        return False, (f"Today's {day_cap} free {what} are used up (shared by everyone on the free tier). "
                       "They reset at 05:30 IST."), reset
    if kind == "question" and company_units_today >= QUESTIONS_PER_COMPANY_DAY:
        return False, f"This bot has used its {QUESTIONS_PER_COMPANY_DAY} questions for today. They reset at 05:30 IST.", reset
    return True, "", 0

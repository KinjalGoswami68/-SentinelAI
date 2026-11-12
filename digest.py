"""
Daily digest (M14 step 6): one short message per bot per day, built from numbers (no LLM):
what normal looks like, what looked wrong, and the change since yesterday.

Sent by the service at DIGEST_AT_IST (default 20:30, inside the 09:00-21:00 awake window)
for every bot with answers in the last 24 h. The digests table's primary key (bot, day) makes
it one per day even across restarts or two instances during a deploy.
`build()` is pure (tests/test_digest.py).
"""

import os
import threading
import time
from datetime import datetime, timedelta, timezone

import quality

DIGEST_AT_IST = os.environ.get("DIGEST_AT_IST", "20:30")
IST = timedelta(minutes=330)


def _pct(x):
    return f"{round(100 * x)}%"


def build(name, day, today, yesterday, incidents_today, learning_state):
    """today / yesterday: {"answers", "flagged", "nonanswers", "avg_len", "quality": {cat: n},
    "spot_checked", "spot_problems", "spot_examples": [request ids]}. Returns {"lines", "summary"}."""
    n, n0 = today["answers"], yesterday["answers"]
    lines = [f"*Daily digest: {name}, {day}*"]
    trend = "" if not n0 else f" ({'+' if n >= n0 else ''}{n - n0} vs yesterday)"
    lines.append(f"{n} answers today{trend}. State: {learning_state}.")
    if n:
        refusal = today["nonanswers"] / n
        lines.append(f"Normal looks like: answers of about {round(today['avg_len'])} characters, "
                     f"{_pct(refusal)} refusals or empty answers.")
    wrong = []
    if incidents_today:
        wrong.append(f"{len(incidents_today)} incident(s): " + "; ".join(
            f"#{i['id']} {i.get('cause') or 'no note'}" for i in incidents_today[:3]))
    top = sorted(today["quality"].items(), key=lambda kv: -kv[1])[:3]
    if top and n:
        wrong.append("quality flags: " + ", ".join(f"{_pct(v / n)} {quality.LABEL.get(k, k)}" for k, v in top))
    if today["spot_problems"]:
        wrong.append(f"spot-checks: {today['spot_problems']} of {today['spot_checked']} likely have a problem "
                     f"(e.g. {', '.join(today['spot_examples'][:2])})")
    lines.append("Looked wrong: " + (" · ".join(wrong) if wrong else "nothing."))
    if n and n0:
        f_now, f_then = today["flagged"] / n, yesterday["flagged"] / n0
        q_now = sum(today["quality"].values()) / n
        q_then = sum(yesterday["quality"].values()) / n0
        lines.append(f"Since yesterday: flagged {_pct(f_then)} → {_pct(f_now)}, quality flags {_pct(q_then)} → {_pct(q_now)}.")
    summary = "nothing wrong" if not wrong else f"{len(wrong)} thing(s) to look at"
    # partly supported answers: a neutral note for the dashboard, never in the lines (= Slack)
    return {"lines": lines, "summary": summary, "spot_partly": today.get("spot_partly", 0)}


def text(body):
    return "\n".join(body["lines"])


def run_once(notify, now=None):
    """Build, store and send today's digest for every active bot. Returns how many were sent."""
    import database
    now = now or datetime.now(timezone.utc)
    day = (now + IST).date()
    sent = 0
    with database.connection() as conn:
        bots = database.active_bots(conn, now - timedelta(hours=24))
    for cid, name, state in bots:
        try:
            with database.connection() as conn:
                today = database.digest_stats(conn, cid, now - timedelta(hours=24), now)
                yesterday = database.digest_stats(conn, cid, now - timedelta(hours=48), now - timedelta(hours=24))
                incs = database.incidents_between(conn, cid, now - timedelta(hours=24), now)
                body = build(name, day.strftime("%d %b"), today, yesterday, incs, state)
                fresh = database.save_digest(conn, cid, day, body)
            if fresh:
                ok = notify(cid, text(body))
                if ok:
                    with database.connection() as conn:
                        database.mark_digest_sent(conn, cid, day)
                sent += 1
        except Exception as e:
            print(f"digest for company {cid} failed: {type(e).__name__}", flush=True)
    print(f"daily digest: {sent} sent for {day}", flush=True)
    return sent


def _seconds_until(at_ist, now):
    h, m = map(int, at_ist.split(":"))
    ist_now = now + IST
    target = ist_now.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= ist_now:
        target += timedelta(days=1)
    return (target - ist_now).total_seconds()


def start(notify):
    """Background thread: sleep until DIGEST_AT_IST each day, then run_once."""
    def loop():
        while True:
            time.sleep(min(3600, max(5, _seconds_until(DIGEST_AT_IST, datetime.now(timezone.utc)))))
            if _seconds_until(DIGEST_AT_IST, datetime.now(timezone.utc)) > 86400 - 120:   # just passed the time
                try:
                    run_once(notify)
                except Exception as e:
                    print(f"daily digest failed: {type(e).__name__}", flush=True)
    threading.Thread(target=loop, daemon=True, name="digest").start()

"""
"Try without code" (M13): run the production detection and investigation on answers someone
pastes or uploads, and return the result. Nothing is stored: no database rows, no text in
the logs (the endpoint only counts the try in llm_usage).

Input: two groups, `before` (what normal looked like) and `after` (what to check). Each
answer: {"response": str, "prompt"?: str, "request_id"?: str, "t"?: ISO time,
"prompt_version"?: str, "model_version"?: str}. The page splits one timestamped list into
the two groups in the browser.

Same code as /ingest + the incident engine + the investigation: kNN threshold learned from
`before`, per-answer flags, distance and refusal CUSUMs, the version check at 10 and 30
answers, incidents.step, then analyse() + write_report() on gpt-oss-20b (rules-only note
when the free quota is used up). It is the scorecard's replay loop (scripts/m10_scorecard.py).
"""

import time
from collections import deque
from datetime import datetime, timedelta, timezone

import numpy as np

import budget
import database   # change_point only (pure); no DB access here
import detector
import embeddings
import incidents
import investigation as inv
import learning

# Sizes fit Render free: measured 2026-11-12, embedding costs 0.456 s per answer there
# (210 answers: 95.7 s embed, 101.8 s total). 75 + 30 = 105 answers ~ 48 s embed + ~6 s detect
# and note, under the 60 s target. The after maximum of 30 keeps both version checks (10, 30).
BEFORE_MIN, BEFORE_MAX = 30, 75
AFTER_MIN, AFTER_MAX = 10, 30
MAX_CHARS = 2000            # per prompt / response
MAX_BYTES = 400_000         # request body
FIELDS = ("response", "prompt", "request_id", "t", "prompt_version", "model_version")


class TryInvalid(ValueError):
    """Input problem, shown to the visitor as a 400."""


def _clean(group, name):
    if not isinstance(group, list):
        raise TryInvalid(f"`{name}` must be a list of answers")
    out = []
    for i, r in enumerate(group):
        if isinstance(r, str):
            r = {"response": r}
        if not isinstance(r, dict):
            raise TryInvalid(f"`{name}` item {i + 1}: each answer is an object or a string")
        resp = r.get("response")
        if not isinstance(resp, str) or not resp.strip():
            raise TryInvalid(f"`{name}` item {i + 1}: `response` is missing or empty")
        row = {}
        for f in FIELDS:
            v = r.get(f)
            if v is None or v == "":
                continue
            if not isinstance(v, (str, int, float)):
                raise TryInvalid(f"`{name}` item {i + 1}: `{f}` must be text")
            v = str(v)
            if len(v) > MAX_CHARS:
                raise TryInvalid(f"`{name}` item {i + 1}: `{f}` is over {MAX_CHARS} characters")
            row[f] = v
        if "t" in row:
            try:
                ts = datetime.fromisoformat(row["t"].replace("Z", "+00:00"))
                row["t"] = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            except ValueError:
                raise TryInvalid(f"`{name}` item {i + 1}: `t` is not an ISO time like 2026-11-12T10:30:00Z")
        out.append(row)
    return out


def validate(body):
    if not isinstance(body, dict):
        raise TryInvalid("Send {\"before\": [...], \"after\": [...]}")
    before, after = _clean(body.get("before"), "before"), _clean(body.get("after"), "after")
    if not BEFORE_MIN <= len(before) <= BEFORE_MAX:
        raise TryInvalid(f"`before` needs {BEFORE_MIN}-{BEFORE_MAX} answers (got {len(before)}): "
                         f"ShiftLens needs {BEFORE_MIN} answers to learn what normal looks like")
    if not AFTER_MIN <= len(after) <= AFTER_MAX:
        raise TryInvalid(f"`after` needs {AFTER_MIN}-{AFTER_MAX} answers (got {len(after)})")
    return before, after


def _rows(group, tag, start, step_min, vectors):
    """Rows in the shape the production tools use (as in m10_scorecard.rows_for)."""
    out = []
    timed = all("t" in r for r in group)
    for i, (r, v) in enumerate(zip(group, vectors)):
        out.append({"id": f"{tag}{i}", "request_id": r.get("request_id") or f"{tag}{i + 1:03d}",
                    "ts": r["t"] if timed else start + timedelta(minutes=step_min * i),
                    "prompt": r.get("prompt", ""), "response": r["response"], "is_anomaly": 0,
                    "similarity": None, "status": "ok", "prompt_version": r.get("prompt_version"),
                    "model_version": r.get("model_version"), "latency_ms": None,
                    "prompt_embedding": None, "response_embedding": v})
    if timed:
        out.sort(key=lambda x: x["ts"])
    return out


def run(before, after, chat=inv.groq_chat, now=None):
    """The full pipeline. Returns the result dict (see docs/API.md, POST /try)."""
    t0 = time.monotonic()
    vb = embeddings.embed([r["response"] for r in before])
    va = embeddings.embed([r["response"] for r in after])
    t_embed = time.monotonic() - t0

    now = now or datetime.now(timezone.utc)
    after_start = now - timedelta(minutes=len(after))
    base = _rows(before, "b", after_start - timedelta(minutes=60 + 5 * len(before)), 5, vb)
    inc = _rows(after, "a", after_start, 1, va)
    if base[-1]["ts"] >= inc[0]["ts"]:
        raise TryInvalid("Every `before` answer must be earlier than every `after` answer")

    bv = np.array([r["response_embedding"] for r in base], dtype=np.float32)
    a = learning.assess(bv, [r["ts"] for r in base])
    thr = a["threshold"]
    bu = detector.unit(bv)
    stats = detector.baseline_stats(bv, [r["response"] for r in base])
    dc, rc = detector.DistanceCUSUM(stats["dist_mean"], stats["dist_sd"]), detector.RateCUSUM(stats["refusal_rate"])
    known = {f: {r[f] for r in base if r[f]} for f in ("prompt_version", "model_version")}
    window, incident, open_at, trigger, sev_reasons = deque(), None, None, None, None
    for i, r in enumerate(inc):
        d = detector.knn_distance(bu, r["response_embedding"])
        r["is_anomaly"] = int(d > thr)
        r["similarity"] = 1 - d
        r["_distance"] = d
        nonans = detector.is_refusal_or_empty(r["response"])
        r["_nonanswer"] = nonans
        alarms, vt = set(), None
        if dc.update(d)[0]:
            alarms.add("drift")
        if rc.update(nonans):
            alarms.add("refusal_drift")
        for f in ("prompt_version", "model_version"):
            v = r[f]
            if v and v not in known[f]:
                group = [x for x in inc[:i + 1] if x[f] == v]
                if len(group) in (10, 30):
                    sig = inv.check_version_group(base, group, f, v)
                    if sig:
                        vt = {"type": "version_regression", "field": f, "value": v, "first_seen": group[0]["ts"], "signals": sig}
                        alarms.add("version_regression")
                        break
        window.append((r["ts"], r["is_anomaly"], nonans))
        while window and r["ts"] - window[0][0] > timedelta(minutes=incidents.WINDOW_MINUTES):
            window.popleft()
        counts = {"answers": len(window), "flagged": sum(w[1] for w in window),
                  "nonanswers": sum(w[2] for w in window), "hard_failures": 0, "usual_flag_rate": 0.02}
        sev, reasons = incidents.severity_for(counts, alarms)
        if r["is_anomaly"] or alarms or nonans:
            incident, actions = incidents.step(incident, {"severity": sev, "reasons": reasons}, r["ts"])
            if "open" in actions and open_at is None:
                open_at, trigger = i, vt
    t_detect = time.monotonic() - t0 - t_embed

    result = {
        "learning": {"state": a["state"], "why": a.get("why"), "answers": a["n"], "threshold": thr,
                     "threshold_margin": None if a.get("low") is None else round((a["high"] - a["low"]) / 2, 4)},
        "after": {"answers": len(inc), "flagged": sum(r["is_anomaly"] for r in inc),
                  "nonanswers": sum(bool(r["_nonanswer"]) for r in inc)},
        "incident": None, "note": None,
        "distance_series": {"threshold": thr, "change_at": None, "points": [
            {"t": r["ts"].isoformat(), "request_id": r["request_id"], "distance": round(float(r.get("_distance", 0) or 0), 4),
             "flagged": bool(r["is_anomaly"]), "nonanswer": bool(r.get("_nonanswer")), "prompt_version": r["prompt_version"],
             "model_version": r["model_version"], "group": "after" if r["id"].startswith("a") else "before"}
            for r in inc]},
    }
    if open_at is not None:
        allrows = base + inc
        if trigger:
            start = trigger["first_seen"]
        else:
            since = inc[open_at]["ts"] - timedelta(minutes=incidents.WINDOW_MINUTES)
            start = database.change_point([(x["ts"], bool(x["is_anomaly"]) or detector.is_refusal_or_empty(x["response"]))
                                           for x in allrows if x["ts"] >= since])
            start = start or inc[open_at]["ts"]
        baseline_rows = [x for x in allrows if x["ts"] < start][-300:]
        window_rows = [x for x in allrows if x["ts"] >= start]
        pack = inv.analyse("your bot", start, baseline_rows, window_rows, past=[], trigger=trigger)
        report, by, fallback = inv.write_report(pack, chat=chat, models=(budget.ASK_MODEL,), retry=False)
        result["incident"] = {"opened_after_answers": open_at + 1, "severity": incident["severity"],
                              "reasons": incident["reasons"], "trigger": "version regression" if trigger else "signals"}
        result["note"] = {"cause": report["cause"], "confidence": report["confidence"], "written_by": by,
                          "fallback_reason": fallback, "report": report,
                          "quota_used_up": by == "rules" and "429" in (fallback or "")}
        result["distance_series"]["change_at"] = start.isoformat()
    result["timings_s"] = {"embed": round(t_embed, 2), "detect": round(t_detect, 2),
                           "note": round(time.monotonic() - t0 - t_embed - t_detect, 2),
                           "total": round(time.monotonic() - t0, 2), "texts_embedded": len(before) + len(after)}
    return result

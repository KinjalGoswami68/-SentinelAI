"""
M8 done-when: the bad-hour fixture yields 1 incident and <= 3 Slack messages.

  python scripts/m8_eval.py

Built from fixtures/m7_streams.jsonl (baseline 60, control 30, regression 40), one
answer per simulated minute, through the production signals (detector.py: kNN
flag, distance CUSUM, refusal CUSUM) and the incident state machine (incidents.py):
  bad hour       10 normal, 40 alternating degraded/normal, 10 normal, then 40 quiet min
  flapping       the bad hour, then a second degraded burst 60 min after it resolves
  control hour   60 normal answers: must give 0 incidents
"""
import json
import sys
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import detector    # noqa: E402
import embeddings  # noqa: E402
import incidents   # noqa: E402

T0 = datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc)


def load():
    rows = [json.loads(l) for l in open(ROOT / "fixtures/m7_streams.jsonl", encoding="utf-8") if l.strip()]
    by = {p: [r["response"] for r in rows if r["phase"] == p] for p in ("baseline", "control", "regression")}
    vec = {p: embeddings.embed(by[p]) for p in by}
    return by, vec


def simulate(events, base_unit, thr, stats):
    """events: list of (minute, text, vector) or (minute, None, None) for a sweep tick."""
    dc = detector.DistanceCUSUM(stats["dist_mean"], stats["dist_sd"])
    rc = detector.RateCUSUM(stats["refusal_rate"])
    window = deque()          # (time, flagged, nonanswer, hard_failure)
    incident, opened, slack, log = None, 0, [], []
    for minute, text, vec in events:
        now = T0 + timedelta(minutes=minute)
        if text is not None:
            d = detector.knn_distance(base_unit, vec)
            flagged = d > thr
            nonanswer = detector.is_refusal_or_empty(text)
            alarms = set()
            if dc.update(d)[0]:
                alarms.add("drift")
            if rc.update(nonanswer):
                alarms.add("refusal_drift")
            window.append((now, flagged, nonanswer, not text.strip()))
            while window and now - window[0][0] > timedelta(minutes=incidents.WINDOW_MINUTES):
                window.popleft()
            counts = {"answers": len(window), "flagged": sum(w[1] for w in window),
                      "nonanswers": sum(w[2] for w in window), "hard_failures": sum(w[3] for w in window),
                      "usual_flag_rate": 0.02}
            sev, reasons = incidents.severity_for(counts, alarms)
            incident, actions = incidents.step(incident, {"severity": sev, "reasons": reasons}, now)
        else:
            incident, actions = incidents.sweep(incident, now)
        for a in actions:
            if a == "open":
                opened += 1
            if incidents.slack_allowed(incident, a):
                incident["slack_messages"] += 1
                slack.append((minute, a, incident["severity"]))
            log.append((minute, a, incident["severity"], incident.get("flapping")))
        # sweep every minute as the service does
        if text is not None:
            incident, actions = incidents.sweep(incident, now)
            for a in actions:
                if incidents.slack_allowed(incident, a):
                    incident["slack_messages"] += 1
                    slack.append((minute, a, incident["severity"]))
                log.append((minute, a, incident["severity"], incident.get("flapping")))
    return {"incidents_opened": opened, "slack": slack, "log": log, "final": incident}


def bad_hour(vec, by, start=0):
    c, r = list(zip(by["control"], vec["control"])), list(zip(by["regression"], vec["regression"]))
    ev, m, ci, ri = [], start, 0, 0
    for _ in range(10):
        ev.append((m, *c[ci % len(c)])); m += 1; ci += 1
    for k in range(40):
        if k % 2 == 0:
            ev.append((m, *r[ri % len(r)])); ri += 1
        else:
            ev.append((m, *c[ci % len(c)])); ci += 1
        m += 1
    for _ in range(10):
        ev.append((m, *c[ci % len(c)])); m += 1; ci += 1
    for _ in range(40):
        ev.append((m, None, None)); m += 1
    return ev, m


def main():
    by, vec = load()
    base_unit = detector.unit(vec["baseline"])
    thr = detector.knn_threshold(vec["baseline"])["threshold"]
    stats = detector.baseline_stats(vec["baseline"], by["baseline"])

    ev, end = bad_hour(vec, by)
    flap_ev = list(ev)
    burst = [(end + 60 + i, *list(zip(by["regression"], vec["regression"]))[i]) for i in range(10)]
    flap_ev += burst + [(end + 70 + i, None, None) for i in range(40)]
    control_ev = [(i, t, v) for i, (t, v) in enumerate(zip((by["control"] * 2)[:60], list(vec["control"]) * 2))]

    results = {"bad hour": simulate(ev, base_unit, thr, stats),
               "bad hour + flapping": simulate(flap_ev, base_unit, thr, stats),
               "control hour": simulate(control_ev, base_unit, thr, stats)}
    print("| scenario | incidents opened | Slack messages | timeline (minute: action, SEV) |")
    print("|---|---|---|---|")
    for name, r in results.items():
        tl = "; ".join(f"{m}: {a} SEV{s}" + (" (flapping)" if f and a == "reopen" else "") for m, a, s, f in r["log"]) or "none"
        print(f"| {name} | {r['incidents_opened']} | {len(r['slack'])} ({', '.join(a for _, a, _ in r['slack']) or '-'}) | {tl} |")

    ok = (results["bad hour"]["incidents_opened"] == 1 and len(results["bad hour"]["slack"]) <= 3
          and results["bad hour + flapping"]["incidents_opened"] == 1
          and len(results["bad hour + flapping"]["slack"]) <= 3
          and results["control hour"]["incidents_opened"] == 0)
    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

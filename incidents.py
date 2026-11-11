"""
M8: incidents. Alarms become one incident per problem, with at most 3 Slack
messages (opened, escalated, resolved).

Pure state machine: `step(incident, signal, now)` and `sweep(incident, now)` take the
current incident (a dict, or None) and return (incident, actions). The database
layer (main.py) loads and stores the dict and performs the actions; the offline
check (scripts/m8_eval.py) drives the same functions with simulated time.

Opening conditions (any):
  - a CUSUM alarm (distance drift, refusal/empty rate) or a version regression
  - >= RATE_MIN_FLAGGED flagged answers in the last 30 min and a flagged share
    >= RATE_FACTOR x the company's usual flagged share
  - >= HARD_MIN failures (status error/timeout, or an empty answer) in 30 min
Severity: SEV1 hard failures, or >= SEV1_SHARE of the last 30 min's answers flagged
or non-answers (with >= SEV1_MIN_ANSWERS answers); SEV2 a CUSUM alarm or version
regression; SEV3 the flagged-rate rule alone. Lower number = worse.
Closing: no new signal for QUIET_MINUTES -> resolved. A signal within
FLAP_HOURS of resolving reopens the same incident as flapping (no new Slack).
"""

import os
from datetime import timedelta

WINDOW_MINUTES   = 30
RATE_MIN_FLAGGED = 5
RATE_FACTOR      = 3.0
RATE_FLOOR       = 0.01      # usual flagged share is at least this
HARD_MIN         = 5
SEV1_SHARE       = 0.5
SEV1_MIN_ANSWERS = 10
QUIET_MINUTES    = int(os.environ.get("INCIDENT_QUIET_MINUTES", "30"))   # lower only for local tests
FLAP_HOURS       = 2
MAX_SLACK        = 3


def severity_for(window, alarms):
    """
    window: counts over the last 30 min: answers, flagged, nonanswers, hard_failures,
            usual_flag_rate. alarms: set of alarm kinds raised by this answer
            ('drift', 'refusal_drift', 'version_regression').
    Returns (severity or None, reasons).
    """
    reasons = []
    n = window["answers"]
    sev = None
    if window["hard_failures"] >= HARD_MIN:
        sev, reasons = 1, reasons + [f"{window['hard_failures']} failed answers (errors, timeouts, empty) in 30 min"]
    bad_share = (window["flagged"] + window["nonanswers"]) / n if n else 0.0
    if n >= SEV1_MIN_ANSWERS and bad_share >= SEV1_SHARE:
        sev, reasons = 1, reasons + [f"{bad_share:.0%} of the last {n} answers flagged or non-answers"]
    if alarms & {"drift", "refusal_drift", "version_regression"}:
        names = {"drift": "answers drifting from normal", "refusal_drift": "refusals/non-answers rising",
                 "version_regression": "new version answers differently"}
        reasons += [names[a] for a in sorted(alarms) if a in names]
        sev = min(sev or 2, 2)
    usual = max(window["usual_flag_rate"], RATE_FLOOR)
    share = window["flagged"] / n if n else 0.0
    if window["flagged"] >= RATE_MIN_FLAGGED and share >= RATE_FACTOR * usual:
        reasons.append(f"{window['flagged']} flagged answers in 30 min ({share:.0%} vs usual {usual:.0%})")
        sev = min(sev or 3, 3)
    return sev, reasons


def step(incident, signal, now):
    """
    Apply one answer's signal. signal = {"severity": int|None, "reasons": [...]}.
    Returns (incident, actions); actions are any of 'open', 'escalate', 'reopen'.
    """
    sev = signal["severity"]
    if sev is None:
        return incident, []
    if incident and incident["status"] == "open":
        incident = dict(incident, last_signal_at=now, signal_count=incident["signal_count"] + 1,
                        reasons=_merge(incident["reasons"], signal["reasons"]))
        if sev < incident["severity"]:
            incident["severity"] = sev
            return incident, ["escalate"]
        return incident, []
    if incident and incident["status"] == "resolved" and now - incident["closed_at"] <= timedelta(hours=FLAP_HOURS):
        return dict(incident, status="open", closed_at=None, last_signal_at=now, flapping=True,
                    reopen_count=incident["reopen_count"] + 1, severity=min(sev, incident["severity"]),
                    signal_count=incident["signal_count"] + 1,
                    reasons=_merge(incident["reasons"], signal["reasons"])), ["reopen"]
    return {"id": None, "status": "open", "severity": sev, "opened_at": now, "last_signal_at": now,
            "closed_at": None, "flapping": False, "reopen_count": 0, "signal_count": 1,
            "reasons": list(signal["reasons"]), "slack_messages": 0}, ["open"]


def sweep(incident, now):
    """Resolve an open incident after QUIET_MINUTES without a signal."""
    if incident and incident["status"] == "open" and now - incident["last_signal_at"] >= timedelta(minutes=QUIET_MINUTES):
        return dict(incident, status="resolved", closed_at=now), ["resolve"]
    return incident, []


def slack_allowed(incident, action):
    """Max 3 messages per incident: opened, escalated, resolved. Reopening (flapping)
    sends nothing; the resolved message mentions it."""
    if action not in ("open", "escalate", "resolve"):
        return False
    if action == "escalate":      # keep the last slot for 'resolved'
        return incident["slack_messages"] < MAX_SLACK - 1
    return incident["slack_messages"] < MAX_SLACK


def _merge(a, b):
    return list(dict.fromkeys(list(a) + list(b)))[:8]

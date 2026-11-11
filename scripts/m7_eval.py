"""
M7 done-when: the control stream raises 0 alarms and the regression stream is detected.

  python scripts/m7_eval.py

fixtures/m7_streams.jsonl (all gpt-oss-20b): 60 baseline, 30 control (same prompt),
40 regression (degraded prompt). Streams, each starting from fresh CUSUMs on the
same baseline:
  control                 must give 0 alarms
  regression              must give an alarm (reports after how many answers)
  control -> regression   alarm must come only after the switch
Per stream: per-answer kNN flags, distance-CUSUM alarms, refusal-CUSUM alarms, and
flags of the 5-feature Isolation Forest candidate (offline only).
"""
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import detector    # noqa: E402
import embeddings  # noqa: E402


def run(stream, base_unit, thr, stats, forest):
    dc = detector.DistanceCUSUM(stats["dist_mean"], stats["dist_sd"])
    rc = detector.RateCUSUM(stats["refusal_rate"])
    out = {"n": len(stream), "flags": 0, "dist_alarms": [], "refusal_alarms": [], "if_flags": 0}
    for i, (vec, text) in enumerate(stream, 1):
        d = detector.knn_distance(base_unit, vec)
        out["flags"] += d > thr
        alarm, _ = dc.update(d)
        if alarm:
            out["dist_alarms"].append(i)
        if rc.update(detector.is_refusal_or_empty(text)):
            out["refusal_alarms"].append(i)
        out["if_flags"] += detector.if5_flag(forest, detector.features(stats, d, text))
    out["first_alarm"] = min(out["dist_alarms"] + out["refusal_alarms"], default=None)
    return out


def main():
    rows = [json.loads(l) for l in open(ROOT / "fixtures/m7_streams.jsonl", encoding="utf-8") if l.strip()]
    by = {p: [r for r in rows if r["phase"] == p] for p in ("baseline", "control", "regression")}
    vec = {p: embeddings.embed([r["response"] for r in by[p]]) for p in by}
    text = {p: [r["response"] for r in by[p]] for p in by}

    base_unit = detector.unit(vec["baseline"])
    thr = detector.knn_threshold(vec["baseline"])["threshold"]
    stats = detector.baseline_stats(vec["baseline"], text["baseline"])
    loo = detector.loo_knn_distances(base_unit)
    forest = detector.if5_fit([detector.features(stats, d, t) for d, t in zip(loo, text["baseline"])])
    print(f"baseline: n={len(base_unit)}, kNN threshold {thr:.3f}, distance mean {stats['dist_mean']:.3f} "
          f"sd {stats['dist_sd']:.3f}, refusal/empty rate {stats['refusal_rate']:.0%}")
    print(f"refusal/empty rate: control {np.mean([detector.is_refusal_or_empty(t) for t in text['control']]):.0%}, "
          f"regression {np.mean([detector.is_refusal_or_empty(t) for t in text['regression']]):.0%}")

    streams = {
        "control": list(zip(vec["control"], text["control"])),
        "regression": list(zip(vec["regression"], text["regression"])),
        "control -> regression": list(zip(vec["control"], text["control"])) + list(zip(vec["regression"], text["regression"])),
    }
    print("\n| stream | answers | per-answer kNN flags | distance-CUSUM alarms at | refusal-CUSUM alarms at | IF5 flags (candidate) |")
    print("|---|---|---|---|---|---|")
    res = {}
    for name, s in streams.items():
        r = res[name] = run(s, base_unit, thr, stats, forest)
        print(f"| {name} | {r['n']} | {r['flags']} | {r['dist_alarms'] or 'none'} | {r['refusal_alarms'] or 'none'} | {r['if_flags']} |")

    n_control = len(streams["control"])
    ok_control = res["control"]["first_alarm"] is None
    ok_regression = res["regression"]["first_alarm"] is not None
    mixed = res["control -> regression"]["first_alarm"]
    ok_mixed = mixed is not None and mixed > n_control
    print(f"\ncontrol 0 alarms: {ok_control}; regression detected: {ok_regression} "
          f"(first alarm at answer {res['regression']['first_alarm']}); mixed stream first alarm at "
          f"{mixed} (switch after {n_control}): {ok_mixed}")
    ok = ok_control and ok_regression and ok_mixed
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

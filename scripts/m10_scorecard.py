"""
M10 scorecard: replay 8 scenarios (300 baseline + 100 incident answers each, one seed) plus
the change-during-learning scenario (with and without version metadata)
through the production detection, incident and investigation functions, and write
docs/SCORECARD.md (counts, not percentages).

  python scripts/m10_scorecard.py               # rules-only notes (no Groq)
  python scripts/m10_scorecard.py --llm         # notes written by gpt-oss-120b (~4K tokens each)
  python scripts/m10_scorecard.py --seeds 1,2   # more seeds, if their pools exist

Pools: fixtures/m10/seed<N>_<pool>.jsonl (scripts/make_m10_fixtures.py).
Scoring (rebuild prompt, M10):
  prompt change / model swap / topic shift   right if the note names that cause
  control / harmless version bump            right if no incident opens
  silent change / double change / slow drift right if the note says "not sure"
                                              (unknown, ambiguous, or confidence < 0.6);
                                              a confident specific cause counts wrong
Evidence precision: cited example ids that are answers from the changed stream.
"""
import argparse
import json
import sys
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import database      # noqa: E402  (change_point only; no DB access)
import detector      # noqa: E402
import embeddings    # noqa: E402
import incidents     # noqa: E402
import investigation as inv  # noqa: E402
import learning      # noqa: E402

POOLS = ROOT / "fixtures" / "m10"
OUT = ROOT / "docs" / "SCORECARD.md"
T0 = datetime(2026, 11, 1, 3, 30, tzinfo=timezone.utc)
M20, M120 = "openai/gpt-oss-20b", "openai/gpt-oss-120b"
NOT_SURE = {"silent_change", "double_change", "slow_drift"}
REASSESS_LEARNING, ASSESS_MAX_ROWS = 10, 300   # as in main.py

SCENARIOS = [  # name, expected cause (None = no incident should open), description
    ("prompt_change", "prompt_change", "upsell prompt v2, prompt_version v1 -> v2"),
    ("model_swap", "model_change", "same prompt, gpt-oss-20b -> gpt-oss-120b, model_version changes"),
    ("topic_shift", "topic_shift", "users ask about solar / EV chargers the bot was not set up for"),
    ("control", None, "more normal answers"),
    ("harmless_bump", None, "normal answers, prompt_version v1 -> v1.1"),
    ("silent_change", "unknown", "upsell prompt v2 with no version metadata at all"),
    ("double_change", "ambiguous", "upsell prompt v2 and gpt-oss-120b at the same time"),
    ("slow_drift", "unknown", "deflecting answers mixed in, 0% -> 100% over 100 answers, no version change"),
]


def load(seed):
    pools = {}
    for f in POOLS.glob(f"seed{seed}_*.jsonl"):
        pools[f.stem.split("_", 1)[1]] = [json.loads(l) for l in open(f, encoding="utf-8") if l.strip()]
    return pools


def rows_for(texts_prompts, pv, mv, start, step_min, tag, vec):
    out = []
    for i, ((prompt, response), v) in enumerate(zip(texts_prompts, vec)):
        out.append({"id": f"{tag}{i}", "request_id": f"{tag}{i:03d}", "ts": start + timedelta(minutes=step_min * i),
                    "prompt": prompt, "response": response, "is_anomaly": 0, "similarity": None, "status": "ok",
                    "prompt_version": pv, "model_version": mv, "latency_ms": None,
                    "prompt_embedding": None, "response_embedding": v})
    return out


def build(scenario, pools, emb):
    """(baseline rows, incident rows) for one scenario and seed."""
    normal = pools["normal"]
    no_meta = scenario == "silent_change"
    pv0, mv0 = (None, None) if no_meta else ("v1", "gpt-oss-20b")
    base = rows_for([(r["prompt"], r["response"]) for r in normal[:300]], pv0, mv0, T0, 5, "b", emb["normal"][:300])
    t1 = T0 + timedelta(minutes=5 * 300 + 60)
    held = [(r["prompt"], r["response"]) for r in normal[300:400]]
    if scenario == "prompt_change":
        inc = rows_for([(r["prompt"], r["response"]) for r in pools["prompt_v2"]], "v2", "gpt-oss-20b", t1, 1, "i", emb["prompt_v2"])
    elif scenario == "model_swap":
        inc = rows_for([(r["prompt"], r["response"]) for r in pools["model_swap"]], "v1", "gpt-oss-120b", t1, 1, "i", emb["model_swap"])
    elif scenario == "topic_shift":
        inc = rows_for([(r["prompt"], r["response"]) for r in pools["topic"]], "v1", "gpt-oss-20b", t1, 1, "i", emb["topic"])
    elif scenario == "control":
        inc = rows_for(held, "v1", "gpt-oss-20b", t1, 1, "i", emb["normal"][300:400])
    elif scenario == "harmless_bump":
        inc = rows_for(held, "v1.1", "gpt-oss-20b", t1, 1, "i", emb["normal"][300:400])
    elif scenario == "silent_change":
        inc = rows_for([(r["prompt"], r["response"]) for r in pools["prompt_v2"]], None, None, t1, 1, "i", emb["prompt_v2"])
    elif scenario == "double_change":
        inc = rows_for([(r["prompt"], r["response"]) for r in pools["double"]], "v2", "gpt-oss-120b", t1, 1, "i", emb["double"])
    else:  # slow_drift: share of deflecting answers rises 0 -> 100%
        mix, vec, acc = [], [], 0.0
        for k in range(100):
            acc += k / 99                             # error diffusion: share rises evenly 0 -> 100%
            if acc >= 1.0:
                acc -= 1.0
                mix.append((pools["degraded"][k]["prompt"], pools["degraded"][k]["response"])); vec.append(emb["degraded"][k])
            else:
                mix.append(held[k]); vec.append(emb["normal"][300 + k])
        inc = rows_for(mix, "v1", "gpt-oss-20b", t1, 1, "i", vec)
    return base, inc


def replay(base, inc, chat):
    """Production signals over the incident stream; investigation at the end."""
    bv = np.array([r["response_embedding"] for r in base], dtype=np.float32)
    a = learning.assess(bv, [r["ts"] for r in base])
    bu = detector.unit(bv)
    thr = a["threshold"]
    stats = detector.baseline_stats(bv, [r["response"] for r in base])
    dc, rc = detector.DistanceCUSUM(stats["dist_mean"], stats["dist_sd"]), detector.RateCUSUM(stats["refusal_rate"])
    loo = detector.loo_knn_distances(bu)
    forest = detector.if5_fit([detector.features(stats, d, r["response"]) for d, r in zip(loo, base)])
    known = {f: {r[f] for r in base if r[f]} for f in ("prompt_version", "model_version")}
    window, incident, open_at, trigger, ifl, flags = deque(), None, None, None, 0, 0
    for i, r in enumerate(inc):
        d = detector.knn_distance(bu, r["response_embedding"])
        r["is_anomaly"] = int(d > thr)
        r["similarity"] = 1 - d
        flags += r["is_anomaly"]
        nonans = detector.is_refusal_or_empty(r["response"])
        ifl += detector.if5_flag(forest, detector.features(stats, d, r["response"]))
        alarms = set()
        if dc.update(d)[0]:
            alarms.add("drift")
        if rc.update(nonans):
            alarms.add("refusal_drift")
        vt = None
        for f in ("prompt_version", "model_version"):
            v = r[f]
            if v and v not in known[f]:
                group = [x for x in inc[:i + 1] if x[f] == v]
                if len(group) in (10, 30):
                    sig = inv.check_version_group(base, group, f, v)
                    if sig:
                        vt = {"type": "version_regression", "field": f, "value": v,
                              "first_seen": group[0]["ts"], "signals": sig}
                        alarms.add("version_regression")
                        break
        window.append((r["ts"], r["is_anomaly"], nonans))
        while window and r["ts"] - window[0][0] > timedelta(minutes=incidents.WINDOW_MINUTES):
            window.popleft()
        counts = {"answers": len(window), "flagged": sum(w[1] for w in window),
                  "nonanswers": sum(w[2] for w in window), "hard_failures": 0, "usual_flag_rate": 0.02}
        sev, reasons = incidents.severity_for(counts, alarms)
        signal = r["is_anomaly"] or alarms or nonans
        incident, actions = incidents.step(incident, {"severity": sev, "reasons": reasons}, r["ts"]) if signal else (incident, [])
        if "open" in actions and open_at is None:
            open_at, trigger = i, vt
    result = {"opened": open_at is not None, "detect_at": None if open_at is None else open_at + 1,
              "flags": flags, "if5_flags": ifl, "threshold": thr, "state": a["state"]}
    if open_at is None:
        return result
    allrows = base + inc
    if trigger:
        start = trigger["first_seen"]
    else:
        opened_ts = inc[open_at]["ts"]
        since = opened_ts - timedelta(minutes=incidents.WINDOW_MINUTES)
        start = database.change_point([(x["ts"], bool(x["is_anomaly"]) or detector.is_refusal_or_empty(x["response"]))
                                       for x in allrows if x["ts"] >= since])
    baseline_rows = [x for x in allrows if x["ts"] < start][-300:]
    window_rows = [x for x in allrows if x["ts"] >= start]
    pack = inv.analyse("Volt (scorecard)", start, baseline_rows, window_rows, past=[], trigger=trigger)
    if chat:   # Groq free: 8K tokens/min and one note is ~4K, so one investigation per 65 s (the service: 1 per 30 min)
        time.sleep(65)
        report, by, fb = inv.write_report(pack, chat=chat, sleep=time.sleep)
    else:
        report, by, fb = inv.rules_only_report(pack), "rules", "rules mode"
    changed_ids = {x["request_id"] for x in inc}
    result.update(cause=report["cause"], confidence=report["confidence"], written_by=by, fallback=fb,
                  examples=report["example_ids"], examples_ok=sum(e in changed_ids for e in report["example_ids"]),
                  dropped=(report.get("dropped") or {}), trigger="version regression" if trigger else "incident")
    return result


def learning_change(pools, emb, meta, chat, change_at=20, step_min=5):
    """The prompt changes while the bot is still learning (M6 known limitation).
    Simulates /ingest during learning: the state is rebuilt every REASSESS_LEARNING
    answers from the accepted (not flagged) answers; provisional flags are dashboard
    only. When it turns ready, the accepted answers become the frozen anchor and the
    rest of the stream goes through replay() like any ready bot."""
    pv1, pv2, mv = ("v1", "v2", "gpt-oss-20b") if meta else (None, None, None)
    normal, v2 = pools["normal"], pools["prompt_v2"]
    stream = rows_for([(r["prompt"], r["response"]) for r in normal[:change_at]], pv1, mv, T0, step_min, "n",
                      emb["normal"][:change_at])
    stream += rows_for([(r["prompt"], r["response"]) for r in v2], pv2, mv,
                       T0 + timedelta(minutes=step_min * change_at), step_min, "i", emb["prompt_v2"])
    accepted, a, bu, prov_flags = [], None, None, 0
    changed = len(v2)
    for i, r in enumerate(stream):
        if i % REASSESS_LEARNING == 0:
            use = accepted[-learning.ROLLING_ROWS:][-ASSESS_MAX_ROWS:]
            vec = np.array([x["response_embedding"] for x in use], dtype=np.float32)
            a = learning.assess(vec, [x["ts"] for x in use]) if use else {"state": "learning"}
            if a["state"] == "ready":
                r0 = replay(use, stream[i:], chat)
                return {"meta": meta, "change_at": change_at, "ready_at": i, "anchor": len(use),
                        "anchor_changed": sum(x["id"].startswith("i") for x in use),
                        "prov_flags": prov_flags, "changed": changed, "after_ready": len(stream) - i, **r0}
            bu = detector.unit(vec) if use else None
        flagged = a["state"] == "provisional" and detector.knn_distance(bu, r["response_embedding"]) > a["threshold"]
        prov_flags += bool(flagged and r["id"].startswith("i"))
        if not flagged:
            accepted.append(r)
    use = accepted[-learning.ROLLING_ROWS:][-ASSESS_MAX_ROWS:]   # never ready: what it was learning from at the end
    return {"meta": meta, "change_at": change_at, "ready_at": None, "anchor": len(use),
            "anchor_changed": sum(x["id"].startswith("i") for x in use), "stream": len(stream),
            "width": a.get("width"), "prov_flags": prov_flags, "changed": changed, "after_ready": 0, "opened": False, "detect_at": None}


def correct(scenario, expected, r):
    if expected is None:
        return not r["opened"]
    if not r["opened"]:
        return False
    if scenario in NOT_SURE:
        return r["cause"] in ("unknown", "ambiguous") or r["confidence"] < 0.6
    return r["cause"] == expected


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--seeds", default="1")   # one seed generated (decided 2026-11-11)
    a = ap.parse_args()
    chat = None
    if a.llm:
        import dev_truststore
        dev_truststore.maybe_enable()
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        chat = inv.groq_chat
    seeds = [int(s) for s in a.seeds.split(",")]
    results = {s: {} for s in seeds}
    for seed in seeds:
        pools = load(seed)
        need = {"normal": 400, "prompt_v2": 100, "degraded": 100, "topic": 100, "model_swap": 100, "double": 100}
        missing = [p for p, n in need.items() if len(pools.get(p, [])) < n]
        if missing:
            print(f"seed {seed}: pools not complete yet ({', '.join(missing)}); skipped")
            results.pop(seed)
            continue
        emb = {p: embeddings.embed([r["response"] for r in rows]) for p, rows in pools.items()}
        for name, expected, _ in SCENARIOS:
            base, inc = build(name, pools, emb)
            r = replay(base, inc, chat)
            r["correct"] = correct(name, expected, r)
            results[seed][name] = r
            print(f"seed {seed} {name:14s} opened={r['opened']} at={r['detect_at']} cause={r.get('cause')} "
                  f"conf={r.get('confidence')} correct={r['correct']}", flush=True)
        results[seed]["_learning"] = [learning_change(pools, emb, meta, chat) for meta in (True, False)]
        for r in results[seed]["_learning"]:
            print(f"seed {seed} change during learning meta={r['meta']}: ready at answer {r['ready_at']}, "
                  f"anchor {r['anchor_changed']}/{r['anchor']} post-change, opened={r['opened']} "
                  f"at={r['detect_at']} cause={r.get('cause')}", flush=True)
    if not results:
        return 1
    write(results, a.llm)
    return 0


def write(results, llm):
    seeds = sorted(results)
    n = len(seeds)
    lines = ["# Scorecard", "",
             f"Generated by `python scripts/m10_scorecard.py{' --llm' if llm else ''}` on "
             f"{datetime.now(timezone.utc):%Y-%m-%d} from `fixtures/m10/` (seed{'s' if n > 1 else ''} {', '.join(map(str, seeds))}). "
             "Counts, not percentages. Each run: 300 baseline answers (5 min apart, so the bot is ready), then "
             "100 answers of the scenario, one per minute, through the production detection, incident and "
             "investigation code. Notes written by " + ("gpt-oss-120b (rules-only when it was unavailable)." if llm
                                                         else "the rules-only report (no LLM)."), "",
             f"**{n} seed{'s' if n > 1 else ''}** (decided 2026-11-11: the fixtures were generated in one day on "
             "Groq's free tier). Every cell is one run per seed, so read the counts as single examples, "
             "not rates.", "",
             "| scenario | what changed | incident opened | cause right | answers until detected (per seed) | "
             "cited examples from the changed answers | notes said |",
             "|---|---|---|---|---|---|---|"]
    tot_ok = tot = 0
    for name, expected, desc in SCENARIOS:
        rs = [results[s][name] for s in seeds]
        opened = sum(r["opened"] for r in rs)
        ok = sum(r["correct"] for r in rs)
        tot_ok, tot = tot_ok + ok, tot + n
        det = ", ".join(str(r["detect_at"]) if r["opened"] else "-" for r in rs)
        ex_ok = sum(r.get("examples_ok", 0) for r in rs)
        ex_n = sum(len(r.get("examples", [])) for r in rs)
        said = ", ".join(f"{r['cause']} {r['confidence']:.2f} ({'LLM' if r['written_by'] != 'rules' else 'rules: ' + str(r['fallback'])})"
                        for r in rs if r["opened"]) or "-"
        exp = "no incident" if expected is None else ("not sure" if name in NOT_SURE else expected)
        lines.append(f"| {name.replace('_', ' ')} | {desc} | {opened}/{n} | {ok}/{n} (want: {exp}) | {det} | "
                     f"{ex_ok}/{ex_n} | {said} |")
    false_inc = sum(results[s][x]["opened"] for s in seeds for x in ("control", "harmless_bump"))
    lines += ["", f"**Cause right: {tot_ok} of {tot}.** False incidents on control + harmless bump: "
              f"**{false_inc} of {2 * n}**.", "",
              "## Isolation Forest candidate (5 features)", "",
              "Answers it flagged in each scenario's 100-answer stream (summed over seeds), next to the kNN flags:", "",
              "| scenario | IF5 flags | kNN flags |", "|---|---|---|"]
    for name, _, _ in SCENARIOS:
        lines.append(f"| {name.replace('_', ' ')} | {sum(results[s][name]['if5_flags'] for s in seeds)} | "
                     f"{sum(results[s][name]['flags'] for s in seeds)} |")
    lines += ["", "## Change during learning", "",
              "The upsell prompt v2 replaces v1 after 20 normal answers, while the bot is still learning "
              "(one answer every 5 min). Not part of the cause count above: this is the known limitation in "
              "`docs/M6_learning.md`.", "",
              "| seed | version metadata | ready at answer | anchor answers after the change | provisional "
              "(dashboard-only) flags on changed answers | incident after ready | answers after ready until "
              "detected | note said |", "|---|---|---|---|---|---|---|---|"]
    for s in seeds:
        for r in results[s]["_learning"]:
            lines.append(f"| {s} | {'yes (v1 -> v2)' if r['meta'] else 'none'} | "
                         f"{r['ready_at'] if r['ready_at'] is not None else 'not within %d answers (interval %.3f wide, needs < %.2f)' % (r['stream'], r['width'], learning.MAX_WIDTH)} | "
                         f"{r['anchor_changed']} of {r['anchor']}{'' if r['ready_at'] is not None else ' (learning baseline at the end)'} | "
                         f"{r['prov_flags']} of {r['changed']} | "
                         f"{'yes' if r['opened'] else 'no'} | {r['detect_at'] or '-'} | "
                         f"{(r.get('cause') or '-')}{(' %.2f' % r['confidence']) if r.get('confidence') is not None else ''} |")
    lines += ["", "Per-answer false positives on ordinary Volt answers, before and after M6/M7: "
              "`docs/detection_scorecard.md`.", ""]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    sys.exit(main())

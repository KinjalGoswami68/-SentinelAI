"""
M14 scorecard additions: day-1 judging, through the production functions, written into the
"Day-1 judging (M14)" section of docs/SCORECARD.md (the M10 script keeps that section).

  python scripts/m14_scorecard.py            # detection scenarios + spot-checks on Groq
  python scripts/m14_scorecard.py --no-llm   # detection scenarios only

Scenarios (fixtures/m10, seed 1; no new generation):
  off-topic after ready   300 normal answers learned, then 100 questions each paired with the
                          answer to another question (shuffled pairs); control: 100 normal
  bad from day 1          a new bot whose traffic is bad from the first answer (20% deflecting
                          answers, or 50% shuffled pairs), plus a clean start; one answer every
                          2 minutes, as /ingest handles a bot that is still learning
  spot-checks             the same items judged by each model: unaltered answers vs answers with
                          one policy fact altered (with Volt's policy text as context), and
                          right vs shuffled answers (no context). Precision/recall per model.
Groq usage of the spot-checks is recorded in llm_usage (kind "scorecard") so the service's
daily caps see it. Counts, not percentages: one seed.
"""
import argparse
import random
import re
import sys
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import detector      # noqa: E402
import embeddings    # noqa: E402
import incidents     # noqa: E402
import learning      # noqa: E402
import quality       # noqa: E402
import review        # noqa: E402
import spotcheck     # noqa: E402
import m10_scorecard as m10  # noqa: E402

T0 = m10.T0
NOTES = "### Reading (by hand)"
SHIFT = 137                       # shuffled pairs: question i gets the answer to question i + SHIFT
POLICY = ("Volt Electronics customer policy.\n"
          "Returns: within 30 days of delivery; earbuds within 7 days.\n"
          "Shipping: free on orders above Rs 500 (₹500).\n"
          "Cash on delivery: available for orders up to ₹10,000.\n"
          "No-cost EMI: available on orders above ₹3,000.\n"
          "Installation: free for TVs above 32 inches.\n"
          "Extended warranty: Volt Protect, sold at checkout.\n"
          "Support hours: 8 am to 10 pm, every day.")
ALTER = [   # one policy fact changed per answer (the first that matches)
    (r"\b30[- ]day", "60-day"), (r"\b30 days", "60 days"), (r"₹\s?500\b(?![,\d])", "₹1,500"),
    (r"₹\s?3,?000\b", "₹8,000"), (r"₹\s?10,?000\b", "₹25,000"), (r"\b32[- ]inch", "50-inch"),
    (r"\b32 inches", "50 inches"), (r"\b8\s?(am|AM|a\.m\.)", "6 am"), (r"\b10\s?(pm|PM|p\.m\.)", "midnight"),
]


def rows(pairs, pvec, rvec, start, step_min, tag):
    out = m10.rows_for(pairs, "v1", "gpt-oss-20b", start, step_min, tag, rvec)
    for r, v in zip(out, pvec):
        r["prompt_embedding"] = v
    return out


def off_topic_after_ready(pools, emb, pemb):
    n = pools["normal"]
    base = rows([(r["prompt"], r["response"]) for r in n[:300]], pemb[:300], emb[:300], T0, 5, "b")
    t1 = T0 + timedelta(minutes=5 * 300 + 60)
    out = {}
    held = list(range(300, 400))
    shuffled = [(i, 300 + (i - 300 + SHIFT) % 100) for i in held]
    for name, idx in (("off_topic", shuffled), ("control", [(i, i) for i in held])):
        inc = rows([(n[q]["prompt"], n[a]["response"]) for q, a in idx], [pemb[q] for q, _ in idx],
                   [emb[a] for _, a in idx], t1, 1, "i")
        out[name] = m10.replay([dict(r) for r in base], inc, None, day1=True)
        print(f"{name}: opened={out[name]['opened']} at={out[name]['detect_at']} quality-flagged="
              f"{out[name]['quality_flagged']} reasons={out[name]['open_reasons']}", flush=True)
    return out


def day_one(stream):
    """A new bot, as /ingest handles it while learning: re-assess every REASSESS_LEARNING
    answers from the accepted ones; provisional flags are dashboard only; a quality-only
    incident step runs for every answer with day-1 flags. At ready: the baseline review."""
    accepted, a, bu = [], {"state": "learning"}, None
    window, incident, opened_at, reasons_at_open, flagged = deque(), None, None, None, 0
    for i, r in enumerate(stream):
        if i % m10.REASSESS_LEARNING == 0 and accepted:
            use = accepted[-learning.ROLLING_ROWS:][-m10.ASSESS_MAX_ROWS:]
            vec = np.array([x["response_embedding"] for x in use], dtype=np.float32)
            a = learning.assess(vec, [x["ts"] for x in use])
            bu = detector.unit(vec)
            if a["state"] == "ready":
                body = review.build([{"request_id": x["request_id"], "quality_flags": x["qf"]} for x in use])
                return {"ready_at": i, "anchor": len(use), "opened": opened_at is not None,
                        "opened_at": opened_at, "reasons": reasons_at_open, "flagged": flagged, "review": body}
        r["qf"] = quality.check(r["prompt"], r["response"], m10.relevance(r), r["status"])
        flagged += bool(r["qf"])
        window.append((r["ts"], r["qf"]))
        while window and r["ts"] - window[0][0] > timedelta(minutes=incidents.WINDOW_MINUTES):
            window.popleft()
        if r["qf"]:
            reasons = quality.rate_reasons([w[1] for w in window])
            incident, actions = incidents.step(incident, {"severity": 3 if reasons else None, "reasons": reasons}, r["ts"])
            if "open" in actions and opened_at is None:
                opened_at, reasons_at_open = i + 1, reasons
        prov = a["state"] == "provisional" and detector.knn_distance(bu, r["response_embedding"]) > a["threshold"]
        if not prov:
            accepted.append(r)
    return {"ready_at": None, "anchor": len(accepted), "opened": opened_at is not None, "opened_at": opened_at,
            "reasons": reasons_at_open, "flagged": flagged, "review": None}


def bad_from_day_one(pools, emb, pemb, demb, dpemb, n=200):
    nrm, deg = pools["normal"], pools["degraded"]
    out = {}
    for name, share in (("clean start", 0.0), ("20% deflecting", 0.2), ("50% shuffled pairs", 0.5)):
        pairs, pv, rv, acc, used, marks = [], [], [], 0.0, 0, []
        for i in range(n):
            acc += share
            bad = acc >= 1.0 - 1e-9
            if bad:
                acc -= 1.0
            marks.append(bad)
            if bad and "deflecting" in name:
                pairs.append((deg[used]["prompt"], deg[used]["response"])); pv.append(dpemb[used]); rv.append(demb[used]); used += 1
            elif bad:
                j = (i + SHIFT) % 400
                pairs.append((nrm[i]["prompt"], nrm[j]["response"])); pv.append(pemb[i]); rv.append(emb[j])
            else:
                pairs.append((nrm[i]["prompt"], nrm[i]["response"])); pv.append(pemb[i]); rv.append(emb[i])
        r = day_one(rows(pairs, pv, rv, T0, 2, "d"))
        r["bad"], r["seen"] = sum(marks[:r["ready_at"] or n]), r["ready_at"] or n
        out[name] = r
        rv_ = r["review"]
        print(f"{name}: day-1 incident={r['opened']} at={r['opened_at']} reasons={r['reasons']} ready_at={r['ready_at']} "
              f"review={rv_ and rv_['verdict']} {rv_ and [i['text'] for i in rv_['issues']]}", flush=True)
    return out


def spot_items(pools):
    rng = random.Random(14)
    nrm = pools["normal"]
    alterable, plain = [], []
    for r in nrm:
        for pat, new in ALTER:
            if re.search(pat, r["response"]):
                alterable.append((r, re.sub(pat, new, r["response"], count=1)))
                break
        else:
            plain.append(r)
    rng.shuffle(alterable)
    altered = [(r["prompt"], changed, POLICY, True, "altered") for r, changed in alterable[:20]]
    kept = [(r["prompt"], r["response"], POLICY, False, "unaltered") for r, _ in alterable[20:40]]
    idx = list(range(len(nrm)))
    rng.shuffle(idx)
    right = [(nrm[i]["prompt"], nrm[i]["response"], None, False, "right answer") for i in idx[:10]]
    wrong = [(nrm[i]["prompt"], nrm[(i + SHIFT) % len(nrm)]["response"], None, True, "shuffled") for i in idx[10:20]]
    return altered + kept + right + wrong


def spot_checks(items):
    import database
    out = {}
    for model in (spotcheck.PRIMARY, spotcheck.FALLBACK):
        res, tokens, calls = [], 0, 0
        for prompt, response, ctx, problem, label in items:
            r, used, why = spotcheck.judge(prompt, response, ctx, spotcheck.groq_json, models=(model,))
            tokens += sum(t for _, t in used); calls += len(used)
            res.append((label, problem, r, why))
            if len(res) == 3 and not any(x[2] for x in res):
                raise SystemExit(f"{model}: no verdict on the first 3 checks ({why}); nothing written")
            time.sleep(6)                       # ~350 tokens a check: ~3.5K of the 8K tokens/min
        with database.connection() as conn:
            database.record_llm_usage(conn, model, "scorecard", 0, units=len(items), requests=calls, tokens=tokens)
        out[model] = {"results": res, "tokens": tokens, "calls": calls}
        print(f"{model}: {calls} calls, {tokens} tokens; {metrics(res)}; no verdict: {failures(res)}", flush=True)
    return out


def failures(res):
    from collections import Counter
    return dict(Counter(re.sub(r"\d{5,}", "#", (why or "?").split(": ", 1)[-1])[:60] for _, _, r, why in res if not r))


def verdicts(res, label):
    from collections import Counter
    c = Counter(r["verdict"] for lab, _, r, _ in res if lab == label and r)
    return ", ".join(f"{v} {n}" for v, n in c.most_common()) or "-"


def metrics(res, kinds=None):
    sel = [x for x in res if kinds is None or x[0] in kinds]
    tp = sum(1 for _, p, r, _ in sel if r and p and r["likely_problem"])
    fn = sum(1 for _, p, r, _ in sel if r and p and not r["likely_problem"])
    fp = sum(1 for _, p, r, _ in sel if r and not p and r["likely_problem"])
    tn = sum(1 for _, p, r, _ in sel if r and not p and not r["likely_problem"])
    none = sum(1 for _, _, r, _ in sel if not r)
    return {"tp": tp, "fn": fn, "fp": fp, "tn": tn, "no_verdict": none}


def section(ot, d1, sc):
    lines = [m10.M14_HEADING, "",
             f"Generated by `python scripts/m14_scorecard.py{'' if sc else ' --no-llm'}` on {datetime.now(timezone.utc):%Y-%m-%d} "
             "from `fixtures/m10/` (seed 1, no new generation). Counts from one run each: single examples, not rates.", "",
             "### Off-topic answers after the bot is ready", "",
             f"300 normal answers learned, then 100 answers one per minute where every question gets the answer to "
             f"another Volt question (shuffled pairs: fluent, on-brand, wrong). The answers themselves look normal, "
             f"so only the day-1 relevance check (question/answer similarity below {quality.RELEVANCE_MIN}) can see it.", "",
             "| stream | answers flagged by day-1 checks | incident opened | answers until detected | reasons at open |",
             "|---|---|---|---|---|"]
    for name, label in (("off_topic", "100 shuffled pairs"), ("control", "100 normal answers (control)")):
        r = ot[name]
        lines.append(f"| {label} | {r['quality_flagged']} of 100 | {'yes' if r['opened'] else 'no'} | "
                     f"{r['detect_at'] or '-'} | {'; '.join(r['open_reasons'] or []) or '-'} |")
    lines += ["", "### Bad from day 1", "",
              "A new bot whose traffic is bad from its first answer, one answer every 2 minutes. While it learns, "
              "only the day-1 rates can open an incident (SEV3, at least 10 answers in 30 minutes; refusals at 30%, "
              "unrelated answers at 30%). When it becomes ready, the baseline review checks what it learned from "
              "(asks at 10% refusals or unrelated answers). No spot-checks here.", "",
              "| start | bad answers before ready | answers flagged | day-1 incident (answer #) | ready at answer | "
              "baseline review |", "|---|---|---|---|---|---|"]
    for name, r in d1.items():
        b = r["review"]
        said = "-" if not b else (b["verdict"] + (": " + " / ".join(i["text"] for i in b["issues"]) if b["issues"] else ""))
        inc = f"yes (#{r['opened_at']}: {'; '.join(r['reasons'])})" if r["opened"] else "no"
        lines.append(f"| {name} | {r['bad']} of {r['seen']} | {r['flagged']} | {inc} | "
                     f"{r['ready_at'] if r['ready_at'] is not None else 'not ready within 200'} | {said} |")
    if sc:
        lines += ["", "### Spot-checks per model", "",
                  "The same items for both models (strict JSON output). With context: Volt's policy text; 20 answers "
                  "with one policy fact changed (e.g. 30-day returns -> 60-day) should be caught, 20 unaltered answers "
                  "should pass (they come from a bot told these policies, but some add details the policy does not "
                  "state, so a strict judge can flag them). Without context: 10 right answers vs 10 shuffled pairs. "
                  "A \"problem\" verdict is not_supported, partly_supported, does_not_answer or self_contradictory.", "",
                  "| model | items | caught (recall) | flagged that were real problems (precision) | false flags | "
                  "no verdict | tokens |", "|---|---|---|---|---|---|---|"]
        for model, d in sc.items():
            for label, kinds in (("with context (altered vs unaltered)", {"altered", "unaltered"}),
                                 ("no context (shuffled vs right)", {"shuffled", "right answer"})):
                m = metrics(d["results"], kinds)
                lines.append(f"| {model} | {label} | {m['tp']} of {m['tp'] + m['fn']} | {m['tp']} of {m['tp'] + m['fp']} | "
                             f"{m['fp']} of {m['fp'] + m['tn']} | {m['no_verdict']} | "
                             f"{d['tokens'] if 'with' in label else ''} |")
        lines += ["", "Verdicts per group (what each model actually said):", "",
                  "| model | altered (should be caught) | unaltered | shuffled (should be caught) | right answer | no verdict, why |",
                  "|---|---|---|---|---|---|"]
        for model, d in sc.items():
            f = failures(d["results"])
            lines.append(f"| {model} | {verdicts(d['results'], 'altered')} | {verdicts(d['results'], 'unaltered')} | "
                         f"{verdicts(d['results'], 'shuffled')} | {verdicts(d['results'], 'right answer')} | "
                         f"{', '.join(f'{n}: {k}' for k, n in f.items()) or '-'} |")
        lines += ["", "Tokens are for all 60 checks of the model, one check every 6 s (recorded in `llm_usage`, "
                  "kind `scorecard`)."]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")                  # first: it may set SHIFTLENS_DEV_TRUSTSTORE
    import dev_truststore
    dev_truststore.maybe_enable()
    pools = m10.load(1)
    nrm, deg = pools["normal"], pools["degraded"]
    emb = embeddings.embed([r["response"] for r in nrm])
    pemb = embeddings.embed([r["prompt"] for r in nrm])
    demb = embeddings.embed([r["response"] for r in deg])
    dpemb = embeddings.embed([r["prompt"] for r in deg])
    ot = off_topic_after_ready(pools, emb, pemb)
    d1 = bad_from_day_one(pools, emb, pemb, demb, dpemb)
    sc = None if a.no_llm else spot_checks(spot_items(pools))
    old = m10.OUT.read_text(encoding="utf-8")
    head = old[:old.index(m10.M14_HEADING)].rstrip("\n") + "\n\n" if m10.M14_HEADING in old else old.rstrip("\n") + "\n\n"
    notes = old[old.index(NOTES):] if NOTES in old else ""          # hand-written reading, kept across runs
    m10.OUT.write_text(head + section(ot, d1, sc) + ("
" + notes if notes else ""), encoding="utf-8")
    print(f"wrote {m10.OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

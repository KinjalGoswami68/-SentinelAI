"""
Tracked detection numbers (M7 before/after, M10 scorecard).

  python scripts/fp_eval.py              # all detectors in detector.DETECTORS
  python scripts/fp_eval.py --detectors m3

False-positive rate: 6-fold cross-validation over the 60 ordinary Volt answers
(fixtures/volt_electronics_normal.jsonl): train on 50, test the 10 held out,
FP rate = flagged / 60. Detection: train on all 60, test the 30 upsell answers of
fixtures/m9_prompt_change.jsonl (v2) and test_real.py's 5 bad answers.
Embeddings come from the same model as production (embeddings.embed).
"""
import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import dev_truststore  # noqa: E402

dev_truststore.maybe_enable()
import detector     # noqa: E402
import embeddings   # noqa: E402


def load():
    normal = [json.loads(l)["response"] for l in open(ROOT / "fixtures/volt_electronics_normal.jsonl", encoding="utf-8") if l.strip()]
    v2 = [json.loads(l)["response"] for l in open(ROOT / "fixtures/m9_prompt_change.jsonl", encoding="utf-8")
          if l.strip() and json.loads(l)["phase"] == "incident"]
    src = open(ROOT / "test_real.py", encoding="utf-8").read()
    block = src[src.index("bad_outputs = ["):]
    bad = re.findall(r'\(\s*"[^"]*",\s*"([^"]*)",\s*[\d.]+\s*\)', block[:block.index("\n]\n")])
    return normal, v2, bad


def evaluate(name, normal_v, v2_v, bad_v, folds=6):
    fit, flag = detector.DETECTORS[name]
    idx = np.arange(len(normal_v))
    fp = 0
    for f in range(folds):
        test = idx[f::folds]
        train = np.setdiff1d(idx, test)
        m = fit(normal_v[train])
        fp += sum(flag(m, normal_v[i]) for i in test)
    m = fit(normal_v)
    return {"detector": name, "fp": fp, "n_ordinary": len(normal_v),
            "v2_caught": sum(flag(m, v) for v in v2_v), "n_v2": len(v2_v),
            "bad_caught": sum(flag(m, v) for v in bad_v), "n_bad": len(bad_v)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detectors", default=",".join(detector.DETECTORS))
    a = ap.parse_args()
    normal, v2, bad = load()
    nv, vv, bv = embeddings.embed(normal), embeddings.embed(v2), embeddings.embed(bad)
    print("| detector | false positives on ordinary Volt answers (6-fold CV) | v2 upsell answers caught | test_real bad answers caught |")
    print("|---|---|---|---|")
    for name in a.detectors.split(","):
        r = evaluate(name, nv, vv, bv)
        print(f"| {name} | {r['fp']}/{r['n_ordinary']} ({r['fp'] / r['n_ordinary']:.0%}) | "
              f"{r['v2_caught']}/{r['n_v2']} | {r['bad_caught']}/{r['n_bad']} |")


if __name__ == "__main__":
    main()

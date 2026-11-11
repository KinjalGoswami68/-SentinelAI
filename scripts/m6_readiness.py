"""
M6 done-when: replay a narrow and a varied company answer by answer (one answer
every 6 minutes from 09:00 UTC, so traffic spans several hours) through
learning.assess and print when each becomes ready, with its threshold +/- X%.

  python scripts/m6_readiness.py
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import embeddings  # noqa: E402
import learning    # noqa: E402

COMPANIES = [("narrow: QuickShip order-status bot", "fixtures/m6_narrow_orders.jsonl"),
             ("varied: Volt Electronics support bot", "fixtures/volt_electronics_normal.jsonl")]
T0 = datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)
STEP = timedelta(minutes=6)


def replay(path):
    texts = [json.loads(l)["response"] for l in open(ROOT / path, encoding="utf-8") if l.strip()]
    vectors = embeddings.embed(texts)
    times = [T0 + i * STEP for i in range(len(texts))]
    first = {}
    for n in range(1, len(texts) + 1):
        a = learning.assess(vectors[:n], times[:n])
        first.setdefault(a["state"], (n, a))
        if a["state"] == "ready":
            break
    return first, a


def main():
    results = []
    for name, path in COMPANIES:
        first, last = replay(path)
        results.append((name, first.get("ready")))
        print(f"\n{name}")
        for state in ("learning", "provisional", "ready"):
            if state in first:
                n, a = first[state]
                extra = (f", threshold {a['threshold']:.3f} +/- {a['width'] / 2:.3f} ({a['margin_pct']:.1f}%)" if a["threshold"] else "")
                print(f"  {state:11s} from answer {n:3d} ({a['hours']} h of traffic){extra}  [{a['why']}]")
        if "ready" not in first:
            print(f"  not ready after {last['n']} answers: {last['why']} "
                  f"(threshold {last['threshold']:.3f} +/- {last['width'] / 2:.3f} ({last['margin_pct']:.1f}%))")
    (narrow, r_n), (varied, r_v) = results
    n_n = r_n[0] if r_n else None
    n_v = r_v[0] if r_v else None
    ok = n_n is not None and (n_v is None or n_n < n_v)
    print(f"\nnarrow ready at answer {n_n}, varied ready at answer {n_v}: "
          + ("PASS (narrow first)" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

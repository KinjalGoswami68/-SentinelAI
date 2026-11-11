"""
Per-answer detection, as pure functions with a version name, so the scorecard
(scripts/fp_eval.py, later M10) can measure each version on the same fixtures.

  m3:  centroid similarity < mean - 2.5 std (embeddings.find_threshold), OR Isolation
       Forest outlier. The detector main.py used from M3 to M6.
  knn: distance to the 5th-nearest baseline answer > the 99th percentile of the
       baseline's own leave-one-out 5th-NN distances (M6 threshold; M7 builds on it).
"""

import numpy as np

K = 5


def unit(vectors):
    v = np.asarray(vectors, dtype=np.float32)
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-12)


# ---------------------------------------------------------------- m3 (old) ---

def m3_fit(baseline_vectors):
    from embeddings import build_baseline, find_threshold
    from anomaly_detector import SentinelDetector
    b = np.asarray(baseline_vectors, dtype=np.float32)
    centroid = build_baseline(b)
    det = SentinelDetector()
    det.train(b)
    return {"centroid": centroid, "threshold": find_threshold(b, baseline=centroid), "forest": det}


def m3_flag(model, vector):
    from embeddings import compute_similarity
    sim = compute_similarity(vector, model["centroid"])
    return bool(sim < model["threshold"] or model["forest"].predict(vector)["is_anomaly"])


# ---------------------------------------------------------- kNN (M6, M7) ---

def knn_distance(baseline_unit, vector, k=K):
    """1 - cosine similarity to the k-th nearest baseline vector."""
    sims = baseline_unit @ unit(vector)
    k = min(k, len(sims))
    return float(1.0 - np.partition(sims, -k)[-k])


def loo_knn_distances(baseline_unit, k=K):
    """Each baseline vector's k-th-NN distance to the others (leave one out)."""
    s = baseline_unit @ baseline_unit.T
    np.fill_diagonal(s, -np.inf)
    k = min(k, len(s) - 1)
    return 1.0 - np.partition(s, -k, axis=1)[:, -k]


DIST_FLOOR = 0.05   # bots that repeat fixed answers have 5th-NN distance 0; never flag below this


def knn_threshold(baseline_vectors, q=99, resamples=200, frac=0.8, seed=0, k=K):
    """
    Threshold = q-th percentile of the baseline's leave-one-out kNN distances
    (floored at DIST_FLOOR). Used for flagging.

    How sure we are (M6): `resamples` subsamples of `frac` of the answers WITHOUT
    replacement, recomputing every answer's kNN distance inside the subsample. This
    captures what matters for kNN: a company with few answers per topic gets very
    different neighbours in each subsample. Inside a subsample the threshold is
    estimated as mean + 2.326 sd (normal 99th percentile): the empirical 99th of
    < 100 rows is just the maximum, whose resampled spread is meaninglessly narrow.
    The 90% interval of those estimates, scaled to the threshold, gives low/high;
    width = high - low in cosine-distance units.
    """
    b = unit(baseline_vectors)
    S = b @ b.T
    n = len(b)

    def loo(idx):
        s = S[np.ix_(idx, idx)].copy()
        np.fill_diagonal(s, -np.inf)
        kk = min(k, len(idx) - 1)
        return 1.0 - np.partition(s, -kk, axis=1)[:, -kk]

    def est(d):
        return max(DIST_FLOOR, float(d.mean() + 2.326 * d.std()))

    d_all = loo(np.arange(n))
    thr = max(DIST_FLOOR, float(np.percentile(d_all, q)))
    m = max(k + 2, int(round(frac * n)))
    rng = np.random.default_rng(seed)
    boots = np.array([est(loo(rng.choice(n, m, replace=False))) for _ in range(resamples)])
    centre = est(d_all)
    low = thr * float(np.percentile(boots, 5)) / centre
    high = thr * float(np.percentile(boots, 95)) / centre
    width = high - low
    return {"threshold": thr, "low": low, "high": high, "width": width,
            "interval_ratio": width / thr, "margin": width / 2 / thr, "n": n}


def knn_fit(baseline_vectors):
    b = unit(baseline_vectors)
    return {"baseline": b, **knn_threshold(b)}


def knn_flag(model, vector):
    return knn_distance(model["baseline"], vector) > model["threshold"]


DETECTORS = {"m3": (m3_fit, m3_flag), "knn": (knn_fit, knn_flag)}

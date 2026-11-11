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


def knn_threshold(baseline_vectors, q=99, resamples=200, seed=0):
    """
    Threshold = q-th percentile of the baseline's leave-one-out kNN distances.
    Stability = bootstrap of those distances (`resamples` times): 90% interval of
    the percentile. Returns dict(threshold, low, high, margin = half-width / threshold).
    """
    b = unit(baseline_vectors)
    d = loo_knn_distances(b)
    rng = np.random.default_rng(seed)
    boots = np.percentile(rng.choice(d, size=(resamples, len(d)), replace=True), q, axis=1)
    thr = float(np.percentile(d, q))
    low, high = float(np.percentile(boots, 5)), float(np.percentile(boots, 95))
    return {"threshold": thr, "low": low, "high": high,
            "margin": (high - low) / 2 / thr if thr > 0 else float("inf"), "n": len(d)}


def knn_fit(baseline_vectors):
    b = unit(baseline_vectors)
    return {"baseline": b, **knn_threshold(b)}


def knn_flag(model, vector):
    return knn_distance(model["baseline"], vector) > model["threshold"]


DETECTORS = {"m3": (m3_fit, m3_flag), "knn": (knn_fit, knn_flag)}

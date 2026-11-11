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


# ------------------------------------------------------------- M7: drift ---
# CUSUMs over the answer stream, on system-computed signals (not the client's
# quality_score). Each update returns True when the CUSUM crosses its limit, then
# the sum resets, so a sustained shift raises one alarm per crossing.

import math
import re as _re

# Refusals and deflections: the bot does not answer. Apostrophes are normalised
# first (models often write curly ones). "I'm sorry" alone is empathy, not a refusal.
REFUSAL_RE = _re.compile(
    r"\b(i can'?t (?:help|assist|answer|provide)|i cannot (?:help|assist|answer|provide)|"
    r"(?:i am|i'm) (?:unable|not able) to|unable to (?:help|assist|provide|answer)|as an ai|"
    r"(?:i'?m|i am) not (?:sure|certain)|i don'?t know|"
    r"(?:i )?don'?t have (?:that|the|any|specific) (?:information|details)|"
    r"(?:please )?contact (?:the|your) (?:store|retailer|seller|support)|"
    r"reach out to (?:the|your) (?:store|retailer|seller))", _re.I)


def is_refusal_or_empty(text):
    t = (text or "").strip()
    if not t:
        return True
    return bool(REFUSAL_RE.search(t.replace("’", "'").replace("‘", "'")))


class DistanceCUSUM:
    """One-sided CUSUM on z = (kNN distance - baseline mean) / baseline sd.
    k = allowance (shift of interest is ~2k sd), h = decision limit."""

    def __init__(self, mean, sd, k=0.5, h=5.0):
        self.mean, self.sd, self.k, self.h, self.s = mean, max(sd, 1e-6), k, h, 0.0

    def update(self, distance):
        z = (distance - self.mean) / self.sd
        self.s = max(0.0, self.s + z - self.k)
        if self.s > self.h:
            self.s = 0.0
            return True, z
        return False, z


class RateCUSUM:
    """Bernoulli CUSUM for the refusal/empty rate: in control at p0 (the company's
    own rate, floored), alarm for a rise to p1."""

    def __init__(self, p0, h=4.0, floor=0.02):
        self.p0 = min(max(p0, floor), 0.5)
        self.p1 = min(0.95, max(3 * self.p0, self.p0 + 0.15))
        self.h, self.s = h, 0.0
        self.w1 = math.log(self.p1 / self.p0)
        self.w0 = math.log((1 - self.p1) / (1 - self.p0))

    def update(self, hit):
        self.s = max(0.0, self.s + (self.w1 if hit else self.w0))
        if self.s > self.h:
            self.s = 0.0
            return True
        return False


def baseline_stats(baseline_vectors, baseline_texts, baseline_latencies=None):
    """What the M7 signals compare against: kNN distance mean/sd (leave one out),
    log-length mean/sd, refusal/empty rate, latency mean/sd."""
    b = unit(baseline_vectors)
    d = loo_knn_distances(b)
    ll = np.log([max(1, len(t or "")) for t in baseline_texts])
    lat = [x for x in (baseline_latencies or []) if x is not None]
    return {"dist_mean": float(d.mean()), "dist_sd": float(d.std()),
            "loglen_mean": float(ll.mean()), "loglen_sd": float(max(ll.std(), 1e-6)),
            "refusal_rate": float(np.mean([is_refusal_or_empty(t) for t in baseline_texts])),
            "lat_mean": float(np.mean(lat)) if lat else None,
            "lat_sd": float(max(np.std(lat), 1e-6)) if lat else None}


def features(stats, distance, text, latency=None, language_changed=False):
    """The 5-feature vector for the Isolation Forest candidate."""
    lat_z = 0.0 if latency is None or stats["lat_mean"] is None else (latency - stats["lat_mean"]) / stats["lat_sd"]
    return [(distance - stats["dist_mean"]) / max(stats["dist_sd"], 1e-6),
            (math.log(max(1, len(text or ""))) - stats["loglen_mean"]) / stats["loglen_sd"],
            float(is_refusal_or_empty(text)), float(language_changed), lat_z]


def if5_fit(feature_rows, seed=42):
    """Isolation Forest on the 5 features. Offline candidate only (scikit-learn
    costs ~118 MB; Render free has 512 MB). M10 decides whether it is kept."""
    from sklearn.ensemble import IsolationForest
    m = IsolationForest(contamination=0.02, n_estimators=200, random_state=seed)
    m.fit(np.asarray(feature_rows, dtype=np.float32))
    return m


def if5_flag(model, feature_row):
    return bool(model.predict(np.asarray([feature_row], dtype=np.float32))[0] == -1)

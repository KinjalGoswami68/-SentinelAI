# embeddings.py
"""
Sentence embeddings: BAAI/bge-small-en-v1.5 through FastEmbed (int8 ONNX, CPU,
no PyTorch). 384 dimensions, L2-normalised, so cosine = dot product.

Stateless: there is no fitted object. The same text always gets the same vector,
for every company, so nothing learned from one company leaks into another.
Per-company state (baseline, threshold) is built from that company's own stored
vectors in main.py.

Cost (measured 2026-11-11, docs/embeddings_eval.md): the model adds ~155-215 MB
RSS; one prompt+response pair takes ~0.23 s of one CPU core, so ~2 s on Render's
0.1 CPU. Vectors are therefore computed once at ingest and stored, never
recomputed for baselines.
"""

import os
import threading
from pathlib import Path

import numpy as np

MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIM        = 384
MAX_CHARS  = 2000      # the model sees at most 512 tokens anyway
BATCH_SIZE = 32        # bounds memory when embedding many rows at once

# Model files live inside the project so a build step can download them once
# (scripts/fetch_model.py). If they are missing, FastEmbed downloads them here.
CACHE_DIR = os.environ.get("FASTEMBED_CACHE_PATH") or str(
    Path(__file__).resolve().parent / ".fastembed_cache"
)

_model      = None
_model_lock = threading.Lock()
model_state = {"status": "not loaded", "error": None}   # reported by /health


def get_model():
    """Load the model once per process (thread-safe)."""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                model_state["status"] = "loading"
                try:
                    from fastembed import TextEmbedding
                    # threads=1: Render free has 0.1 CPU; more threads only contend.
                    _model = TextEmbedding(MODEL_NAME, cache_dir=CACHE_DIR, threads=1)
                except Exception as e:
                    model_state.update(status="error", error=type(e).__name__)
                    raise
                model_state.update(status="ready", error=None)
    return _model


def embed(texts):
    """list[str] or str -> float32 array (n, 384), each row L2-normalised.

    An empty text gets a zero vector (cosine 0 with everything) instead of the
    model's vector for special tokens only.
    """
    if isinstance(texts, str):
        texts = [texts]
    texts = [str(t).strip()[:MAX_CHARS] for t in texts]

    out = np.zeros((len(texts), DIM), dtype=np.float32)
    idx = [i for i, t in enumerate(texts) if t]
    if idx:
        vecs = np.array(list(get_model().embed([texts[i] for i in idx], batch_size=BATCH_SIZE)),
                        dtype=np.float32)
        vecs /= np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-12)
        out[idx] = vecs
    return out


def build_baseline(vectors):
    """Normalised centroid of a company's good response vectors."""
    vectors = np.asarray(vectors, dtype=np.float32)
    if vectors.ndim != 2 or len(vectors) == 0:
        raise ValueError("Cannot build baseline from an empty set of vectors")
    c = vectors.mean(axis=0)
    return c / max(float(np.linalg.norm(c)), 1e-12)


def compute_similarity(vector, baseline):
    """Cosine similarity, clipped to [0, 1] and rounded to 4 places."""
    if vector is None or baseline is None:
        raise ValueError("Vector and baseline cannot be None")
    v, b = np.asarray(vector, dtype=np.float32), np.asarray(baseline, dtype=np.float32)
    nv, nb = float(np.linalg.norm(v)), float(np.linalg.norm(b))
    if nv == 0 or nb == 0:
        return 0.0
    return round(float(np.clip(np.dot(v, b) / (nv * nb), 0.0, 1.0)), 4)


# Calibrated on bge for M3 (docs/embeddings_eval.md): with 2.5 std, none of the 60
# varied Volt fixture answers falls below its own threshold; 2 std flags 4 of 60.
THRESHOLD_STD  = 2.5
THRESHOLD_CLIP = (0.40, 0.90)


def find_threshold(vectors, baseline=None):
    """mean - 2.5 std of the baseline rows' own similarity to the centroid."""
    vectors = np.asarray(vectors, dtype=np.float32)
    if len(vectors) < 10:
        return THRESHOLD_CLIP[0]
    if baseline is None:
        baseline = build_baseline(vectors)
    sims = np.array([compute_similarity(v, baseline) for v in vectors])
    std = float(sims.std())
    if std < 0.001:
        return round(float(np.clip(sims.mean() - 0.05, *THRESHOLD_CLIP)), 3)
    return round(float(np.clip(sims.mean() - THRESHOLD_STD * std, *THRESHOLD_CLIP)), 3)

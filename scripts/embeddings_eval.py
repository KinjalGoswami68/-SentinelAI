"""
M3 embedding evaluation.

  python scripts/embeddings_eval.py               # 20-pair table (markdown) for all models
  python scripts/embeddings_eval.py --rss MODEL   # memory of `import main` + MODEL, fresh process

Pairs: fixtures/embedding_pairs.jsonl (10 "similar" paraphrases, 10 "different":
topic, refusal, Hinglish, empty). A model passes when every different pair scores
below every similar pair.

Models:
  tfidf     the current embeddings.py settings, fitted on the 40 pair texts
  bge       BAAI/bge-small-en-v1.5 (FastEmbed, int8 ONNX)
  e5_int8   intfloat/multilingual-e5-small, official int8 ONNX ("query: " prefix)
  e5_fp32   intfloat/multilingual-e5-small, full-precision ONNX
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT  = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import dev_truststore  # noqa: E402  local dev only (model downloads behind antivirus HTTPS scanning)

dev_truststore.maybe_enable()

PAIRS = ROOT / "fixtures" / "embedding_pairs.jsonl"
MODELS = ["tfidf", "bge", "e5_int8", "e5_fp32"]

E5_FILES = {"e5_int8": "onnx/model_qint8_avx512_vnni.onnx", "e5_fp32": "onnx/model.onnx"}


def load_pairs():
    with open(PAIRS, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def cosine(u, v):
    nu, nv = np.linalg.norm(u), np.linalg.norm(v)
    if nu == 0 or nv == 0:          # empty text -> zero vector -> no similarity
        return 0.0
    return float(np.dot(u, v) / (nu * nv))


def make_embedder(name):
    """Returns f(list[str]) -> np.ndarray of shape (n, dim)."""
    if name == "tfidf":
        from sklearn.feature_extraction.text import TfidfVectorizer
        vec = TfidfVectorizer(max_features=1000, stop_words="english", ngram_range=(1, 2))
        corpus = [t for p in load_pairs() for t in (p["a"], p["b"]) if t.strip()]
        vec.fit(corpus)
        return lambda texts: vec.transform(texts).toarray()

    from fastembed import TextEmbedding
    if name == "bge":
        model = TextEmbedding("BAAI/bge-small-en-v1.5")
        return lambda texts: np.array(list(model.embed(texts)))

    if name in E5_FILES:
        from fastembed.common.model_description import ModelSource, PoolingType
        TextEmbedding.add_custom_model(
            model=f"shiftlens/{name}", pooling=PoolingType.MEAN, normalization=True,
            sources=ModelSource(hf="intfloat/multilingual-e5-small"), dim=384,
            model_file=E5_FILES[name], license="mit",
        )
        model = TextEmbedding(f"shiftlens/{name}")
        # e5 card: use "query: " on both sides for symmetric similarity tasks.
        return lambda texts: np.array(list(model.embed([f"query: {t}" for t in texts])))

    raise ValueError(name)


def pair_scores(name):
    embed = make_embedder(name)
    out = {}
    for p in load_pairs():
        # For "" TF-IDF gives a zero vector (cosine 0); transformer models still return
        # a vector (special tokens only), so their score for empty answers is real.
        va, vb = embed([p["a"], p["b"]])
        out[p["id"]] = cosine(va, vb)
    return out


def table(models):
    pairs  = load_pairs()
    scores = {m: pair_scores(m) for m in models}

    lines = ["| id | kind | change | " + " | ".join(models) + " |",
             "|---|---|---|" + "---|" * len(models)]
    for p in pairs:
        cells = " | ".join(f"{scores[m][p['id']]:.3f}" for m in models)
        lines.append(f"| {p['id']} | {p['kind']} | {p['change']} | {cells} |")

    lines.append("")
    lines.append("| model | min similar | max different | gap | different pairs above min similar |")
    lines.append("|---|---|---|---|---|")
    verdict = {}
    for m in models:
        sim  = [scores[m][p["id"]] for p in pairs if p["kind"] == "similar"]
        dif  = {p["id"]: scores[m][p["id"]] for p in pairs if p["kind"] == "different"}
        low  = min(sim)
        bad  = [i for i, s in dif.items() if s >= low]
        verdict[m] = not bad
        lines.append(f"| {m} | {low:.3f} | {max(dif.values()):.3f} | {low - max(dif.values()):+.3f} | "
                     f"{', '.join(bad) if bad else 'none (pass)'} |")
    return "\n".join(lines), verdict


def rss(name):
    import psutil
    proc = psutil.Process()
    mb = lambda b: b / 2**20
    t0 = time.monotonic()
    sys.path.insert(0, str(ROOT))
    import main  # noqa: F401  the real app imports (FastAPI, sklearn, pandas, ...), no DB call
    app_rss = mb(proc.memory_info().rss)
    if name != "none":
        embed = make_embedder(name)
        texts = [p["a"] for p in load_pairs()] * 5          # 100 texts, one batch
        t1 = time.monotonic(); embed(texts); t_embed = time.monotonic() - t1
    else:
        t_embed = 0.0
    mi = proc.memory_info()
    peak = mb(getattr(mi, "peak_wset", mi.rss))             # Windows peak working set
    print(json.dumps({"model": name, "app_only_MB": round(app_rss), "after_model_MB": round(mb(mi.rss)),
                      "peak_MB": round(peak), "embed_100_texts_s": round(t_embed, 2),
                      "total_s": round(time.monotonic() - t0, 1)}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rss", metavar="MODEL", choices=MODELS + ["none"])
    ap.add_argument("--models", default=",".join(MODELS))
    a = ap.parse_args()
    if a.rss:
        rss(a.rss)
    else:
        md, verdict = table(a.models.split(","))
        print(md)
        print("\nverdict:", json.dumps(verdict))

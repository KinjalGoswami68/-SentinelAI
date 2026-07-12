
# embeddings.py


from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np
import warnings
warnings.filterwarnings("ignore")

print("Loading text vectorizer...")

vectorizer = TfidfVectorizer(
    max_features = 1000,
    stop_words   = 'english',
    ngram_range  = (1, 2)
)
is_fitted = False

print("Vectorizer ready.")


def embed(texts):
    global vectorizer, is_fitted

    if isinstance(texts, str):
        texts = [texts]

    texts = [str(t).strip() for t in texts]
    texts = [t for t in texts if t]
    texts = [t[:2000] for t in texts]

    if not texts:
        raise ValueError("No valid texts to embed")

    if not is_fitted:
        vectorizer.fit(texts)
        is_fitted = True
        vectors = vectorizer.transform(texts).toarray()
    else:
        try:
            vectors = vectorizer.transform(texts).toarray()
        except Exception:
            vectorizer.fit(texts)
            is_fitted = True
            vectors = vectorizer.transform(texts).toarray()

    return vectors


def build_baseline(good_texts):
    if not good_texts:
        raise ValueError("Cannot build baseline from empty list")

    if len(good_texts) < 10:
        raise ValueError(
            f"Need at least 10 texts. Got {len(good_texts)}."
        )

    vectors  = embed(good_texts)
    baseline = np.mean(vectors, axis=0)
    return baseline


def compute_similarity(vector, baseline):
    if vector is None or baseline is None:
        raise ValueError("Vector and baseline cannot be None")

    v     = np.array(vector).reshape(1, -1)
    b     = np.array(baseline).reshape(1, -1)
    score = cosine_similarity(v, b)[0][0]

    return round(float(np.clip(score, 0.0, 1.0)), 4)


def find_threshold(good_texts, vectors=None):
    if not good_texts or len(good_texts) < 10:
        return 0.50

    if vectors is None:
        vectors = embed(good_texts)

    baseline = np.mean(vectors, axis=0)

    scores = [
        compute_similarity(vec, baseline)
        for vec in vectors
    ]

    scores_array = np.array(scores)
    mean         = float(np.mean(scores_array))
    std          = float(np.std(scores_array))

    if std < 0.001:
        return 0.50

    threshold = mean - (2.0 * std)
    threshold = float(np.clip(threshold, 0.25, 0.75))

    return round(threshold, 3)
# embeddings.py
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np
import warnings
warnings.filterwarnings("ignore")


MAX_TEXT_LENGTH = 2000

MIN_BASELINE_TEXTS = 10


print("Loading sentence-transformers model...")
try:
    model = SentenceTransformer('all-MiniLM-L6-v2')
    print("Model ready.")
except Exception as e:
    print(f"ERROR: Failed to load model — {e}")
    print("Run: pip install sentence-transformers")
    raise


# FUNCTION 1 — embed
# Converts text strings into embedding vectors.
# Input:
#   texts — string OR list of strings
# Output:
#   numpy array shape (n, 384)

def embed(texts):
    if isinstance(texts, str):
        texts = [texts]

    
    if not isinstance(texts, (list, tuple)):
        raise TypeError(
            f"texts must be a string or list. Got {type(texts)}"
        )

    
    texts = [str(t).strip() for t in texts]
    texts = [t for t in texts if t]

    
    if not texts:
        raise ValueError(
            "No valid texts to embed. "
            "All inputs were empty or whitespace."
        )

    texts = [t[:MAX_TEXT_LENGTH] for t in texts]


    vectors = model.encode(
        texts,
        show_progress_bar    = False,
        normalize_embeddings = True,
        batch_size           = 32
    )

    return vectors



# FUNCTION 2 — build_baseline
# Input:
#   good_texts — list of strings
# Output:
#   numpy array (384,) — baseline vector
def build_baseline(good_texts):
    if not good_texts:
        raise ValueError(
            "Cannot build baseline from empty list. "
            "Send more outputs first."
        )

    if len(good_texts) < MIN_BASELINE_TEXTS:
        raise ValueError(
            f"Need at least {MIN_BASELINE_TEXTS} texts for baseline. "
            f"Got {len(good_texts)}. "
            f"Keep sending good outputs — baseline builds automatically."
        )

    vectors  = embed(good_texts)
    baseline = np.mean(vectors, axis=0)

    return baseline

# FUNCTION 3 — compute_similarity
# Input:
#   vector   — numpy array (384,) one output
#   baseline — numpy array (384,) normal average
# Output:
#   float between 0.0 and 1.0
#   1.0 = identical meaning to baseline
#   0.5 = somewhat similar
#   0.0 = completely different meaning

def compute_similarity(vector, baseline):
    if vector is None:
        raise ValueError("vector cannot be None")

    if baseline is None:
        raise ValueError("baseline cannot be None")

    v = np.array(vector).reshape(1, -1)
    b = np.array(baseline).reshape(1, -1)

    score = cosine_similarity(v, b)[0][0]

    score = float(np.clip(score, 0.0, 1.0))

    return round(score, 4)

# FUNCTION 4 — find_threshold
# Input:
#   good_texts — list of good output strings
#   vectors    — optional pre-computed vectors
# Output:
#   float between 0.25 and 0.75

def find_threshold(good_texts, vectors=None):
    
    if not good_texts or len(good_texts) < MIN_BASELINE_TEXTS:
        return 0.50

    
    if vectors is None:
        vectors = embed(good_texts)

    baseline = np.mean(vectors, axis=0)

    
    scores = []
    for vec in vectors:
        sim = compute_similarity(vec, baseline)
        scores.append(sim)

    scores_array = np.array(scores)

    mean      = float(np.mean(scores_array))
    std       = float(np.std(scores_array))

    
    if std < 0.001:
        return 0.50

    raw_threshold = mean - (2.0 * std)


    threshold = float(np.clip(raw_threshold, 0.25, 0.75))

    return round(threshold, 3)
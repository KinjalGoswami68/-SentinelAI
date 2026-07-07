
# anomaly_detector.py

from sklearn.ensemble import IsolationForest
import numpy as np
import warnings
warnings.filterwarnings("ignore")

# Minimum vectors needed before training
MIN_TRAINING_VECTORS = 20


class SentinelDetector:

    def __init__(self):
        self._forest = None

        self.is_trained     = False
        self.training_count = 0

    # Input:
    #   good_vectors — numpy array (n, 384)
    #                  n must be >= MIN_TRAINING_VECTORS

    def train(self, good_vectors):
        if good_vectors is None:
            raise ValueError(
                "good_vectors cannot be None"
            )

        good_vectors = np.array(good_vectors)

        if good_vectors.ndim != 2:
            raise ValueError(
                f"good_vectors must be 2D array. "
                f"Got shape {good_vectors.shape}"
            )

        n = len(good_vectors)

        if n < MIN_TRAINING_VECTORS:
            raise ValueError(
                f"Need at least {MIN_TRAINING_VECTORS} vectors. "
                f"Got {n}. Send more outputs first."
            )

        
        max_samp = min(256, n)

        self._forest = IsolationForest(
            # Expected fraction of anomalies in data
            # 0.05 = 5% — conservative to avoid false positives
            contamination = 0.05,
            # Number of trees in the forest
            n_estimators  = 200,
            random_state  = 42,
            max_samples   = max_samp
        )

        self._forest.fit(good_vectors)
        self.is_trained     = True
        self.training_count = n

     
    def retrain(self, good_vectors):
        self.is_trained     = False
        self.training_count = 0
        self._forest        = None
        self.train(good_vectors)

    
    # Input:
    #   vector — numpy array (384,)
    # Output:
    #   dict:
    #     is_anomaly         — True or False
    #     raw_score          — Isolation Forest score
    #     anomaly_confidence — 0.0 to 1.0
    #                          higher = more anomalous
    
    def predict(self, vector):
        if not self.is_trained:
            raise ValueError(
                "Detector is not trained. "
                "Warmup must complete before prediction."
            )

        if vector is None:
            raise ValueError("vector cannot be None")

        v2d = np.array(vector).reshape(1, -1)

        # 1 = normal, -1 = anomaly
        label = int(self._forest.predict(v2d)[0])

        
        raw_score = float(self._forest.score_samples(v2d)[0])

        is_anomaly = (label == -1)

        anomaly_confidence = float(np.clip(-raw_score, 0.0, 1.0))

        return {
            "is_anomaly"        : is_anomaly,
            "raw_score"         : round(raw_score, 4),
            "anomaly_confidence": round(anomaly_confidence, 4)
        }

    
    def status(self):
        return {
            "is_trained"    : self.is_trained,
            "training_count": self.training_count,
            "ready"         : self.is_trained
        }
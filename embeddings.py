
# SentinelAI 
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np
import warnings
warnings.filterwarnings("ignore")


print("Loading ML model...")
model = SentenceTransformer('all-MiniLM-L6-v2')
print("Model loaded successfully!")
print()


good_outputs = [
    "Your refund will be processed within 3 to 5 business days.",
    "You can return the product within 30 days of purchase.",
    "Your order has been shipped and will arrive in 2 days.",
    "Our customer support is available from 9am to 6pm.",
    "You can track your order using the link sent to your email.",
    "We accept Visa, Mastercard, and UPI payments.",
    "Free shipping is available on orders above 500 rupees.",
    "Your password can be reset from the login page.",
]


bad_outputs = [
    "Refunds require a notarized letter and take 6 months.",
    "Returns are never allowed under any circumstances.",
    "Your order is definitely lost forever. Sorry.",
]


print("Converting good outputs to vectors...")
good_vectors = model.encode(good_outputs)

print("Converting bad outputs to vectors...")
bad_vectors = model.encode(bad_outputs)
print()

# CREATE BASELINE

baseline = np.mean(good_vectors, axis=0)


print("=" * 50)
print("RESULTS")
print("=" * 50)
print(f"Vector size  : {len(baseline)} numbers per sentence")
print(f"Good outputs : {len(good_outputs)}")
print(f"Bad outputs  : {len(bad_outputs)}")
print()

# COMPARE EACH OUTPUT TO BASELINE
THRESHOLD = 0.50

print("CHECKING BAD OUTPUTS")
print("(These should score BELOW 0.50)")
print("-" * 50)

for i in range(len(bad_outputs)):
    score = cosine_similarity(
        [bad_vectors[i]],
        [baseline]
    )[0][0]
    score = round(float(score), 2)

    if score < THRESHOLD:
        label = "FLAGGED"
    else:
        label = "missed"

    print(f"{label} | score: {score} | {bad_outputs[i]}")

print()
print("CHECKING GOOD OUTPUTS")
print("(These should score ABOVE 0.50)")
print("-" * 50)

for i in range(len(good_outputs)):
    score = cosine_similarity(
        [good_vectors[i]],
        [baseline]
    )[0][0]
    score = round(float(score), 2)

    if score >= THRESHOLD:
        label = "normal"
    else:
        label = "wrongly flagged"

    print(f"{label} | score: {score} | {good_outputs[i]}")

print()
print("=" * 50)
print("embeddings.py complete")
print("Next file: anomaly_detector.py")
print("=" * 50)
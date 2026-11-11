"""
Download the embedding model into the project's .fastembed_cache/ at build time.

Render Build Command:  pip install -r requirements.txt && python scripts/fetch_model.py

Files written during the build ship with the deploy, so a cold start loads the
model from disk instead of downloading ~65 MB from Hugging Face. Without this
step the app still works: it downloads the model on first start.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import dev_truststore  # noqa: E402  local dev only

dev_truststore.maybe_enable()

import embeddings  # noqa: E402

t0 = time.monotonic()
vec = embeddings.embed(["model download check"])
print(f"{embeddings.MODEL_NAME} ready in {embeddings.CACHE_DIR} "
      f"({time.monotonic() - t0:.1f}s, dim {vec.shape[1]})", flush=True)

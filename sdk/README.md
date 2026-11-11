# sentinelai (Python client)

Sends each chatbot exchange to SentinelAI in a background thread. No dependencies.

```bash
pip install "git+https://github.com/KinjalGoswami68/-SentinelAI.git#subdirectory=sdk"
```

```python
import sentinelai
sentinelai.init(api_key="sk-...")          # or set SENTINELAI_API_KEY
sentinelai.track(prompt, answer, model_version="openai/gpt-oss-20b", latency_ms=840)
```

- `track()` returns immediately. The POST runs in a daemon thread with one retry
  after 2 s (on network errors, 5xx and 429), and never raises.
- Every call carries a `request_id` (a new UUID unless you pass one), so a retry
  cannot store the same exchange twice.
- Optional fields: `request_id`, `prompt_version`, `model_version`, `status`
  (`ok`, `error`, `timeout`, `refused`, ...), `latency_ms`, `session_id` (stored
  only as a hash), `quality_score` (0-10, display only).
- In a short script, call `sentinelai.flush()` before exiting so pending sends finish.

Not on PyPI yet: the final package name is chosen before publishing.

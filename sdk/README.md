# shiftlens (Python client)

Sends each chatbot exchange to ShiftLens in a background thread. No dependencies.

```bash
pip install "git+https://github.com/KinjalGoswami68/ShiftLens.git#subdirectory=sdk"
```

```python
import shiftlens
shiftlens.init(api_key="sk-...")          # or set SHIFTLENS_API_KEY
shiftlens.track(prompt, answer, model_version="openai/gpt-oss-20b", latency_ms=840)
```

- `track()` returns immediately. The POST runs in a daemon thread (60 s timeout)
  with one retry after 10 s on network errors, 5xx and 429, so a free server waking
  from sleep (~60-90 s) is covered. It never raises.
- Every call carries a `request_id` (a new UUID unless you pass one), so a retry
  cannot store the same exchange twice.
- Optional fields: `request_id`, `prompt_version`, `model_version`, `status`
  (`ok`, `error`, `timeout`, `refused`, ...), `latency_ms`, `session_id` (stored
  only as a hash), `quality_score` (0-10, display only).
- In a short script, call `shiftlens.flush()` before exiting so pending sends finish.

Not on PyPI yet (the name `shiftlens` is free there); until then install from GitHub as above.

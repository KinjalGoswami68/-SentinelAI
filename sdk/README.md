# shiftlens (Python client)

Sends each chatbot exchange to ShiftLens in a background thread. No dependencies.

```bash
pip install "git+https://github.com/KinjalGoswami68/ShiftLens.git#subdirectory=sdk"
```

```python
import shiftlens
shiftlens.auto()      # every OpenAI / Groq chat completion is tracked from here on
```

That's all. The first run registers a bot, saves its key in `~/.shiftlens/config.json` and
prints the dashboard link (never the key). In containers without a persistent home folder, set
`SHIFTLENS_API_KEY` instead, or every restart registers a new bot.

By hand, for any other client:

```python
shiftlens.track(question, answer, model_version="gpt-oss-20b", prompt_version="v3",
                context=retrieved_docs)   # context: used for a spot-check, never stored
```

- Never slows or breaks the bot: one background worker sends from a bounded queue (1,000
  answers; beyond that they are dropped and counted, see `shiftlens.stats()`), errors are never
  raised, and a wrapped client returns exactly what it returned before. A failed provider call
  is recorded with `status="error"` and the exception is re-raised unchanged.
- `auto()` covers sync, async and streaming calls of `openai` and `groq`; libraries that are
  not installed are skipped.
- Each answer carries a `request_id`, so a retry cannot store it twice.
- Short script? Call `shiftlens.flush()` before it exits.

Have logs from before? Build the baseline from them at once instead of waiting for traffic:

```python
shiftlens.import_history([(question, answer), ...])   # or dicts with prompt, response, t (ISO time)
```

Up to 300 answers per bot per day. Imported answers teach the bot what normal looks like (and
are checked in its baseline review) but never open incidents or post to Slack. It blocks until
the batches are sent and returns `{"accepted", "skipped", "batches", "stopped"}`.

Not on PyPI yet (the name `shiftlens` is free there); until then install from GitHub as above.

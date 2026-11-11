# SentinelAI

**Your chatbot changed and nobody told you.**

SentinelAI watches every answer your chatbot gives, learns what normal looks like for *your*
bot, and when the answers drift it opens one incident and sends one Slack message: what
changed, probably why, and the request ids that prove it.

- **Site:** https://kinjalgoswami68.github.io/-SentinelAI/
- **Live demo (no sign-up):** https://kinjalgoswami68.github.io/-SentinelAI/app/demo/ —
  Volt Electronics, a fictional support bot fed by real fixture traffic
- **App:** https://kinjalgoswami68.github.io/-SentinelAI/app/
- **API:** `https://sentinelai-qqph.onrender.com` ([docs/API.md](docs/API.md))

## Install

Register a bot in the [app](https://kinjalgoswami68.github.io/-SentinelAI/app/#/setup) to get a
key, then:

```bash
pip install "git+https://github.com/KinjalGoswami68/-SentinelAI.git#subdirectory=sdk"
```

```python
import sentinelai
sentinelai.init(api_key="sk-...")
sentinelai.track(question, answer, prompt_version="v3", model_version="gpt-oss-20b")
```

`track()` returns immediately (background thread, one retry, never raises). Send
`prompt_version` / `model_version` if you have them: they turn "something changed" into
"prompt v3 changed it". No client? It's one HTTP call — see [docs/API.md](docs/API.md).

## How it works

1. **Detect (math, no LLM, no cost per answer).** Each answer is embedded with
   `bge-small-en-v1.5` and compared with the bot's own normal answers: distance to the 5th
   nearest neighbour against a learned threshold, plus two CUSUMs (answers drifting away;
   refusals and empty answers rising) and a group check when a new `prompt_version` or
   `model_version` shows up.
2. **Learn first.** A new bot is `learning` (under 30 answers), then `provisional` (flags on
   the dashboard only), then `ready` once the threshold is stable and traffic spans 2 hours.
   Slack and investigations only start at `ready`.
3. **Investigate.** When an incident opens, code tools compare the numbers, cluster the
   changed answers, line up version changes and look for similar past incidents. Then one LLM
   call (Groq `openai/gpt-oss-120b`, free tier) writes the note. Every claim must cite an
   evidence id, invented ids are dropped, and the LLM can lower the confidence the rules
   computed but never raise it. If Groq is unavailable you get a rules-only note.
4. **Alert.** Signals become one incident with at most 3 Slack messages: opened (with the
   note), escalated, resolved. It resolves after 30 quiet minutes.

## Scorecard

8 scripted scenarios replayed through the production code (300 normal answers, then 100
changed ones): **cause named right 7 of 8, 0 false incidents on normal traffic, a prompt change
caught after 10 answers**. The miss: a topic shift was caught after 4 answers but the note said
"unknown". One seed, so read these as single runs, not rates.
Full table, including what didn't work: [docs/SCORECARD.md](docs/SCORECARD.md).

Per-answer false positives on ordinary answers went from 5 of 60 (the first TF-IDF +
Isolation Forest version) to 1 of 60: [docs/detection_scorecard.md](docs/detection_scorecard.md).

## Architecture

```text
your bot ──track()──▶ FastAPI on Render (free, Singapore)
                        ├─ embed (FastEmbed, bge-small int8 ONNX, ~0.8 s, in process)
                        ├─ kNN threshold + CUSUMs + version check   (detector.py, learning.py)
                        ├─ incident state machine                   (incidents.py)
                        ├─ investigation: tools + rules + 1 Groq call (investigation.py)
                        └─ Slack webhook (max 3 messages per incident)
                      Supabase Postgres (free, session pooler, 15 connections)
static site + app ◀── GitHub Pages (Vite, React, Tailwind, GSAP on the landing page only)
```

- Everything runs on free tiers, with no card anywhere. Hitting a limit degrades instead of
  billing: Groq 429 falls back to a rules-only note, and the server sleeps outside
  09:00–21:00 IST (kept awake inside it by cron-job.org).
- Memory on Render: ~320 MB of 512 MB.
- CI (GitHub Actions): every deploy is checked against the live API; the site is built,
  scanned for secrets and measured with Lighthouse after each Pages deploy.
- Design notes per component: [docs/](docs/).

## Limits

- **It doesn't detect hallucinations.** It notices when answers *change*, not whether a single
  answer is true. Built to catch when your bot's behaviour changes, not to fact-check answers.
- **On-topic changes without version metadata are hard.** If your bot doesn't send
  `prompt_version` or `model_version`, a subtle change often only gets "something changed".
- **A change during learning is learned as normal.** The first answers become the baseline,
  mistakes included. Measured in the scorecard: a prompt change after 20 answers was absorbed
  with or without version metadata.
- **A topic it never saw while learning gets flagged.** That is a real change for the bot, but
  maybe one you expected (it happened to the demo bot: its fixture was posted grouped by topic).
- **Free tier, so it's slow to wake.** Outside 09:00–21:00 IST the API sleeps and the first
  request takes about a minute.
- **Caps.** One investigation per bot per 30 minutes and 20 per day in total (Groq free:
  200K tokens/day). Registration: 5 per IP per hour, 50 per day; bots with 0 answers after 7
  days are deleted.
- **English only.** The embedding model is English; Hinglish answers are not evaluated yet.
- **Small evidence.** The scorecard is one seed of generated fixtures, and the demo bot is
  fictional. Real traffic is the next test — see below.

## Try it on your bot

Have a bot with real traffic? Send me 50 answers (anonymised) and I'll send back what it finds:
[open an issue](https://github.com/KinjalGoswami68/-SentinelAI/issues/new?title=50+answers).
Issues are public, so never paste customer data or keys.

## Run locally

```bash
pip install -r requirements.txt          # Python 3.14 (.python-version)
python scripts/fetch_model.py            # downloads the embedding model once
# .env: DATABASE_URL (Postgres), optional SLACK_WEBHOOK_URL, GROQ_API_KEY
CORS_ORIGINS=http://localhost:5173 python -m uvicorn main:app --port 8000
cd web && npm ci && VITE_API_BASE=http://127.0.0.1:8000 npm run dev    # site + app on :5173
```

Tests: `python -m unittest discover -s tests`. Live end-to-end check: `python test_real.py`.

## Licence

MIT. Built by Kinjal Goswami.

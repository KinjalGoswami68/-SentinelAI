# Launch posts

Drafts in my voice. Every number here is from the repo (docs/SCORECARD.md,
docs/embeddings_eval.md, docs/detection_scorecard.md, git history). Links use the new name
(ShiftLens); they work once the GitHub repo is renamed. X: @getshiftlens.

- site: https://kinjalgoswami68.github.io/ShiftLens/
- demo: https://kinjalgoswami68.github.io/ShiftLens/app/demo/
- repo: https://github.com/KinjalGoswami68/ShiftLens

---

## LinkedIn

i built a monitoring tool for chatbots and the first version was mostly wrong.

back in june i used tf-idf to compare answers. turns out tf-idf scored two paraphrases of the
same answer at 0.03 and a completely different answer at 0.137. so it couldn't tell "same
thing, different words" from "different thing". i only found that out in november when i
actually measured it.

so i rebuilt it:
- answers are embedded with bge-small, and each bot learns its own normal before it alerts
- detection is plain math (nearest neighbours + cusum), no llm, no cost per answer
- when something changes, one llm call writes the incident note, and every claim has to cite
  evidence ids

the part i like most: on a test where the bot started refusing (0% to 90% refusals), the note
said the cause was "unknown", confidence 0.30. it didn't invent a reason. the refusals were
real, the cause wasn't in the data, and it said so.

scorecard on 8 scripted scenarios: cause right 7 of 8, 0 false incidents on normal traffic. the
miss: a topic shift got caught but labelled "unknown". one seed, so these are single runs.

everything runs on free tiers. render, supabase, groq, github pages. ₹0.

if you have a bot with real traffic, send me 50 answers and i'll send back what it finds.

demo: https://kinjalgoswami68.github.io/ShiftLens/app/demo/
code: https://github.com/KinjalGoswami68/ShiftLens

---

## X thread

1/
built a thing that watches your chatbot's answers and tells you when they change. not
"is this answer true", just "your bot is behaving differently and here's why". free tier
everything. thread on what broke first 👇

2/
v1 in june used tf-idf. in november i measured it: two paraphrases of the same answer scored
0.03, a totally different answer scored 0.137. it literally couldn't tell them apart. switched
to bge-small embeddings.

3/
then the infra. /ingest was returning 500s because every query opened its own db connection
and supabase free gives you 15. the 16th just gets refused. fixed it with one connection per
request + a 503 that tells the client to retry.

4/
render's free instance once didn't wake up at all. no logs, connection reset, nothing on the
status page. an empty commit fixed it. now every deploy is checked from github actions, not
from my laptop.

5/
github cron also ran my keep-alive 5 times in 2 days instead of every 10 min. moved it to
cron-job.org and put the demo traffic inside the service.

6/
the investigation is one llm call (gpt-oss-120b on groq free). it has to cite evidence ids,
and it can lower its confidence but never raise it. on a refusal spike it said "unknown,
0.30" instead of making up a cause. that's the behaviour i wanted.

7/
scorecard, 8 scripted scenarios: cause right 7 of 8, 0 false incidents on normal traffic,
prompt change caught after 10 answers. miss: topic shift caught but called "unknown". one
seed, so small numbers.

8/
demo (fictional support bot, real fixture traffic): https://kinjalgoswami68.github.io/ShiftLens/app/demo/
code: https://github.com/KinjalGoswami68/ShiftLens
got a bot with real traffic? send me 50 answers and i'll tell you what it finds.

---

## Reddit (r/SideProject)

**title:** i built a free monitor that tells you when your chatbot's answers change (and it
admits when it doesn't know why)

hey. side project i started in june and rebuilt properly over the last few days.

**what it does:** you send it each question + answer your bot gives. it learns what normal
looks like for your bot, and when answers drift (new prompt, model swap, refusals going up)
it opens one incident and sends one slack message with what changed, probably why, and the
request ids that prove it.

**what it doesn't do:** it doesn't fact-check single answers. it catches when behaviour
changes.

**mistakes i made, in order:**
- used tf-idf for similarity. measured it later: paraphrases scored lower than unrelated
  answers. replaced with bge-small embeddings.
- opened a db connection per query on supabase free (15 connections max). 500s everywhere.
- trusted github cron for keep-alive. it ran 5 times in 2 days.
- the demo bot had a "false" incident on its own normal traffic. turned out i fed its fixture
  in topic order, so it learned orders and payments first and then saw laptop questions as
  new. the incident note said "new product topics", which was actually right.
- found a bug where incidents closed 30 min after the first signal even if signals kept
  coming. caught it live on the demo.

**numbers (honest version):** 8 scripted scenarios, cause right 7 of 8, 0 false incidents on
normal traffic. the miss was a topic shift it caught but labelled "unknown". one seed of
generated data, so it needs real traffic next.

**stack:** fastapi on render free, supabase postgres, fastembed (bge-small), groq free for the
one llm call per incident, github pages for the site. costs ₹0.

demo: https://kinjalgoswami68.github.io/ShiftLens/app/demo/
code: https://github.com/KinjalGoswami68/ShiftLens

if you run a bot with real users and want to try it, send me 50 anonymised answers and i'll
send back what it finds. feedback on the scorecard welcome, especially if you think the
scenarios are too easy.

---

## dev.to build log

**title:** building a chatbot monitor on free tiers: what broke, what i measured, what's still weak

**tags:** python, llm, monitoring, sideproject

### why

if you ship a chatbot, the answers can change without anyone touching your code. someone edits
the prompt, the provider swaps a model, the bot starts refusing more. your uptime monitor says
everything is fine. i wanted something that notices the *answers* changed and tells you why.

### v1 (june): it looked done

first version: tf-idf vectors, an isolation forest, cusum, a streamlit dashboard. it deployed
(after a detour through railway, then render), it switched to supabase in july, and the readme
said "production complete". it wasn't.

### november: measuring it properly

i wrote 20 answer pairs. 10 paraphrases of the same support answer, 10 that really change it
(different topic, refusal, empty answer, switch to hinglish). a good similarity measure scores
every paraphrase above every change.

tf-idf scored one paraphrase pair at **0.03** and one changed pair at **0.137**. so it ranked a
change as more similar than a paraphrase. `bge-small-en-v1.5` separated all 20 cleanly.
`multilingual-e5-small` did too, but it didn't fit in render's 512 mb, so bge it is.

the old detector also flagged **5 of 60** ordinary answers. the new one (distance to the 5th
nearest normal answer, threshold learned per bot) flags **1 of 60**.

### the infra fights

- **supabase free = 15 connections** in session mode. i measured it: 15 held connections
  work, the 16th is refused immediately. my code opened one per query, so `/ingest` returned
  500s under very little load. now it's one connection per request, and a db blip returns 503
  with retry-after instead of a stack trace.
- **render's free instance didn't wake up once.** no logs, connection reset, no incident on the
  status page. an empty commit to redeploy fixed it. after that, every push runs a github
  actions job that waits for the new commit on render, runs an end-to-end test against the live
  api and watches for restarts for 12 minutes.
- **github cron is best effort.** my "every 10 minutes" keep-alive ran 5 times in about 2 days.
  keep-alive moved to cron-job.org, demo traffic moved inside the service.
- **memory.** importing scikit-learn cost 118 mb of the 512, just for the isolation forest. it
  left the request path (it's still an offline candidate i'm evaluating). 475 mb → 318 mb.

### the investigation (the one llm call)

when an incident opens, code does the evidence work first: compare stats before vs after,
cluster the changed answers, line up prompt/model version changes, look for similar past
incidents. rules pick a cause and a confidence. then one call to `gpt-oss-120b` on groq's free
tier writes the note as strict json.

the rules i care about:
- every claim cites an evidence id. ids it invents get dropped.
- the llm can lower the confidence, never raise it.
- if groq is down or rate-limited, you get a rules-only note instead of nothing.

on a test where refusals jumped from 0% to 90%, the note said: cause **unknown**, confidence
**0.30**, refusal rate up, answers ~32% shorter, same question topics as before, check the
refusal handling. it didn't make up a story. that's the bar.

### the scorecard

8 scripted scenarios (prompt change, model swap, topic shift, a control, a harmless version
bump, a silent change with no version info, a double change, a slow drift). 300 normal answers,
then 100 changed ones, through the same code that runs in production.

- cause right: **7 of 8**
- false incidents on normal traffic: **0 of 2**
- prompt change caught after **10 answers** (when the bot sends `prompt_version`)
- the miss: the topic shift was caught after 4 answers, but the note said "unknown"

also measured, and not great: if the prompt changes while the bot is still learning, the new
behaviour gets learned as normal. with or without version info. that's in the limits now.

it's one seed of generated data. it needs real traffic.

### things that surprised me on the live demo

the demo bot opened a real incident on its own "normal" traffic. i'd fed its fixture file in
order, and the file was grouped by topic, so it finished learning on orders and payments and then
saw laptop and tv questions as new. the note said "new product topics not seen in baseline",
which was correct. i shuffled the feed and let it learn again.

the same afternoon i found that incidents were closing 30 minutes after the *first* signal even
when signals kept coming: a new signal on an open incident was never saved. small fix, plus a
test so it stays fixed.

### cost

₹0. render free, supabase free, groq free, github pages + actions on a public repo,
cron-job.org. the rule was: hitting a limit has to degrade, never bill.

### try it

demo: https://kinjalgoswami68.github.io/ShiftLens/app/demo/
code: https://github.com/KinjalGoswami68/ShiftLens

if you run a bot with real users, send me 50 anonymised answers and i'll send back what it
finds. that's the test i can't fake.

# Akin

**A two-player conversation game about how each of you sees romantic relationships, played on a first date or
between friends.** Two people share one device (usually a laptop) and pass it back and forth. Each round shows a short scenario; both players answer privately; then the app reveals
both answers and gives the pair one conversation prompt written for *those two answers*. Behind the scenes, AI
agents work out each player's relationship type on three axes, and at the end the app reveals each type with
evidence quoted from that player's own answers.

| Axis | Sides |
|---|---|
| One vs. Many | Is there one soulmate, or could you build a great life with many people? |
| Found vs. Built | Is a good relationship found, or built? |
| Home vs. Engine | Does the right person feel like home, or like an engine propelling you somewhere new? |

The three axes combine into 8 types: The Soulmate, The Stargazer, The Anchor, The Ride-or-Die, The
Hearthseeker, The Wayfarer, The Craftsman, and The Co-Founder.

---

## Run it (about 5 minutes)

You need **Python 3.10+** and a **`PORTKEY_API_KEY`** (the course key). The app only uses `gpt-6-luna`.

**1. Get the code**

```bash
git clone https://github.com/emchwo/akin.git akin
cd akin
```

**2. Add your key.** Copy `.env.example` to a file named `.env` and paste the key after `PORTKEY_API_KEY=`.
The app looks for `.env` in **two places, in this order**:

1. the repo folder itself (`akin/.env`)
2. the folder one level above it (`akin/../.env`)

So a single `.env` in the parent folder works for any project cloned next to it.

**3. Install and start**

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

**4. Play.** Open **http://127.0.0.1:5050** in a browser. The terminal prints which `.env` it loaded.
If it can't find a key, it stops right away and tells you both places it looked.

To stop the server, press `Ctrl+C`. It runs as a single process (no debug reloader), so nothing is left behind.

### Pages

| URL | What it is |
|---|---|
| `/` | The game |
| `/results` | User-testing summary: % "Yes, feels right", % "learned something new about the other person", % would play again, per setting (first date or friends), plus game length and cost |
| `/icons` | Test page showing all 8 type icons drawing themselves, at 112, 48, and 24 px |

### Safety tests

```bash
python tests/safety_tests.py            # 33 checks; 4 real model calls (< $0.001)
python tests/safety_tests.py --offline  # no model calls at all
```

Each check prints `HELD` or `FAILED`, and each defense that triggers writes a line to `logs/safety.jsonl`.

---

## How it works

```
             ┌──────────── Planner (plain code) ────────────┐
             │ scores each player on each axis; picks the     │
             │ least-certain axis; 6 rounds, up to 8          │
             └───────────────┬───────────────────────────────┘
                             │ target axis
                             ▼
   Scenario Writer (LLM, JSON) ──draft──▶ code screen ──▶ Checker (LLM, JSON)
            ▲                                                   │
            └──────── reason for rejection (max 2 rewrites) ◀───┘
                             │ approved (or a hand-written fallback)
                             ▼
        players answer privately ──▶ Prompt Writer (LLM) ──▶ reveal + conversation prompt
                             │
                             ▼ after the last round
           Reveal Writer (LLM, JSON) ──▶ code check: every cited round must exist ──▶ end screen
```

| Agent | Kind | Job |
|---|---|---|
| **Planner** | Plain Python (`planner.py`) | Tracks a +1/−1 score per player per axis. Picks the axis with the least certainty across both players. Rounds 1–6 ask each axis exactly twice, so every axis can be decided by round 6. After round 6 the players choose to end or keep going; rounds 7–8 go to whichever axis is still closest to the middle. Hard cap: 8 rounds. Decides the final types, so the model never does. |
| **Scenario Writer** | `gpt-6-luna`, structured JSON | Writes one scenario + two options for the target axis, avoiding earlier scenarios. Code then validates it and randomly swaps option order. |
| **Checker** | `gpt-6-luna`, structured JSON | Reviews every draft before players see it: PG for strangers, really tests the axis, option→side mapping correct, doesn't give away what it measures. Rejections go back to the writer with the reason (max 2 rewrites, then a hand-written fallback). |
| **Prompt Writer** | `gpt-6-luna`, text | Writes one direct conversation prompt for this scenario and these two answers. Code rejects "match"/"split". |
| **Reveal Writer** | `gpt-6-luna`, structured JSON | Writes each player's evidence and the "Where you'd click / clash" card. Code checks that every cited round (in the list *and* in the text) actually happened; otherwise it retries once, then falls back to code-written evidence. |

**Speed:** the next scenario is written in the background while the players talk about the current reveal.
From round 6 on, the final reveal is prepared too, so "End now" and "Keep going" both respond at once.

**Prompt caching:** every system prompt starts with the same static guide (`prompts.py`), and all per-call
data goes in the user message, so most input tokens are served from cache (90% across 6 benchmark games; caching cut cost per game by 58%).

## Token tracking and cost

* Every LLM call records input, cached input, cache-write, and output tokens, tagged by agent (`llm.py`).
* The **Stats** button (bottom right) shows tokens and estimated cost per agent for the current game.
* Each finished game appends one line to `logs/games.jsonl`: tokens by agent, rounds, Checker rejections,
  fallbacks, cost. No player names.
* Prices used (per 1M tokens, gpt-6-luna): input $0.10, cached input $0.01, cache write $0.125, output $0.50.

## Safety defenses

| Risk | Defense | Where |
|---|---|---|
| Prompt injection | Names are the only free text: max 20 characters and 2 words; anything that isn't a letter, space, hyphen, apostrophe, or period is stripped; code/markup characters or instruction-like words ("ignore", "system", "prompt"...) are rejected. **Names are never sent to the model at all.** Agents only see "Player A"/"Player B"; real names are swapped in afterwards. | `safety.py`, `app.py` |
| Unsafe content | A code screen blocks obvious non-PG terms, then the Checker reviews every scenario. Anything that fails 3 times is replaced by a hand-written, pre-checked scenario. | `agents.py` |
| Invented facts | Evidence must cite real round numbers; validated in code. | `agents.py` |
| Overspending | Hard caps: 40 LLM calls per game (2 held back for the reveal), 600 per server run, and an output-token cap on every call. When a cap is hit, the game ends with a friendly message and still shows results. | `llm.py` |
| Infinite loops | Retry limits on every agent; 8-round cap. | `agents.py`, `planner.py` |

Every defense that triggers is logged to `logs/safety.jsonl` with what it caught (rejected names are logged
by reason and length only, never the text).

## Playing on one device

Each round, the first player takes the device and turns the screen away from the second player, answers,
then hands it over; the second player does the same. Every hand-off screen says who should hold the device.
A misclick can be fixed at any point: each answer screen has a Back button, the hand-off screens let the
last player change their answer, and the reveal screen can take back the whole round and replay it.

## User testing

All "Does this feel right?" and feedback answers ("Did you learn something new about the other person?",
"Would you play again with someone else?") are saved without names to `logs/feedback.jsonl`, tagged by
setting, and summarized at `/results` alongside game length and cost from `logs/games.jsonl`.

## Report, slides, and measured numbers

`report.html` and `slides.html` (repo root) are self-contained: images are embedded, so they open by
double-clicking. Every number in them is computed from real runs:

```bash
python tools/benchmark.py                       # full games vs. the real model -> logs/benchmark.jsonl
python tools/benchmark.py --games 3 --no-cache  # same with prompt caching defeated, to measure savings
python tests/safety_tests.py > report/safety_test_output.txt
python report/build.py                          # fills report/*_src.html, embeds report/img/, writes the HTML
```

`report/data/` keeps a copy of the benchmark games and the traced game the report cites (no player names).
Screenshots come from `report/screenshots.py` (Playwright with your installed Chrome) against a server started
with `AKIN_LOG_TAG=screenshots AKIN_TRACE=1 PORT=5077 python app.py`, so those runs never count as real sessions.

## Project layout

```
app.py            Flask routes
planner.py        Planner agent (plain code)
agents.py         Scenario Writer, Checker, Prompt Writer, Reveal Writer + validation and fallbacks
prompts.py        Static system prompts (shared guide first, for prompt caching)
llm.py            The one place that calls the model: caps, token tracking, .env lookup
game.py           In-memory game state, games.jsonl logging
game_data.py      Axes, 8 types, fallback scenarios
safety.py         Name filter, safety log
results.py        /results summaries
static/           app.js (screens), icons.js (8 SVG icons + draw animation), style.css
templates/        index.html, results.html, icons.html
tests/            safety_tests.py
tools/            benchmark.py (measured token use and cost)
report/           report and slide sources, screenshots, build script, measured data
logs/             created at runtime (git-ignored)
```

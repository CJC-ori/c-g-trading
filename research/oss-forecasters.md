# OSS AI forecasting bots, mechanically dissected

**Research agent output — 2026-08-11.** All four repos were cloned (shallow, into the session
scratchpad, not this repo) and read directly; file references below are to actual code, with GitHub
URLs for citation. License discipline: **Panshul42/Forecasting_Bot_Q2 is AGPL-3.0** and
**llm_forecasting + metac-bot-template have no license** — from those three, reimplement *ideas
only*, never copy code. **forecasting-tools is MIT** (LICENSE: "MIT License, Copyright (c) 2024
CodexVeritas") — safe to depend on or fork.

| Repo | License | Last commit (at clone) | What it is |
|---|---|---|---|
| [Panshul42/Forecasting_Bot_Q2](https://github.com/Panshul42/Forecasting_Bot_Q2) | AGPL-3.0 | 2025-06-15 (frozen) | Q2 2025 Metaculus AI Benchmark winner, full pipeline |
| [dannyallover/llm_forecasting](https://github.com/dannyallover/llm_forecasting) | none | 2026-04-19 | Code for Halawi et al., *Approaching Human-Level Forecasting with Language Models* (NeurIPS 2024, [arXiv:2402.18563](https://arxiv.org/abs/2402.18563)) |
| [Metaculus/forecasting-tools](https://github.com/Metaculus/forecasting-tools) | MIT | 2026-08-09 (active) | Framework: bots, LLM wrapper, cost manager, benchmarker, prompt optimizer |
| [Metaculus/metac-bot-template](https://github.com/Metaculus/metac-bot-template) | none | 2026-06-04 | 30-min quickstart wrapper around forecasting-tools + GitHub Actions scheduler |

---

## 1. Panshul42/Forecasting_Bot_Q2 (the Q2 2025 tournament winner)

### Retrieval stack
Source: [`Bot/search.py`](https://github.com/Panshul42/Forecasting_Bot_Q2/blob/main/Bot/search.py).

- **Serper** (Google + Google News wrapper): `num: 20` results per query, keep ≤12 after
  date-filtering, scrape with a custom `FastContentExtractor` (BeautifulSoup + trafilatura +
  readability fallbacks), keep the first **3** articles with ≥100 words, truncate each to 8,000
  chars, and summarize each with an o3 call ("assistant to a superforecaster" summarizer prompt).
- **AskNews**: 2 calls per query — 8 articles `strategy="latest news"` + 8 articles
  `strategy="news knowledge"`.
- **Perplexity `sonar-deep-research`** (legacy path, 800s timeout) — later replaced by a
  home-grown **agentic search loop**: up to **7 iterations**, each iteration an o3 call that emits
  up to **5 new Serper queries**, reads raw scraped content, rewrites a running ~1000-word
  analysis, and *signals completion by omitting the "Search queries:" section* (regex-detected).
- Date discipline is **partial**: `google_search()` drops results dated after
  `question.resolution_date`, but AskNews and the agentic loop have **no date filter** — a
  leakage hazard if reused for backtesting.

Per binary question, roughly: 2 search-planning calls → ~6 planned queries → ~4 Serper searches +
~6 article-summarization calls + 1 AskNews (2 API hits) + 1 agentic search (up to 7 o3 calls and
35 Serper hits) → then 10 large reasoning calls (5 forecasters × 2 stages). Order of magnitude
**~25–40 LLM calls and ~10–40 search API hits per question**.

### Prompt architecture
Source: [`Bot/prompts.py`](https://github.com/Panshul42/Forecasting_Bot_Q2/blob/main/Bot/prompts.py).
The distinctive move is **two separate research tracks feeding two sequential reasoning stages**:

1. `*_PROMPT_historical` → outside-view research ("the most relevant historical context needed to
   generate an outside view")
2. `*_PROMPT_current` → inside-view research (latest news)
3. `PROMPT_1` = outside-view forecast: "Reference class analysis: Identify a few possible
   reference classes and evaluate respective suitabilities"
4. `PROMPT_2` = final inside-view forecast, with an explicit **evidence-weighing rubric**
   ("Strong evidence (can warrant relatively large prediction shifts): Multiple independent,
   reliable and identifiable... sources confirming same direction / Direct causal mechanisms
   clearly established...") and a **forecasting checklist** run *inside* the reasoning:

> "3. Consistency check (write a single line) — '{{your prediction}} out of 100 times,
> {{resolution criteria}} happens.' Does this make sense...
> 5. Blind-spot statement — Name the one scenario most likely to make your forecast look silly in
> hindsight...
> 6. Status quo outcome — The world changes slowly most of the time."

Odds-awareness is prompted directly: "Small differences in probabilities can be significant: 90%
is a 9:1 odds and 99% is a 99:1 odds." Base-rate anchoring: "Check that your final prediction
distribution genuinely is rooted to this base rate... **Outside first, usually.**"

There are also **Monte Carlo prompt variants** (`*_PROMPT_MONTE_CARLO`): the LLM designs a 2–4
variable simulation and emits an executable NumPy script inside `<python>` tags ("Simulate at
least 100,000 iterations... print the final probability"), which the harness runs. Numeric
questions get a **bound hint** injected: "The answer is expected to be above {lower} and below
{upper}. Think carefully, and reconsider your sources, if your projections are outside this range."

### Ensembling / aggregation math
Source: [`Bot/binary.py`](https://github.com/Panshul42/Forecasting_Bot_Q2/blob/main/Bot/binary.py),
[`Bot/numeric.py`](https://github.com/Panshul42/Forecasting_Bot_Q2/blob/main/Bot/numeric.py).

- Binary: 5 forecasters — in code: Claude Sonnet ×2, o4-mini ×1, **o3 ×2** — weighted mean with
  `weights = [1, 1, 1, 2, 2]`; per-forecaster clamp to [1%, 99%], final clamp to [0.001, 0.999].
  (The README claims 2×o4-mini + 1×o3 double-weighted; the code disagrees — trust the code.)
- Numeric: each forecaster emits ~10 percentiles → interpolated to a 201-point CDF per forecaster
  → **weighted average of CDFs** (same weights) with Metaculus constraints enforced (min step
  5e-5, max step 0.59, open-bound tails pinned to 0.001/0.999).
- Multiple choice: `np.average(probs_matrix, axis=0, weights=[1,1,1,2,2])`.
- On top, `main.py` sets `NUM_RUNS_PER_QUESTION = 5  # The median forecast is taken between
  NUM_RUNS_PER_QUESTION runs` — a median over 5 full pipeline runs.

### Model choice + cost
`claude-sonnet-4-20250514` with extended thinking (16k budget, cached system prompt), OpenAI `o3`
and `o4-mini` ([`Bot/llm_calls.py`](https://github.com/Panshul42/Forecasting_Bot_Q2/blob/main/Bot/llm_calls.py)).
Only the agentic search tracks its own cost (hardcoded o3 pricing $1.10/$4.40 per M tokens);
total per-question cost is **not tracked**. Given ~10 large o3/Sonnet reasoning calls + retrieval
+ (×5 runs at the tournament setting), a realistic estimate is **$1–5+ per question at full
tournament settings** — *unverified, my estimate from call counts*.

### Calibration tricks
Clamps, status-quo nudging, checklist self-verification during reasoning, evidence-weight rubric,
outside-view-first anchoring, mixed-model ensemble with the strongest reasoner double-weighted,
median-of-5-runs.

### Known weaknesses (visible in the code)
- **Regex output parsing everywhere** ("a regex looking for 'Probability:' will be used to extract
  your answer") with escalating in-prompt format pleading — the checklists exist because parsing
  kept breaking.
- Partial date filtering (see above) → not point-in-time safe.
- README/code drift (ensemble composition, "Claude 3.7" vs sonnet-4 in code).
- No end-to-end cost accounting; unbounded agentic-search fan-out.
- And the repo's own admission, confirming the news-aggregator hypothesis — README "Future
  Actionables": **"Integration of structured numerical data sources (e.g., economic indicators,
  polls)"** ([README](https://github.com/Panshul42/Forecasting_Bot_Q2/blob/main/README.md)).

---

## 2. dannyallover/llm_forecasting (Halawi et al., NeurIPS 2024)

### Retrieval stack
Source: [`llm_forecasting/information_retrieval.py`](https://github.com/dannyallover/llm_forecasting/blob/main/llm_forecasting/information_retrieval.py),
[`config/constants.py`](https://github.com/dannyallover/llm_forecasting/blob/main/llm_forecasting/config/constants.py).

- **NewsCatcher API** + **Google News (gnews lib)**; LM-generated queries (default
  `NUM_SEARCH_QUERY_KEYWORDS: 3` per template × 2 prompt templates), **5 articles per query**,
  dedup, site whitelist/blacklist.
- **Wikipedia point-in-time**: `get_wikipedia_article_on_date(title, date)` fetches the page
  *revision as of a date* via `oldid` — the one genuinely point-in-time-safe source in any of
  these repos, because the whole system is built to make **simulated forecasts on already-resolved
  questions** (`retrieval_dates` threads through every function).
- Funnel: optional **embedding pre-filter** (cosine sim vs question, threshold 0.32, or 0.36 if
  ≥100 articles) → **GPT-3.5 relevance rating 1–6** per article, keep ≥4 ("If the text content is
  an error message about JavaScript, paywall, cookies... output a score of 1") → GPT-3.5
  summarization → top-N (~20) summaries into the reasoning context.

### Prompt architecture
Source: [`prompts/base_reasoning.py`](https://github.com/dannyallover/llm_forecasting/blob/main/llm_forecasting/prompts/base_reasoning.py).
A library of scratchpad templates, ensembled *across prompts* as well as models:

> "1. Given the above question, rephrase and expand it to help you do better answering...
> 2. Provide a few reasons why the answer might be no. Rate the strength of each reason...
> 4. Aggregate your considerations. Think like a superforecaster (e.g. Nate Silver)...
> 6. Evaluate whether your calculated probability is excessively confident or not confident
> enough... 7. Output your final prediction (a number between 0 and 1) with an asterisk at the
> beginning and end of the decimal."

One variant answers in **probability words** mapped to bins ("No (0%-10%), Extremely Unlikely
(10%-20%)...") instead of numbers — a token-space calibration hack.

### Ensembling / aggregation math
Source: [`llm_forecasting/ensemble.py`](https://github.com/dannyallover/llm_forecasting/blob/main/llm_forecasting/ensemble.py).
The most explicit aggregation menu of the four repos:

- `mean`, `vote-or-median` (median for probabilities, majority vote for tokens), `weighted-mean`,
- `meta`: feed *all base reasonings* + retrieved info to a meta-model (default gpt-4, temp 0.2)
  that writes its own aggregated forecast,
- `calculate_normalized_weighted_trimmed_mean`: find the prediction farthest from the median,
  **halve its weight**, redistribute the saved weight equally, weighted mean.
- Parse failure or out-of-range ⇒ **defaults to 0.5** (fine for tournament Brier, catastrophic if
  a trading system treats 0.5 as signal).

### Model choice + cost
Era models: `gpt-4-1106-preview` base reasoning at **temperature 1.0** (diversity), `claude-2.1`,
`gpt-3.5-turbo-1106` for query-gen/rank/summarize, `gpt-4` meta-aggregation at temp 0.2. Also a
**fine-tuning pipeline** (`scripts/fine_tune/`) that trains on the system's own reasonings from
questions where it beat the crowd (the paper's self-supervised trick). No cost tracking in code;
the paper's pipeline is cheap-stage-heavy by design. Evaluation is Brier vs
`community_pred_at_retrieval` ([`evaluation.py`](https://github.com/dannyallover/llm_forecasting/blob/main/llm_forecasting/evaluation.py)).

### Weaknesses
2023–24 models; NewsCatcher is a paid dependency; the 0.5 fallback; news+Wikipedia only; no
license.

---

## 3. Metaculus/forecasting-tools (MIT — our recommended base)

### Architecture
`ForecastBot` runs `run_research` × `research_reports_per_question`, then each forecast prompt ×
`predictions_per_research_report`, then aggregates
([`forecast_bots/forecast_bot.py`](https://github.com/Metaculus/forecasting-tools/blob/main/forecasting_tools/forecast_bots/forecast_bot.py)).
**Aggregation is deliberately boring**: binary = `statistics.median(predictions)`
([`binary_report.py`](https://github.com/Metaculus/forecasting-tools/blob/main/forecasting_tools/data_models/binary_report.py#L79)),
multiple-choice = per-option arithmetic mean, numeric = median of the 201-point CDFs. `MainBot` =
AskNews research + **GPT-5 (reasoning_effort=high, temp 0.3) × 5 predictions, median**, gpt-4o
summarizer ([`main_bot.py`](https://github.com/Metaculus/forecasting-tools/blob/main/forecasting_tools/forecast_bots/main_bot.py)).

### Retrieval stack
Pluggable `researcher` slot: **AskNews** (`news-summaries` or `deep-research` at
low/medium/high depth — as of Feb 2026 Metaculus runs
`asknews/deep-research/high-depth/claude-opus-4-6`), **SmartSearcher** = Exa search (2 searches ×
10 sites, 15 highlight-quotes evaluated) + LLM synthesis, **Perplexity**
(`sonar-deep-research`, `search_context_size: high`), Gemini grounding, or any plain LLM
([`template_bot_2026_summer.py`](https://github.com/Metaculus/forecasting-tools/blob/main/forecasting_tools/forecast_bots/official_bots/template_bot_2026_summer.py),
[`run_bots.py`](https://github.com/Metaculus/forecasting-tools/blob/main/run_bots.py)).

### Prompt architecture
The famous minimal Metaculus template (also the metac-bot-template prompt):

> "You are a professional forecaster interviewing for a job... Before answering you write:
> (a) The time left until the outcome to the question is known. (b) The status quo outcome if
> nothing changed. (c) A brief description of a scenario that results in a No outcome. (d) A brief
> description of a scenario that results in a Yes outcome. You write your rationale remembering
> that good forecasters put extra weight on the status quo outcome since the world changes slowly
> most of the time."

Output parsing is done by a **separate parser LLM** (`structure_output`, pydantic-typed, with
`num_validation_samples=2`) instead of regex — the single biggest reliability upgrade over the
other repos. Binary clamp [0.01, 0.99].

### Cost per question (actual numbers, from Metaculus's own bot fleet)
[`run_bots.py`](https://github.com/Metaculus/forecasting-tools/blob/main/run_bots.py) carries an
`estimated_cost_per_question` for every bot it runs: gpt-4o ≈ **$0.05**, gpt-4o-mini ≈ **$0.005**,
deepseek-r1 ≈ **$0.039**, sonnet-4 ≈ **$0.25**, gpt-5 ≈ **$0.20** ($0.38 at high reasoning),
opus-class ≈ **$1.50**, plus "guess_at_search_cost = 0.015" per search call, sonar-deep-research ≈
$0.45/call. ("a lot of pricing is probably outdated" — their comment.) This brackets our own
P&L line item: **a competitive question costs $0.05–$0.50; frontier-everything costs $1.50+.**
`MonetaryCostManager` is a context manager with a hard USD limit wired into litellm callbacks.

### Calibration / evaluation machinery
- `Benchmarker` scores bots **against the community prediction** on ~100+ questions, with the
  honest caveat in its docstring: "even with 100 there is ~30% of the 'worse bot' winning if there
  are not large skill differences"
  ([`cp_benchmarking/benchmarker.py`](https://github.com/Metaculus/forecasting-tools/blob/main/forecasting_tools/cp_benchmarking/benchmarker.py)).
- `BotOptimizer` runs an **evolutionary prompt search** (population 20, 5 survivors/iteration, 3
  mutations per survivor, 5 bred prompts) scored by benchmark
  ([`auto_optimizers/bot_optimizer.py`](https://github.com/Metaculus/forecasting-tools/blob/main/forecasting_tools/auto_optimizers/bot_optimizer.py)).
- Experimental agents: `base_rate_researcher`, `estimator`, `niche_list_researcher`, and
  `find_a_dataset.py` (Perplexity + computer-use agent that *downloads and analyzes datasets*) —
  the only gesture toward structured data in any repo, and it's experimental/agentic, not a feed.

### Weaknesses
Everything is Metaculus-question-shaped; API explicitly unstable ("still in an experimental
phase"); benchmarking is vs community prediction, not vs resolution or vs market post-fee; no
trading/market layer at all.

---

## 4. Metaculus/metac-bot-template

A fork-and-go wrapper: `main.py` subclasses forecasting-tools' `ForecastBot` with the identical
"interviewing for a job" prompts; defaults `research_reports_per_question=1`,
`predictions_per_research_report=5`, median aggregation. `main_with_no_framework.py` (1,648 lines)
is the same logic with raw HTTP — the best single-file read for understanding the whole loop.
The operationally interesting part is the **GitHub Actions cron**: "the bot will keep forecasting
on new questions automatically **every 20 minutes**", skipping already-forecasted questions
([README](https://github.com/Metaculus/metac-bot-template/blob/main/README.md)). No license file →
wiring reference only. Nothing novel in retrieval (AskNews/SmartSearcher via the framework),
prompts, or aggregation.

---

## 5. Testing Chris's hypothesis: "sophisticated news aggregators with reasoning scaffolding"

**Where it's true — which is almost everywhere.** The entire *evidence input* of all four systems
is: question text → LLM-written search queries → **news search APIs** (Serper/Google News,
AskNews, NewsCatcher, GNews, Perplexity, Exa) → scrape → summarize → reason. Concretely:

- Panshul42's inputs are exclusively Serper/AskNews/Perplexity article text, and its README's #1
  future actionable is "Integration of structured numerical data sources (e.g., economic
  indicators, polls)" — the author naming exactly the gap Chris hypothesizes.
- llm_forecasting's `information_retrieval.py` imports precisely `gnews`, `newscatcherapi`, and
  the Wikipedia API. Nothing else.
- forecasting-tools' researcher registry is AskNews / Exa / Perplexity / Gemini-grounding — all
  news/web search under different branding.

**Signal sources NONE of them consume** (all verified absent by grep across the four codebases):

- **Poll aggregates** (FiveThirtyEight/Silver Bulletin/RCP/VoteHub APIs or CSVs) — despite
  elections being a huge question category.
- **FEC filings** (fundraising as an election predictor) or any campaign-finance data.
- **Economic time series**: no FRED, no BLS/BEA release APIs, no scheduled-release calendars —
  even though CPI/NFP/GDP questions recur constantly and resolve off exactly these series.
- **Market prices as an input**: none reads Kalshi/Polymarket/Metaculus community odds into the
  prompt as a prior (tournament bots are actually blinded to community prediction by design).
  This is the Bridgewater AIA finding in reverse: model+market ensembles beat both, and these
  bots deliberately amputate the market half.
- Sports/weather models, court dockets, legislative trackers, earnings calendars, county-level
  election-return feeds (the Michigan trade's actual signal).

**Where the hypothesis undersells them.** The scaffolding is not decorative: (1) outside-view
prompts extract *base rates from the model's parametric knowledge*, which is a non-news signal;
(2) Panshul's Monte Carlo code-gen turns the LLM into a distribution builder, not a summarizer;
(3) llm_forecasting's fine-tuning-on-own-wins and the ensemble math are real statistical
machinery; (4) forecasting-tools' `find_a_dataset` computer-use agent is a first crack at
structured data. But as *shipped*, the hypothesis stands: **the differentiated alpha for a
trading bot — structured ground-truth feeds (polls, FEC, FRED/BLS, live returns) and the market
price itself — is absent from every one of these systems.** That's our lane.

---

## 6. The 10 design ideas most worth reimplementing (ideas only from AGPL/unlicensed code)

1. **Two-track research: outside-view vs inside-view.** Separate retrieval passes and reports for
   historical/base-rate context vs current news, with the final forecast forced to anchor on the
   outside view first ("Outside first, usually"). (Panshul42 — reimplement.)
2. **Cheap-stage funnel before expensive reasoning.** Embedding cosine pre-filter → cheap-LLM
   relevance rating 1–6 (keep ≥4, auto-1 for paywall/JS garbage) → cheap-LLM summarization →
   only summaries reach the expensive judge. (llm_forecasting — reimplement.) Directly serves our
   "inference cost is a P&L line item" constraint.
3. **Point-in-time retrieval as a first-class parameter.** Every retrieval function takes
   `retrieval_dates`; Wikipedia fetched by revision `oldid` as-of-date. (llm_forecasting.) This is
   *mandatory* for our contamination-safe backtest of post-Feb-2026 markets.
4. **Ensemble across models AND prompts, aggregate robustly.** Median (forecasting-tools) or the
   trimmed-mean variant (halve the weight of the farthest-from-median prediction, redistribute —
   llm_forecasting); double-weight the strongest reasoner (Panshul's [1,1,1,2,2]). Median of N
   independent runs on top.
5. **Parser-LLM structured output instead of regex.** A separate cheap model converts free-form
   reasoning into a typed pydantic object with validation resampling. (forecasting-tools, MIT —
   just use it.)
6. **The checklist prompt block.** Status-quo weighting, "{{p}} out of 100 times, {{criteria}}
   happens" consistency line, blind-spot statement, resolution-criteria paraphrase to catch
   bait-and-switch. (Panshul42 + Metaculus template — reimplement the ideas.)
7. **Monte Carlo code-gen for distributional questions.** LLM designs a 2–4 variable simulation,
   emits NumPy, harness executes it for percentiles. (Panshul42.) Natural fit for Kalshi
   margin-bracket markets — the exact Michigan "wins by ≥15 at 62%" mispricing.
8. **Per-question cost ledger with hard caps.** `MonetaryCostManager`-style context manager +
   an `estimated_cost_per_question` registry per strategy variant. (forecasting-tools, MIT.)
   Charge it in the backtest as negative P&L.
9. **Agentic search with an explicit stop signal and iteration cap.** Iterative
   query→read→rewrite-analysis loop (≤7 steps, ≤5 queries/step) that halts by *omitting* the
   next-queries block — bounded depth, self-terminating. (Panshul42 — reimplement with a cost cap.)
10. **Evolutionary prompt optimization against a frozen benchmark.** Population/mutate/breed
    search over prompts scored on held-out resolved questions (forecasting-tools `BotOptimizer`,
    MIT) — but score ours on **post-fee simulated P&L vs market**, not distance from community.

## 7. The 5 mistakes to avoid

1. **Regex-parsing free-form LLM output and pleading for format in the prompt.** Panshul's prompts
   are ~30% format-verification checklists because parsing kept breaking; llm_forecasting
   **defaults to 0.5 on parse failure**. In a trading system a silent 0.5 is a position. Rule:
   parse failure ⇒ no trade, loudly logged.
2. **Leaky retrieval.** Panshul date-filters only Google results (AskNews and the agentic loop are
   unfiltered); backtests built on that are contaminated. Every source must enforce
   published-before-t, or the strategy is untestable (our constraint #2).
3. **Uniform probability clamps tuned for tournaments.** [0.01, 0.99] / [1, 99]% clamps and
   "leave moderate probability on most options" hedging optimize log-score, but they destroy
   exactly the tails where prediction markets pay (1–3¢ longshots, 97–99¢ favorites — the
   El-Sayed NO at 1.5¢ trade lives outside the clamp).
4. **Optimizing against the community/crowd instead of post-fee P&L.** Every repo benchmarks vs
   Metaculus community prediction; none models fees, spread, depth, or fill probability. A bot
   that matches the crowd exactly scores well and earns $0 (worse: minus fees). Our benchmark must
   be resolution-scored AND fee/fill-adjusted from day one.
5. **Unbounded fan-out with no cost accounting.** Panshul's pipeline can fire ~25–40 LLM calls +
   ~40 search hits per question, ×5 runs, with no budget enforcement or total cost tracking. At
   forecasting-tools' own numbers ($0.20–$1.50/question for frontier models) that's fine for a
   tournament and fatal for $1–3k-depth markets: cap per-question spend and route cheap models to
   every stage except the final judge.

---

### Source list
- https://github.com/Panshul42/Forecasting_Bot_Q2 (README, LICENSE, `Bot/{search,prompts,binary,numeric,multiple_choice,llm_calls,main,benchmark}.py`)
- https://github.com/dannyallover/llm_forecasting (README, `llm_forecasting/{ensemble,information_retrieval,ranking,evaluation}.py`, `config/constants.py`, `prompts/*.py`, `scripts/fine_tune/`, `scripts/training_data/`)
- https://github.com/Metaculus/forecasting-tools (LICENSE, README, `run_bots.py`, `forecasting_tools/forecast_bots/{forecast_bot,main_bot}.py`, `official_bots/template_bot_2026_summer.py`, `data_models/{binary_report,multiple_choice_report,numeric_report}.py`, `cp_benchmarking/benchmarker.py`, `auto_optimizers/bot_optimizer.py`, `agents_and_tools/research/{smart_searcher,find_a_dataset}.py`)
- https://github.com/Metaculus/metac-bot-template (README, `main.py`, `main_with_no_framework.py`)
- Paper for llm_forecasting: https://arxiv.org/abs/2402.18563 (Halawi et al., NeurIPS 2024)
- Q2 2025 tournament context (Panshul42 = winner claim, cross-referenced from this repo's `docs/prior-art.md`): https://www.lesswrong.com/posts/Surnjh8A4WjgtQTkZ/q2-ai-benchmark-results-pros-maintain-clear-lead — *winner claim not independently re-verified in this session*.

**Flagged as unverified:** Panshul42 per-question total cost (estimated from call counts, not
measured); the LessWrong-sourced claim that Panshul42 won Q2 2025 (taken from `docs/prior-art.md`
which did fetch it); forecasting-tools' own cost numbers carry their maintainer caveat "a lot of
pricing is probably outdated."

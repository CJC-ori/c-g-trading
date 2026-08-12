# FutureSearch deep-dive

*Compiled 2026-08-11 by a Claude research agent. Every load-bearing claim cites the URL it
was fetched from on that date. Claims I could not verify are flagged **[UNVERIFIED]**.
Numbers I computed myself from their published data are flagged **[derived]** and the
computation is shown so you can re-run it.*

**TL;DR for the engineering agents.** FutureSearch is the closest public analogue to what
we're building, and they publish enough to (a) copy their market-selection filter verbatim,
(b) copy their forecaster prompt and 6-agent research decomposition verbatim, (c) grade
ourselves against two free datasets — their 153-question Feb 2026 Kalshi forecast dump and
their 214-position live trade record — and (d) avoid two mistakes they made. The single most
important thing in this document: **their published record does not actually establish that
an LLM forecaster beats a prediction market.** Their whole realized P&L is one Polymarket
longshot; strip the top few trades and both portfolios are deeply negative
([§4](#4-the-live-trading-record-what-it-actually-shows)). Treat their pipeline as a design
template, not as evidence.

Two files have been staged for immediate reuse:

- `research/futuresearch-data/fs_kalshi_forecasts_2026-02-26_slim.csv` — 153 Kalshi markets,
  point-in-time price + volume + resolution date + three model forecasts + median, snapshotted
  2026-02-26 16:09 UTC.
- `research/futuresearch-data/fs_positions_2026-08-11.json` — all 214 of their Kalshi +
  Polymarket positions with forecast prob, market prob, entry price, shares, P&L, and outcome.

---

## 1. Who they are and what they publish

FutureSearch is a commercial AI-forecasting company (Dan Schwarz, Tom Liptay, Jack Wildman,
Nikos Bosse et al.). They sell a forecasting API/SDK and, to market it, run public forecasting
pipelines over Kalshi, Polymarket, and the S&P 500 and publish the results — wins and losses.
The surfaces that matter to us:

| Surface | URL | What's there |
|---|---|---|
| Kalshi forecaster case study | <https://futuresearch.ai/blog/kalshi-forecaster-case-study/> | The full pipeline, filters, prompts, ~$0.60/question |
| Kalshi trader case study | <https://futuresearch.ai/blog/kalshi-trader-case-study/> | Order-book fill simulation, the 43%/50% fill numbers, all 24 positions |
| Live trading results | <https://markets.futuresearch.ai/> | Real-money + paper portfolios, all 214 positions with P&L |
| Evals hub | <https://evals.futuresearch.ai/> | BTF-3, BTF-2, Deep Research Bench, Metaculus/ForecastBench standings |
| The Colab notebook | <https://colab.research.google.com/drive/13EcOjk-ym3kDzqKw5s2acUw87jGnaCpb> | Runnable source of the whole forecaster (see §3) |
| The 153-question dump | <https://docs.google.com/spreadsheets/d/1di3X__LFSHX4YVf0lUCMEivWb3m4qHsqp9t1vPlwTIo/> | Full research + rationales + forecasts, CSV-exportable |
| Python SDK | <https://github.com/futuresearch/futuresearch-python> | MIT-licensed; `agent_map`, `classify`, `rank`, `multi_agent` |
| Blog index | <https://futuresearch.ai/blog/> | ~43 posts incl. three excellent forecaster-failure-mode postmortems |

---

## 2. The forecasting pipeline (and its ~$0.60/question claim)

All of §2 is from <https://futuresearch.ai/blog/kalshi-forecaster-case-study/> unless noted.

### 2.1 Stated economics

| Item | Cost | Source |
|---|---|---|
| Screening classification | **~$0.01 per market** | forecaster case study |
| Research + forecast | **~$0.60 per question** | forecaster case study (headline claim) |
| Single Opus-4.6 agent run on BTF-2 | **~$0.55 per question** | <https://futuresearch.ai/blog/run-agents-twice> |
| Their notebook, as shipped to the public | **"approximately $1" per question** | notebook cell 0 markdown |
| Their SDK's `forecast` op | **$0.09–$0.20/row at LOW effort; ~$1.20/row at HIGH** | <https://futuresearch.ai/docs/find-profitable-prediction-market-trades/> |

**Reconciliation, and what to budget.** The $0.60 is *their* internal cost for 6 research
agents + 3 forecaster calls on their own infrastructure. The same notebook billed to an
outside user is ~$1.00. On DRB their measured per-task LLM spend for a good agent is
$0.24–$0.53 (<https://evals.futuresearch.ai/>). So: **budget $0.60–$1.00/question of true
LLM cost for a 6-agent + 3-forecaster architecture, and ~$0.01 for a classify-style screen.**
Note the screen is ~60–100× cheaper than the forecast — the "cheap screen, expensive judge"
pattern in ORCHESTRATION constraint #3 is exactly what they do, and the ratio is the reason.

An important sanity check on scale: they screened ~3,500 open Kalshi events down to 153
forecasted questions. At $0.01 to screen and $0.60 to forecast that's ~$35 of screening +
~$92 of forecasting ≈ **$127 for one full weekly sweep of Kalshi**. That is the real number
to put in our backtest's cost line. [derived: 3500×$0.01 + 153×$0.60]

### 2.2 Five stages

1. **Find markets.** Fetch all open events from `https://api.elections.kalshi.com/trade-api/v2`
   (~3,500), rank by trading volume, take the top N. For multi-outcome events, take the top 2
   subquestions by volume.
2. **Screen (2 LLM filters, ~$0.01 each).** Insider-information/moral-hazard filter, then
   methodology-fit filter. Full prompts in §3.2 — these are copy-pasteable.
3. **Research (6 parallel agents, each doing live web search).** Stage 1 is *current state*
   (runs first, everything else depends on it); stage 2 runs five in parallel: *base rates*,
   *key factors*, *expert and market opinions*, *YES investor thesis*, *NO investor thesis*.
4. **Forecast (3 models, median).** Gemini 3 Preview (high), Claude Opus 4.6 (high), and a
   **second independent Opus 4.6 run**. Final forecast = **median of the three**.
5. **Compare to price.** `edge = forecast − market_price`, sort by |edge|.

### 2.3 The filter — exact criteria

This is the "3–97¢, >10 days out, no sports/crypto" filter our README refers to. The precise
version, cross-checked against the notebook source:

| Filter | Value | Where it's actually enforced |
|---|---|---|
| Price band | **3% ≤ last_price ≤ 97%** | Hard-coded: `MIN_PRICE = 3`, `MAX_PRICE = 97` (notebook cell 7) |
| Minimum volume | **≥ 5,000 contracts** | `MIN_VOLUME = 5000` (notebook cell 7) |
| Max subquestions per event | **2**, by volume | `MAX_SUBQUESTIONS = 2` (notebook cell 7) |
| Catch-all subquestions | dropped | `EXCLUDED_SUBQUESTIONS = ["other", "none of the above", "none", "other (not listed)"]` |
| Status | `active` only | notebook cell 9 |
| Days to resolution | **exclude resolving within 10 days** | **Blog text only — I found no code enforcing it in the public notebook.** ⚠ |
| Sports / crypto | LLM `classify` screen | §3.2 |
| Insider / moral hazard | LLM `classify` screen | §3.2 |

**⚠ The 10-day rule is not what the published data shows.** [derived] Parsing the 153-question
CSV against the 2026-02-26 snapshot date: 138 of 153 rows have a parseable resolution date, and
**13 of them resolve in under 10 days** (minimum = 0 days). The published price band, by
contrast, is exactly honoured — `market_price` in the CSV runs min 3.0, max 97.0. So treat
"3–97¢" and "≥5,000 contracts" as real, hard filters; treat ">10 days out" as an aspiration
they stated but did not enforce in the public artifact. If we adopt it, we should enforce it
ourselves and log the exclusions.

Their stated *reason* for the 10-day rule is worth keeping even if their code drops it:
"near-term markets tend to be more information-sensitive, where insiders or close observers
have an edge over AI research."

Other useful shape data from that CSV [derived]: median volume 231,270 contracts (min 9,384 —
so the effective volume floor is well above the nominal 5,000); median true days-to-resolution
250; 10th percentile 12 days; max 1,405 days.

### 2.4 Ensembling — and the evidence for it

- **Method used in production: median of 3.** Not mean, not geometric mean of odds.
- Their own ablation (<https://futuresearch.ai/blog/run-agents-twice>) uses the **mean of 4
  runs** on 1,367 BTF-2 questions and reports Brier **0.130 → 0.125**, i.e. **−0.005 Brier for
  ~4× the cost**. They describe this as "~5% closer probability on every question."
- On BTF-2 the full FutureSearch ensemble scores **0.119** vs **0.130** for a single Opus 4.6
  agent (<https://evals.futuresearch.ai/>) — a −0.011 gain.

**What this means for us:** ensembling is real but small. −0.005 to −0.011 Brier for 3–4×
inference cost. Given ORCHESTRATION constraint #3 (inference cost is a P&L line), **a
single-model run should be our default and the ensemble should have to earn its cost in the
backtest.** Their own numbers say the marginal run buys ~0.0017 Brier per extra run at the
margin, which at $0.55/run is expensive.

Note also [derived from the 153-question CSV]: the two independent Opus runs disagree by a
**median of 0 points and a mean of 0.88 points** (max 20). The Opus↔Opus2 pair is nearly
degenerate; almost all of the ensemble's spread comes from Gemini-vs-Opus (mean spread across
all three = 3.6 points, median 2.0). **A "median of 3" where two of the three are the same
model at the same temperature is mostly a 2-model ensemble with a tie-break.** If we ensemble,
use genuinely different models.

---

## 3. Reusable artifacts (copy these)

### 3.1 The forecaster prompt

Fetched verbatim from the notebook (`FORECASTER_PROMPT`, cell 19). Full text is in the
notebook; the load-bearing rules, which are the transferable part:

- Frame: *"You are an expert forecaster being evaluated for a prestigious forecasting
  position. Your forecast will be graded on Brier score across hundreds of questions."*
- **"Do NOT do any additional web research. Only use the information already provided."**
  (research and judgment are separated — the judge is a pure synthesizer)
- **"If you are being asked this question, you should assume it has NOT yet resolved. If the
  resolution seems ambiguous, forecast as if the resolution threshold has not been met."**
- **"Do not trust any research that includes information from the future. If it is Feb 2026,
  then do NOT trust research that tells you what a website says in Dec 2026. It is
  hallucinated and you should disregard everything that particular piece of research says."**
  ← *This is a contamination guard baked into the prompt. We should copy it verbatim for our
  point-in-time backtests; it is cheap defence-in-depth on top of retrieval-side cutoffs.*
- Contradiction handling: 5-vs-1 → trust the 5, but *add considerable uncertainty*.
- Calibration rails: **"Even if something seems impossible, never forecast less than 3%. Even
  if something seems certain, never forecast more than 97%."** ← *Note this is the same 3/97
  band as the market filter. It means their forecast can never claim more than 94 points of
  edge and, more importantly, it caps tail losses on longshots.*
- **"Put extra weight on the status quo outcome since the world changes slowly most of the
  time."**
- Required output: rationale + **2–4 explicitly named key uncertainties** + integer probability.

### 3.2 The two screening prompts

Both fetched verbatim from the notebook. These are the highest-value copy-paste in this
document — they encode adverse-selection avoidance, which is the thing that keeps a small bot
from being picked off.

**`INSIDER_SCREEN_TASK`** — reject if: pre-taped/pre-recorded shows (reality TV, game shows,
pre-recorded interviews); celebrity/individual personal decisions ("will X attend", travel,
personal announcements, what someone will wear); outcomes already determined but not publicly
announced (award winners decided by committee, pre-selected nominees); what a specific person
will say/do in a private setting; any question where a small group already knows the answer.
Reject also for **moral hazard**: low-cost stunts a bettor could perform, self-fulfilling bets,
markets a few people can resolve in their own favour. Pass: politics/elections/legislation,
economic indicators and central-bank decisions, geopolitics, large-scale outcomes no individual
can influence, scientific/technological milestones, public health/natural disasters/climate.
Closing rule: **"When in doubt, REJECT. It is better to skip a good market than to trade into
one with adverse selection."**

**`METHODOLOGY_SCREEN_TASK`** — reject **sports** (pro, college, international, esports, sports
awards/draft/trades/coaching, fantasy) and **cryptocurrency** (price targets, market-cap
milestones, DeFi/on-chain metrics, NFTs/meme coins). Pass: elections and nomination races,
geopolitics, government policy/legislation/executive orders/regulation, economic indicators,
public health/science/tech milestones, company events (IPOs, M&A, leadership changes), legal
outcomes (SCOTUS, major trials). Closing rule: **"When in doubt about whether specialized
traders have an edge, REJECT."**

Their stated rationale for the sports/crypto exclusion is not squeamishness — it's that those
domains have *dedicated adversaries with better data* (statistical models and injury intel for
sports; technical analysis and on-chain data for crypto) that a web-research agent structurally
cannot match.

### 3.3 The SDK

`pip install futuresearch`, MIT licence, Python 3.12+ (<https://github.com/futuresearch/futuresearch-python>).
Ops: `agent_map()` (web research per row, 1–11¢), `classify()` (0.1–0.7¢), `rank()` (1–5¢),
`multi_agent()` ($0.30–$2), `dedupe()`, `merge()`. **We should not depend on it** — it bills
against their API, so their cost model becomes our cost model and their per-row pricing sits
inside our P&L. Reimplement the pattern (`agent_map` is just "run this prompt over a dataframe
with a Pydantic response model and web search enabled") against our own model calls. The
*architecture* — parallel typed research agents feeding a no-search synthesizer — is the
portable part, and it's a very close match to what already exists in `forecasting/`.

---

## 4. The live trading record: what it actually shows

Source: <https://markets.futuresearch.ai/> as of 2026-08-11 16:41 UTC. I parsed the page's
embedded position data — all 214 positions with entry price, size, forecast probability,
market probability, outcome and realized P&L — into
`research/futuresearch-data/fs_positions_2026-08-11.json`.

### 4.1 Their headline numbers

| Portfolio | First traded | Balance | Unrealized | Annualized ROI | Record |
|---|---|---|---|---|---|
| Kalshi **paper** | Feb 26, 2026 | $149,845 | +$11,462 | **+143.4%** | 41 open / 44 won / 39 lost (53% win) |
| Kalshi **real money** | Jun 10, 2026 | $105,910 | +$3,745 | **+39.7%** | 70 open / 45 won / 22 lost (67.2% win) |
| Polymarket paper | Apr 24, 2026 | $80,363 | −$5,851 | **−51.7%** | 16 open / 24 won / 21 lost (53.3% win) |
| S&P 500 long–short paper | Aug 5, 2025 | — | — | +27.5% net, Sharpe 1.45 | 51 long / 231 short |

Both prediction-market portfolios start from a **$100,000 bankroll**. Their own caveat: the
real account "is much younger, so its annualized figure is an early extrapolation." They state
unrealized P&L is marked "net of fees and entry spread" — so as of Aug 2026 they *are* modelling
fees, even though the Feb trader case study modelled none.

### 4.2 What the position-level data says [all derived from the parsed JSON]

```
Resolved positions (won+lost), n=128
  Kalshi:      n=83   realized P&L  +$27,798  on cost basis $85,610   → +32.5%
  Polymarket:  n=45   realized P&L  −$19,829  on cost basis $111,141  → −17.8%
  Combined:    n=128  realized P&L   +$7,969  on cost basis $196,751  →  +4.1%
```

**The entire combined result is five trades.** Top-5 P&L sums to **+$46,782** against a
combined total of **+$7,969** — i.e. the other 123 resolved positions lost ~$38,800 between
them. Worse:

- Polymarket total is **−$19,829**. Its single best trade (a NO on "Chong Won-oh wins the 2026
  Seoul Mayoral Election," entered at 10¢, +$26,609) carries it. **Polymarket excluding that
  one trade: −$46,438 on 44 positions.**
- Kalshi total is **+$27,798**; the top 3 trades are +$17,828 of it. **Kalshi excluding its top
  3: +$9,971 on 80 positions.** Kalshi is the healthier book but still concentrated.
- Win rates: Kalshi 53.0%, Polymarket 53.3% [derived] — note these are *raw* win rates on a book
  that is 70% NO-side, so they're not directly meaningful without price.

**Direct forecaster-vs-market comparison.** Only 21 resolved Kalshi positions carry both a
forecast probability and a market probability. On that paired subset [derived]:

```
Brier(FutureSearch forecast) = 0.0963
Brier(market price)          = 0.1033      n = 21
```

The forecaster is ahead — by 0.007 Brier, on 21 questions, on a *selected* subset (positions
they chose to take, i.e. the questions where they most disagreed with the market). **This is
not evidence. n=21 with selection bias cannot distinguish a real edge from noise.** Anyone
citing "FutureSearch beats the market" should be pointed at this number.

Across all resolved positions where a forecast exists, their unconditional Brier is 0.1623
(Kalshi, n=77) and 0.2624 (Polymarket, n=45) — but there's no market baseline on those, and
these are hard, deliberately-contested questions, so the absolute level says little.

### 4.3 How they actually size and time

Reading the position data, not the blog:

- **Sizing is equal-weight, not Kelly.** "We divide the $100,000 portfolio equally among all
  qualifying markets" (trader case study). In the Feb run that's $100,000/24 = **$4,166.67 per
  market**, and every fully-filled position in the published table is exactly $4,167. In the
  live book the per-position cost is much smaller and more varied: median $405, max $7,016
  [derived from open positions]. So they equal-weight a *target* and let the order book do the
  sizing.
- **Direction is overwhelmingly NO.** 90 NO vs 38 YES among resolved positions; 55 NO vs 31 YES
  among open [derived]. They are systematically fading overpriced YES — betting against
  longshots and against the favourite-longshot bias. That is a *price-only* systematic tilt
  riding underneath the forecaster, and our backtest must control for it: a naive
  "always-sell-YES-above-X" baseline could reproduce much of this P&L with no LLM at all.
  **Build that baseline. It is the honest null hypothesis for the whole thesis.**
- **Entry price spread is wide.** Resolved positions: min 1¢, q25 29¢, median 53¢, q75 73¢,
  max 95¢. 15 of 128 entered at ≤10¢ and 7 at ≥90¢ [derived]. They are *not* confined to the
  mid-band, despite the 3–97 filter (the filter is on the market's YES price, not on the price
  of the side they buy).
- **Cadence is weekly-to-monthly, not continuous.** Open positions cluster on a handful of
  `forecastAt` dates (e.g. 69 positions dated 2026-07-23, 17 dated 2026-07-02) [derived]. This
  is a batch re-forecast job, not a live market-making loop.
- **They do exit early, and they scale in and out.** Only 8 of 86 open positions traded on more
  than one day, but those show real position management. Example (verbatim from the data):
  `Ukraine election held by December 31, 2026?` — NO, bought 709 sh @ 0.8465 on 2026-04-24,
  **sold 295.75 sh @ 0.9110 on 2026-05-08** (profit-take into a favourable move), then bought
  30 more @ 0.88 on 2026-07-02. Another: `US obtains Iranian enriched uranium by Dec 31` — NO,
  bought 4,414 @ 0.8259 on 06-09, sold 1,262 @ 0.8800 on 07-02. **The pattern is
  rebalance-to-target after each weekly re-forecast, not a discretionary exit rule.** There is
  no published stop-loss, take-profit threshold, or time-based exit.
- **Horizons are long.** Open positions at forecast time: min 9 days, q25 55, median 161, max
  221 [derived]. Exactly one open position was under 10 days — so the 10-day rule is roughly,
  but not exactly, respected in the live book.
- **Entry timing has no special machinery.** Nothing in anything they publish times entries
  around scheduled volatility, event calendars, or election nights. Their edge claim is purely
  "our probability is better than the price, take the mispricing, hold." **The
  scheduled-volatility / overcorrection thesis in our README is a genuinely differentiated idea
  that FutureSearch is not pursuing.**

---

## 5. Fills and execution — the ~43% number

From <https://futuresearch.ai/blog/kalshi-trader-case-study/>. Their fill simulation is
methodologically sound and is exactly the discipline ORCHESTRATION constraint #4 demands:

> "For each forecasted market, we pull the live Kalshi order book — not just the midpoint
> price, but the full set of resting limit orders on both sides... For each position, we walk
> the order book, accumulating shares only at prices within our edge filters... Not all target
> positions fill completely. Thin order books, wide spreads, and prices outside our filters all
> reduce fill rates... This is a realistic simulation — we're limited by actual available
> liquidity, not by wishful thinking about what we could buy."

Published Feb 26, 2026 run:

| Metric | Value |
|---|---|
| Markets analyzed | 153 |
| Positions taken | 24 |
| Target portfolio | $100,000 |
| Capital deployed | ~$50,000 |
| Fully filled positions | 8 of 24 |
| **Average fill rate** | **~43%** |

**⚠ Discrepancy — the ~43% headline does not match their own position table.** [derived]
The published per-position fill column is: 100% ×8, 76% ×3, then 40, 30, 26, 20, 17, 14, 10,
6, 4, 4, 2, 1, 0. Simple mean = **50.1%**. Capital-weighted = $50,059/$100,000 = **50.1%**
(which also reproduces their "~$50,000 deployed"). I cannot reconcile 43% with the table; the
page was "Updated March 30, 2026," so the metrics box and the position table may be from
different runs. **Recommendation: our backtest should not hard-code 43%. Simulate fills for
real against real book snapshots, and use 40–50% as the sanity band to check our simulator
against.**

The shape of the fill distribution matters more than its mean, and it is brutal:

- Fill rate is **bimodal**: either you get the whole $4,167 or you get scraps. 8 positions at
  100%, 3 at 76%, and 13 of 24 under 41%.
- **Fill rate is anticorrelated with edge.** Their biggest edge (+39, SpaceX launches >12) got
  a **1% fill — $60 deployed**. Second-biggest edge (+31, Greenland) filled 100%. The markets
  where the crowd is most wrong are frequently the markets nobody is trading. Any backtest that
  sizes by edge without book-walking will manufacture most of its P&L in exactly the positions
  it could never have entered.
- Only ~half the bankroll can be deployed at all. **A 30% return on deployed capital is a 15%
  return on bankroll.** Their own success bar — "A 30% annualized return would be a remarkable
  achievement — that's about 2.2% per month" — should be read against deployed capital.

Their Polymarket run shows the same funnel from a different angle
(<https://futuresearch.ai/blog/polymarket-forecasting-tutorial/>): of 100 initial questions,
**55 survived filtering and only 31 had adequate liquidity to trade**.

### Edge threshold and the ROI metric

- Trader case study: take a position only if **|edge| ≥ 2 percentage points**, and if
  **annualized expected return exceeds a threshold** (threshold value never published
  **[UNVERIFIED]**), with a preference for shorter duration "because shorter-duration markets
  mean faster compounding."
- Their public docs use a **5-point** screen instead: `profitable = df[df["edge"].abs() > 0.05]`
  (<https://futuresearch.ai/docs/find-profitable-prediction-market-trades/>).
- The docs state the ranking principle explicitly and it's the right one:
  **"a small edge in a market resolving next week is usually better than a large edge in one
  resolving a year from now."** So the sort key is annualized ROI, not raw edge. Given median
  time-to-resolution in their universe is ~250 days [derived], most of their book is compounding
  at well under 2 turns/year — which is a large part of why the deployed-capital numbers look
  better than the bankroll numbers.
- **No fee model in the Feb trader case study** (the word doesn't appear). The Aug live-results
  page does claim marks are "net of fees and entry spread." **We must model Kalshi fees
  explicitly; do not inherit their Feb methodology.**
- **No Kelly, no depth cap, no per-category bankroll cap** anywhere in what they publish. Our
  ORCHESTRATION constraint #5 (fractional Kelly capped by measured depth) is *stricter than
  theirs*, and given their P&L concentration (§4.2), that is the right call.

---

## 6. Their evals — what we can reuse

Source for this whole section: <https://evals.futuresearch.ai/> plus the HuggingFace dataset
API, unless noted.

### 6.1 Bench to the Future (BTF) — pastcasting

The core idea: ask an agent to forecast an event that has already resolved, but serve it a
**frozen web corpus** (via their **RetroSearch** system, which proxies live Serper queries and
drops any result they don't have a pre-anchor snapshot of) so the agent cannot see the future.
This is *exactly* the evaluation pattern ORCHESTRATION constraint #2 requires. Paper:
<https://arxiv.org/abs/2506.21558> (Wildman, Bosse, Hnyk, Mühlbacher, Hambly, Evans, Schwarz,
Phillips; CC-BY-NC-SA 4.0).

| | BTF-2 | BTF-3 |
|---|---|---|
| Questions | 1,417 binary | 1,907 (1,515 binary + 392 numeric) |
| Asked (anchor) | October 2025 | late April – late May 2026 |
| Resolved | December 2025 | mid-May – early July 2026 |
| Corpus | frozen 15M documents | `aux/scraped_pages.parquet`, 737 MB |
| Resolution split | 1,030 No / 387 Yes | not stated |
| HuggingFace | `BTF-2/BTF-2` | `BTF-2/BTF-3` |
| Licence | **CC-BY-NC-4.0** | **CC-BY-NC-4.0** |

**BTF-3 binary schema** (from the HF datasets-server): `question_id, question,
resolution_criteria, background, present_date, date_cutoff_start, date_cutoff_end,
expected_resolution_date, resolution` (0.0/1.0), `resolution_explanation`,
`sota_forecast_probability`, `sota_summary_rationale`.

The binary questions parquet is **6.4 MB** — trivially within our disk allowance. The 737 MB
scraped-pages corpus is optional and is only needed if we want to reproduce the hermetic
retrieval condition.

```bash
# 6.4 MB, no auth needed
curl -L -o btf3_binary.parquet \
  "https://huggingface.co/datasets/BTF-2/BTF-3/resolve/main/btf3_binary_questions_and_forecasts.parquet"
```

**Three hard caveats before anyone wires this into our benchmark:**

1. **⚠ Licence.** Both datasets are **CC-BY-NC**. Using them to develop a system that trades
   real money is very plausibly a non-commercial-clause violation. Fine for internal
   methodology research and for a public write-up; **flag it before it touches a live-money
   decision path.** BTF-2's paper is CC-BY-NC-**SA**, which additionally has share-alike
   implications for derivatives.
2. **⚠ BTF-2 is contaminated for us.** Its questions were asked Oct 2025 and resolved Dec 2025 —
   entirely inside the training window of every model we'd run. Do not use BTF-2 to score a
   forecaster. It remains useful for *qualitative* failure-mode study (§6.4).
3. **⚠ BTF-3 is borderline for us, and this contradicts an ORCHESTRATION assumption.**
   ORCHESTRATION.md states "Model training cutoff is Jan 2026." FutureSearch's own leaderboard
   footnote says: *"Claude Opus 5 reports a May 2026 training cutoff, significantly closer to
   our question snapshots (late April to May 2026) than any other model on the board."* Our
   session model **is** Opus 5. BTF-3 anchors are April–May 2026 and resolutions are May–July
   2026, so BTF-3 sits directly on our cutoff boundary. FutureSearch ran a four-part leakage
   audit on Opus 5 (recall probes on May 2026 events — it scored 0/5; timing gradients — its
   advantage does *not* concentrate near the cutoff; a confidence audit — fewer ≥98% forecasts
   than peers; a trace audit — every load-bearing source predated the anchor) and found no
   resolution leakage, while conceding a *recency* advantage they cannot rule out. **Actionable:
   correct ORCHESTRATION's stated cutoff, and treat BTF-3 as a "probably clean but audit-it"
   set rather than a guaranteed-clean one. Our own Kalshi markets resolving after Aug 2026 are
   strictly safer.**

### 6.2 BTF-3 leaderboard (evaluated June–July 2026) — use this for model selection

Brier scale, lower is better; brackets are 95% percentile-bootstrap CIs (5,000 resamples).
Numeric questions are scored by normalized ranked probability score; pooled weights each
numeric question 3×.

| # | Agent | Pooled (n=1,907) | Binary Brier (n=1,515) | Numeric RPS (n=392) |
|---|---|---|---|---|
| 1 | FutureSearch SOTA (ensemble) | **0.116** [0.108–0.123] | 0.115 | 0.116 |
| 2 | Claude Opus 5 (xhigh) | 0.118 [0.111–0.126] | 0.117 | 0.120 |
| 3 | Claude Opus 4.8 (xhigh) | 0.130 | 0.131 | 0.129 |
| 4 | Claude Fable 5 (high) | 0.131 | 0.132 | 0.129 |
| 5 | GPT-5.5 (high, agent SDK) | 0.134 | 0.142 | 0.124 |
| 6 | GPT-5.6 Sol (high) | 0.135 | 0.141 | 0.129 |
| 7 | Claude Opus 4.8 (high, agent SDK) | 0.137 | 0.135 | 0.140 |
| 8 | Claude Opus 4.8 (high) | 0.140 | 0.135 | 0.145 |
| 9 | GPT-5.5 (high) | 0.143 | 0.148 | 0.136 |
| 10 | Claude Sonnet 5 (xhigh) | 0.154 | 0.154 | 0.154 |

**The single most decision-relevant fact on this board:** their whole multi-agent scaffold
beats a *bare* Claude Opus 5 (xhigh) by **0.0023 pooled, p = 0.054, n.s.** — statistically
indistinguishable. Meanwhile Opus 5 beats Opus 4.8 (xhigh) by 0.0123, p < 0.001.

> **A single well-prompted frontier model is currently worth more than an entire multi-agent
> research pipeline built on the previous frontier model.** Before we spend hours building a
> 6-agent scaffold, we should benchmark one xhigh-effort Opus 5 call with web search against
> it, on the same questions, with cost logged. FutureSearch's own leaderboard says the scaffold
> may not pay for itself.

Their BTF-2 board (2026-04-20) decomposes Brier into calibration and refinement, which is a
better diagnostic than Brier alone and we should copy the decomposition: FutureSearch Agent
0.119 (calib 0.002 / refine 0.081), Opus 4.6 Agent 0.130 (0.005 / 0.075), Gemini 3.1 Pro 0.141
(0.012 / 0.069), GPT-5.4 0.152 (0.010 / 0.056), Grok 4.20 Beta 0.165 (0.003 / 0.039). Note the
pattern: **the models are all well-calibrated; they differ almost entirely in refinement
(resolution).** Being calibrated is easy; being *sharp* is the hard part, and sharpness is what
generates trading edge. Our eval must report both.

### 6.3 Deep Research Bench (DRB) — the cost/accuracy frontier

169 real-world open-web research tasks, each with 10–100k offline pages and curated answers,
served through RetroSearch. Papers: <https://arxiv.org/abs/2506.06287> (May 2025) and
<https://arxiv.org/abs/2409.14913> (Sep 2024). Leaderboard (2026-06-10) includes measured
**cost per task**, which is the number we care about for the research layer:

| Agent | Score | Cost | Runtime (s) |
|---|---|---|---|
| Opus 4.6 (high) | 0.553 | $0.53 | 183 |
| Sonnet 4.6 (high) | 0.549 | $0.46 | 262 |
| Opus 4.5 (high) | 0.548 | $0.46 | 140 |
| GPT-5.5 (high) | 0.540 | $0.36 | 231 |
| Opus 4.6 (medium) | 0.532 | $0.36 | 119 |
| Opus 4.6 (low) | 0.514 | $0.24 | 73 |
| Gemini 3 Flash (low) | 0.498 | **$0.10** | 96 |
| Haiku 4.5 (low) | 0.455 | $0.10 | 83 |
| Gemini 3.1 Flash Lite (low) | 0.373 | **$0.02** | 131 |

**Actionable:** Gemini 3 Flash (low) at $0.10 reaches 0.498 vs Opus 4.6 (high) at $0.53 reaching
0.553 — **90% of the research quality for 19% of the cost.** This is direct empirical support
for ORCHESTRATION constraint #3's "cheap-model retrieval + one expensive judge." Use a
Flash-class model for the 6 research agents and reserve the expensive model for the
synthesis/forecast step. Their own pipeline does *not* do this (they run Opus for research too)
— we can be cheaper than $0.60/question.

### 6.4 The failure-mode postmortems — read these before writing the forecaster prompt

Three short posts derived from auditing BTF-2 traces, each naming a specific, reproducible LLM
forecasting pathology. These are the cheapest quality wins available to us:

- **"Agents sometimes catastrophize"** (<https://futuresearch.ai/blog/agents-catastrophize>) —
  agents model the most extreme version of an outcome, correctly explain why *that* is
  unlikely, then assign that low probability to the whole question. Three geopolitical cases.
- **"History doesn't repeat itself as often as LLMs think"**
  (<https://futuresearch.ai/blog/history-doesnt-repeat>) — agents extrapolate base rates without
  checking whether the generating mechanism is still active. Directly relevant: our forecaster
  has a dedicated *base rates* agent, which is precisely the component this failure attacks.
- **"Some rare examples of AIs being underconfident"**
  (<https://futuresearch.ai/blog/ais-underconfident>) — cases where an agent derived the right
  answer, wrote out the math, cited the right precedent, and then emitted a probability
  inconsistent with its own analysis.

Concrete mitigations to add to our judge prompt: (a) require the agent to state the *modal*
outcome separately from the extreme one; (b) require any base-rate citation to include an
explicit "is the mechanism that produced this base rate still active?" check; (c) add a
self-consistency pass that re-reads the rationale and asks whether the stated number follows.

### 6.5 Live tournament standings (their external validation)

- **Summer 2026 FutureEval Bot Tournament: #1 of 197** (score 1852.88).
- **Metaculus Cup Summer 2026: ranked above the #3 human** (leader `laertes` 612.19,
  FutureSearch 478.17).
- **Market Pulse Challenge 26Q3: ranked above the #2 human** (leader `MarcosO` 799.33,
  FutureSearch 794.25).
- **MiniBench** (rolling two-week): #3/162, #2/147, #1/132, #1/116, #2/111 across the five most
  recent rounds — i.e. genuinely strong but *not* dominant; they lose rounds to `acm_bot`,
  `metac-azimuth`, `laertes`.
- **ForecastBench: #20 of 273** (leader `rice-demon` 69.1, FutureSearch 66.9).

Scored by peer score against the field. These are real, externally-run results and they do
establish that FutureSearch's forecaster is genuinely good at *forecasting*. They say nothing
about whether it beats a *market price* net of fees and fills — which is our actual question,
and is the gap §4.2 shows is still open.

---

## 7. What to steal, what to skip, what to do better

### Steal (high confidence, do it today)

| Thing | Where |
|---|---|
| The two screening prompts, near-verbatim | §3.2 |
| The forecaster prompt's contamination guard, 3/97 rails, status-quo weighting, and "2–4 key uncertainties" output contract | §3.1 |
| 6-agent research decomposition, with the adversarial YES-thesis / NO-thesis pair | §2.2 |
| Hard filters: 3–97¢, ≥5,000 contracts volume, active only, drop "other/none of the above", max 2 subquestions per event | §2.3 |
| The >10-day rule — but *actually enforce it*, which they didn't | §2.3 |
| Order-book **walking** for fills (never mid, never assumed) | §5 |
| Ranking by **annualized** expected return, not raw edge | §5 |
| Cost per question as a first-class logged field | §2.1 |
| Reporting **calibration and refinement separately**, not just Brier | §6.2 |
| Their honesty norm: publish the losing portfolio next to the winning one | §4.1 |

### Skip / do better

| Their choice | Our better choice | Why |
|---|---|---|
| Equal-weight $100k/N sizing | Fractional Kelly (¼–½) capped by measured book depth | Their P&L is 5 trades out of 128 (§4.2); equal-weight over-allocates to low-edge and under-allocates to high-edge |
| No fee model (Feb methodology) | Kalshi taker fee modelled per contract, maker vs taker explicit | ORCHESTRATION #1; a 2-point edge threshold may not survive fees at all |
| Median-of-3 with 2 identical Opus runs | Single strong model as default; ensemble only if it earns its cost in backtest | Opus↔Opus2 median disagreement is 0 points (§2.4); ensemble buys 0.005–0.011 Brier for 3–4× cost (§2.4); scaffold ≈ bare Opus 5 on BTF-3 (§6.2) |
| Opus-class models for all 6 research agents | Flash-class for research, expensive model only for the judge | DRB: 90% of quality at 19% of cost (§6.3) |
| No event-timing logic | Scheduled-volatility / overcorrection calendar | Nobody public is doing this; it's our differentiator (§4.3) |
| "43% fill rate" as a constant | Simulate fills against real book snapshots; use 40–50% only as a sanity band | Their own table says 50%, not 43% (§5) |
| Hold to resolution, rebalance weekly | Same, but add an explicit exit policy and test it | They have no published exit rule; ours should be a tested parameter, not an accident |

### The three evals to build, in order

1. **`eval/fs_replication`** — the cheapest possible check that our pipeline works at all. Take
   `research/futuresearch-data/fs_kalshi_forecasts_2026-02-26_slim.csv` (153 Kalshi markets with
   point-in-time price, volume, resolution date and their median forecast). Resolve every one of
   those tickers against the Kalshi settled-markets API — **they all resolved months ago, and
   the outcomes are free.** Then compute, on the same question set:
   `Brier(FutureSearch median)` vs `Brier(Kalshi price at 2026-02-26 16:09 UTC)` vs
   `Brier(our forecaster)`. This is a real, honest, n=153 head-to-head against both a published
   AI forecaster *and* the market, and it costs one Kalshi API sweep plus our own inference.
   **This should be the first thing built after the data layer.** ⚠ Contamination note: those
   markets resolved between Feb and Aug 2026, straddling our model's cutoff — so run it with
   strict point-in-time retrieval and report separately for markets resolving before vs after
   May 2026.
2. **`eval/fs_trade_replay`** — replay `fs_positions_2026-08-11.json` through our own backtest
   harness. We know their entry price, size, and outcome for 128 resolved positions, so we can
   check that our fee model, fill model and P&L accounting reproduce their published
   +$27,798 (Kalshi) / −$19,829 (Polymarket). **This is a unit test for the backtest harness
   against an external ground truth**, which is otherwise very hard to come by. If our harness
   can't reproduce a known book, it can't be trusted on ours.
3. **`eval/btf3`** — the BTF-3 binary parquet (6.4 MB) as a 1,515-question forecasting-quality
   gate with a published SOTA baseline (`sota_forecast_probability`) per question. Use it for
   prompt and model selection only, with the licence and cutoff caveats in §6.1 attached.

### The one number to carry forward

FutureSearch is the best-resourced public attempt at exactly our thesis, has been at it since
at least Feb 2026, has a genuinely #1-ranked forecaster on Metaculus's bot tournament, and
their combined realized prediction-market P&L across 128 resolved positions is **+4.1% on
deployed capital, of which 100%+ comes from five trades.** Their own framing was right in
February and is still right in August: *"We don't know yet whether the AI forecaster adds
enough accuracy."* Our benchmark's job is to answer that question honestly for ourselves — and
to make sure the answer isn't "no, we just got lucky on a longshot."

---

## Appendix: reproducing the data pulls

```bash
# 153-question Kalshi forecast dump (4.4 MB full version, incl. all research + rationales)
curl -L -o fs153.csv \
  "https://docs.google.com/spreadsheets/d/1di3X__LFSHX4YVf0lUCMEivWb3m4qHsqp9t1vPlwTIo/export?format=csv&gid=881080145"

# The forecaster notebook source (55 KB .ipynb — contains both screening prompts verbatim,
# the full FORECASTER_PROMPT, and the Kalshi filter/ranking code)
curl -L -o kalshi_forecaster.ipynb \
  "https://drive.google.com/uc?export=download&id=13EcOjk-ym3kDzqKw5s2acUw87jGnaCpb"

# BTF-3 binary questions + SOTA forecasts + resolutions (6.4 MB)
curl -L -o btf3_binary.parquet \
  "https://huggingface.co/datasets/BTF-2/BTF-3/resolve/main/btf3_binary_questions_and_forecasts.parquet"

# Live position record: the JSON is embedded in the Next.js flight payload of
# https://markets.futuresearch.ai/ — see research/futuresearch-data/fs_positions_2026-08-11.json
# for the already-extracted 214 positions. Re-extract by concatenating the
# self.__next_f.push([1,"..."]) chunks, unicode-unescaping, and brace-matching from
# {"status":"open|won|lost","venue":"Kalshi|Polymarket","p":{...}}
```

**Flagged as unverified:** the exact annualized-return threshold in their trader filter is
never published; the reconciliation between the "~43%" headline fill rate and the ~50% implied
by their own position table is my inference, not their statement; the claim in our
`docs/reading-list.md` that FutureSearch "claims ~25% market outperformance with a
market-neutral portfolio" does not appear on any FutureSearch page I fetched (their published
S&P 500 net return is 27.5% vs 23.5% for the index, i.e. ~4 points of outperformance, Sharpe
1.45) — that line in our reading list should be corrected.

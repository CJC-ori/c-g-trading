# SYNTHESIS — ranked strategy portfolio, benchmark spec, risks

**Synthesis lead output, 2026-08-11.** Inputs: all 9 research reports in `research/`
(`kalshi-api.md`, `polymarket-data.md`, `systematic-edges.md`, `oss-arb.md`,
`futuresearch.md`, `oss-forecasters.md`, `ground-truth.md`, `point-in-time-retrieval.md`,
`benchmarks.md`, `cost-architecture.md`) plus `docs/viability.md` and `docs/prior-art.md`.
Audience: engineering agents building prototypes in the next few hours. Every load-bearing
claim cites either a URL or the research file (with its own citations) it came from.
Unverified items are flagged ⚠.

---

## 0. The verdict in one paragraph

Build five prototypes today, in this order: **(1) favorite–longshot-bias harvesting
(taker + maker variants)** — the highest-evidence edge on Kalshi and the mandatory null
baseline for everything else; **(2) weather ground-truth trading** — the only category with
enough independent resolutions (>4,000/yr) to statistically prove or kill an edge inside
this build window, with a *measured* mispricing precursor already in hand; **(3)
event-night panic-wick resting ladders** — the founding Michigan thesis, high per-episode
payoff, explicitly hits-based; **(4) a cost-engineered LLM forecaster** — mandatory to test
the repo's core thesis, but ranked fourth because no public evidence yet shows an LLM
beating a real-money market post-fee, and the realistic edge (~0.004 Brier ≈ 6¢ RMS
disagreement) needs ≥500 resolved markets to even detect; **(5) a price-only structure
scanner** (multi-outcome overround + cross-instrument consistency + cross-venue divergence
as a *signal*) — cheap, partially riskless, and it feeds signals into (1) and (4). Do NOT
build arbitrage (intra-venue arb is structurally impossible, cross-venue arb is fee-dead:
`research/oss-arb.md` §5–6), do not target Fed/headline-control markets (efficient;
use as calibration oracles: `research/ground-truth.md` §3.5, §5).

---

## 1. Ranked strategy portfolio

### P-1. Favorite–longshot bias harvesting (price-only) — BUILD FIRST

**Why #1:** the single best-evidenced post-fee edge on Kalshi, it backtests on data already
pulled, and it is the honest null hypothesis: FutureSearch's live book is 70% NO-side
(fading longshots), so much of any "AI forecaster P&L" may be this effect wearing a hat
(`research/futuresearch.md` §4.3). Every other prototype must beat this one.

**Evidence.** Bürgi–Deng–Whelan (GWU working paper 2026-001, Feb 2026, transaction-level,
313,972 contract-day observations, 2021–Apr 2025): contracts ≤10¢ lose >60% of stake;
statistically significant **positive post-fee returns above 70¢**; **makers buying ≥50¢
contracts earn +2.6% per episode after fees, SD 33%**; bias rejected-unbiasedness in every
year, category, and days-to-close bucket, largest in the largest-size quintile
([PDF](https://www2.gwu.edu/~forcpgm/2026-001.pdf)). Independently replicated on 72M Kalshi
trades ([hackingthemarkets](https://hackingthemarkets.com/why-kalshi-takers-lose-money-72m-trades-analyzed/),
⚠ partially paywalled). Full analysis: `research/systematic-edges.md` §1–§2, §4.

**Trading rules (three variants, run all three):**
- **R1-taker:** for every market ≤10 days to close with a side priced 70–97¢, buy that side
  at the ask (taker fee charged), hold to resolution. Ablate by category.
- **R2-maker:** rest a bid on the ≥50¢ side at best-bid or (last trade − 1¢), size ≤ 20–25%
  of trailing 1-hour taker volume; reprice hourly; **stand down in the final 6 hours**
  (GWU: maker returns deteriorate at closing prices). Fill only when the tape prints at or
  through the bid.
- **R5-endgame:** in the final 6/12/24h, buy (maker) the side priced 90–98¢; report the
  win-rate-vs-price curve by category; drop any category where realized win rate <
  breakeven (price + fee). Sports supplies the n (23M-trade study: calibration "departs
  sharply as expiry approaches" — [arXiv:2607.14430](https://arxiv.org/html/2607.14430)).

**Data (confirmed available).** Kalshi settled markets + tick trades back to **Nov 2022**,
unauthenticated, via `/historical/trades` and `/historical/markets`; 1-min/hourly/daily
candlesticks to market inception; trades carry `taker_book_side` for maker-fill simulation;
exclude `is_block_trade` prints (`research/kalshi-api.md` §5–6,
[docs.kalshi.com/getting_started/historical_data](https://docs.kalshi.com/getting_started/historical_data)).
Enumerate history via series → events → `/historical/markets?event_ticker=` (no date filters
exist on that endpoint — §8).

**Backtest design.** Zero contamination (no LLM at decision time) → full-history backtest.
Time-forward split per SPEC §7 (train first 60% of the pull window, test last 40%).
Cluster standard errors by event (brackets of one event are correlated — GWU does this).
Maker fills replayed against the actual tape (trade at/through limit, ≤25% of printed
volume). **Mandatory caution:** the lazy version of this edge did NOT survive
out-of-sample on ForecastBench data — a logistic recalibration of market prices fit on
pre-2026 data gained +0.0027 Brier in-sample and −0.0005 out-of-sample
(`research/benchmarks.md` §1.7). Different venue/horizon, but it means: validate
time-forward before believing anything, and expect the effect to be weaker in 2025–26 data
(GWU notes the bias weakened in 2025).

**Expected edge after costs.** Taker ≥70¢: +1–3%/episode. Maker ≥50¢: benchmark +2.6%/episode,
SD 33% (not annualized; holding period days). Inference cost: $0. Capacity: modest per market
(top-decile Kalshi markets average $526k *lifetime* volume — GWU), but the strategy runs
across hundreds of markets simultaneously.

**Kill criteria.** (a) Post-fee mean return ≤ 0 on the held-out time split with
event-clustered CI; (b) 2025–26 subsample effect < half the 2021–24 effect and CI spans 0;
(c) simulated maker fill rate > 60% (fill model is lying — SPEC §3); (d) P&L killed by
fee×1.5 stress.

---

### P-2. Weather ground-truth trading (ground-truth data) — BUILD SECOND

**Why #2:** the only strategy where the ground truth is a free, queryable physical model and
settlement is a published instrument reading; **>4,000 independent resolutions/year vs ~40
for the entire 2026 election calendar**; ~$1.4M/day of measured turnover across 12 city
series; and the research already **measured a persistent grid-vs-station bias** that is
itself the edge: NYC Central Park **+1.54°F** (MAE 1.67, sd 1.29, n=97 days), Miami −1.19°F,
LAX −0.60°F, Chicago Midway −0.58°F — against 1°F-wide strikes
(`research/ground-truth.md` §2, all endpoints probed live 2026-08-11).

**Trading rule (pipeline sketch).**
1. Pull the Open-Meteo **ensemble** forecast for each Kalshi city/station
   ([ensemble-api.open-meteo.com](https://open-meteo.com/en/docs/ensemble-api), verified 200);
   bias-correct per city (and season) using the measured grid→station offsets fit on
   ACIS station history ([RCC-ACIS](https://www.rcc-acis.org/docs_webservices.html), verified).
2. Integrate the corrected empirical distribution against the strike ladder → fair
   probability per strike.
3. Trade any strike where |fair − market| > fee + 2¢ buffer; maker-first (weather series are
   plain `quadratic`, **zero maker fees** — `research/ground-truth.md` §6.2); depth-capped
   (Chicago/Austin/Dallas books show <$100 at top; LAX/NYC show $17k–128k — measure at
   decision time).
4. **Intraday variant (highest concrete edge in all the research):** parse the ~4 PM ET
   intermediate NWS CLI report (`api.weather.gov/products/types/CLI/locations/{loc}`,
   verified, publishes max-so-far + time it occurred) and trade the remaining ~8 hours of
   the session where the day's max is already substantially determined. ⚠ The "max is
   usually set by 4 PM" prior is unverified — measure it from CLI/ACIS history first.

**Data (confirmed).** Settlement truth: NWS CLI product (live, rolling ~1-week window) +
ACIS `StnData`/`MultiStnData` for history (matches CLI values). Point-in-time forecasts:
Open-Meteo **previous-runs API** serves the forecast *as it stood N days earlier*
(`temperature_2m_previous_day1/3/5`, verified, `past_days=92`). Kalshi side: series
`KXHIGH*` markets + candlesticks + trades via the API (station map extracted from
`rules_primary` — note **Chicago settles on Midway KMDW, not O'Hare**).

**Backtest design.** Contamination-free (pure math, no LLM). Window bounded by
previous-runs `past_days=92` (⚠ whether it extends further is an open item) → ~90 days ×
12 cities × ~12 strikes ≈ **>10,000 strike-level observations**, resolving daily. Replay:
at t = day-before (and 4 PM day-of for the intraday variant), compute fair probabilities
from the point-in-time forecast, fill against Kalshi candles/tape honestly. Day-ahead
residual sd vs station ≈ 3.1°F (`research/ground-truth.md` §2.5 ⚠ derived) — the market
genuinely has something to price, so calibration is the product.

**Expected edge after costs.** Not yet measured end-to-end — that is precisely what today's
backtest answers with real statistical power. Priors: the NYC bias alone is >1 strike;
retail-heavy flow (FLB present in climate category per GWU); fees at extreme strike prices
are ~0.3–0.6¢. Inference cost: $0.

**Kill criteria.** (a) Post-fee P&L ≤ 0 over ≥60 resolved days across ≥6 cities; (b)
bias-corrected ensemble fails to beat the *market's own* Brier on strike outcomes; (c) all
P&L concentrated in one city or one weather regime (robustness split by city/month); (d)
measured book depth too thin to deploy >$200/day/city after the 25% depth cap.

---

### P-3. Event-night panic-wick resting ladders (price-only) — BUILD THIRD

**Why #3:** the founding thesis, now verified tick-level (Michigan: 98.4¢ → **74¢ trough
lasting ~3 minutes** at 03:17 UTC → 98¢ within 2h → resolved YES; ~$230k notional traded
≤80¢ — `docs/viability.md` §2, independently reproduced from raw candles in
`research/kalshi-api.md` §5). Highest per-episode payoff in the portfolio (+24–28% dip-buy;
~11–14x net on the cheap-NO convexity variant). Ranked below P-1/P-2 only because n is
small (~4 qualifying events/yr) and adverse selection on election night is real — some
wicks are the market being right early.

**Trading rules.**
- **R4 (dip-side):** for scheduled-resolution binary markets whose YES averaged ≥95¢ over
  the prior 24h: at event-window start, place a GTC resting YES bid ladder at 85/80/75¢
  (size split by measured depth, maker fees zero on elections). Exit via resting ask at
  (pre-event price − 2¢) or hold to resolution. No stop-loss — binary, sized for total loss.
- **R4b (convexity):** buy NO at ≤3¢ pre-event; rest an NO ask ladder at 15/20/25¢ through
  the event window; count the bleed-to-zero (no-scare) cases honestly. Note the ≤2¢ region
  uses **0.1¢ ticks** (`tapered_deci_cent` price structure) — the fill simulator must
  quantize to `price_ranges` per market or it misprices exactly this trade
  (`research/kalshi-api.md` §2).

**Data (confirmed).** 1-min candles for every settled election/primary series back to
inception, including the **entire 2024 election night** via `/historical/markets/PRES-2024-DJT/candlesticks`
(481 1-min candles verified) and 2022 midterms via `/historical/trades`. Event calendar =
scheduled result nights (primaries 2025–26, 2024 general, 2022 general).

**Backtest design.** Contamination-free. Universe: all markets matching
election/scheduled-event series with ≥95¢ 24h-average before a known event window (rule
generalizes to rulings/data releases). Fills: maker bid fills only when candle low <
bid (strictly through), ≤25% of that minute's volume. Report per-episode P&L distribution,
not the mean; the epistemically honest output is (hit rate, payoff when hit, loss when
wrong) per episode class. Historical precedent beyond Michigan: PredictIt 2020 red-mirage
(61→42→87¢), 58% of Polymarket 2024 national markets showed negative daily serial
correlation (`research/systematic-edges.md` §3).

**Expected edge after costs.** Per hit: +10–25% (dip-buy) or 5–15x (convexity); frequency
and false-positive rate unknown — measuring them *is* the backtest. Portfolio math from
`docs/viability.md` §5: at 4 events/yr, $25k each, 85% hit rate → ~+$9–10k/yr, but one miss
= −$19.5k; treat as hits-based under SPEC §7's hits-based clause. Inference cost: $0.

**Kill criteria.** (a) Across all historical event nights, ladder fills are dominated by
wicks that did NOT revert (adverse-selection rate >~35%); (b) total simulated fills < 10
episodes over the full history (not enough data to say anything — park it until Nov 2026
midterms paper-trade); (c) EV per episode ≤ 0 after honest fills.

---

### P-4. LLM forecaster vs market (LLM strategy) — BUILD FOURTH, WITH HUMILITY

**Why #4 and not higher:** the bar is brutal. On ForecastBench's baseline board **no bare
LLM beats the market price** (best LLM market-Brier ~0.132 vs market 0.077); only
superforecasters edge it (0.073), i.e. the best humans on earth beat market consensus by
**0.004 Brier ≈ a 6.3¢ RMS disagreement** (`research/benchmarks.md` §1.5, §4.1,
[forecastbench-datasets](https://github.com/forecastingresearch/forecastbench-datasets)).
FutureSearch — the best-resourced public attempt at exactly this thesis — has combined
realized P&L of **+4.1% on 128 resolved positions, all of it from 5 trades**, and their own
n=21 paired Brier comparison is not evidence (`research/futuresearch.md` §4.2). Their whole
scaffold is statistically indistinguishable from one bare Opus 5 call on BTF-3 (Δ0.0023,
p=0.054 — §6.2). Bridgewater's AIA underperformed liquid-market consensus (`README.md`).
**Nobody has publicly demonstrated an LLM beating a real-money prediction market post-fee.**
We build it anyway because it is the repo's core thesis and the niche (thin, research-heavy,
neglected markets) is genuinely untested — but it must prove itself against P-1's price-only
null.

**Pipeline (cost-engineered, from `research/cost-architecture.md` §3, §9).**
- Tier 0 (free): Kalshi API prefilter — price 3–97¢, volume ≥5,000 contracts, >10 days to
  close (**actually enforce it** — FutureSearch stated it but didn't enforce it:
  `research/futuresearch.md` §2.3), drop sports/crypto/insider-prone via the two
  FutureSearch screening prompts (copy near-verbatim — §3.2).
- Tier 1: Haiku 4.5 batched screen (~$0.0003–0.0006/market).
- Tier 2: one Sonnet 5 call per **event** (not per bracket) producing a compressed 6-section
  dossier (current state / base rates / key factors / expert+market opinion / YES thesis /
  NO thesis); shared dossier + prompt caching = 52–92% savings on bracket events.
- Tier 3: judge ensemble (2× Opus framing-diverse + 1 cross-provider, median), `effort:
  medium` default, EV-gated escalation. **$0.175/question single-judge, $0.353 3-judge**
  vs a computed break-even budget of **$0.63/question at a 5-point realized edge**
  (`research/cost-architecture.md` §4.2).
- **Trade gate: |forecast − price| ≥ 4–5 points** (at 2 points, fees eat 87.5% of edge —
  §4.3). Rank candidate trades by annualized expected return, not raw edge. Maker-first.
  Prefer prices away from 50¢ and bracket/margin events (cheapest fees, cheapest research
  via dossier sharing, and the Michigan-verified highest-EV mispricing class).
- Judge prompt: FutureSearch's contamination guard, status-quo weighting, named
  uncertainties (`research/futuresearch.md` §3.1) + the three failure-mode mitigations
  (catastrophizing, dead base rates, self-inconsistency — §6.4). Parser-LLM structured
  output, never regex; parse failure ⇒ no trade (`research/oss-forecasters.md` §7).
- Differentiator none of the OSS bots have: **structured ground-truth feeds in the prompt**
  (VoteHub polls, FEC Schedule E, ALFRED vintages, market price itself) — every surveyed
  bot is news-only (`research/oss-forecasters.md` §5), and model+price ensembles are the
  only thing that beat markets in the Bridgewater result.

**Data + evals (confirmed).** Eval ladder before any Kalshi backtest
(`research/benchmarks.md` §5): rung 0 — reproduce ForecastBench market Brier **0.1172,
n=1,216** (harness self-test, no keys, already cloned); rung 2 — run our forecaster on the
2026-03-01+ market questions with retrieval bounded to `freeze_datetime`; also
`eval/fs_replication` on FutureSearch's 153-question CSV (staged at
`research/futuresearch-data/`) scoring us vs their median vs the Kalshi price. Retrieval
stack: GDELT DOC with **both** date gates (its `enddatetime` leaks up to ~24h and even
`seendate` can be wrong by days — `research/point-in-time-retrieval.md` §1.3/1.3b, measured),
Wikipedia revisions (strongest PIT source; poll tables as-of-date), ALFRED vintages for
econ, FEC by `receipt_date`. **Live search engines are banned in backtests** (ranking
encodes the future — §5.3). Retrieval ledger + blocking audit + placebo test per §5.

**Backtest design (contamination-safe).** Scoring window: markets resolving after
2026-02-01 — **but note Opus 5's knowledge cutoff is May 2026**
([platform.claude.com pricing/models docs](https://platform.claude.com/docs/en/about-claude/models/overview),
`research/cost-architecture.md` §8), so: **Sonnet 5 (Jan 2026 cutoff) is the honest judge
for the Feb–May 2026 window; Opus 5 only for June 2026+ and forward paper-trading.** Charge
real token usage to P&L via `Strategy.last_inference_cost_cents` (already wired in the
harness). Run the no-retrieval ablation (market title only) as a standing column — if the
full pipeline barely beats it, the "forecasting" is prior recall.

**Expected edge after costs.** Realistic ceiling: ΔBrier vs market ~+0.004–0.02 pooled on
the traded universe; P&L-wise, a 5-point realized edge nets ~3.25 points/contract after
taker fees at mid, ~double as maker; inference at $0.18–0.35/q is 5–11% of expected net at
that edge (inside the 20% rule). Expect **pooled-neutral, traded-subset-positive** as the
success shape (`research/benchmarks.md` §4.2). Fill realism: 40–50% (FutureSearch's own
table says 50%, their headline says 43% — simulate, don't hard-code).

**Kill criteria.** (a) Fails to beat the always-0.5 and market-price baselines on
ForecastBench rung 2 (don't even proceed to the Kalshi backtest); (b) on the traded subset
(|q−p| ≥ 4 pts), ΔBrier ≤ 0 with n ≥ 200; (c) simulated post-fee post-inference P&L ≤ 0 on
the held-out window; (d) P&L survives only via ≤3 trades (FutureSearch's failure shape);
(e) no-retrieval ablation matches the full pipeline (edge is prior recall, not research);
(f) placebo test (run at D and D+14 scores flat) fires — the run is leaking and is void.

---

### P-5. Price-only structure scanner: overround + consistency + cross-venue divergence — BUILD FIFTH (CHEAP)

**Why #5:** small build cost, three sub-signals share plumbing, one is riskless when it
fires, and it doubles as original research (nobody has published Kalshi overround
frequency — `research/systematic-edges.md` §7).

**Rules.**
- **R3 (overround NO-set):** scan mutually exclusive Kalshi events; when Σ best-ask YES >
  100¢ + total fees + 1¢ buffer, buy the full NO set (depth-capped, all-or-nothing);
  riskless payoff (n−1)·100 at resolution. Separately: when a single ≤10¢ bracket
  contributes ≥ half the overround, fade just that bracket per R1 (higher capacity).
  On Polymarket this is negRisk-bot-competed; on Kalshi no convert function exists and the
  frequency is unmeasured — measure it.
- **Cross-instrument consistency:** binary control markets vs their own seat-count ladders
  (e.g. `CONTROLS-2026-D` at 48¢ vs `KXDSENATESEATS` implying P(D≥51)=47% — coherent today;
  trade when they diverge). Price-only, backtests on any period
  (`research/ground-truth.md` §3.5).
- **R7 (cross-venue divergence as signal, NOT arb):** for a hand-curated, structurally
  verified pair map (~10–20 series; exact strike + resolution date + resolution source —
  never fuzzy title matching, whose confidence is anti-correlated with correctness:
  `research/oss-arb.md` §4.2), when |Kalshi mid − Polymarket mid| > 4¢ sustained ≥10 min,
  trade Kalshi toward the Polymarket price (maker), exit at gap <1¢ or resolution.
  Polymarket leads Kalshi in price discovery ([arXiv:2603.03152](https://arxiv.org/html/2603.03152v2)).

**Data (confirmed).** Kalshi events (`mutually_exclusive`), orderbooks, candles; Polymarket
CLOB `prices-history` (1-min midpoints back to ~Dec 2022, chained 15-day windows, anchor on
`closedTime` never `endDate`) + data-api trade tape (complete for thin markets, exactly our
segment — `research/polymarket-data.md` §2, §4). Polymarket `p` is the **midpoint**, so R7
entries must add a spread model; fee schedule read per-market from Gamma `feeSchedule`.

**Backtest design.** Contamination-free. R3: replay Σ-ask over all settled multi-outcome
events; report frequency, average net edge, fillable size. R7: replay divergence episodes
on the curated pairs vs a no-signal baseline on the same markets; charge Kalshi fees +
spread both ways. Note Polymarket was effectively fee-free until ~Q2 2026 — report realized-fee
and current-schedule P&L separately (`research/polymarket-data.md` §5.3).

**Expected edge after costs.** R3: unknown frequency, riskless-when-present, likely small;
value is partly the screen. R7: documented 2–8¢ gaps on liquid pairs at high-news moments;
realistic captured ~1–4¢ episodic on the slow leg. Inference: $0.

**Kill criteria.** R3: overround-after-fees frequency < 1 event/week or median fillable
size < $200 → demote to monitoring. R7: divergence-following on the curated pairs fails to
beat no-signal baseline, or the pair audit can't produce ≥10 truly identical pairs.

---

### Explicitly NOT building today

| Idea | Why not | Source |
|---|---|---|
| Intra-venue YES+NO arb | Structurally impossible on both venues (0/236 live Kalshi markets; CLOB mints sets) | `research/oss-arb.md` §0, §5.1 |
| Cross-venue two-leg arb | Fee floor ~2.76¢ at mid; most-liquid pair measured at 0.00¢ gross gap; latency + leg risk | `research/oss-arb.md` §5.3, §6 |
| Fed decision markets | Ground truth *is* the futures market; CME blocks scraping; use as calibration oracle (harness should show ~0 edge there) | `research/ground-truth.md` §5 |
| Headline control markets | Internally coherent, pro-priced; the Silver-Bulletin "9-point edge" is a resolution-rule mismatch (50-50 Senate resolves R) | `research/ground-truth.md` §3.5 |
| Generic 10-min mean reversion | n=3-contract study, execution-fragile | `research/systematic-edges.md` §3 |

---

## 2. Deterministic benchmark spec — what the harness must compute

`bot/backtest/SPEC.md` already exists and is close to right. This section states the exact
comparable metric set and the **amendments the research demands**.

### 2.1 Fee engine (amendments — current SPEC/ORCHESTRATION formulas are wrong in detail)

- Taker fee = `0.07 × fee_multiplier × contracts × P × (1−P)`, **ceiling-rounded to
  $0.0001** (NOT `round(·, 2)`), with the per-order rounding-fee/rebate accumulator on top
  ([docs.kalshi.com/getting_started/fee_rounding](https://docs.kalshi.com/getting_started/fee_rounding.md);
  `research/kalshi-api.md` §3.4). Centicent-ceiled trade fee alone is the honest v1.
- **Per-series fee config, resolved point-in-time:** `fee_type` ∈ {`quadratic`,
  `quadratic_with_maker_fees`, `flat`} and `fee_multiplier` ∈ {0, 0.5, 1} come from the
  API (`/series`, `/series/fee_changes?show_historical=true`). Maker fees exist on only
  **130 of 12,658 series** — none in Elections/Politics/Weather, but **CPI (`KXCPIYOY`) and
  Fed series DO charge makers** (`research/kalshi-api.md` §3.1, `research/ground-truth.md`
  §6.2). Maker coefficient 0.0175 ⚠ (triangulated; official PDF still unfetched — 429).
- Polymarket: read `feeSchedule` off the Gamma market record per market; maker = 0
  (`research/polymarket-data.md` §5).

### 2.2 Fills (unchanged from SPEC §3, with two additions)

Taker fills walk the book / candle volume, ≤25% of printed volume; maker fills only on
tape prints at/through the limit; quantize to per-market `price_ranges` (0.1¢ ticks in the
tails on tapered markets). **Additions:** (1) report the simulated fill rate and treat
40–50% as the sanity band (FutureSearch's own position table implies 50.1%, not the 43%
headline — `research/futuresearch.md` §5); flag any maker fill rate >60%. (2) Port the
**opportunity-lifetime metric**: for every signal, log how long the trigger condition
persisted in the historical tape; a strategy whose median lifetime < ~5s is not ours to
trade (`research/oss-arb.md` §7.3).

### 2.3 The metric block (computed identically for every prototype)

1. **Net P&L** after fees AND inference cost, on point-in-time replay, $ and % on the $10k
   bankroll; annualized return on average deployed capital. Report gross-of-inference too.
2. **Brier vs market baseline — three numbers, always together**
   (`research/benchmarks.md` §4.2):
   (a) pooled ΔBrier = Brier(market mid at same instant) − Brier(p_hat), with **paired CI**;
   (b) ΔBrier restricted to the traded subset (|p_hat − mid| ≥ gate) — the real skill claim;
   (c) simulated post-fee P&L on those trades — the only number that pays rent.
   A strategy that is pooled-neutral but subset-positive is what success looks like.
3. **Calibration**: reliability curve + ECE on p_hat, AND the **calibration/refinement
   decomposition** (models are all calibrated; they differ in sharpness — that's what
   generates edge: `research/futuresearch.md` §6.2).
4. **Drawdown & concentration**: max drawdown, worst single-market loss, top-5-trades share
   of P&L (FutureSearch's book fails this — use it as the cautionary unit test).
5. **Capacity**: P&L at 1×/3×/10× the depth caps.
6. **Trade stats**: n trades, fill rate, maker/taker mix, avg entry edge, holding period,
   opportunity lifetime.
7. **Robustness**: time split (train 60% / held-out 40%), category split, **fees ×1.5**, and
   **inference ×2** stress (a strategy whose edge dies at 2× model prices is one price
   change from dead — `research/cost-architecture.md` §7).
8. **Cost ratios** (LLM strategies): inference/net-P&L (<20%), inference/notional-deployed
   (<1%; fee drag is ~3.5% at mid — if inference approaches it, the architecture is wrong).

### 2.4 Statistical gates (amendment — SPEC §7's n≥100 is unsound)

Measured paired-difference SD on real 2026 ForecastBench data is 0.0698 → minimum
detectable ΔBrier at n=100 is **0.0137** — 3× larger than the superforecaster-vs-market
edge (0.004). **Raise the forecast-strategy gate to n ≥ 500 resolved markets and always
report the paired CI, never a point estimate** (`research/benchmarks.md` §4.3). Sanity
scale for results: +0.004 = superforecaster-class; +0.02 = top-2026-bot-class (be
suspicious); **>+0.05 = you have a leak**.

### 2.5 Contamination enforcement (LLM strategies)

Retrieval ledger (every byte attributable, immutable row before content reaches the model)
+ blocking audit with two independent date gates + banned-source list (all live search
engines) + placebo/future-shift test as a standing CI check + no-retrieval ablation column.
Details and verified leak examples: `research/point-in-time-retrieval.md` §5–6. Judge-model
cutoffs: Sonnet 5 for Feb–May 2026 markets, Opus 5 only June 2026+.

### 2.6 Harness validation

Unit-test the harness against external ground truth before trusting any number:
`eval/fs_trade_replay` — replay FutureSearch's 128 resolved positions
(`research/futuresearch-data/fs_positions_2026-08-11.json`) and reproduce +$27,798 Kalshi /
−$19,829 Polymarket (`research/futuresearch.md` §7). Read
[PredictionMarketBench](https://github.com/Oddpool/PredictionMarketBench)'s
`execution.py`/`maker_queue.py`/`fees.py` (already cloned) as the reference maker-queue
implementation (`research/benchmarks.md` §3.1). Run the Fed-market zero-edge oracle check.

---

## 3. Key risks & unknowns

**Corrections to existing repo docs (propagate these):**
1. **ORCHESTRATION.md's "model cutoff Jan 2026" is wrong for Opus 5** — its cutoff is May
   2026 (FutureSearch's leaderboard footnote + Anthropic model docs). The clean
   Opus-judged backtest window starts ~June 2026 (small n). Fix the constraint text.
2. **Fee formula**: ceiling to $0.0001, not round-to-cent; no `fee_multiplier` > 1 exists
   (prior-art.md's "higher for crypto" is wrong — two crypto series are at 0).
3. **README's "dead heat on Metaculus" is wrong** — pros beat every bot decisively and the
   gap is flat-to-widening (Q2 2025 head-to-head −20.03, p≈0.00001; `research/benchmarks.md`
   §2.6). Honest version: top bots ≈ top-3% of the public crowd, clearly behind pros.
4. SPEC §7's n≥100 Brier gate → n≥500 (§2.4 above).

**Live risks, ranked:**
1. **The LLM edge may simply not exist post-fee.** Every public data point is consistent
   with zero: ForecastBench baselines, Bridgewater, FutureSearch's 5-trade P&L. The
   portfolio is built so P-1/P-2/P-3/P-5 stand without it.
2. **FLB may be decaying.** GWU notes weaker bias in 2025; the ForecastBench recalibration
   didn't transfer out-of-sample. Time-forward validation is mandatory, not optional.
3. **No historical order books exist on either venue.** Depth for past dates must be
   inferred from tape/candles; every day without a live book snapshotter is data lost —
   **start the snapshotter cron today** (`research/polymarket-data.md` §3,
   `research/kalshi-api.md` §7).
4. **Adverse selection on event nights** (P-3): the counterparty may be running live
   precinct models. The backtest measures the historical false-positive rate but cannot
   promise the future one.
5. **Capacity is the hard ceiling everywhere**: $1–3k top-of-book on mid-tier markets;
   volume and depth nearly uncorrelated (SENATEGA: $1.03M volume, $176 on the book).
   Realistic project ceiling stays $10–40k/yr on a $50–100k bankroll (`docs/viability.md` §5).
6. **Data-source fragility**: Kalshi's historical cutoff advances (archive locally now);
   Wayback unreachable from this container; GDELT rate limits severe and its date bounds
   leak; Cleveland Fed nowcast has no archive (start snapshotting); `previous-runs-api`
   beyond 92 days ⚠ unknown.
7. **Unverified load-bearing items**: official Kalshi fee PDF (429 on every fetch — a human
   should download it); maker coefficient 0.0175; Polymarket negRisk mechanics for bracket
   markets; ALFRED keyless access; demo-env key generation.
8. **Regulatory/tax**: state-level Kalshi restrictions moving (MN ban Aug 2026); tax
   treatment unresearched — flag before live money (`docs/viability.md` §4).

**Immediate side actions (cheap, start today, needed regardless of strategy ranking):**
order-book snapshotter (both venues); NWS CLI + Cleveland Fed + VoteHub daily snapshot
crons; register free FRED/ALFRED and api.data.gov (FEC) keys; download the fee PDF from a
normal browser; pull FutureSearch's 153-ticker outcomes from the Kalshi settled API
(free n=153 head-to-head).

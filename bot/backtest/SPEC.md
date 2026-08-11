# Deterministic benchmark spec — the win condition

This is the single reproducible measure every prototype is judged by. If a
strategy can't win here, it doesn't graduate. The harness implementing this
lives in `bot/backtest/`; strategies implement the interface in §2.

*(Amended 2026-08-11 per `research/SYNTHESIS.md` §2: exact fee engine,
n≥500 Brier gate, three-number Brier block, opportunity lifetime,
fill-rate sanity band, inference ×2 stress, judge-cutoff rules.)*

## 1. Point-in-time replay (no lookahead, ever)

The backtester replays history as a sequence of decision points. At decision
time `t` a strategy receives ONLY:

- Market static data whose visibility predates `t` (rules, strikes, open/close
  times — all known at listing).
- Price history strictly `< t` (candlesticks/trades).
- For LLM/ground-truth strategies: an information pack assembled under
  point-in-time discipline (sources dated `< t`; retrieval is logged and
  auditable; any leak invalidates the run).
- Its own portfolio state.

Lookahead bugs are the #1 way backtests lie. The harness enforces this
structurally: strategies never touch the DB, they get a `MarketView` object
sliced at `t`.

**Contamination rule for LLM strategies** *(corrected 2026-08-11 per
research/cost-architecture.md §8 — cutoffs differ by model)*: only markets
that *resolve after 2026-02-01* count toward LLM-strategy scores, AND the
judge model must postdate its own knowledge cutoff:

- **Sonnet 5 (cutoff Jan 2026)** is the honest judge for markets resolving
  **Feb–May 2026** — the bulk of the clean backtest window.
- **Opus 5 / Fable 5 (cutoff ≈ May 2026)** may judge only markets resolving
  **June 2026 or later** (and forward paper-trading, where cutoff is
  irrelevant). An Opus-judged run on March–May 2026 markets is contaminated
  even though the same window is clean for Sonnet 5.

Price-only strategies may use the full history.

## 2. Strategy interface

```python
class Strategy(Protocol):
    def on_decision_point(self, view: MarketView, portfolio: Portfolio) -> list[OrderIntent]: ...
```

`OrderIntent`: ticker, side (yes/no), action (buy/sell), limit price (cents),
size (contracts), time-in-force (rest until cancel / this tick only). No
market orders — everything is a priced limit order; crossing the book = taker.

Decision-point cadence is strategy-declared (e.g. hourly per market, or
event-driven "when price moves >X"), but the harness generates the clock —
strategies cannot peek between their own ticks.

## 3. Fill simulation (honest fills)

- **Taker fills:** an intent that crosses the last known ask/bid fills only up
  to observed traded volume / book depth at that level in the following
  candle period (cap: ≤ 25% of that candle's volume — we can't be most of the
  tape). Partial fills are normal.
- **Maker fills:** a resting limit order fills only if a subsequent trade
  prints AT or THROUGH the limit price, again volume-capped at 25% of prints
  at/through the level.
- **Fill-rate sanity band** *(amended 2026-08-11)*: the simulated maker fill
  rate is reported per run; **40–50% is the sanity band** (FutureSearch's own
  positions table implies ~50.1%, their headline says 43% — simulate, don't
  hard-code), and **any maker fill rate > 60% is flagged in the report: the
  fill model is lying** and maker P&L must not be trusted.
- **Tick structure** *(amended 2026-08-11)*: limit prices are quantized to
  the per-market `price_ranges` structure (`MarketInfo.price_structure`,
  read from the market record). Election-style markets are
  `tapered_deci_cent` — **0.1¢ ticks below 10¢ and above 90¢** — which is
  exactly where the cheap-NO convexity trades live; a simulator snapping to
  whole cents misprices them. Quantization is conservative (buys snap down,
  sells snap up). All observed structures admit whole-cent prices, so
  integer-cent intents are always on-grid.
- **Opportunity lifetime** *(amended 2026-08-11)*: for every order the
  harness logs how long the trigger condition persisted in the historical
  tape (first adverse print/candle after placement). Reported as
  median/p25/p75; a strategy whose **median lifetime < ~5 s is not ours to
  trade** (it belongs to colocated bots — research/oss-arb.md §7.3) and is
  flagged.
- Where only candles exist (no trades), use conservative OHLC rules: a maker
  buy at `p` fills only if low < p (strictly through), volume-capped.
- Settlement: held positions pay out at settlement result; voided markets
  refund at cost.

## 4. Cost model

- **Kalshi trading fees** *(corrected 2026-08-11 per research/kalshi-api.md
  §3 — the old `ceil_to_cent` per contract formula was wrong in detail)*:

  `fee = 0.07 × fee_multiplier × contracts × P × (1−P)`, **ceiling-rounded
  to $0.0001** (centicent — NOT round-to-cent), per Kalshi's fee_rounding
  doc. Per-series `fee_type ∈ {quadratic, quadratic_with_maker_fees, flat}`
  and `fee_multiplier ∈ {0, 0.5, 1}` come from the API's series objects
  (never > 1; two crypto series sit at 0) and are resolved via
  `fees.FeeSchedule` (bundled snapshot `series_fees.json`, or
  `FeeSchedule.from_store()` against the DB — full coverage of all 12,174
  pulled series, 162 non-default). **Maker fee = 0.0175 × … on the 130
  `quadratic_with_maker_fees` series only** (⚠ coefficient triangulated,
  official PDF unfetched), 0 everywhere else — none in
  Elections/Politics/Weather, but CPI and Fed series DO charge makers.
  The engine charges whole cents through a per-order accumulator so
  multi-fill orders converge to the single-fill-equivalent fee. Pass
  `EngineConfig(fee_schedule=FeeSchedule.load_default())` (or the
  `full_report(fee_schedule=...)` arg) for real-data runs; the legacy
  per-contract-ceil path remains the default only for backward
  compatibility of synthetic tests and strictly overstates taker fees.
- **Inference cost:** every LLM strategy logs actual tokens per decision and
  is charged real API prices into P&L as `inference_cost` (use
  `bot.backtest.costs.CostLedger` — real `usage` blocks, caching/batch
  discounts, amortized dossiers; never understate). Reported both ways
  (gross and net of inference).
- No borrowing; cash earns 0. (Kalshi pays interest on balances now — ignore,
  conservative.)

## 5. Sizing (in-harness, not strategy-invented)

Strategies output *conviction* via their limit price and requested size, but
the harness enforces global risk rules so all strategies are comparable:

- Fractional Kelly: size ≤ ¼·Kelly given the strategy's stated probability
  edge vs fill price (strategy must emit `p_hat` with each intent).
- Per-market cap: ≤ 5% of bankroll; per-event cap ≤ 10%; total deployed ≤ 80%.
- Depth cap: ≤ 25% of observed volume (see §3).
- Default bankroll: $10,000.

## 6. Metrics (reported for every run)

1. **Net P&L** ($ and % on $10k bankroll) after fees and inference cost; also
   annualized return on average deployed capital.
2. **Brier vs market baseline — three numbers, ALWAYS reported together**
   *(amended 2026-08-11 per research/benchmarks.md §4.2)*:
   (a) **pooled ΔBrier** = Brier(market mid at the same instant) −
   Brier(p_hat), **with the paired 95% CI** (never a bare point estimate);
   (b) **ΔBrier on the traded subset** (|p_hat − mid| ≥ the trade gate,
   default 4¢) — the real skill claim;
   (c) **simulated post-fee P&L on those trades** — the only number that
   pays rent.
   A strategy that is pooled-neutral but subset-positive is what success
   looks like. *The market mid is the baseline to beat — Bridgewater's AIA
   couldn't.* Sanity scale for ΔBrier: +0.004 = superforecaster-class;
   +0.02 = top-2026-bot-class (be suspicious); **>+0.05 = you have a leak**
   (auto-flagged).
3. **Calibration curve** + expected calibration error on `p_hat`, AND the
   **calibration/refinement (Murphy) decomposition** — reliability −
   resolution + uncertainty. Frontier forecasters are all roughly
   calibrated; they differ in resolution (sharpness), which is what
   generates edge.
4. **Max drawdown**, worst single-market loss, P&L concentration (top-5 share).
5. **Capacity**: P&L at 1×, 3×, 10× the depth caps — does the edge survive size?
6. **Trade stats**: n trades, fill rate (intents→fills), maker/taker mix,
   simulated maker fill rate (sanity band 40–50%, flag >60%), avg edge at
   entry, holding period, opportunity lifetime (median/p25/p75).
7. **Robustness splits**: by time (train: pull-period first 60%, test: last
   40%), by category, with fees ×1.5 (fee-sensitivity), and with
   **inference ×2** (a strategy whose edge dies at 2× model prices is one
   price change from dead — research/cost-architecture.md §7).
8. **Cost ratios** (LLM strategies): inference/net-P&L (**< 20%**) and
   inference/notional-traded (**< 1%**; fee drag is ~3.5% of notional at
   mid — if inference approaches it, the architecture is wrong).

## 7. Win condition (graduation gates)

A prototype "works" iff on the **held-out time split**:

- Net P&L > 0 after fees + inference, AND
- P&L not concentrated: top-5 markets < 60% of profit (unless the strategy is
  explicitly hits-based, in which case: ≥3 independent hits and thesis-level
  reasoning documented per hit), AND
- Fee-stressed (×1.5) AND inference-stressed (×2) P&L still > 0, AND
- For forecast strategies: Brier ≤ market baseline Brier on **n ≥ 500
  resolved markets, with the paired CI reported** *(amended 2026-08-11: the
  old n ≥ 100 gate was unsound — measured paired-difference SD on real 2026
  ForecastBench data is 0.0698, so the minimum detectable ΔBrier at n=100
  is 0.0137, 3× the superforecaster-vs-market edge of 0.004;
  research/benchmarks.md §4.3)*, AND
- Simulated maker fill rate ≤ 60% (fill-model sanity; band 40–50%).

Anything that passes graduates to paper trading on live markets (demo env).

## 8. Anti-overfitting rules

- The held-out test window is touched ONLY by the final ranked candidates
  (max ~2 evaluations per strategy family).
- No parameter may be tuned on test data; tuning log kept in each strategy's
  README.
- Prefer strategies with a causal story documented BEFORE seeing results.

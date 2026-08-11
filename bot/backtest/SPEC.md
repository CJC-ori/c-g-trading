# Deterministic benchmark spec — the win condition

This is the single reproducible measure every prototype is judged by. If a
strategy can't win here, it doesn't graduate. The harness implementing this
lives in `bot/backtest/`; strategies implement the interface in §2.

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

**Contamination rule for LLM strategies:** only markets that *resolve after
2026-02-01* count toward LLM-strategy scores (model cutoff Jan 2026).
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
  at/through the level. (FutureSearch measured ~43% fill rates; if our simulated
  fill rate on maker orders exceeds ~60% we should be suspicious.)
- Where only candles exist (no trades), use conservative OHLC rules: a maker
  buy at `p` fills only if low < p (strictly through), volume-capped.
- Settlement: held positions pay out at settlement result; voided markets
  refund at cost.

## 4. Cost model

- **Kalshi trading fees:** taker `fee = ceil_to_cent(0.07 · P · (1−P)) ·
  contracts` (verify multiplier per category against official schedule; some
  categories differ). Maker fee per verified schedule (≈0 on most markets).
- **Inference cost:** every LLM strategy logs actual tokens per decision and
  is charged real API prices into P&L as `inference_cost`. Reported both ways
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
2. **Brier score vs market baseline** (forecast strategies): strategy `p_hat`
   vs the market mid at the same instant, both scored against resolution.
   *The market mid is the baseline to beat — Bridgewater's AIA couldn't.*
3. **Calibration curve** + expected calibration error on `p_hat`.
4. **Max drawdown**, worst single-market loss, P&L concentration (top-5 share).
5. **Capacity**: P&L at 1×, 3×, 10× the depth caps — does the edge survive size?
6. **Trade stats**: n trades, fill rate (intents→fills), maker/taker mix,
   avg edge at entry, holding period.
7. **Robustness splits**: by time (train: pull-period first 60%, test: last
   40%), by category, and with fees ×1.5 (fee-sensitivity).

## 7. Win condition (graduation gates)

A prototype "works" iff on the **held-out time split**:

- Net P&L > 0 after fees + inference, AND
- P&L not concentrated: top-5 markets < 60% of profit (unless the strategy is
  explicitly hits-based, in which case: ≥3 independent hits and thesis-level
  reasoning documented per hit), AND
- Fee-stressed (×1.5) P&L still > 0, AND
- For forecast strategies: Brier ≤ market baseline Brier on n ≥ 100 resolved
  markets.

Anything that passes graduates to paper trading on live markets (demo env).

## 8. Anti-overfitting rules

- The held-out test window is touched ONLY by the final ranked candidates
  (max ~2 evaluations per strategy family).
- No parameter may be tuned on test data; tuning log kept in each strategy's
  README.
- Prefer strategies with a causal story documented BEFORE seeing results.

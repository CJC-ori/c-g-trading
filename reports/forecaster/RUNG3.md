# Rung 3 — the forecaster as a Strategy through the backtest engine (Kalshi train split)

Run 2026-08-12. Runner: `bot/forecaster/run_rung3.py` (seed 11, resumable);
engine artifacts in `rung3/` (`report.md`, `report.json`, `forecasts-used.json`).
Universe: markets **closing 2026-03-05..2026-05-31**, judged by **Sonnet 5**
(Jan-2026 cutoff — the honest judge for this window; June-2026+ markets, which
would admit Fable/Opus judges, are left to a forward run). Forecast anchored at
`asof = close − 21d`, full PIT retrieval discipline, same pipeline as rung 2.

## Selection

kalshi.db: 1,427 resolved candidates (volume ≥5,000, ≥30d lifetime, category
prefilter) → 429 after event dedupe → 259 past the Haiku screen → **18 markets**
with backfilled hourly candles (public Kalshi API into a private store) and
price 3–97¢ at the anchor. Mix: 8 elections, 3 econ-stat, 2 weather, 2 climate,
plus Fed, financials, politics singles.

## Forecasts (18/18 completed, 0 no-trade; mean inference $0.087/market)

Only 3 of 18 markets showed |forecast − anchor price| ≥ 4pts at asof:
`KXECONSTATU3-26MAR-T4.2` (+27c vs market 4c — market was right, resolved NO),
`KXRAINNYCM-26APR-2` (+31c vs 46c — we were right, resolved YES),
`KXWALESPARLIAMENT-26MAY07-PCYM` (−7c vs 85c — market was right, resolved YES).
The engine, however, re-evaluates the ≥4-point gate against the **live** price
at each decision point while the forecast stays frozen at asof, so price drift
opened "edge" on 15 markets (avg 11.4c at entry) — a known stale-forecast
artifact that inflates trading activity, noted below.

## Engine result (maker-first ForecasterStrategy, ≥4pt gate, exact per-series fees, inference in P&L)

| | value |
|---|---|
| Net P&L after fees, gross of inference | **−$431.24** |
| **Net P&L after fees + inference** | **−$432.80** (−4.33% of $10,000) |
| Fees / inference | $0.00 (all maker) / $1.56 |
| Traded markets / orders / decisions | 15 / 15 / 1,700 |
| Contracts filled | 11,474 (86% maker fill — **above the 40–50% sanity band; fill model optimistic**) |
| Max drawdown / worst market | $1,560 / −$499.50 |
| Fee stress ×1.5 | −$432.80 (all-maker ⇒ unchanged) — NEGATIVE |
| Inference stress ×2 | −$434.36 — NEGATIVE |
| Capacity 3× / 10× | −$524 / −$589 (worse at scale) |

Three-number block, now complete:
- (a) pooled ΔBrier (n=15): **−0.0160** [−0.0582, +0.0263] — 0.1923 ours vs 0.1764 market;
- (b) traded-subset ΔBrier (n=14): **−0.0154** [−0.0608, +0.0300];
- (c) post-fee post-inference P&L: **−$432.80**.

Per-category: the only positive pocket is Climate/Weather, **+$1,263 across
2 markets** (KXRAINNYCM among them); every other category is negative
(Elections −$404 over 8, Econ −$561 over 3, Politics −$500, Financials −$230).

## Kill-criteria verdicts

| criterion | verdict |
|---|---|
| (b) traded-subset ΔBrier ≤ 0, n ≥ 200 | Point estimate negative at n=14 — underpowered, cannot fire formally, but the sign agrees with rung 2's traded subset. |
| (c) simulated post-fee post-inference P&L ≤ 0 on held-out window | **FIRES.** −$432.80, and it is robust: negative under fee stress, inference stress, and (a fortiori) any correction of the optimistic 86% maker-fill model. |
| (d) P&L survives only via ≤3 trades | Mirror image fires: total P&L is negative and the only positive pocket is 2 weather markets — the FutureSearch concentration shape, on the loss-avoiding side. |

## Honest caveats, both directions

- n=15 traded markets is a smoke test; the confidence intervals on ΔBrier are
  ±0.05 wide. Nothing here is a 500-n skill claim in either direction.
- The stale-forecast/live-gate interaction did most of the damage: the largest
  loss (KXECONSTATU3-26MAR-T4.2, −$500 cap) came from a forecast 27pts above a
  4¢ market that drifted while the forecast never updated. A production system
  would re-judge before entry; rung 3 as specced does not. That is a real
  design flaw of the strategy-as-built, not an engine artifact.
- Conversely, the 86% maker-fill rate flatters us — real fills at 40–50% would
  not change the sign (fills are roughly proportional across winners and
  losers here), and the capacity curve says more size loses more.

## Verdict

Rung 3 **confirms rung 2**: market-parity forecasting (Δ ≈ −0.02 on the
traded set) monetizes as a **loss** once traded against real Kalshi prices,
even with zero-fee maker fills and inference at only 0.05% of notional.
Kill-criterion (c) fires on this window. The forecaster does not advance to a
live/forward allocation in its current form. The two defensible follow-ups,
if any: (1) weather/climate-only universe (the one positive pocket, and P-2's
home turf where ground-truth feeds exist), (2) re-judge-before-entry to kill
the stale-forecast artifact, then a June-2026+ window with Fable/Opus judges.

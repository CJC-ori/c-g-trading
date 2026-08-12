# Rung 1 — FutureSearch trade replay (harness economics)

*Generated 2026-08-11T22:47:30+00:00 — `python -m bot.evals.fs_trade_replay`*

**Verdict: PASS — harness economics reproduce the external book**

## Pass A — gross reconciliation against the published book

| venue | n (pub/ours) | published P&L | our gross P&L | residual $ | reconciles |
|---|---|---|---|---|---|
| Kalshi | 83/83 | $27,798 | $27,797.96 | -0.04 | yes |
| Polymarket | 45/45 | $-19,829 | $-19,828.59 | +0.41 | yes |
| Combined | 128/128 | $7,969 | $7,969.37 | +0.37 | yes |

The residual is the difference between their *published, rounded-to-the-dollar* figure and our cent-exact recomputation from `shares`, `entryPrice` and the outcome. Worst single-position residual against their own `pnl` field: $0.0050 (0 positions off by >1c). 

their published pnl == shares*(1[won] - entry_price) exactly, i.e. the book is gross: zero fees, zero slippage on exit, held to resolution.

## Pass B — the same book through our fee accounting

| venue | n | cost basis | gross P&L | gross ROI | fees (spec) | net P&L (spec) | net ROI | fee drag (bps) |
|---|---|---|---|---|---|---|---|---|
| Kalshi | 83 | $85,610 | $27,798 | +32.5% | $2,235 | $25,563 | +29.9% | 261 |
| Polymarket | 45 | $111,141 | $-19,829 | -17.8% | $0 | $-19,829 | -17.8% | 0 |
| Combined | 128 | $196,751 | $7,969 | +4.1% | $2,235 | $5,735 | +2.9% | 114 |

**Residual explanation.** Pass A matches to the cent, so the harness's P&L arithmetic is correct. The only gap between their number and ours is the fee line they never charged: FutureSearch's February trader methodology models no fees at all (`research/futuresearch.md` §5), so our net book is strictly below their published one by $2,235 of Kalshi taker fees (261 bps of Kalshi cost basis). Polymarket contributes zero fee drag by construction. Their published edge threshold is 2 percentage points; this drag is of the same order, which is the practical finding.

**Fee-engine divergence.**

| engine | total Kalshi fees | vs exact |
|---|---|---|
| `spec` — fees.trade_fee_centicents (exact) | $2,234.65 | — |
| `legacy` — fees.taker_fee_cents (per-contract ceil) | $4,652.44 | 2.08x |
| `independent` — this module, float price | $2,228.62 | +6.03 |

`spec` is bot/backtest/fees.trade_fee_centicents (one ceiling per order, to $0.0001, per-series multiplier) — the correct model. `legacy` is fees.taker_fee_cents, which ceilings per *contract* and multiplies; on multi-thousand-contract orders that inflates the fee substantially, so any backtest still running without a FeeSchedule is over-charging itself. `independent` is this module's own float-precision implementation of the same formula; it differs from `spec` only because the harness quantizes the execution price to a whole cent before charging, while these entry prices carry four decimals.

## Concentration (the cautionary metric)

| venue | n | total P&L | top-5 P&L | top-5 share | best | worst | n positive |
|---|---|---|---|---|---|---|---|
| Kalshi | 83 | $27,798 | $21,618 | 78% | $8,726 | $-3,877 | 44 |
| Polymarket | 45 | $-19,829 | $34,808 | n/a (total ≤ 0) | $26,609 | $-5,316 | 24 |
| Combined | 128 | $7,969 | $46,782 | 587% | $26,609 | $-5,316 | 68 |

SPEC §7 fails any strategy whose top-5 markets carry >60% of profit. The combined book's top 5 positions make $46,782 against a combined total of $7,969 — the other 123 positions lose $38,813 between them. Polymarket's share is undefined because its total is negative: one trade (+$26,609) carries the venue and everything else loses. This is the cautionary unit test for our own concentration gate (SYNTHESIS §2.3 item 4), not a book to imitate.

## Their forecaster vs the market, on the traded subset

| n | Brier(FS forecast) | Brier(market) | ΔBrier | 95% CI | gate verdict |
|---|---|---|---|---|---|
| 21 | 0.0963 | 0.1033 | +0.0070 | [-0.0159, +0.0300] | UNDERPOWERED |

positions they chose to take, i.e. maximum-disagreement questions; selection-biased and tiny — not evidence of forecaster skill At n=21 the minimum detectable ΔBrier is 0.0229 — larger than the entire superforecaster-over-market edge (0.004).

## Assumptions and known gaps

- held to resolution: entry taker fee only, no exit trade, no settlement fee
- every FutureSearch fill is treated as a taker fill (they walk the book, §5)
- Polymarket taker fee = 0 on this book (research/polymarket-data.md §5)
- fee_multiplier read from today's /series config, not point-in-time (/series/fee_changes?show_historical=true not yet pulled)
- each position is charged as ONE order; Kalshi's per-order rounding-fee accumulator would apply per real order, so a position filled across many orders pays slightly more than modelled here
- 7 Kalshi positions carry fractional share counts (Kalshi contracts are integral); the spec engine uses them as-is, the harness engine rounds

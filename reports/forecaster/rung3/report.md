# Backtest report - rung3-forecaster

## P&L
- Net P&L (after fees, gross of inference): $-431.24
- Net P&L (after fees + inference): $-432.80 (-4.33% of $10,000.00)
- Fees: $0.00 | Inference: $1.56
- Annualized return on avg deployed ($162.09): -315.07%

## Forecast quality
The three-number block (always read together, SPEC §6.2):
- (a) Pooled dBrier (n=15): -0.0160 (95% CI [-0.0582, +0.0263]) — strategy 0.1923 vs market 0.1764 -> does NOT beat baseline
- (b) Traded-subset dBrier (|p_hat-mid| >= 4c, n=14): -0.0154 (95% CI [-0.0608, +0.0300])
- (c) Trade P&L (the number that pays rent): $-432.80 after fees+inference across 15 traded markets
- n gate: 15 < 500 resolved — NOT enough for a forecast-skill claim (SPEC §7)
- FLAG: n=15 < 500: below the forecast-strategy gate (minimum detectable dBrier at n=100 is ~0.0137, 3x the superforecaster edge)
- Calibration ECE: 0.1487 (n=15)
- Murphy decomposition: reliability 0.0554 (miscalibration, lower better) | resolution 0.1156 (sharpness, higher better) | uncertainty 0.2489

| p_hat bin | n | mean p_hat | observed freq |
|---|---|---|---|
| [0.0,0.1) | 1 | 0.089 | 0.000 |
| [0.1,0.2) | 5 | 0.131 | 0.200 |
| [0.2,0.3) | 1 | 0.200 | 1.000 |
| [0.3,0.4) | 5 | 0.325 | 0.400 |
| [0.7,0.8) | 2 | 0.773 | 1.000 |
| [0.8,0.9) | 1 | 0.836 | 1.000 |

## Risk
- Max drawdown: $1,560.09 (14.07% of peak)
- Worst single-market loss: $-499.50
- Top-5 markets' share of net P&L: n/a across 15 traded markets

## Trade stats
- Decisions: 1700 | Intents: 15 | Orders: 15
- Contracts requested/ordered/filled: 15000/13396/11474 (fill rate vs ordered: 85.65%)
- Maker share of filled contracts: 100.00%
- Simulated maker fill rate: 85.65% (sanity band 40-50%; >60% = fill model lying)
- Avg edge at entry: 11.39c/contract
- Avg holding period: 226.0h
- Opportunity lifetime (s): median 3600 [p25 3600, p75 10800] over 15 orders
- FLAG: maker fill rate 86% > 60% — the fill model is lying (sanity band is 40-50%); do not trust maker P&L

## Cost ratios (LLM strategies)
- Inference / net P&L: n/a (target < 20%)
- Inference / notional traded: 0.05% (target < 1%; fee drag is ~3.5% at mid)
- FLAG: inference cost with non-positive net P&L

## Capacity curve (depth caps scaled)
| depth x | net P&L (after inference) | contracts filled |
|---|---|---|
| 1x | $-432.80 | 11474 |
| 3x | $-524.00 | 12632 |
| 10x | $-588.96 | 12910 |

## Fee stress (x1.5)
- Net P&L after inference: $-432.80 -> NEGATIVE

## Inference stress (x2 model prices)
- Net P&L after stressed inference: $-434.36 -> NEGATIVE

## Robustness splits
- Time split (60/40 by first entry): train $0.00 over 0 markets | test $-431.24 over 15 markets

| category | markets | net P&L |
|---|---|---|
| Climate and Weather | 2 | $1,263.14 |
| Economics | 3 | $-560.74 |
| Elections | 8 | $-404.14 |
| Financials | 1 | $-230.00 |
| Politics | 1 | $-499.50 |

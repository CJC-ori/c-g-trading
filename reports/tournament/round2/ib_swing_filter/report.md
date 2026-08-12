# Backtest report - ib_swing_filter

## P&L
- Net P&L (after fees, gross of inference): $-78.93
- Net P&L (after fees + inference): $-78.93 (-0.79% of $10,000.00)
- Fees: $2.34 | Inference: $0.00
- Annualized return on avg deployed ($10.22): -251.49%

## Forecast quality
The three-number block (always read together, SPEC §6.2):
- (a) Pooled dBrier (n=549): -0.0002 (95% CI [-0.0006, +0.0001]) — strategy 0.0181 vs market 0.0179 -> does NOT beat baseline
- (b) Traded-subset dBrier: no decisions past the gate
- (c) Trade P&L (the number that pays rent): $-78.93 after fees+inference across 81 traded markets
- Calibration ECE: 0.0113 (n=549)
- Murphy decomposition: reliability 0.0001 (miscalibration, lower better) | resolution 0.2137 (sharpness, higher better) | uncertainty 0.2316

| p_hat bin | n | mean p_hat | observed freq |
|---|---|---|---|
| [0.0,0.1) | 353 | 0.029 | 0.020 |
| [0.9,1.0) | 196 | 0.969 | 0.985 |

## Risk
- Max drawdown: $177.37 (1.77% of peak)
- Worst single-market loss: $-124.46
- Top-5 markets' share of net P&L: n/a across 81 traded markets

## Trade stats
- Decisions: 185562 | Intents: 549 | Orders: 549
- Contracts requested/ordered/filled: 103684/57459/6694 (fill rate vs ordered: 11.65%)
- Maker share of filled contracts: 100.00%
- Simulated maker fill rate: 11.65% (sanity band 40-50%; >60% = fill model lying)
- Avg edge at entry: 1.25c/contract
- Avg holding period: 17.1h
- Opportunity lifetime (s): median 3600 [p25 1839, p75 8105] over 549 orders

## Capacity curve (depth caps scaled)
| depth x | net P&L (after inference) | contracts filled |
|---|---|---|
| 1x | $-78.93 | 6694 |
| 3x | $-14.70 | 8826 |
| 10x | $-3.78 | 9245 |

## Fee stress (x1.5)
- Net P&L after inference: $-80.03 -> NEGATIVE

## Inference stress (x2 model prices)
- Net P&L after stressed inference: $-78.93 -> NEGATIVE

## Robustness splits
- Time split (60/40 by first entry): train $-143.12 over 57 markets | test $64.19 over 24 markets

| category | markets | net P&L |
|---|---|---|
| Climate and Weather | 45 | $-180.32 |
| Economics | 29 | $144.21 |
| Politics | 3 | $27.88 |
| World | 4 | $-70.70 |

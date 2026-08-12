# Backtest report - r5_endgame_12h

## P&L
- Net P&L (after fees, gross of inference): $-7,224.90
- Net P&L (after fees + inference): $-7,224.90 (-72.25% of $10,000.00)
- Fees: $2.62 | Inference: $0.00
- Annualized return on avg deployed ($105.47): -2232.00%

## Forecast quality
The three-number block (always read together, SPEC §6.2):
- (a) Pooled dBrier (n=4432): -0.0004 (95% CI [-0.0005, -0.0003]) — strategy 0.0317 vs market 0.0312 -> does NOT beat baseline
- (b) Traded-subset dBrier: no decisions past the gate
- (c) Trade P&L (the number that pays rent): $-7,224.90 after fees+inference across 727 traded markets
- Calibration ECE: 0.0056 (n=4432)
- Murphy decomposition: reliability 0.0000 (miscalibration, lower better) | resolution 0.1776 (sharpness, higher better) | uncertainty 0.2097

| p_hat bin | n | mean p_hat | observed freq |
|---|---|---|---|
| [0.0,0.1) | 3187 | 0.039 | 0.036 |
| [0.9,1.0) | 1245 | 0.963 | 0.973 |

## Risk
- Max drawdown: $7,296.81 (72.82% of peak)
- Worst single-market loss: $-499.70
- Top-5 markets' share of net P&L: n/a across 727 traded markets

## Trade stats
- Decisions: 185562 | Intents: 4432 | Orders: 4427
- Contracts requested/ordered/filled: 613824/416941/42219 (fill rate vs ordered: 10.13%)
- Maker share of filled contracts: 100.00%
- Simulated maker fill rate: 10.13% (sanity band 40-50%; >60% = fill model lying)
- Avg edge at entry: 1.24c/contract
- Avg holding period: 21.9h
- Opportunity lifetime (s): median 3600 [p25 1020, p75 8131] over 4427 orders

## Capacity curve (depth caps scaled)
| depth x | net P&L (after inference) | contracts filled |
|---|---|---|
| 1x | $-7,224.90 | 42219 |
| 3x | $-7,547.06 | 51967 |
| 10x | $-7,516.56 | 53914 |

## Fee stress (x1.5)
- Net P&L after inference: $-7,226.11 -> NEGATIVE

## Inference stress (x2 model prices)
- Net P&L after stressed inference: $-7,224.90 -> NEGATIVE

## Robustness splits
- Time split (60/40 by first entry): train $-6,835.54 over 523 markets | test $-389.36 over 204 markets

| category | markets | net P&L |
|---|---|---|
| Climate and Weather | 674 | $-6,839.52 |
| Economics | 39 | $182.70 |
| Politics | 7 | $-77.42 |
| World | 7 | $-490.66 |

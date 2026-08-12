# Backtest report - r5_endgame_12h

## P&L
- Net P&L (after fees, gross of inference): $141.41
- Net P&L (after fees + inference): $141.41 (1.41% of $10,000.00)
- Fees: $0.06 | Inference: $0.00
- Annualized return on avg deployed ($7.35): 1668.97%

## Forecast quality
The three-number block (always read together, SPEC §6.2):
- (a) Pooled dBrier (n=90): -0.0001 (95% CI [-0.0003, +0.0001]) — strategy 0.0019 vs market 0.0017 -> does NOT beat baseline
- (b) Traded-subset dBrier: no decisions past the gate
- (c) Trade P&L (the number that pays rent): $141.41 after fees+inference across 17 traded markets
- n gate: 90 < 500 resolved — NOT enough for a forecast-skill claim (SPEC §7)
- FLAG: n=90 < 500: below the forecast-strategy gate (minimum detectable dBrier at n=100 is ~0.0137, 3x the superforecaster edge)
- Calibration ECE: 0.0361 (n=90)
- Murphy decomposition: reliability 0.0013 (miscalibration, lower better) | resolution 0.2480 (sharpness, higher better) | uncertainty 0.2480

| p_hat bin | n | mean p_hat | observed freq |
|---|---|---|---|
| [0.0,0.1) | 41 | 0.031 | 0.000 |
| [0.9,1.0) | 49 | 0.960 | 1.000 |

## Risk
- Max drawdown: $251.14 (2.51% of peak)
- Worst single-market loss: $0.12
- Top-5 markets' share of net P&L: 77.88% across 17 traded markets

## Trade stats
- Decisions: 112037 | Intents: 90 | Orders: 90
- Contracts requested/ordered/filled: 33886/13820/2175 (fill rate vs ordered: 15.74%)
- Maker share of filled contracts: 100.00%
- Simulated maker fill rate: 15.74% (sanity band 40-50%; >60% = fill model lying)
- Avg edge at entry: 1.22c/contract
- Avg holding period: 11.0h
- Opportunity lifetime (s): median 7200 [p25 3600, p75 10800] over 90 orders

## Capacity curve (depth caps scaled)
| depth x | net P&L (after inference) | contracts filled |
|---|---|---|
| 1x | $141.41 | 2175 |
| 3x | $141.41 | 2175 |
| 10x | $141.41 | 2175 |

## Fee stress (x1.5)
- Net P&L after inference: $141.38 -> still positive

## Inference stress (x2 model prices)
- Net P&L after stressed inference: $141.41 -> still positive

## Robustness splits
- Time split (60/40 by first entry): train $0.00 over 0 markets | test $141.41 over 17 markets

| category | markets | net P&L |
|---|---|---|
| Climate and Weather | 1 | $6.30 |
| Economics | 8 | $69.60 |
| Elections | 2 | $50.86 |
| Politics | 4 | $12.31 |
| Science and Technology | 2 | $2.34 |

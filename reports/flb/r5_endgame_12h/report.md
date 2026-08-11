# Backtest report - r5_endgame_12h

## P&L
- Net P&L (after fees, gross of inference): $197.45
- Net P&L (after fees + inference): $197.45 (1.97% of $10,000.00)
- Fees: $0.19 | Inference: $0.00
- Annualized return on avg deployed ($7.08): 2419.13%

## Forecast quality
The three-number block (always read together, SPEC §6.2):
- (a) Pooled dBrier (n=74): -0.0002 (95% CI [-0.0004, +0.0000]) — strategy 0.0019 vs market 0.0018 -> does NOT beat baseline
- (b) Traded-subset dBrier: no decisions past the gate
- (c) Trade P&L (the number that pays rent): $197.45 after fees+inference across 22 traded markets
- n gate: 74 < 500 resolved — NOT enough for a forecast-skill claim (SPEC §7)
- FLAG: n=74 < 500: below the forecast-strategy gate (minimum detectable dBrier at n=100 is ~0.0137, 3x the superforecaster edge)
- Calibration ECE: 0.0371 (n=74)
- Murphy decomposition: reliability 0.0014 (miscalibration, lower better) | resolution 0.2484 (sharpness, higher better) | uncertainty 0.2484

| p_hat bin | n | mean p_hat | observed freq |
|---|---|---|---|
| [0.0,0.1) | 40 | 0.030 | 0.000 |
| [0.9,1.0) | 34 | 0.954 | 1.000 |

## Risk
- Max drawdown: $268.00 (2.67% of peak)
- Worst single-market loss: $0.12
- Top-5 markets' share of net P&L: 66.06% across 22 traded markets

## Trade stats
- Decisions: 112037 | Intents: 74 | Orders: 68
- Contracts requested/ordered/filled: 27621/6928/3478 (fill rate vs ordered: 50.20%)
- Maker share of filled contracts: 100.00%
- Simulated maker fill rate: 50.20% (sanity band 40-50%; >60% = fill model lying)
- Avg edge at entry: 1.23c/contract
- Avg holding period: 11.9h
- Opportunity lifetime (s): median 7200 [p25 3600, p75 10800] over 68 orders

## Capacity curve (depth caps scaled)
| depth x | net P&L (after inference) | contracts filled |
|---|---|---|
| 1x | $197.45 | 3478 |
| 3x | $254.06 | 4543 |
| 10x | $266.56 | 4769 |

## Fee stress (x1.5)
- Net P&L after inference: $197.37 -> still positive

## Inference stress (x2 model prices)
- Net P&L after stressed inference: $197.45 -> still positive

## Robustness splits
- Time split (60/40 by first entry): train $0.00 over 0 markets | test $197.45 over 22 markets

| category | markets | net P&L |
|---|---|---|
| Climate and Weather | 2 | $27.10 |
| Economics | 8 | $71.10 |
| Elections | 2 | $51.54 |
| Entertainment | 3 | $5.66 |
| Politics | 5 | $39.35 |
| Science and Technology | 2 | $2.70 |

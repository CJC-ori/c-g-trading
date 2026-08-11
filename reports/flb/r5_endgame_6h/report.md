# Backtest report - r5_endgame_6h

## P&L
- Net P&L (after fees, gross of inference): $81.75
- Net P&L (after fees + inference): $81.75 (0.82% of $10,000.00)
- Fees: $0.21 | Inference: $0.00
- Annualized return on avg deployed ($2.78): 2545.96%

## Forecast quality
The three-number block (always read together, SPEC §6.2):
- (a) Pooled dBrier (n=44): -0.0002 (95% CI [-0.0005, +0.0000]) — strategy 0.0020 vs market 0.0018 -> does NOT beat baseline
- (b) Traded-subset dBrier: no decisions past the gate
- (c) Trade P&L (the number that pays rent): $81.75 after fees+inference across 13 traded markets
- n gate: 44 < 500 resolved — NOT enough for a forecast-skill claim (SPEC §7)
- FLAG: n=44 < 500: below the forecast-strategy gate (minimum detectable dBrier at n=100 is ~0.0137, 3x the superforecaster edge)
- Calibration ECE: 0.0370 (n=44)
- Murphy decomposition: reliability 0.0014 (miscalibration, lower better) | resolution 0.2454 (sharpness, higher better) | uncertainty 0.2454

| p_hat bin | n | mean p_hat | observed freq |
|---|---|---|---|
| [0.0,0.1) | 25 | 0.032 | 0.000 |
| [0.9,1.0) | 19 | 0.956 | 1.000 |

## Risk
- Max drawdown: $268.00 (2.68% of peak)
- Worst single-market loss: $0.04
- Top-5 markets' share of net P&L: 91.62% across 13 traded markets

## Trade stats
- Decisions: 112037 | Intents: 44 | Orders: 43
- Contracts requested/ordered/filled: 19103/4198/1328 (fill rate vs ordered: 31.63%)
- Maker share of filled contracts: 100.00%
- Simulated maker fill rate: 31.63% (sanity band 40-50%; >60% = fill model lying)
- Avg edge at entry: 1.22c/contract
- Avg holding period: 9.2h
- Opportunity lifetime (s): median 7140 [p25 3600, p75 7200] over 43 orders

## Capacity curve (depth caps scaled)
| depth x | net P&L (after inference) | contracts filled |
|---|---|---|
| 1x | $81.75 | 1328 |
| 3x | $109.19 | 1720 |
| 10x | $109.23 | 1722 |

## Fee stress (x1.5)
- Net P&L after inference: $81.65 -> still positive

## Inference stress (x2 model prices)
- Net P&L after stressed inference: $81.75 -> still positive

## Robustness splits
- Time split (60/40 by first entry): train $0.00 over 0 markets | test $81.75 over 13 markets

| category | markets | net P&L |
|---|---|---|
| Climate and Weather | 1 | $0.40 |
| Economics | 7 | $58.64 |
| Elections | 2 | $22.07 |
| Politics | 2 | $0.16 |
| Science and Technology | 1 | $0.48 |

# Backtest report - r5_endgame_24h

## P&L
- Net P&L (after fees, gross of inference): $297.67
- Net P&L (after fees + inference): $297.67 (2.98% of $10,000.00)
- Fees: $0.25 | Inference: $0.00
- Annualized return on avg deployed ($15.71): 1644.14%

## Forecast quality
The three-number block (always read together, SPEC §6.2):
- (a) Pooled dBrier (n=103): -0.0001 (95% CI [-0.0003, +0.0001]) — strategy 0.0025 vs market 0.0024 -> does NOT beat baseline
- (b) Traded-subset dBrier: no decisions past the gate
- (c) Trade P&L (the number that pays rent): $297.67 after fees+inference across 35 traded markets
- n gate: 103 < 500 resolved — NOT enough for a forecast-skill claim (SPEC §7)
- FLAG: n=103 < 500: below the forecast-strategy gate (minimum detectable dBrier at n=100 is ~0.0137, 3x the superforecaster edge)
- Calibration ECE: 0.0421 (n=103)
- Murphy decomposition: reliability 0.0018 (miscalibration, lower better) | resolution 0.2375 (sharpness, higher better) | uncertainty 0.2375

| p_hat bin | n | mean p_hat | observed freq |
|---|---|---|---|
| [0.0,0.1) | 63 | 0.037 | 0.000 |
| [0.9,1.0) | 40 | 0.950 | 1.000 |

## Risk
- Max drawdown: $323.92 (3.16% of peak)
- Worst single-market loss: $0.08
- Top-5 markets' share of net P&L: 48.37% across 35 traded markets

## Trade stats
- Decisions: 112037 | Intents: 103 | Orders: 92
- Contracts requested/ordered/filled: 43690/9701/5017 (fill rate vs ordered: 51.72%)
- Maker share of filled contracts: 100.00%
- Simulated maker fill rate: 51.72% (sanity band 40-50%; >60% = fill model lying)
- Avg edge at entry: 1.22c/contract
- Avg holding period: 18.4h
- Opportunity lifetime (s): median 7200 [p25 3600, p75 14400] over 92 orders

## Capacity curve (depth caps scaled)
| depth x | net P&L (after inference) | contracts filled |
|---|---|---|
| 1x | $297.67 | 5017 |
| 3x | $378.08 | 6514 |
| 10x | $389.72 | 6694 |

## Fee stress (x1.5)
- Net P&L after inference: $297.55 -> still positive

## Inference stress (x2 model prices)
- Net P&L after stressed inference: $297.67 -> still positive

## Robustness splits
- Time split (60/40 by first entry): train $0.00 over 0 markets | test $297.67 over 35 markets

| category | markets | net P&L |
|---|---|---|
| Climate and Weather | 6 | $37.92 |
| Economics | 9 | $73.01 |
| Elections | 3 | $62.42 |
| Entertainment | 5 | $45.16 |
| Financials | 1 | $0.09 |
| Politics | 8 | $48.84 |
| Science and Technology | 3 | $30.23 |

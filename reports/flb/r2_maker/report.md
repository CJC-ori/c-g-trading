# Backtest report - r2_maker

## P&L
- Net P&L (after fees, gross of inference): $-161.79
- Net P&L (after fees + inference): $-161.79 (-1.62% of $10,000.00)
- Fees: $0.26 | Inference: $0.00
- Annualized return on avg deployed ($126.96): -110.62%

## Forecast quality
The three-number block (always read together, SPEC §6.2):
- (a) Pooled dBrier (n=344): +0.0003 (95% CI [-0.0021, +0.0026]) — strategy 0.0890 vs market 0.0893 -> BEATS baseline
- (b) Traded-subset dBrier (|p_hat-mid| >= 4c, n=27): +0.0033 (95% CI [-0.0248, +0.0313])
- (c) Trade P&L (the number that pays rent): $-161.79 after fees+inference across 80 traded markets
- n gate: 344 < 500 resolved — NOT enough for a forecast-skill claim (SPEC §7)
- FLAG: n=344 < 500: below the forecast-strategy gate (minimum detectable dBrier at n=100 is ~0.0137, 3x the superforecaster edge)
- Calibration ECE: 0.1092 (n=344)
- Murphy decomposition: reliability 0.0225 (miscalibration, lower better) | resolution 0.1841 (sharpness, higher better) | uncertainty 0.2500

| p_hat bin | n | mean p_hat | observed freq |
|---|---|---|---|
| [0.0,0.1) | 112 | 0.022 | 0.000 |
| [0.1,0.2) | 30 | 0.152 | 0.067 |
| [0.2,0.3) | 13 | 0.244 | 0.231 |
| [0.3,0.4) | 17 | 0.348 | 0.529 |
| [0.4,0.5) | 6 | 0.446 | 0.833 |
| [0.5,0.6) | 21 | 0.561 | 0.905 |
| [0.6,0.7) | 29 | 0.652 | 0.931 |
| [0.7,0.8) | 19 | 0.753 | 0.895 |
| [0.8,0.9) | 20 | 0.858 | 1.000 |
| [0.9,1.0) | 77 | 0.963 | 0.883 |

## Risk
- Max drawdown: $481.96 (4.79% of peak)
- Worst single-market loss: $-236.50
- Top-5 markets' share of net P&L: n/a across 80 traded markets

## Trade stats
- Decisions: 112037 | Intents: 344 | Orders: 343
- Contracts requested/ordered/filled: 32327/24009/4552 (fill rate vs ordered: 18.96%)
- Maker share of filled contracts: 100.00%
- Simulated maker fill rate: 18.96% (sanity band 40-50%; >60% = fill model lying)
- Avg edge at entry: 1.61c/contract
- Avg holding period: 127.1h
- Opportunity lifetime (s): median 7200 [p25 3600, p75 28800] over 343 orders

## Capacity curve (depth caps scaled)
| depth x | net P&L (after inference) | contracts filled |
|---|---|---|
| 1x | $-161.79 | 4552 |
| 3x | $-136.44 | 4667 |
| 10x | $-124.50 | 4707 |

## Fee stress (x1.5)
- Net P&L after inference: $-161.90 -> NEGATIVE

## Inference stress (x2 model prices)
- Net P&L after stressed inference: $-161.79 -> NEGATIVE

## Robustness splits
- Time split (60/40 by first entry): train $0.00 over 0 markets | test $-161.79 over 80 markets

| category | markets | net P&L |
|---|---|---|
| Climate and Weather | 9 | $-23.37 |
| Economics | 16 | $104.51 |
| Elections | 16 | $149.46 |
| Entertainment | 5 | $-73.14 |
| Financials | 1 | $3.28 |
| Politics | 27 | $-322.94 |
| Science and Technology | 6 | $0.41 |

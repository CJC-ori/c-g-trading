# Backtest report - r5_endgame_24h

## P&L
- Net P&L (after fees, gross of inference): $160.21
- Net P&L (after fees + inference): $160.21 (1.60% of $10,000.00)
- Fees: $0.15 | Inference: $0.00
- Annualized return on avg deployed ($13.31): 1044.43%

## Forecast quality
The three-number block (always read together, SPEC §6.2):
- (a) Pooled dBrier (n=139): -0.0001 (95% CI [-0.0002, +0.0001]) — strategy 0.0023 vs market 0.0023 -> does NOT beat baseline
- (b) Traded-subset dBrier: no decisions past the gate
- (c) Trade P&L (the number that pays rent): $160.21 after fees+inference across 33 traded markets
- n gate: 139 < 500 resolved — NOT enough for a forecast-skill claim (SPEC §7)
- FLAG: n=139 < 500: below the forecast-strategy gate (minimum detectable dBrier at n=100 is ~0.0137, 3x the superforecaster edge)
- Calibration ECE: 0.0408 (n=139)
- Murphy decomposition: reliability 0.0017 (miscalibration, lower better) | resolution 0.2490 (sharpness, higher better) | uncertainty 0.2490

| p_hat bin | n | mean p_hat | observed freq |
|---|---|---|---|
| [0.0,0.1) | 74 | 0.039 | 0.000 |
| [0.9,1.0) | 65 | 0.958 | 1.000 |

## Risk
- Max drawdown: $251.14 (2.50% of peak)
- Worst single-market loss: $0.04
- Top-5 markets' share of net P&L: 57.69% across 33 traded markets

## Trade stats
- Decisions: 112037 | Intents: 139 | Orders: 139
- Contracts requested/ordered/filled: 48343/20647/3417 (fill rate vs ordered: 16.55%)
- Maker share of filled contracts: 100.00%
- Simulated maker fill rate: 16.55% (sanity band 40-50%; >60% = fill model lying)
- Avg edge at entry: 1.24c/contract
- Avg holding period: 17.1h
- Opportunity lifetime (s): median 7200 [p25 3600, p75 14400] over 139 orders

## Capacity curve (depth caps scaled)
| depth x | net P&L (after inference) | contracts filled |
|---|---|---|
| 1x | $160.21 | 3417 |
| 3x | $160.97 | 3436 |
| 10x | $160.97 | 3436 |

## Fee stress (x1.5)
- Net P&L after inference: $160.15 -> still positive

## Inference stress (x2 model prices)
- Net P&L after stressed inference: $160.21 -> still positive

## Robustness splits
- Time split (60/40 by first entry): train $0.00 over 0 markets | test $160.21 over 33 markets

| category | markets | net P&L |
|---|---|---|
| Climate and Weather | 5 | $20.16 |
| Economics | 9 | $70.20 |
| Elections | 3 | $22.72 |
| Entertainment | 4 | $7.54 |
| Financials | 1 | $0.33 |
| Politics | 8 | $31.95 |
| Science and Technology | 3 | $7.31 |

# Backtest report - baseline_hold_favorite

## P&L
- Net P&L (after fees, gross of inference): $-3.67
- Net P&L (after fees + inference): $-3.67 (-0.04% of $10,000.00)
- Fees: $4.41 | Inference: $0.00
- Annualized return on avg deployed ($20.85): -15.28%

## Forecast quality
The three-number block (always read together, SPEC §6.2):
- (a) Pooled dBrier (n=1158): -0.0044 (95% CI [-0.0087, -0.0001]) — strategy 0.0459 vs market 0.0415 -> does NOT beat baseline
- (b) Traded-subset dBrier (|p_hat-mid| >= 4c, n=65): -0.0916 (95% CI [-0.1649, -0.0182])
- (c) Trade P&L (the number that pays rent): $-3.67 after fees+inference across 32 traded markets
- Calibration ECE: 0.0036 (n=1158)
- Murphy decomposition: reliability 0.0000 (miscalibration, lower better) | resolution 0.0000 (sharpness, higher better) | uncertainty 0.0460

| p_hat bin | n | mean p_hat | observed freq |
|---|---|---|---|
| [0.9,1.0) | 1158 | 0.955 | 0.952 |

## Risk
- Max drawdown: $101.92 (1.02% of peak)
- Worst single-market loss: $-50.47
- Top-5 markets' share of net P&L: n/a across 32 traded markets

## Trade stats
- Decisions: 112037 | Intents: 1158 | Orders: 133
- Contracts requested/ordered/filled: 115800/4553/816 (fill rate vs ordered: 17.92%)
- Maker share of filled contracts: 0.00%
- Simulated maker fill rate: n/a (sanity band 40-50%; >60% = fill model lying)
- Avg edge at entry: 6.74c/contract
- Avg holding period: 410.7h
- Opportunity lifetime (s): median 248400 [p25 36000, p75 669600] over 133 orders

## Capacity curve (depth caps scaled)
| depth x | net P&L (after inference) | contracts filled |
|---|---|---|
| 1x | $-3.67 | 816 |
| 3x | $-48.71 | 1248 |
| 10x | $-49.76 | 1385 |

## Fee stress (x1.5)
- Net P&L after inference: $-5.79 -> NEGATIVE

## Inference stress (x2 model prices)
- Net P&L after stressed inference: $-3.67 -> NEGATIVE

## Robustness splits
- Time split (60/40 by first entry): train $0.00 over 0 markets | test $-3.67 over 32 markets

| category | markets | net P&L |
|---|---|---|
| Economics | 9 | $17.67 |
| Elections | 6 | $11.14 |
| Entertainment | 3 | $1.44 |
| Financials | 1 | $0.09 |
| Politics | 8 | $10.69 |
| Science and Technology | 5 | $-44.70 |

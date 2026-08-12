# Backtest report - r1_taker

## P&L
- Net P&L (after fees, gross of inference): $-16.92
- Net P&L (after fees + inference): $-16.92 (-0.17% of $10,000.00)
- Fees: $12.02 | Inference: $0.00
- Annualized return on avg deployed ($16.74): -87.72%

## Forecast quality
The three-number block (always read together, SPEC §6.2):
- (a) Pooled dBrier (n=1078): -0.0100 (95% CI [-0.0224, +0.0024]) — strategy 0.1076 vs market 0.0975 -> does NOT beat baseline
- (b) Traded-subset dBrier (|p_hat-mid| >= 4c, n=647): -0.0200 (95% CI [-0.0406, +0.0006])
- (c) Trade P&L (the number that pays rent): $-16.92 after fees+inference across 75 traded markets
- Calibration ECE: 0.1033 (n=1078)
- Murphy decomposition: reliability 0.0171 (miscalibration, lower better) | resolution 0.1204 (sharpness, higher better) | uncertainty 0.2117

| p_hat bin | n | mean p_hat | observed freq |
|---|---|---|---|
| [0.0,0.1) | 325 | 0.030 | 0.225 |
| [0.1,0.2) | 62 | 0.144 | 0.323 |
| [0.2,0.3) | 16 | 0.244 | 0.500 |
| [0.7,0.8) | 123 | 0.775 | 0.894 |
| [0.8,0.9) | 102 | 0.849 | 0.961 |
| [0.9,1.0) | 450 | 0.964 | 0.980 |

## Risk
- Max drawdown: $213.66 (2.13% of peak)
- Worst single-market loss: $-70.58
- Top-5 markets' share of net P&L: n/a across 75 traded markets

## Trade stats
- Decisions: 112037 | Intents: 1078 | Orders: 232
- Contracts requested/ordered/filled: 1078000/5991/1678 (fill rate vs ordered: 28.01%)
- Maker share of filled contracts: 0.00%
- Simulated maker fill rate: n/a (sanity band 40-50%; >60% = fill model lying)
- Avg edge at entry: 2.55c/contract
- Avg holding period: 132.6h
- Opportunity lifetime (s): median 180000 [p25 39600, p75 556140] over 232 orders

## Capacity curve (depth caps scaled)
| depth x | net P&L (after inference) | contracts filled |
|---|---|---|
| 1x | $-16.92 | 1678 |
| 3x | $-40.67 | 3354 |
| 10x | $-72.86 | 3749 |

## Fee stress (x1.5)
- Net P&L after inference: $-45.09 -> NEGATIVE

## Inference stress (x2 model prices)
- Net P&L after stressed inference: $-16.92 -> NEGATIVE

## Robustness splits
- Time split (60/40 by first entry): train $0.00 over 0 markets | test $-16.92 over 75 markets

| category | markets | net P&L |
|---|---|---|
| Climate and Weather | 8 | $0.46 |
| Economics | 16 | $43.69 |
| Elections | 13 | $28.21 |
| Entertainment | 5 | $-30.09 |
| Financials | 4 | $9.72 |
| Politics | 24 | $-67.18 |
| Science and Technology | 5 | $-1.73 |

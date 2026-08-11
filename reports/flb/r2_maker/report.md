# Backtest report - r2_maker

## P&L
- Net P&L (after fees, gross of inference): $262.74
- Net P&L (after fees + inference): $262.74 (2.63% of $10,000.00)
- Fees: $0.63 | Inference: $0.00
- Annualized return on avg deployed ($94.63): 241.02%

## Forecast quality
The three-number block (always read together, SPEC §6.2):
- (a) Pooled dBrier (n=246): -0.0009 (95% CI [-0.0033, +0.0016]) — strategy 0.0691 vs market 0.0682 -> does NOT beat baseline
- (b) Traded-subset dBrier (|p_hat-mid| >= 4c, n=18): -0.0135 (95% CI [-0.0445, +0.0175])
- (c) Trade P&L (the number that pays rent): $262.74 after fees+inference across 82 traded markets
- n gate: 246 < 500 resolved — NOT enough for a forecast-skill claim (SPEC §7)
- FLAG: n=246 < 500: below the forecast-strategy gate (minimum detectable dBrier at n=100 is ~0.0137, 3x the superforecaster edge)
- Calibration ECE: 0.0676 (n=246)
- Murphy decomposition: reliability 0.0111 (miscalibration, lower better) | resolution 0.1907 (sharpness, higher better) | uncertainty 0.2476

| p_hat bin | n | mean p_hat | observed freq |
|---|---|---|---|
| [0.0,0.1) | 88 | 0.022 | 0.000 |
| [0.1,0.2) | 27 | 0.154 | 0.074 |
| [0.2,0.3) | 11 | 0.241 | 0.273 |
| [0.3,0.4) | 9 | 0.350 | 0.444 |
| [0.4,0.5) | 5 | 0.450 | 0.800 |
| [0.5,0.6) | 12 | 0.551 | 0.833 |
| [0.6,0.7) | 8 | 0.649 | 0.750 |
| [0.7,0.8) | 16 | 0.753 | 0.938 |
| [0.8,0.9) | 11 | 0.855 | 1.000 |
| [0.9,1.0) | 59 | 0.964 | 0.949 |

## Risk
- Max drawdown: $234.50 (2.33% of peak)
- Worst single-market loss: $-78.26
- Top-5 markets' share of net P&L: 113.54% across 82 traded markets

## Trade stats
- Decisions: 112037 | Intents: 246 | Orders: 226
- Contracts requested/ordered/filled: 25078/9090/5178 (fill rate vs ordered: 56.96%)
- Maker share of filled contracts: 100.00%
- Simulated maker fill rate: 56.96% (sanity band 40-50%; >60% = fill model lying)
- Avg edge at entry: 1.81c/contract
- Avg holding period: 134.6h
- Opportunity lifetime (s): median 7200 [p25 3600, p75 28800] over 226 orders

## Capacity curve (depth caps scaled)
| depth x | net P&L (after inference) | contracts filled |
|---|---|---|
| 1x | $262.74 | 5178 |
| 3x | $183.61 | 9924 |
| 10x | $183.76 | 10020 |

## Fee stress (x1.5)
- Net P&L after inference: $262.48 -> still positive

## Inference stress (x2 model prices)
- Net P&L after stressed inference: $262.74 -> still positive

## Robustness splits
- Time split (60/40 by first entry): train $0.00 over 0 markets | test $262.74 over 82 markets

| category | markets | net P&L |
|---|---|---|
| Climate and Weather | 9 | $-37.17 |
| Economics | 17 | $215.25 |
| Elections | 16 | $130.04 |
| Entertainment | 5 | $9.70 |
| Financials | 2 | $-26.14 |
| Politics | 27 | $-51.74 |
| Science and Technology | 6 | $22.80 |

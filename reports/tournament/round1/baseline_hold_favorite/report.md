# Backtest report - baseline_hold_favorite

## P&L
- Net P&L (after fees, gross of inference): $-1,399.37
- Net P&L (after fees + inference): $-1,399.37 (-13.99% of $10,000.00)
- Fees: $52.09 | Inference: $0.00
- Annualized return on avg deployed ($26.96): -1690.83%

## Forecast quality
The three-number block (always read together, SPEC §6.2):
- (a) Pooled dBrier (n=2957): -0.0079 (95% CI [-0.0124, -0.0035]) — strategy 0.0797 vs market 0.0718 -> does NOT beat baseline
- (b) Traded-subset dBrier (|p_hat-mid| >= 4c, n=665): -0.0359 (95% CI [-0.0554, -0.0164])
- (c) Trade P&L (the number that pays rent): $-1,399.37 after fees+inference across 347 traded markets
- Calibration ECE: 0.0423 (n=2957)
- Murphy decomposition: reliability 0.0018 (miscalibration, lower better) | resolution 0.0000 (sharpness, higher better) | uncertainty 0.0785

| p_hat bin | n | mean p_hat | observed freq |
|---|---|---|---|
| [0.9,1.0) | 2957 | 0.956 | 0.914 |

## Risk
- Max drawdown: $1,509.91 (14.95% of peak)
- Worst single-market loss: $-97.21
- Top-5 markets' share of net P&L: n/a across 347 traded markets

## Trade stats
- Decisions: 185562 | Intents: 2957 | Orders: 652
- Contracts requested/ordered/filled: 295700/28018/9984 (fill rate vs ordered: 35.63%)
- Maker share of filled contracts: 0.00%
- Simulated maker fill rate: n/a (sanity band 40-50%; >60% = fill model lying)
- Avg edge at entry: 4.24c/contract
- Avg holding period: 58.8h
- Opportunity lifetime (s): median 18514 [p25 4204, p75 86986] over 652 orders

## Capacity curve (depth caps scaled)
| depth x | net P&L (after inference) | contracts filled |
|---|---|---|
| 1x | $-1,399.37 | 9984 |
| 3x | $-2,308.14 | 18665 |
| 10x | $-2,535.32 | 20947 |

## Fee stress (x1.5)
- Net P&L after inference: $-1,424.39 -> NEGATIVE

## Inference stress (x2 model prices)
- Net P&L after stressed inference: $-1,399.37 -> NEGATIVE

## Robustness splits
- Time split (60/40 by first entry): train $-1,080.33 over 248 markets | test $-319.04 over 99 markets

| category | markets | net P&L |
|---|---|---|
| Climate and Weather | 277 | $-1,142.47 |
| Economics | 50 | $-80.03 |
| Politics | 19 | $-184.35 |
| World | 1 | $7.48 |

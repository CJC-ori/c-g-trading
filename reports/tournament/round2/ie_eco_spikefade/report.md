# Backtest report - ie_eco_spikefade

## P&L
- Net P&L (after fees, gross of inference): $-980.38
- Net P&L (after fees + inference): $-980.38 (-9.80% of $10,000.00)
- Fees: $15.45 | Inference: $0.00
- Annualized return on avg deployed ($41.44): -770.73%

## Forecast quality
The three-number block (always read together, SPEC §6.2):
- (a) Pooled dBrier (n=1895): -0.0002 (95% CI [-0.0014, +0.0011]) — strategy 0.0149 vs market 0.0147 -> does NOT beat baseline
- (b) Traded-subset dBrier (|p_hat-mid| >= 4c, n=40): +0.0148 (95% CI [-0.0447, +0.0743])
- (c) Trade P&L (the number that pays rent): $-980.38 after fees+inference across 216 traded markets
- Calibration ECE: 0.0166 (n=1895)
- Murphy decomposition: reliability 0.0003 (miscalibration, lower better) | resolution 0.2196 (sharpness, higher better) | uncertainty 0.2340

| p_hat bin | n | mean p_hat | observed freq |
|---|---|---|---|
| [0.0,0.1) | 1185 | 0.030 | 0.016 |
| [0.1,0.2) | 16 | 0.122 | 0.125 |
| [0.9,1.0) | 694 | 0.969 | 0.990 |

## Risk
- Max drawdown: $1,215.84 (12.14% of peak)
- Worst single-market loss: $-392.65
- Top-5 markets' share of net P&L: n/a across 216 traded markets

## Trade stats
- Decisions: 185562 | Intents: 1914 | Orders: 1911
- Contracts requested/ordered/filled: 301302/201077/16178 (fill rate vs ordered: 8.05%)
- Maker share of filled contracts: 96.51%
- Simulated maker fill rate: 7.79% (sanity band 40-50%; >60% = fill model lying)
- Avg edge at entry: 12.01c/contract
- Avg holding period: 18.2h
- Opportunity lifetime (s): median 4058 [p25 2066, p75 10740] over 1911 orders

## Capacity curve (depth caps scaled)
| depth x | net P&L (after inference) | contracts filled |
|---|---|---|
| 1x | $-980.38 | 16178 |
| 3x | $-1,002.21 | 20334 |
| 10x | $-977.28 | 21246 |

## Fee stress (x1.5)
- Net P&L after inference: $-988.03 -> NEGATIVE

## Inference stress (x2 model prices)
- Net P&L after stressed inference: $-980.38 -> NEGATIVE

## Robustness splits
- Time split (60/40 by first entry): train $-1,099.83 over 108 markets | test $119.45 over 108 markets

| category | markets | net P&L |
|---|---|---|
| Climate and Weather | 178 | $-781.91 |
| Economics | 30 | $-157.00 |
| Politics | 3 | $27.88 |
| World | 5 | $-69.35 |

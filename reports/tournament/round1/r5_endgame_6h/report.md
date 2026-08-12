# Backtest report - r5_endgame_6h

## P&L
- Net P&L (after fees, gross of inference): $-679.17
- Net P&L (after fees + inference): $-679.17 (-6.79% of $10,000.00)
- Fees: $2.34 | Inference: $0.00
- Annualized return on avg deployed ($28.97): -763.91%

## Forecast quality
The three-number block (always read together, SPEC §6.2):
- (a) Pooled dBrier (n=1852): -0.0005 (95% CI [-0.0006, -0.0003]) — strategy 0.0132 vs market 0.0128 -> does NOT beat baseline
- (b) Traded-subset dBrier: no decisions past the gate
- (c) Trade P&L (the number that pays rent): $-679.17 after fees+inference across 215 traded markets
- Calibration ECE: 0.0172 (n=1852)
- Murphy decomposition: reliability 0.0003 (miscalibration, lower better) | resolution 0.2228 (sharpness, higher better) | uncertainty 0.2356

| p_hat bin | n | mean p_hat | observed freq |
|---|---|---|---|
| [0.0,0.1) | 1158 | 0.030 | 0.015 |
| [0.9,1.0) | 694 | 0.969 | 0.990 |

## Risk
- Max drawdown: $914.63 (9.13% of peak)
- Worst single-market loss: $-328.51
- Top-5 markets' share of net P&L: n/a across 215 traded markets

## Trade stats
- Decisions: 185562 | Intents: 1852 | Orders: 1852
- Contracts requested/ordered/filled: 267713/170454/12649 (fill rate vs ordered: 7.42%)
- Maker share of filled contracts: 100.00%
- Simulated maker fill rate: 7.42% (sanity band 40-50%; >60% = fill model lying)
- Avg edge at entry: 1.25c/contract
- Avg holding period: 16.5h
- Opportunity lifetime (s): median 4260 [p25 2283, p75 10740] over 1852 orders

## Capacity curve (depth caps scaled)
| depth x | net P&L (after inference) | contracts filled |
|---|---|---|
| 1x | $-679.17 | 12649 |
| 3x | $-615.04 | 16237 |
| 10x | $-591.02 | 17149 |

## Fee stress (x1.5)
- Net P&L after inference: $-680.27 -> NEGATIVE

## Inference stress (x2 model prices)
- Net P&L after stressed inference: $-679.17 -> NEGATIVE

## Robustness splits
- Time split (60/40 by first entry): train $-798.62 over 107 markets | test $119.45 over 108 markets

| category | markets | net P&L |
|---|---|---|
| Climate and Weather | 178 | $-781.91 |
| Economics | 29 | $144.21 |
| Politics | 3 | $27.88 |
| World | 5 | $-69.35 |

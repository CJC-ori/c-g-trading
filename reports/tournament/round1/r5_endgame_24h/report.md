# Backtest report - r5_endgame_24h

## P&L
- Net P&L (after fees, gross of inference): $-9,999.52
- Net P&L (after fees + inference): $-9,999.52 (-100.00% of $10,000.00)
- Fees: $2.10 | Inference: $0.00
- Annualized return on avg deployed ($114.57): -2843.83%

## Forecast quality
The three-number block (always read together, SPEC §6.2):
- (a) Pooled dBrier (n=5600): -0.0003 (95% CI [-0.0004, -0.0002]) — strategy 0.0384 vs market 0.0381 -> does NOT beat baseline
- (b) Traded-subset dBrier: no decisions past the gate
- (c) Trade P&L (the number that pays rent): $-9,999.52 after fees+inference across 846 traded markets
- Calibration ECE: 0.0019 (n=5600)
- Murphy decomposition: reliability 0.0000 (miscalibration, lower better) | resolution 0.1651 (sharpness, higher better) | uncertainty 0.2040

| p_hat bin | n | mean p_hat | observed freq |
|---|---|---|---|
| [0.0,0.1) | 4134 | 0.043 | 0.044 |
| [0.9,1.0) | 1466 | 0.961 | 0.968 |

## Risk
- Max drawdown: $10,019.83 (100.00% of peak)
- Worst single-market loss: $-499.80
- Top-5 markets' share of net P&L: n/a across 846 traded markets

## Trade stats
- Decisions: 185562 | Intents: 5600 | Orders: 4162
- Contracts requested/ordered/filled: 717627/360947/45856 (fill rate vs ordered: 12.70%)
- Maker share of filled contracts: 100.00%
- Simulated maker fill rate: 12.70% (sanity band 40-50%; >60% = fill model lying)
- Avg edge at entry: 1.23c/contract
- Avg holding period: 26.4h
- Opportunity lifetime (s): median 3330 [p25 933, p75 7200] over 4162 orders

## Capacity curve (depth caps scaled)
| depth x | net P&L (after inference) | contracts filled |
|---|---|---|
| 1x | $-9,999.52 | 45856 |
| 3x | $-9,950.47 | 61167 |
| 10x | $-9,948.63 | 62817 |

## Fee stress (x1.5)
- Net P&L after inference: $-9,999.24 -> NEGATIVE

## Inference stress (x2 model prices)
- Net P&L after stressed inference: $-9,999.52 -> NEGATIVE

## Robustness splits
- Time split (60/40 by first entry): train $-9,372.55 over 812 markets | test $-626.97 over 34 markets

| category | markets | net P&L |
|---|---|---|
| Climate and Weather | 777 | $-9,020.87 |
| Economics | 51 | $131.88 |
| Politics | 10 | $-623.61 |
| World | 8 | $-486.92 |

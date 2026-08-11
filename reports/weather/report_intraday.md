# Backtest report - weather-intraday (train split)

## P&L
- Net P&L (after fees, gross of inference): $-588.16
- Net P&L (after fees + inference): $-588.16 (-5.88% of $10,000.00)
- Fees: $0.00 | Inference: $0.00
- Annualized return on avg deployed ($5.90): -67522.22%

## Forecast quality
The three-number block (always read together, SPEC §6.2):
- (a) Pooled dBrier (n=8): -0.3157 (95% CI [-0.5087, -0.1227]) — strategy 0.3720 vs market 0.0564 -> does NOT beat baseline
- (b) Traded-subset dBrier (|p_hat-mid| >= 4c, n=7): -0.3614 (95% CI [-0.5588, -0.1640])
- (c) Trade P&L (the number that pays rent): $-588.16 after fees+inference across 8 traded markets
- n gate: 8 < 500 resolved — NOT enough for a forecast-skill claim (SPEC §7)
- FLAG: n=8 < 500: below the forecast-strategy gate (minimum detectable dBrier at n=100 is ~0.0137, 3x the superforecaster edge)
- Calibration ECE: 0.5436 (n=8)
- Murphy decomposition: reliability 0.3716 (miscalibration, lower better) | resolution 0.2344 (sharpness, higher better) | uncertainty 0.2344

| p_hat bin | n | mean p_hat | observed freq |
|---|---|---|---|
| [0.0,0.1) | 1 | 0.048 | 0.000 |
| [0.1,0.2) | 1 | 0.154 | 1.000 |
| [0.3,0.4) | 1 | 0.330 | 1.000 |
| [0.5,0.6) | 1 | 0.584 | 1.000 |
| [0.6,0.7) | 2 | 0.648 | 1.000 |
| [0.8,0.9) | 2 | 0.833 | 0.000 |

## Risk
- Max drawdown: $1,350.00 (12.54% of peak)
- Worst single-market loss: $-260.00
- Top-5 markets' share of net P&L: n/a across 8 traded markets

## Trade stats
- Decisions: 2448 | Intents: 8 | Orders: 8
- Contracts requested/ordered/filled: 8000/5568/5568 (fill rate vs ordered: 100.00%)
- Maker share of filled contracts: 100.00%
- Simulated maker fill rate: 100.00% (sanity band 40-50%; >60% = fill model lying)
- Avg edge at entry: 50.03c/contract
- Avg holding period: 11.5h
- Opportunity lifetime (s): median 14400 [p25 322, p75 28800] over 8 orders
- FLAG: maker fill rate 100% > 60% — the fill model is lying (sanity band is 40-50%); do not trust maker P&L

## Capacity curve (depth caps scaled)
| depth x | net P&L (after inference) | contracts filled |
|---|---|---|
| 1x | $-588.16 | 5568 |
| 3x | $-824.98 | 6707 |
| 10x | $-919.77 | 7224 |

## Fee stress (x1.5)
- Net P&L after inference: $-588.16 -> NEGATIVE

## Robustness splits
- Time split (60/40 by first entry): train $-12.44 over 3 markets | test $-575.72 over 5 markets

| category | markets | net P&L |
|---|---|---|
| Climate and Weather | 8 | $-588.16 |

## Brier vs market (all scored strikes)
- pooled: n=61  ours 0.1062 vs market 0.0022  Δ(mkt−ours) -0.1039 [-0.1576, -0.0503] -> market wins
- traded_subset: n=8  ours 0.3720 vs market 0.0168  Δ(mkt−ours) -0.3553 [-0.5714, -0.1391] -> market wins
  - phase pm4: n=61 ours 0.1062 vs mkt 0.0022 Δ -0.1039

## Per-city breakdown
| city | markets | dates | contracts | net P&L |
|---|---|---|---|---|
| KXHIGHLAX | 1 | 1 | 1000 | $-90.00 |
| KXHIGHMIA | 3 | 2 | 3000 | $-120.00 |
| KXHIGHTBOS | 1 | 1 | 61 | $4.88 |
| KXHIGHTLV | 1 | 1 | 263 | $-115.72 |
| KXHIGHTMIN | 1 | 1 | 244 | $-7.32 |
| KXHIGHTSEA | 1 | 1 | 1000 | $-260.00 |

## Kill criteria (SYNTHESIS P-2)
- a_postfee_pnl: ok — {'net_pnl_cents': -58816, 'n_resolved_days': 6, 'n_cities': 6}
- b_brier_vs_market: FIRED — {'delta_market_minus_ours': -0.10394484084843893, 'ci95': [-0.15763221438246883, -0.050257467314409034]}
- c_concentration: FIRED — {'top_city_share_of_positive_pnl': 1.0}
- d_capacity: FIRED — {'avg_deployed_usd_per_day_per_city': 18.03222222222222}

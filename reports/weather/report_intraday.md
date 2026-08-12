# Backtest report - weather-intraday (train split)

## P&L
- Net P&L (after fees, gross of inference): $-6,270.85
- Net P&L (after fees + inference): $-6,270.85 (-62.71% of $10,000.00)
- Fees: $0.00 | Inference: $0.00
- Annualized return on avg deployed ($239.64): -18773.20%

## Forecast quality
The three-number block (always read together, SPEC §6.2):
- (a) Pooled dBrier (n=140): -0.0997 (95% CI [-0.1510, -0.0484]) — strategy 0.2495 vs market 0.1498 -> does NOT beat baseline
- (b) Traded-subset dBrier (|p_hat-mid| >= 4c, n=122): -0.1144 (95% CI [-0.1728, -0.0559])
- (c) Trade P&L (the number that pays rent): $-6,270.85 after fees+inference across 59 traded markets
- n gate: 140 < 500 resolved — NOT enough for a forecast-skill claim (SPEC §7)
- FLAG: n=140 < 500: below the forecast-strategy gate (minimum detectable dBrier at n=100 is ~0.0137, 3x the superforecaster edge)
- Calibration ECE: 0.1696 (n=140)
- Murphy decomposition: reliability 0.0350 (miscalibration, lower better) | resolution 0.0359 (sharpness, higher better) | uncertainty 0.2495

| p_hat bin | n | mean p_hat | observed freq |
|---|---|---|---|
| [0.0,0.1) | 22 | 0.035 | 0.318 |
| [0.1,0.2) | 21 | 0.157 | 0.286 |
| [0.2,0.3) | 9 | 0.250 | 0.444 |
| [0.3,0.4) | 16 | 0.336 | 0.500 |
| [0.5,0.6) | 7 | 0.574 | 0.857 |
| [0.6,0.7) | 18 | 0.645 | 0.444 |
| [0.7,0.8) | 12 | 0.730 | 0.667 |
| [0.8,0.9) | 25 | 0.828 | 0.760 |
| [0.9,1.0) | 10 | 0.938 | 0.700 |

## Risk
- Max drawdown: $7,260.85 (66.07% of peak)
- Worst single-market loss: $-500.00
- Top-5 markets' share of net P&L: n/a across 59 traded markets

## Trade stats
- Decisions: 34447 | Intents: 140 | Orders: 140
- Contracts requested/ordered/filled: 140000/125924/46903 (fill rate vs ordered: 37.25%)
- Maker share of filled contracts: 100.00%
- Simulated maker fill rate: 37.25% (sanity band 40-50%; >60% = fill model lying)
- Avg edge at entry: 24.20c/contract
- Avg holding period: 11.0h
- Opportunity lifetime (s): median 28740 [p25 7200, p75 28740] over 140 orders

## Capacity curve (depth caps scaled)
| depth x | net P&L (after inference) | contracts filled |
|---|---|---|
| 1x | $-6,270.85 | 46903 |
| 3x | $-5,562.95 | 50587 |
| 10x | $-5,287.86 | 51083 |

## Fee stress (x1.5)
- Net P&L after inference: $-6,270.85 -> NEGATIVE

## Robustness splits
- Time split (60/40 by first entry): train $-3,710.65 over 38 markets | test $-2,560.20 over 21 markets

| category | markets | net P&L |
|---|---|---|
| Climate and Weather | 59 | $-6,270.85 |

## Brier vs market (all scored strikes)
- pooled: n=886  ours 0.1215 vs market 0.0248  Δ(mkt−ours) -0.0967 [-0.1108, -0.0825] -> market wins
- traded_subset: n=140  ours 0.2496 vs market 0.1453  Δ(mkt−ours) -0.1043 [-0.1572, -0.0513] -> market wins
  - phase pm4: n=886 ours 0.1215 vs mkt 0.0248 Δ -0.0967

## Per-city breakdown
| city | markets | dates | contracts | net P&L |
|---|---|---|---|---|
| KXHIGHAUS | 7 | 7 | 5800 | $-363.77 |
| KXHIGHCHI | 9 | 7 | 5979 | $601.64 |
| KXHIGHDEN | 2 | 1 | 368 | $269.38 |
| KXHIGHLAX | 2 | 1 | 2000 | $-90.00 |
| KXHIGHMIA | 2 | 2 | 1836 | $-145.40 |
| KXHIGHNY | 13 | 10 | 11066 | $-1,429.16 |
| KXHIGHPHIL | 1 | 1 | 943 | $443.21 |
| KXHIGHTATL | 1 | 1 | 1000 | $-20.00 |
| KXHIGHTBOS | 1 | 1 | 1000 | $-280.00 |
| KXHIGHTDAL | 2 | 2 | 2000 | $590.00 |
| KXHIGHTDC | 2 | 2 | 2000 | $-230.00 |
| KXHIGHTHOU | 1 | 1 | 1000 | $-40.00 |
| KXHIGHTLV | 4 | 3 | 3119 | $-1,939.33 |
| KXHIGHTSATX | 2 | 1 | 2000 | $-460.00 |
| KXHIGHTSEA | 10 | 7 | 6792 | $-3,177.42 |

## Kill criteria (SYNTHESIS P-2)
- a_postfee_pnl: ok — {'net_pnl_cents': -627085, 'n_resolved_days': 33, 'n_cities': 15}
- b_brier_vs_market: FIRED — {'delta_market_minus_ours': -0.09668701102988, 'ci95': [-0.11083160482214693, -0.08254241723761307]}
- c_concentration: ok — {'top_city_share_of_positive_pnl': 0.3159492288221486}
- d_capacity: FIRED — {'avg_deployed_usd_per_day_per_city': 28.292626262626264}

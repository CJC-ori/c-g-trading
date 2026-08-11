# Backtest report - weather-dayahead (train split)

## P&L
- Net P&L (after fees, gross of inference): $83.66
- Net P&L (after fees + inference): $83.66 (0.84% of $10,000.00)
- Fees: $0.00 | Inference: $0.00
- Annualized return on avg deployed ($67.99): 833.94%

## Forecast quality
The three-number block (always read together, SPEC §6.2):
- (a) Pooled dBrier (n=58): -0.0301 (95% CI [-0.0637, +0.0035]) — strategy 0.1581 vs market 0.1281 -> does NOT beat baseline
- (b) Traded-subset dBrier (|p_hat-mid| >= 4c, n=55): -0.0320 (95% CI [-0.0674, +0.0033])
- (c) Trade P&L (the number that pays rent): $83.66 after fees+inference across 56 traded markets
- n gate: 58 < 500 resolved — NOT enough for a forecast-skill claim (SPEC §7)
- FLAG: n=58 < 500: below the forecast-strategy gate (minimum detectable dBrier at n=100 is ~0.0137, 3x the superforecaster edge)
- Calibration ECE: 0.1349 (n=58)
- Murphy decomposition: reliability 0.0393 (miscalibration, lower better) | resolution 0.0020 (sharpness, higher better) | uncertainty 0.1189

| p_hat bin | n | mean p_hat | observed freq |
|---|---|---|---|
| [0.0,0.1) | 8 | 0.062 | 0.125 |
| [0.1,0.2) | 16 | 0.157 | 0.125 |
| [0.2,0.3) | 13 | 0.241 | 0.154 |
| [0.3,0.4) | 14 | 0.354 | 0.143 |
| [0.4,0.5) | 4 | 0.425 | 0.250 |
| [0.5,0.6) | 1 | 0.588 | 0.000 |
| [0.6,0.7) | 1 | 0.615 | 0.000 |
| [0.8,0.9) | 1 | 0.812 | 0.000 |

## Risk
- Max drawdown: $720.42 (7.20% of peak)
- Worst single-market loss: $-258.96
- Top-5 markets' share of net P&L: 1421.67% across 56 traded markets

## Trade stats
- Decisions: 2049 | Intents: 58 | Orders: 58
- Contracts requested/ordered/filled: 58000/12160/11871 (fill rate vs ordered: 97.62%)
- Maker share of filled contracts: 100.00%
- Simulated maker fill rate: 97.62% (sanity band 40-50%; >60% = fill model lying)
- Avg edge at entry: 16.53c/contract
- Avg holding period: 38.5h
- Opportunity lifetime (s): median 3600 [p25 215, p75 23167] over 58 orders
- FLAG: maker fill rate 98% > 60% — the fill model is lying (sanity band is 40-50%); do not trust maker P&L

## Capacity curve (depth caps scaled)
| depth x | net P&L (after inference) | contracts filled |
|---|---|---|
| 1x | $83.66 | 11871 |
| 3x | $-378.12 | 24282 |
| 10x | $-376.44 | 27818 |

## Fee stress (x1.5)
- Net P&L after inference: $83.66 -> still positive

## Robustness splits
- Time split (60/40 by first entry): train $-675.75 over 32 markets | test $759.41 over 24 markets

| category | markets | net P&L |
|---|---|---|
| Climate and Weather | 56 | $83.66 |

## Brier vs market (all scored strikes)
- pooled: n=104  ours 0.1425 vs market 0.1119  Δ(mkt−ours) -0.0307 [-0.0569, -0.0045] -> market wins
- traded_subset: n=58  ours 0.1581 vs market 0.1282  Δ(mkt−ours) -0.0300 [-0.0634, +0.0035] -> market wins
  - phase d0am: n=57 ours 0.1362 vs mkt 0.1109 Δ -0.0253
  - phase d1: n=47 ours 0.1502 vs mkt 0.1130 Δ -0.0372

## Per-city breakdown
| city | markets | dates | contracts | net P&L |
|---|---|---|---|---|
| KXHIGHCHI | 2 | 2 | 158 | $-12.23 |
| KXHIGHLAX | 45 | 38 | 11166 | $216.21 |
| KXHIGHMIA | 3 | 2 | 113 | $76.06 |
| KXHIGHNY | 2 | 2 | 373 | $-200.91 |
| KXHIGHTBOS | 1 | 1 | 38 | $-7.22 |
| KXHIGHTLV | 1 | 1 | 6 | $1.74 |
| KXHIGHTMIN | 1 | 1 | 4 | $-2.60 |
| KXHIGHTSEA | 1 | 1 | 13 | $12.61 |

## Kill criteria (SYNTHESIS P-2)
- a_postfee_pnl: ok — {'net_pnl_cents': 8366, 'n_resolved_days': 42, 'n_cities': 8}
- b_brier_vs_market: FIRED — {'delta_market_minus_ours': -0.030667078581571534, 'ci95': [-0.056856753443111696, -0.004477403720031376]}
- c_concentration: ok — {'top_city_share_of_positive_pnl': 0.705139912595395}
- d_capacity: FIRED — {'avg_deployed_usd_per_day_per_city': 8.870059523809525}

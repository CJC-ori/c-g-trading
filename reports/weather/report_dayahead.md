# Backtest report - weather-dayahead (train split)

## P&L
- Net P&L (after fees, gross of inference): $-10,000.00
- Net P&L (after fees + inference): $-10,000.00 (-100.00% of $10,000.00)
- Fees: $0.00 | Inference: $0.00
- Annualized return on avg deployed ($2,533.29): -2832.02%

## Forecast quality
The three-number block (always read together, SPEC §6.2):
- (a) Pooled dBrier (n=1402): -0.0192 (95% CI [-0.0293, -0.0091]) — strategy 0.2249 vs market 0.2057 -> does NOT beat baseline
- (b) Traded-subset dBrier (|p_hat-mid| >= 4c, n=1203): -0.0221 (95% CI [-0.0339, -0.0103])
- (c) Trade P&L (the number that pays rent): $-10,000.00 after fees+inference across 329 traded markets
- Calibration ECE: 0.1148 (n=1402)
- Murphy decomposition: reliability 0.0194 (miscalibration, lower better) | resolution 0.0059 (sharpness, higher better) | uncertainty 0.2110

| p_hat bin | n | mean p_hat | observed freq |
|---|---|---|---|
| [0.0,0.1) | 242 | 0.051 | 0.285 |
| [0.1,0.2) | 442 | 0.158 | 0.256 |
| [0.2,0.3) | 502 | 0.235 | 0.297 |
| [0.3,0.4) | 133 | 0.335 | 0.398 |
| [0.4,0.5) | 30 | 0.450 | 0.633 |
| [0.5,0.6) | 14 | 0.552 | 0.143 |
| [0.6,0.7) | 16 | 0.652 | 0.375 |
| [0.7,0.8) | 6 | 0.745 | 0.500 |
| [0.8,0.9) | 11 | 0.829 | 0.727 |
| [0.9,1.0) | 6 | 0.953 | 0.333 |

## Risk
- Max drawdown: $11,107.40 (100.00% of peak)
- Worst single-market loss: $-500.00
- Top-5 markets' share of net P&L: n/a across 329 traded markets

## Trade stats
- Decisions: 34447 | Intents: 1402 | Orders: 368
- Contracts requested/ordered/filled: 1402000/218427/193425 (fill rate vs ordered: 88.55%)
- Maker share of filled contracts: 100.00%
- Simulated maker fill rate: 88.55% (sanity band 40-50%; >60% = fill model lying)
- Avg edge at entry: 17.80c/contract
- Avg holding period: 28.8h
- Opportunity lifetime (s): median 18000 [p25 7200, p75 57600] over 368 orders
- FLAG: maker fill rate 89% > 60% — the fill model is lying (sanity band is 40-50%); do not trust maker P&L

## Capacity curve (depth caps scaled)
| depth x | net P&L (after inference) | contracts filled |
|---|---|---|
| 1x | $-10,000.00 | 193425 |
| 3x | $-4,402.32 | 333711 |
| 10x | $-4,071.31 | 346204 |

## Fee stress (x1.5)
- Net P&L after inference: $-10,000.00 -> NEGATIVE

## Robustness splits
- Time split (60/40 by first entry): train $-9,305.96 over 316 markets | test $-694.04 over 13 markets

| category | markets | net P&L |
|---|---|---|
| Climate and Weather | 329 | $-10,000.00 |

## Brier vs market (all scored strikes)
- pooled: n=1772  ours 0.2057 vs market 0.1835  Δ(mkt−ours) -0.0222 [-0.0308, -0.0136] -> market wins
- traded_subset: n=368  ours 0.2322 vs market 0.2055  Δ(mkt−ours) -0.0267 [-0.0472, -0.0062] -> market wins
  - phase d0am: n=886 ours 0.2046 vs mkt 0.1788 Δ -0.0258
  - phase d1: n=886 ours 0.2069 vs mkt 0.1882 Δ -0.0186

## Per-city breakdown
| city | markets | dates | contracts | net P&L |
|---|---|---|---|---|
| KXHIGHAUS | 19 | 14 | 11583 | $-1,532.65 |
| KXHIGHCHI | 56 | 25 | 25855 | $-749.96 |
| KXHIGHDEN | 2 | 1 | 954 | $838.14 |
| KXHIGHLAX | 92 | 30 | 57485 | $-6,007.69 |
| KXHIGHMIA | 57 | 27 | 34053 | $-1,377.67 |
| KXHIGHNY | 49 | 25 | 29290 | $-758.52 |
| KXHIGHPHIL | 1 | 1 | 1000 | $-120.00 |
| KXHIGHTATL | 14 | 8 | 9457 | $1,520.42 |
| KXHIGHTBOS | 7 | 6 | 4095 | $-271.26 |
| KXHIGHTDAL | 2 | 2 | 1724 | $184.44 |
| KXHIGHTDC | 1 | 1 | 1000 | $-170.00 |
| KXHIGHTHOU | 2 | 2 | 1255 | $197.80 |
| KXHIGHTLV | 2 | 1 | 1961 | $50.28 |
| KXHIGHTMIN | 1 | 1 | 769 | $-499.85 |
| KXHIGHTPHX | 6 | 3 | 3200 | $-88.66 |
| KXHIGHTSATX | 2 | 1 | 1718 | $-38.34 |
| KXHIGHTSEA | 5 | 5 | 2783 | $-250.02 |
| KXHIGHTSFO | 11 | 10 | 5243 | $-926.46 |

## Kill criteria (SYNTHESIS P-2)
- a_postfee_pnl: ok — {'net_pnl_cents': -1000000, 'n_resolved_days': 33, 'n_cities': 18}
- b_brier_vs_market: FIRED — {'delta_market_minus_ours': -0.022218329474205305, 'ci95': [-0.03080241096385747, -0.013634247984553143]}
- c_concentration: ok — {'top_city_share_of_positive_pnl': 0.544742536939106}
- d_capacity: FIRED — {'avg_deployed_usd_per_day_per_city': 137.2104377104377}

# Backtest report - weather-dayahead (train split)

## P&L
- Net P&L (after fees, gross of inference): $-344.58
- Net P&L (after fees + inference): $-344.58 (-3.45% of $10,000.00)
- Fees: $0.00 | Inference: $0.00
- Annualized return on avg deployed ($88.49): -2639.02%

## Forecast quality
The three-number block (always read together, SPEC §6.2):
- (a) Pooled dBrier (n=67): -0.0377 (95% CI [-0.0707, -0.0047]) — strategy 0.1832 vs market 0.1455 -> does NOT beat baseline
- (b) Traded-subset dBrier (|p_hat-mid| >= 4c, n=62): -0.0385 (95% CI [-0.0741, -0.0029])
- (c) Trade P&L (the number that pays rent): $-344.58 after fees+inference across 55 traded markets
- n gate: 67 < 500 resolved — NOT enough for a forecast-skill claim (SPEC §7)
- FLAG: n=67 < 500: below the forecast-strategy gate (minimum detectable dBrier at n=100 is ~0.0137, 3x the superforecaster edge)
- Calibration ECE: 0.1505 (n=67)
- Murphy decomposition: reliability 0.0397 (miscalibration, lower better) | resolution 0.0061 (sharpness, higher better) | uncertainty 0.1470

| p_hat bin | n | mean p_hat | observed freq |
|---|---|---|---|
| [0.0,0.1) | 8 | 0.067 | 0.125 |
| [0.1,0.2) | 19 | 0.156 | 0.263 |
| [0.2,0.3) | 18 | 0.242 | 0.111 |
| [0.3,0.4) | 13 | 0.347 | 0.231 |
| [0.4,0.5) | 5 | 0.435 | 0.200 |
| [0.5,0.6) | 2 | 0.557 | 0.000 |
| [0.6,0.7) | 1 | 0.615 | 0.000 |
| [0.8,0.9) | 1 | 0.812 | 0.000 |

## Risk
- Max drawdown: $1,507.34 (13.72% of peak)
- Worst single-market loss: $-499.96
- Top-5 markets' share of net P&L: n/a across 55 traded markets

## Trade stats
- Decisions: 2448 | Intents: 67 | Orders: 67
- Contracts requested/ordered/filled: 67000/17658/16965 (fill rate vs ordered: 96.08%)
- Maker share of filled contracts: 100.00%
- Simulated maker fill rate: 96.08% (sanity band 40-50%; >60% = fill model lying)
- Avg edge at entry: 14.35c/contract
- Avg holding period: 33.7h
- Opportunity lifetime (s): median 7200 [p25 534, p75 45103] over 67 orders
- FLAG: maker fill rate 96% > 60% — the fill model is lying (sanity band is 40-50%); do not trust maker P&L

## Capacity curve (depth caps scaled)
| depth x | net P&L (after inference) | contracts filled |
|---|---|---|
| 1x | $-344.58 | 16965 |
| 3x | $-603.59 | 28752 |
| 10x | $-611.01 | 33188 |

## Fee stress (x1.5)
- Net P&L after inference: $-344.58 -> NEGATIVE

## Robustness splits
- Time split (60/40 by first entry): train $-428.03 over 28 markets | test $83.45 over 27 markets

| category | markets | net P&L |
|---|---|---|
| Climate and Weather | 55 | $-344.58 |

## Brier vs market (all scored strikes)
- pooled: n=122  ours 0.1657 vs market 0.1388  Δ(mkt−ours) -0.0269 [-0.0511, -0.0028] -> market wins
- traded_subset: n=67  ours 0.1832 vs market 0.1624  Δ(mkt−ours) -0.0208 [-0.0514, +0.0097] -> market wins
  - phase d0am: n=61 ours 0.1539 vs mkt 0.1382 Δ -0.0157
  - phase d1: n=61 ours 0.1775 vs mkt 0.1394 Δ -0.0381

## Per-city breakdown
| city | markets | dates | contracts | net P&L |
|---|---|---|---|---|
| KXHIGHCHI | 2 | 2 | 158 | $-12.23 |
| KXHIGHLAX | 42 | 34 | 16230 | $-464.09 |
| KXHIGHMIA | 4 | 2 | 334 | $170.56 |
| KXHIGHNY | 3 | 3 | 182 | $-43.35 |
| KXHIGHTBOS | 1 | 1 | 38 | $-7.22 |
| KXHIGHTLV | 1 | 1 | 6 | $1.74 |
| KXHIGHTMIN | 1 | 1 | 4 | $-2.60 |
| KXHIGHTSEA | 1 | 1 | 13 | $12.61 |

## Kill criteria (SYNTHESIS P-2)
- a_postfee_pnl: ok — {'net_pnl_cents': -34458, 'n_resolved_days': 39, 'n_cities': 8}
- b_brier_vs_market: FIRED — {'delta_market_minus_ours': -0.02691298284933986, 'ci95': [-0.05105140883204308, -0.002774556866636642]}
- c_concentration: FIRED — {'top_city_share_of_positive_pnl': 0.9223946784922394}
- d_capacity: FIRED — {'avg_deployed_usd_per_day_per_city': 13.303141025641025}

# Backtest report - weather-intraday (train split)

## P&L
- Net P&L (after fees, gross of inference): $-638.16
- Net P&L (after fees + inference): $-638.16 (-6.38% of $10,000.00)
- Fees: $0.00 | Inference: $0.00
- Annualized return on avg deployed ($6.58): -65700.15%

## Forecast quality
The three-number block (always read together, SPEC §6.2):
- (a) Pooled dBrier (n=10): -0.2986 (95% CI [-0.4579, -0.1394]) — strategy 0.3434 vs market 0.0448 -> does NOT beat baseline
- (b) Traded-subset dBrier (|p_hat-mid| >= 4c, n=9): -0.3323 (95% CI [-0.4944, -0.1702])
- (c) Trade P&L (the number that pays rent): $-638.16 after fees+inference across 10 traded markets
- n gate: 10 < 500 resolved — NOT enough for a forecast-skill claim (SPEC §7)
- FLAG: n=10 < 500: below the forecast-strategy gate (minimum detectable dBrier at n=100 is ~0.0137, 3x the superforecaster edge)
- Calibration ECE: 0.3436 (n=10)
- Murphy decomposition: reliability 0.2344 (miscalibration, lower better) | resolution 0.1333 (sharpness, higher better) | uncertainty 0.2500

| p_hat bin | n | mean p_hat | observed freq |
|---|---|---|---|
| [0.0,0.1) | 1 | 0.048 | 0.000 |
| [0.1,0.2) | 1 | 0.154 | 1.000 |
| [0.3,0.4) | 2 | 0.321 | 0.500 |
| [0.5,0.6) | 1 | 0.584 | 1.000 |
| [0.6,0.7) | 3 | 0.632 | 0.667 |
| [0.8,0.9) | 2 | 0.833 | 0.000 |

## Risk
- Max drawdown: $757.72 (7.49% of peak)
- Worst single-market loss: $-260.00
- Top-5 markets' share of net P&L: n/a across 10 traded markets

## Trade stats
- Decisions: 2049 | Intents: 10 | Orders: 10
- Contracts requested/ordered/filled: 10000/7568/7568 (fill rate vs ordered: 100.00%)
- Maker share of filled contracts: 100.00%
- Simulated maker fill rate: 100.00% (sanity band 40-50%; >60% = fill model lying)
- Avg edge at entry: 48.21c/contract
- Avg holding period: 12.5h
- Opportunity lifetime (s): median 2970 [p25 259, p75 25200] over 10 orders
- FLAG: maker fill rate 100% > 60% — the fill model is lying (sanity band is 40-50%); do not trust maker P&L

## Capacity curve (depth caps scaled)
| depth x | net P&L (after inference) | contracts filled |
|---|---|---|
| 1x | $-638.16 | 7568 |
| 3x | $-874.98 | 8707 |
| 10x | $-969.77 | 9224 |

## Fee stress (x1.5)
- Net P&L after inference: $-638.16 -> NEGATIVE

## Robustness splits
- Time split (60/40 by first entry): train $-82.44 over 4 markets | test $-555.72 over 6 markets

| category | markets | net P&L |
|---|---|---|
| Climate and Weather | 10 | $-638.16 |

## Brier vs market (all scored strikes)
- pooled: n=61  ours 0.1062 vs market 0.0023  Δ(mkt−ours) -0.1038 [-0.1575, -0.0501] -> market wins
- traded_subset: n=10  ours 0.3434 vs market 0.0133  Δ(mkt−ours) -0.3302 [-0.5082, -0.1522] -> market wins
  - phase pm4: n=61 ours 0.1062 vs mkt 0.0023 Δ -0.1038

## Per-city breakdown
| city | markets | dates | contracts | net P&L |
|---|---|---|---|---|
| KXHIGHLAX | 1 | 1 | 1000 | $-80.00 |
| KXHIGHMIA | 4 | 2 | 4000 | $-150.00 |
| KXHIGHNY | 1 | 1 | 1000 | $-30.00 |
| KXHIGHTBOS | 1 | 1 | 61 | $4.88 |
| KXHIGHTLV | 1 | 1 | 263 | $-115.72 |
| KXHIGHTMIN | 1 | 1 | 244 | $-7.32 |
| KXHIGHTSEA | 1 | 1 | 1000 | $-260.00 |

## Kill criteria (SYNTHESIS P-2)
- a_postfee_pnl: ok — {'net_pnl_cents': -63816, 'n_resolved_days': 7, 'n_cities': 7}
- b_brier_vs_market: FIRED — {'delta_market_minus_ours': -0.10383131625827496, 'ci95': [-0.15754806385834239, -0.050114568658207535]}
- c_concentration: FIRED — {'top_city_share_of_positive_pnl': 1.0}
- d_capacity: FIRED — {'avg_deployed_usd_per_day_per_city': 14.268571428571429}

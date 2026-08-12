# Backtest report - ia_econ_only

## P&L
- Net P&L (after fees, gross of inference): $144.21
- Net P&L (after fees + inference): $144.21 (1.44% of $10,000.00)
- Fees: $2.34 | Inference: $0.00
- Annualized return on avg deployed ($2.89): 1625.21%

## Forecast quality
The three-number block (always read together, SPEC §6.2):
- (a) Pooled dBrier (n=65): +0.0001 (95% CI [-0.0002, +0.0003]) — strategy 0.0015 vs market 0.0016 -> BEATS baseline
- (b) Traded-subset dBrier: no decisions past the gate
- (c) Trade P&L (the number that pays rent): $144.21 after fees+inference across 29 traded markets
- n gate: 65 < 500 resolved — NOT enough for a forecast-skill claim (SPEC §7)
- FLAG: n=65 < 500: below the forecast-strategy gate (minimum detectable dBrier at n=100 is ~0.0137, 3x the superforecaster edge)
- Calibration ECE: 0.0303 (n=65)
- Murphy decomposition: reliability 0.0010 (miscalibration, lower better) | resolution 0.2286 (sharpness, higher better) | uncertainty 0.2286

| p_hat bin | n | mean p_hat | observed freq |
|---|---|---|---|
| [0.0,0.1) | 42 | 0.037 | 0.000 |
| [0.9,1.0) | 23 | 0.982 | 1.000 |

## Risk
- Max drawdown: $5.39 (0.05% of peak)
- Worst single-market loss: $0.07
- Top-5 markets' share of net P&L: 35.00% across 29 traded markets

## Trade stats
- Decisions: 185562 | Intents: 65 | Orders: 65
- Contracts requested/ordered/filled: 45254/15489/4132 (fill rate vs ordered: 26.68%)
- Maker share of filled contracts: 100.00%
- Simulated maker fill rate: 26.68% (sanity band 40-50%; >60% = fill model lying)
- Avg edge at entry: 1.25c/contract
- Avg holding period: 11.2h
- Opportunity lifetime (s): median 1509 [p25 497, p75 4681] over 65 orders

## Capacity curve (depth caps scaled)
| depth x | net P&L (after inference) | contracts filled |
|---|---|---|
| 1x | $144.21 | 4132 |
| 3x | $171.19 | 5131 |
| 10x | $174.40 | 5291 |

## Fee stress (x1.5)
- Net P&L after inference: $143.11 -> still positive

## Inference stress (x2 model prices)
- Net P&L after stressed inference: $144.21 -> still positive

## Robustness splits
- Time split (60/40 by first entry): train $103.94 over 18 markets | test $40.27 over 11 markets

| category | markets | net P&L |
|---|---|---|
| Economics | 29 | $144.21 |

# Rung 2 — ForecastBench market-consensus baseline

*Generated 2026-08-11T22:47:30+00:00 — `python -m bot.evals.forecastbench`*

**Verdict: SELF-TEST PASS — baseline established, no forecaster scored**

## Self-test — did we reproduce §1.6?

| check | expected | actual | tol | pass |
|---|---|---|---|---|
| pooled market Brier | 0.1172 | 0.1172 | 0.0050 | yes |
| pooled sd | 0.1785 | 0.1785 | 0.0100 | yes |
| n resolved market questions | 1216 | 1216 | 60.8000 | yes |
| infer Brier | 0.0651 | 0.0651 | 0.0050 | yes |
| manifold Brier | 0.0963 | 0.0963 | 0.0050 | yes |
| polymarket Brier | 0.1253 | 0.1253 | 0.0050 | yes |
| metaculus Brier | 0.1554 | 0.1554 | 0.0050 | yes |

Targets are `research/benchmarks.md` §1.6, computed on the same window (`forecast_due_date >= 2026-02-01`, market sources only, `resolved == true`). The dataset refreshes nightly and resolutions accrete, so `n` is checked to ±5% and Brier to ±0.005. A failure here means the join or the `resolved == true` filter is wrong — and a broken `resolved` filter scores forecasts against *market prices* instead of outcomes, which looks plausible and is completely worthless (§1.3).

## The baseline table (the bar for rung 2)

| scope | n | base rate | Brier | Brier Index | log loss |
|---|---|---|---|---|---|
| pooled (2026-02-01+) | 1216 | 0.3586 | 0.1172 | 65.7609 | — |
| infer | 102 | 0.2059 | 0.0651 | 74.4911 | 0.2171 |
| manifold | 357 | 0.4006 | 0.0963 | 68.9731 | 0.3110 |
| polymarket | 534 | 0.3820 | 0.1253 | 64.6045 | 0.3878 |
| metaculus | 223 | 0.3049 | 0.1554 | 60.5830 | 0.4765 |

Pooled market-price Brier **0.1172** (sd 0.1785, 95% CI [0.1072, 0.1273], n=1216) is **the bar**. Always-0.5 scores 0.2500 on the same set, which fixes the scale.

This is higher (worse) than ForecastBench's published leaderboard baseline of 0.077 for two reasons (§1.6): their baseline uses the market value on the *due date*, 10 days better-informed than the `freeze_datetime_value` we have offline; and our subset is only questions that have already resolved in the 2026 window, which skews short-horizon. **0.1172 is our internal bar** because it is what our own code produces on our own subset — the apples-to-apples comparison for anything we score the same way.

Read the per-source spread as a warning, not a menu: INFER (0.065) and Metaculus (0.155) differ by more than any forecaster edge we could plausibly find, so a forecaster's apparent skill can be manufactured entirely by which sources it happens to cover. Always report `by_source` alongside the pooled number.

## Market Brier by forecast-due-date cohort

| forecast_due_date | n | base rate | market Brier |
|---|---|---|---|
| 2026-02-01 | 57 | 0.1754 | 0.0613 |
| 2026-02-15 | 61 | 0.1967 | 0.0575 |
| 2026-03-01 | 130 | 0.3462 | 0.1156 |
| 2026-03-15 | 147 | 0.2857 | 0.1133 |
| 2026-03-29 | 127 | 0.4173 | 0.1506 |
| 2026-04-12 | 119 | 0.4370 | 0.1289 |
| 2026-04-26 | 105 | 0.3524 | 0.1522 |
| 2026-05-10 | 96 | 0.3958 | 0.1441 |
| 2026-05-24 | 94 | 0.3830 | 0.1289 |
| 2026-06-07 | 103 | 0.3981 | 0.0995 |
| 2026-06-21 | 76 | 0.3947 | 0.0936 |
| 2026-07-05 | 61 | 0.3934 | 0.0903 |
| 2026-07-19 | 40 | 0.4000 | 0.1148 |

## Market-price calibration at T−10d

| price bucket | n | mean price | realized freq |
|---|---|---|---|
| [0.00, 0.05) | 230 | 0.0220 | 0.0087 |
| [0.05, 0.15) | 173 | 0.1002 | 0.0347 |
| [0.15, 0.35) | 247 | 0.2395 | 0.1862 |
| [0.35, 0.65) | 239 | 0.4949 | 0.4351 |
| [0.65, 0.85) | 166 | 0.7385 | 0.7590 |
| [0.85, 0.95) | 79 | 0.8984 | 0.9114 |
| [0.95, 1.01) | 82 | 0.9719 | 0.9756 |

Textbook longshot bias in the 5-35¢ band. **`research/benchmarks.md` §1.7 already showed this does not survive out of sample** — a logistic recalibration fitted pre-2026 gained +0.0027 in-sample and *lost* 0.0005 on this window. Do not build a price-only strategy on this table without a time-forward split.

## Statistical power at this n

| n | min detectable ΔBrier | equivalent RMS disagreement |
|---|---|---|
| 100 | 0.0137 | 11.7¢ |
| 200 | 0.0097 | 9.8¢ |
| 500 | 0.0061 | 7.8¢ |
| 1216 | 0.0039 | 6.3¢ |
| 2000 | 0.0031 | 5.5¢ |

At paired-difference sd 0.0698 (measured, §4.3). The gate is **n ≥ 500** (SYNTHESIS §2.4): at n=100 the smallest detectable edge is 0.0137, three times the entire superforecaster-over-market edge of 0.004, so a 'win' at n=100 is noise. Tiers: +0.004 superforecaster-class, +0.02 top-2026-bot-class (be suspicious), >+0.05 you have a leak.

## Scored forecaster

No forecasts supplied. Pass `--forecasts p.json` (`{"<due>|<source>|<id>": p_yes}`) or call `bot.evals.forecastbench.score(...)` from the forecaster's own harness.

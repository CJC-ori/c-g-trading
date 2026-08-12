# Rung 2a — FutureSearch 153-question replication (3-way Brier)

*Generated 2026-08-11T22:47:30+00:00 — `python -m bot.evals.fs_replication`*

**Verdict: UNDERPOWERED — n=61 resolved of 153 (gate is n≥500); numbers are directional only**

## 3-way Brier

| split | n | base rate | Brier(FS median) | Brier(Kalshi price) | Brier(ours) | ΔBrier FS−market [95% CI] |
|---|---|---|---|---|---|---|
| all_resolved | 61 | 0.33 | 0.0636 | 0.0644 | not wired | +0.0008 [-0.0082, +0.0099] |
| pre_cutoff_2026_05_01 | 21 | 0.29 | 0.0394 | 0.0363 | not wired | -0.0031 [-0.0107, +0.0045] |
| post_cutoff_2026_05_01 | 40 | 0.35 | 0.0762 | 0.0791 | not wired | +0.0029 [-0.0103, +0.0161] |
| unknown_resolution_date | 0 | — | — | — | — | — |

Δ is oriented *market minus forecaster*: positive means the forecaster beat the price. Every split is reported with its paired CI, never as a point estimate (SYNTHESIS §2.4). Column 3 is scored only where `--forecasts` supplied a probability for that ticker.

## Statistical gates (SYNTHESIS §2.4)

| gate | required | actual | verdict |
|---|---|---|---|
| n (SYNTHESIS §2.4) | 500 | 61 | UNDERPOWERED |
| min detectable ΔBrier at this n | ≤ 0.004 (superforecaster edge) | 0.0090 | FAIL |
| paired CI excludes 0 | yes | no | FAIL |
| tier (§4.4) | — | parity (CI spans 0, point estimate >= 0) | — |

## Judge-cutoff hygiene

Judge-model cutoff is **2026-05-01** (SYNTHESIS §3 correction #1: ORCHESTRATION.md's "Jan 2026" is wrong for Opus 5). Splits:

- **pre-cutoff** (resolves < 2026-05-01): n=21. Contaminated by construction for any LLM forecaster we run — the outcome is inside the model's training window. Useful only as a leak *detector*: if our forecaster scores dramatically better here than post-cutoff, that gap is memorisation, not skill.
- **post-cutoff** (resolves ≥ 2026-05-01): n=40. The only admissible evidence.
- **unknown resolution date**: n=0 (free-text date column the parser could not read).

FutureSearch's own forecasts carry no such caveat — their snapshot is genuinely point-in-time at 2026-02-26 16:09 UTC, and the Kalshi price column is the price at that same instant, so the FS-vs-market comparison is clean at every n.

## Coverage — and a correction to research/futuresearch.md §7

| metric | value |
|---|---|
| questions in the published CSV | 153 |
| resolved (scorable) | 61 |
| resolution rate | 40% |
| our forecasts supplied | 0 |

| outcome source | n |
|---|---|
| local_db | 61 |

**Why the rest are unscorable:**

| reason | n |
|---|---|
| still active in local DB | 76 |
| not in local DB | 13 |
| scalar settlement (excluded from Brier) | 3 |

⚠ **`research/futuresearch.md` §7 is wrong about this dataset.** It says the 153 markets "all resolved months ago, and the outcomes are free". Neither half holds (measured 2026-08-11):

1. **Half of them have not resolved.** Only 65 of 153 carry a resolution date on or before today; 87 are still ahead of us and the set runs out to 2030-01-01. FutureSearch's filter was 3–97¢ + volume, *not* a horizon filter — their >10-day rule sets a floor, not a ceiling. 76 tickers are confirmed still active on Kalshi right now.
2. **0 of the ones that did resolve are gone.** Kalshi purges settled markets from the public API ~90 days after close, and our local store post-dates the purge window for the Feb–Apr resolutions. Those tickers 404 on `/markets/{ticker}`, return an empty list from `/markets?event_ticker=…&status=settled`, and have no candlestick history locally either. Their outcomes are not recoverable from Kalshi at all.

Consequences: this rung is a **calibration smoke test, not evidence**. To make it evidence, either (a) backfill outcomes into `bot/evals/fs_resolution_overrides.json` from citable public sources, or (b) re-run it periodically as the 76 still-active markets settle. Option (b) is free and grows n automatically *provided* the data layer snapshots settled markets on a schedule tighter than Kalshi's ~90-day purge — worth confirming with whoever owns `bot/data/`, because without that snapshot the same outcomes evaporate again.

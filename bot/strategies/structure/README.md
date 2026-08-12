# P-5 structure scanner — build notes and tuning log

Everything for SYNTHESIS §1 P-5 lives here, including this package's own
Kalshi candle puller and Polymarket client (nothing outside
`bot/strategies/structure/` was modified; `bot/backtest/`, `bot/data/`,
`bot/evals/` are read-only dependencies).

## Layout

| file | what |
|---|---|
| `kalshi_events.py` | multi-outcome event universe over `data/kalshi.db` (+ the mutual-exclusivity audit) |
| `kalshi_candles.py` | batch/historical candle puller -> `data/structure/structure.db` |
| `pull_kalshi.py` | runner: hourly candles for every settled multi-outcome event leg |
| `overround.py` | **R3 math** — NO-set / YES-set / literal-ask-sum, fees in centicents |
| `scan_r3.py` | the scan: forward-filled hourly grid, frequency / edge / fillable size |
| `run_r3.py` | R3 strategies through the harness (per-event runs) |
| `consistency.py` | ladder monotonicity + binary-vs-ladder coherence, with the control-rule table |
| `scan_consistency.py` / `run_consistency.py` | coherence history + divergence backtest |
| `polymarket.py` | self-contained Gamma + CLOB client (no keys) |
| `pairs.py` | **the curated pair map** with per-pair structural audit |
| `pull_r7.py` | pair audit + both venues' 1-minute history |
| `run_r7.py` | R7 divergence backtest vs the no-signal control |
| `strategy.py` | engine-facing `Strategy` implementations |
| `test_*.py` | 58 tests: scan math, pair audit, coherence, trigger discipline |

Data written: `data/structure/structure.db` (Kalshi candles) and
`data/polymarket/{pm_prices.db,pm_markets.json}`. Both are under the repo
root `data/`, which `.gitignore` already excludes via the root-anchored
`/data/` entry (verified: `git check-ignore -v` matches both paths).

## Train/test discipline (SPEC §8)

The held-out split is **the last 40% by time** and nothing was tuned on it:

* **R3** — no parameters were fitted at all. The gate (Σ > 100¢ + fees +
  1¢ buffer) is the one SYNTHESIS specifies; the depth cap (25%) and the
  bankroll are the harness defaults. The scan is a measurement, so it runs
  over the whole history; the *strategy* backtest reports train (first 60%
  of events by close date) and test separately and they are quoted
  separately in ANALYSIS.md.
* **Staleness / fee sensitivity** were run at 1 h / 6 h / 24 h / 72 h and
  ×1.5 fees as *robustness reporting*, not as selection: the headline uses
  the middle setting (24 h) chosen before looking at results, and the
  conclusion is identical at every setting (0.66–0.76 firing events/week).
* **R7** — the gate (4¢), persistence (10 min) and exit (<1¢) are
  SYNTHESIS's numbers, not fitted. Split is by resolution date: train =
  the 13 pairs resolving through 2025-12-10, test = the 10 pairs resolving
  2026-01-28 onward. One evaluation on test.
* **Consistency** — the control mappings are read off the venues' own
  resolution rules; the only free parameter (divergence gate) reuses R7's
  4¢.

Nothing in this package uses an LLM, so the contamination window does not
apply (SPEC §1: price-only strategies may use full history).

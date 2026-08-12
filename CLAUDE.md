# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A prediction-market trading bot for Kalshi (Polymarket secondary), built around
one non-negotiable idea: **every strategy is judged by the deterministic
backtest defined in `bot/backtest/SPEC.md`** — point-in-time replay with
structural no-lookahead, exact per-series fees, calibrated maker-queue fills,
and harness-enforced Kelly sizing. Strategies that can't win there don't
graduate. Read `reports/FINDINGS.md` first for what has already been tried,
what won, and what was killed (with pre-registered kill criteria) — do not
re-litigate killed strategies without new evidence.

## Commands

```bash
python -m pytest bot/ -q                      # full test suite (~650 tests)
python -m pytest bot/backtest/ -q             # harness only
python -m pytest bot/backtest/test_fills.py -q -k maker   # single file / pattern
python -m bot.backtest.validate_wiring        # real-data sanity gauntlet (needs data/kalshi.db)
python -m bot.evals.ladder                    # external-reconciliation eval gates
python -m bot.data.pull stats                 # local dataset coverage
python -m bot.data.pull markets --status open # data pulls (see --help; hist-* subcommands hit the archive tier)
python -m bot.strategies.run_flb --help       # FLB/R5 backtest runner (tournament used this)
python -m bot.strategies.weather.run_backtest # weather strategy runner
python -m bot.forecaster.run_rung2 --help     # LLM forecaster eval (ForecastBench rung 2)
```

Python 3.11, stdlib + `httpx` only (no pandas). Real-data tests auto-skip when
`data/kalshi.db` is absent. `/data/` (root-anchored) is gitignored — note the
distinction from `bot/data/`, which is source code.

## Architecture (the parts that span multiple files)

**Data flow:** `bot/data/` pulls Kalshi's public REST *and* the unauthenticated
`/historical/*` archive tier into SQLite at `data/kalshi.db` (markets /
candlesticks / trades / series / events, raw API JSON retained per row).
`bot/backtest/dataport.py` is the only boundary between that DB and the
harness: it converts dollar-string prices to integer cents with conservative
directional rounding, maps settlement vocabulary (`yes`/`no`/`''`=never
settled/`scalar` with fractional payout), and builds immutable `MarketView`
slices. Strategies never touch the DB.

**Backtest engine:** `bot/backtest/` — `types.py` (frozen dataclasses,
`Strategy` protocol: `on_decision_point(view, portfolio) -> [OrderIntent]`),
`engine.py` (replay loop, order lifecycle incl. cancel/replace),
`fills.py` (pure fill functions; maker-queue model; NO-side complementarity is
structural — buy NO @ p ≡ sell YES @ 100−p), `fees.py` (exact centicent
ceiling formula, per-series `FeeSchedule` — pass
`EngineConfig(fee_schedule=FeeSchedule.load_default())` for any real-data run;
the legacy default overstates taker fees ~2×), `risk.py` (fee-aware ¼-Kelly +
caps; clamps intents, never inflates), `metrics.py` (three-number Brier block,
calibration, capacity/fee/inference stress), `costs.py` (CostLedger — LLM
strategies charge real token costs into P&L).

**Strategies:** `bot/strategies/<family>/` each with a runner CLI and a
`reports/<family>/ANALYSIS.md` carrying pre-registered kill criteria and
verdicts. The tournament-graduated config (R5-6h Economics-only maker-join
90–98¢) is frozen in `reports/tournament/round4/`.

**LLM forecaster:** `bot/forecaster/` — tiered pipeline (Haiku screen → Sonnet
dossier per event → multi-pass judge, geometric-mean-of-odds). `llm.py` has
three backends; no API key exists in the dev container, so evaluation runs
execute prompts via model-pinned subagents and write through to the replay
cache (`data/forecaster-cache/`, keyed by prompt-hash — cached runs replay
deterministically at $0). `retrieval.py` is point-in-time-disciplined (double
date gates; live search engines are banned in backtests). `event_anchor.py`
derives decision dates from rules text / election calendar — never anchor on
`close_time` (markets can close months after their event; this bug cost a 20×).

## Invariants that must not regress

- **No lookahead:** strategies see only pre-sliced `MarketView`s; there is a
  test asserting data at ≥ t is invisible. Any new data path must preserve this.
- **Contamination rules (SPEC §1):** LLM strategies are judged only by models
  that postdate their own knowledge cutoff (Sonnet 5 for markets resolving
  Feb–May 2026; Opus/Fable 5 June 2026+). Price-only strategies are exempt.
- **Fill honesty:** simulated maker fill rate >60% means the fill model is
  lying (auto-flagged); 40–50% is the sanity band. Fills are volume-capped at
  25% of the tape and queue-modeled.
- **Held-out discipline:** the train/test boundary is
  `split_ts = 2024-08-02T00:01:41Z` (`reports/tournament/round1/universe.json`).
  The held-out window is spent for the R5 family — new strategy families get
  one evaluation there, ever. Tune on train only, log every parameter's
  provenance.
- **Data quirks** (full list in `bot/data/NOTES.md`): mark to bid/ask mid,
  never `price_close` (only ~42% of candles have a trade price); 2022 midterms
  are `category=Politics`, not `Elections`; volume and book depth are nearly
  uncorrelated — cap sizing by depth at decision time; ~80% of raw market
  listings are auto-generated parlay spam (`KXMVE*` etc., excluded by default).

## Repo map (beyond the obvious)

- `research/` — 12 cited research reports + `SYNTHESIS.md` (the ranked strategy
  portfolio that drove the build); factual claims cite URLs fetched 2026-08-11/12.
- `reports/` — per-strategy analyses, `TOURNAMENT_PROTOCOL.md` (pre-registered),
  tournament rounds 1–4, `case-wisconsin/` (out-of-sample election-night study),
  `FINDINGS.md` (the session synthesis + roadmap).
- `forecasting/` — the original grantmaking forecasting engine (methodology
  docs are the craft reference; `pytest forecasting/` passes standalone).
- `docs/` — the original pitch, viability stress-test, prior-art survey.
- `bot/evals/` — external ground truth: the harness must reproduce
  FutureSearch's published book and ForecastBench's market baseline before its
  numbers are trusted. BTF-derived datasets are CC-BY-NC: internal evals only,
  never a live-money decision path.

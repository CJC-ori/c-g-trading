# Orchestration plan — autonomous prototype build (2026-08-11)

Chris asked for a long (~6h) autonomous run: take this brainstorm repo and build a
working, backtested prediction-market trading bot prototype targeting Kalshi
(and/or Polymarket). This file is the orchestrator's persistent state: phases,
decisions, and status. Subagents: read this first.

## Non-negotiable design constraints (from Chris + verified facts)

1. **Deterministic benchmark first.** Everything is judged by a reproducible
   backtest: strategy sees only info ≤ t, fills simulated against real historical
   trades/candles, Kalshi fees applied (taker ≈ round(0.07·P·(1−P),2)/contract —
   verify vs official schedule), Kelly-fraction sizing capped by book depth.
2. **Contamination discipline.** Model training cutoff is Jan 2026. LLM-forecast
   strategies may only be backtested on markets resolving AFTER Feb 2026, with
   point-in-time retrieval discipline (no post-resolution sources). Price-only
   strategies backtest cleanly on any period.
3. **Inference cost is a P&L line item.** Every strategy's backtest must charge
   per-question inference cost (log token usage; use real API pricing). Prefer
   cheap-model retrieval + one expensive judge.
4. **Fees and fills are honest.** FutureSearch got ~43% fill rates in sim.
   No assumed fills at mid. Maker vs taker modeled explicitly.
5. **Bet sizing:** fractional Kelly (¼–½), capped by measured order-book depth
   and bankroll caps per market/category.

## Verified environment facts

- Kalshi public REST works unauthenticated from this container:
  `https://api.elections.kalshi.com/trade-api/v2/` — markets (incl. settled,
  cursor pagination), trades (`/markets/trades`), candlesticks (per series).
- Polymarket Gamma API works: `https://gamma-api.polymarket.com/markets`.
  CLOB prices-history to be probed.
- Python 3.11.15, pip installs fine (httpx installed). Disk is a fixed
  allowance — keep raw datasets bounded (~1–2 GB max, prefer filtered pulls).
- No Kalshi/Polymarket API keys available in this session → build + backtest +
  paper-trade design only; live order placement is out of scope for this run.

## Repo layout (target)

```
bot/
  data/         # pullers + local store (sqlite/parquet) for Kalshi/Polymarket history
  backtest/     # deterministic harness: point-in-time replay, fills, fees, P&L
  strategies/   # multiple prototypes, common Strategy interface
  forecaster/   # LLM forecasting pipeline (adapted from forecasting/)
research/       # research agent outputs (markdown, cited)
ORCHESTRATION.md
```

## Phases & status

- [x] P0 Env probe (Kalshi/Polymarket reachable, pip OK)
- [ ] P1 Research sweep (workflow, ~9 agents) → research/*.md + SYNTHESIS.md
- [ ] P2 Data layer: Kalshi settled-market + candlestick/trade history puller;
      bounded real dataset pulled locally
- [ ] P3 Backtest harness + deterministic benchmark spec (the "win condition")
- [ ] P4 Strategy prototypes (multiple, in parallel):
      price-only systematic (longshot/favorite bias, time-decay, election-night
      overcorrection/panic-dip); LLM forecaster vs market; ground-truth-data
      strategies (elections: FEC, poll aggregates); cross-venue divergence
- [ ] P5 Tournament (per Chris's mid-run instruction): run a workflow tournament
      between strategy variants — every variant backtested on the SAME train
      window under identical harness settings; judge panel ranks on the SPEC.md
      metrics + robustness; then an integration round folds the losers'
      demonstrated strengths (signals, filters, sizing tweaks) into the winner;
      adversarial verifier attacks each round's winner (lookahead, overfit,
      fill optimism); loop until improvements go dry (2 consecutive rounds
      without held-out-safe gains). Only final champions touch the test split.
- [ ] P6 Final report + commit + push + draft PR

## Decisions log

- 2026-08-11: Kalshi-first (public data, richer docs, demo env), Polymarket as
  secondary data source for cross-venue signals.
- Research outputs land in research/ as markdown files so orchestrator context
  stays clean; agents return short summaries only.

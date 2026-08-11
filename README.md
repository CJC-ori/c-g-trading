# c-g-trading

**Chris & Griffin build a prediction-market trading bot with an AI forecaster
as its backbone.** This repo is the starting kit: Chris's original pitch, the
forecasting engine lifted from his grantmaking tooling, and three research docs
(written 2026-08-11 by Claude agents that fetched every source cited) that turn
the rough idea into a concrete, honestly stress-tested sketch.

## Griffin: read in this order

1. **[`docs/brain-dump.md`](docs/brain-dump.md)** — Chris's pitch, ~2 min. The
   two candidate strategies and the Michigan story that started this.
2. **This README** — the structured version of the idea + what the research
   found.
3. **[`docs/viability.md`](docs/viability.md)** — the stress-test. The Michigan
   example verified minute-by-minute from Kalshi's own trade API, the evidence
   for and against, back-of-envelope economics, and the proposed MVP.
4. **[`docs/reading-list.md`](docs/reading-list.md)** — annotated links; the top
   two (the ACX post and FutureSearch's Kalshi case study) are the fastest way
   to see that "AI agent profitably reasons about prediction markets" is a real
   thing people do, not a fantasy.
5. **[`docs/prior-art.md`](docs/prior-art.md)** — the open-source landscape:
   what we can fork, what the Kalshi API exposes, and the gap nobody has filled.
6. **[`forecasting/`](forecasting/README.md)** — the engine we'd adapt, with its
   own README on what's portable.

## The idea in one paragraph

Prediction markets like Kalshi appear to **overcorrect around time-bound,
high-volatility events** — election nights especially — and mid-tier markets
are too small for professional capital to bother correcting. A bot with genuine
forecasting ability (calibrated, source-grounded, ensembled — the thing in
`forecasting/`) that is called frequently could (a) spot setups where a big
scheduled move is coming, (b) hold a better probability than the market, and
(c) monetize the gap, either by buying overcorrected dips or by taking
positions in mispriced side markets (victory margins, etc.) before the event.

## The seed example, now verified

On election night of the Michigan Democratic Senate primary (Aug 4, 2026),
Abdul El-Sayed's Kalshi "wins the primary" contract went **98–98.5¢ → 99.9¢ on
early returns → 74¢ trough at 11:17pm ET → back to ~98¢ within two hours →
resolved YES** (he won by ~1 point over Haley Stevens; ~$98M in ad spend,
mostly against him). Chris called 98.5% "too high" *before* election night.

The instinct was monetizable three ways (details in `docs/viability.md`):

- **Cheap NO + sell into the panic**: positions are tradeable until resolution,
  so NO bought at ~1.5–2¢ pre-election and sold at the trough (NO ≈ 26¢) paid
  ~11–14x net — you didn't need him to lose, only for the market to panic once.
  The catch: in the no-scare world NO bleeds to 0 (total loss), and the trough
  lasted ~3 minutes, so it takes resting limit orders — bot territory. Only
  *holding NO to resolution* lost 100%.
- **Buying the dip the other way**: YES at 77¢ → +24% in 5 hours after fees,
  with ~$230k of notional actually trading near the trough.
- **The victory-margin markets — the biggest mispricing**: Kalshi priced
  "wins by ≥15" at 62% that morning (polling said anywhere from tie to +15) and
  the bracket that actually happened (0–3%) at 2–5¢. NO on ≥15 paid ~+150%
  overnight. That's a *pre-event forecasting* edge, not a reflexes edge —
  exactly what an AI forecaster is for.

## What the research says (short version)

**For:** People are already doing this. Preseen turned $35 into ~$1.9M on
Kalshi in seven months and is the first bot to win a human forecasting
tournament; FutureSearch runs real-money positions on Kalshi/Polymarket and
published its whole pipeline (~$0.60/question). Best AIs and top human
forecasters are now in a statistical dead heat on Metaculus. And a Feb 2026
academic study of Kalshi found post-fee profitable patterns that persist
*because the markets are too small for professional capital* — which is
precisely the two-person-side-project niche.

**Against:** Bridgewater's AIA Forecaster hit superforecaster parity yet still
**underperformed liquid-market consensus alone** — only model+price ensembles
beat the market, so the edge lives in thin/neglected markets, not headline
ones. Pure arbitrage is already owned by millisecond bots (skip it as a primary
strategy). Order-book depth on mid-tier markets is $1–3k, capping any edge at
side-project scale (realistic ceiling maybe $10–40k/yr on a $50–100k bankroll
*if* the edge is real). Election-night counterparties include people running
live precinct models. And hits-based betting at n≈4 events/year has wipeout
variance. Nobody has publicly proven an AI forecaster beats Kalshi post-fee.

## What we'd concretely build

Three layers; the third is the one that doesn't exist anywhere in open source:

1. **Forecasting engine** — adapt `forecasting/` (methodology + validation are
   portable, tests pass in this repo) on top of
   [Metaculus/forecasting-tools](https://github.com/Metaculus/forecasting-tools)
   (MIT, maintained) for LLM plumbing, retrieval, and cost tracking. The Q2
   benchmark winner's pipeline is public as a design reference (AGPL — reimplement,
   don't fork).
2. **Market plumbing** — Kalshi official Python SDKs or
   [pmxt](https://github.com/pmxt-dev/pmxt) ("CCXT for prediction markets",
   MIT) for orderbooks, fills, and order placement.
3. **The forecast→trade bridge (our actual product)** — market-rules → well-posed
   forecasting question compiler; edge threshold after fees (taker fee
   ≈ 0.07·P·(1−P) — verify against the official schedule, it 429'd our fetch);
   Kelly-fraction sizing against measured book depth; event calendar +
   scheduler that concentrates calls around scheduled volatility (election
   nights, rulings, data releases); P&L/Brier logging.

## The MVP (before any real money)

From `docs/viability.md` §6 — 8–12 weeks, $0 at risk:

1. Run the forecaster over live Kalshi markets (FutureSearch-style filter:
   3–97¢, >10 days out, no sports/crypto/insider), logging forecast vs. price.
2. Simulated fills against real order books (FutureSearch got only ~43% fills
   in simulation — model that honestly).
3. **Gate:** beat the market's Brier score over ≥100 resolved markets.
4. Paper-trade an election-night model (county baselines vs. live returns)
   through the Nov 2026 midterms.
5. Only then: real money, margin-market-first, small.

## Repo map

```
├── README.md            ← you are here
├── docs/
│   ├── brain-dump.md    ← Chris's original pitch (the "why")
│   ├── viability.md     ← thesis stress-test; Michigan verified from Kalshi API
│   ├── reading-list.md  ← annotated sources, top-5 first
│   └── prior-art.md     ← forkable repos, Kalshi API surface, the gap
└── forecasting/         ← the engine (see its README)
    ├── methodology/     ← the craft docs the LLM agents run on (the crown jewel)
    ├── forecast-skill.md← orchestrator prompt (design template)
    ├── store_forecast.py← validation/aggregation — runs standalone, stdlib only
    ├── evals/           ← forecast quality scoring (pure checks portable)
    └── nvf-deps/        ← ⚠ reference-only NVF persistence layer
```

Everything NVF-specific is fenced behind `NVF-ONLY` banner comments — grep for
`NVF-ONLY` to see every seam that needs new DB infrastructure. `pytest
forecasting/` passes with no database and no NVF code.

*Provenance: research docs were compiled by Claude agents on 2026-08-11; every
factual claim in them cites the URL it was fetched from, and unverifiable
claims are flagged inline. The forecasting engine is extracted from the NVF
grantmaking system's `/forecast` tool.*

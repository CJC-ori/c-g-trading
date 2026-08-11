# `bot/evals` — the eval ladder

The harness tells us what a strategy earns. This package tells us whether to
believe the harness, and what the strategy has to beat before it is allowed to
exist. Nothing here trades; everything here is a gate.

Four rungs, each gated on the one below it. A rung-2 Brier computed on a harness
that failed rung 1 is not a measurement, it is a number.

```
rung 0   harness self-tests        pytest bot/backtest bot/evals
rung 1   fs_trade_replay           our P&L accounting vs an external, published book
rung 2   forecastbench             our forecaster's Brier vs the market-consensus baseline
  2a     fs_replication            3-way Brier on FutureSearch's 153 Kalshi questions
rung 3   Kalshi tournament replay  fills + fees + P&L on our own market data
```

Run the whole thing:

```bash
python -m bot.evals.ladder                # rungs 0 -> 2a, stops at the first failure
python -m bot.evals.ladder --online       # also queries Kalshi for fresh resolutions
```

Or a single rung:

```bash
python -m bot.evals.fs_trade_replay
python -m bot.evals.forecastbench --ensure-data
python -m bot.evals.fs_replication --online
```

Every module writes `reports/evals/<name>.json` (machine-readable, what CI
asserts on) and `reports/evals/<name>.md` (the same thing for a human). Override
the destination with `EVAL_REPORTS_DIR`.

---

## Rung 0 — harness self-tests

**Question it answers:** does the code do what its own spec says?

**What runs:** `pytest bot/backtest bot/evals` — the fee, fill, risk, engine,
metrics and universe tests owned by `bot/backtest/`, plus this package's scoring
tests (`bot/evals/test_scoring.py`, which check every formula against a
hand-computed value or a published table, never against the implementation).

**Gate:** all green. No exceptions, no xfail on a scoring function.

Rung 0 is necessary and nowhere near sufficient: it proves internal consistency,
not correctness. A fee model can be perfectly self-consistent and still charge
the wrong fee. That is what rung 1 is for.

---

## Rung 1 — `fs_trade_replay`: the economics, against external ground truth

**Question it answers:** if we are handed a book whose every input is known —
entry price, share count, outcome — do we compute the same P&L the venue did?

**Data:** `research/futuresearch-data/fs_positions_2026-08-11.json`, 214
FutureSearch positions of which **128 are resolved**, parsed from
<https://markets.futuresearch.ai/> on 2026-08-11.

**Two passes, deliberately separated.**

*Pass A (gross)* reconstructs their accounting from first principles:
`cost = shares × entry_price`, `payout = shares if won else 0`. This must land on
their published book:

| venue | n | published | ours (Pass A) | residual |
|---|---|---|---|---|
| Kalshi | 83 | +$27,798 | +$27,797.96 | −$0.04 |
| Polymarket | 45 | −$19,829 | −$19,828.59 | +$0.41 |
| Combined | 128 | +$7,969 | +$7,969.37 | +$0.37 |

The residual is rounding: their figures are published to the dollar and ours are
cent-exact. Position by position, their own `pnl` field equals
`shares × (1[won] − entry_price)` to within half a cent on all 128 — which is
also the proof that **their book is gross: no fees, no exit slippage, held to
resolution**. (`research/futuresearch.md` §5 confirms it — the word "fee" does
not appear in their February trader methodology.)

*Pass B (net)* pushes the same positions through our fee model. Because Pass A
matches exactly, the entire Pass A → Pass B gap **is** the fee line they never
charged — which is the number this rung exists to produce:

| | Kalshi | Polymarket | combined |
|---|---|---|---|
| gross P&L | +$27,798 | −$19,829 | +$7,969 |
| Kalshi taker fees (exact model) | −$2,235 | $0 | −$2,235 |
| **net P&L** | **+$25,563** | **−$19,829** | **+$5,735** |
| net ROI on cost basis | +29.9% | −17.8% | +2.9% |
| fee drag | 261 bps | 0 | 114 bps |

Three fee engines are computed side by side each run — the harness's exact
`trade_fee_centicents`, its legacy per-contract path, and an independent
float-precision reimplementation local to this module (a unit test that imports
the thing under test for *both* sides of the comparison tests nothing). See
"Findings this package reports upward" below.

**Gate:** every venue reconciles to within $1 in Pass A. `test_fs_trade_replay.py`
asserts it, so the ladder breaks loudly if the harness's arithmetic drifts.

**What it does not test:** fills. Every FutureSearch fill is taken as given, so
this rung validates fee + P&L accounting only. Fill realism is rung 3's problem,
and `research/futuresearch.md` §5 is the sanity band there (40–50%, *not* the
43% headline — their own position table implies 50.1%).

---

## Rung 2 — `forecastbench`: the Brier bar

**Question it answers:** is our forecaster better than the market price, by
enough, on enough questions to tell?

**Data:** `forecastingresearch/forecastbench-datasets`, cloned to
`data/forecastbench-datasets` (139 MB, gitignored via `/data/`). Auto-clone with
`--ensure-data`; refresh with `--update` (it is a nightly-refreshed repo).

**Self-test first.** The rung reproduces `research/benchmarks.md` §1.6 on the
contamination-safe window (`forecast_due_date >= 2026-02-01`, market sources
only, `resolved == true`) before it is allowed to score anything:

```
n = 1,216   market-price Brier = 0.1172   sd = 0.1785   95% CI [0.1072, 0.1273]
```

per-source, reproduced exactly:

| source | n | base rate | market Brier |
|---|---|---|---|
| infer | 102 | 0.206 | **0.0651** |
| manifold | 357 | 0.401 | 0.0963 |
| polymarket | 534 | 0.382 | **0.1253** |
| metaculus | 223 | 0.305 | 0.1554 |

If those numbers do not come out, the join is wrong or — far more likely — the
`resolved == true` filter is wrong. A broken `resolved` filter scores forecasts
against **live market prices instead of outcomes**, produces plausible-looking
Briers, and is worthless. It is the single easiest way to silently corrupt this
eval (`research/benchmarks.md` §1.3), which is why it is a hard self-test rather
than a comment.

**0.1172 is our internal bar**, not ForecastBench's published 0.077. Theirs uses
the market value on the due date (10 days better-informed than the offline
`freeze_datetime_value`) over a question pool that includes long-dated
near-certainties. 0.1172 is what our own code produces on our own subset, so it
is the only apples-to-apples comparison for anything we score the same way.

**API for the forecaster:**

```python
from bot.evals import forecastbench as fb

rows = fb.questions(min_due="2026-03-01")     # hard-margin window
# each row carries question, background, resolution_criteria, url,
# freeze (retrieval MUST be bounded to this), close, market_p, outcome

result = fb.score({r.key: my_forecast(r) for r in rows},
                  rows=rows,
                  gate=0.06,
                  simulated_pnl_usd=pnl_from_backtest)
```

`score()` returns the mandatory three-number block plus the gate verdict, the
per-source breakdown and a calibration table.

**Gates (SYNTHESIS §2.3–2.4):**

1. **n ≥ 500** resolved market questions. SPEC §7's n ≥ 100 is unsound: at the
   measured paired-difference sd of 0.0698, n=100 detects nothing smaller than
   ΔBrier 0.0137 — more than three times the entire superforecaster-over-market
   edge of 0.004. A "win" at n=100 is noise.

   | n | min detectable ΔBrier | equiv. RMS disagreement |
   |---|---|---|
   | 100 | 0.0137 | 11.7¢ |
   | 500 | 0.0061 | 7.8¢ |
   | 1,216 | 0.0039 | 6.3¢ |

2. **The three-number block, always all three together.** Reporting one or two
   of these is how a strategy talks its way past the gate:

   - **(a) pooled ΔBrier vs market, with the paired CI.** The calibration sanity
     check — proves we are not *worse*. A point estimate without the CI is not an
     answer.
   - **(b) ΔBrier on the traded subset** (`|p̂ − mid| ≥ gate`, default 6 points).
     The actual skill claim, and the one that should be large.
   - **(c) simulated post-fee P&L on those trades.** The only number that pays
     rent. `score()` marks the block `complete: false` until the caller supplies
     it, so two-thirds of a block cannot be quoted as a result.

   *Pooled-neutral but subset-positive is what a profitable niche strategy looks
   like. Pooled-positive but subset-negative is a calibration-shrinkage artifact
   that will lose money.*

3. **Tier check** (`research/benchmarks.md` §4.4): +0.004 superforecaster-class;
   +0.02 top-2026-bot-class, be suspicious; **> +0.05 you have a leak** — go check
   the point-in-time discipline and the `resolved` filter before celebrating.

4. **`by_source` reported alongside the pooled number, always.** INFER (0.065)
   and Metaculus (0.155) differ by more than any edge we could plausibly find, so
   apparent skill can be manufactured entirely by source mix.

**Point-in-time discipline is the caller's job.** Every row carries `freeze`
(= due date − 10 days). The eval hands it to you; it cannot enforce that your
retrieval layer respects it. See `research/point-in-time-retrieval.md` §5–6 and
SYNTHESIS §2.5 for the retrieval ledger and the placebo/future-shift check.

### Rung 2a — `fs_replication`: the Kalshi-native 3-way Brier

ForecastBench has **no Kalshi**. Rung 2a is the closest thing to a Kalshi-native
forecast eval we can build for free: FutureSearch's 153-market snapshot of
2026-02-26 16:09 UTC, with their point-in-time price, their median forecast, and
a placeholder column for ours (`--forecasts p.json`).

Results are **always split at 2026-05-01**, the judge model's training cutoff
(SYNTHESIS §3 correction #1 — ORCHESTRATION.md's "Jan 2026" is wrong for Opus 5).
Pre-cutoff rows are contaminated by construction and are useful only as a leak
*detector*: if our forecaster scores dramatically better before the cutoff than
after, that gap is memorisation.

> ⚠ **This rung is currently a smoke test, not evidence, and
> `research/futuresearch.md` §7 is wrong about why.** §7 says the 153 markets
> "all resolved months ago, and the outcomes are free". Neither half holds.
> Measured 2026-08-11: **76 of the 153 are still active on Kalshi** — the set
> runs out to 2030, because FutureSearch's filter was 3–97¢ + volume with a
> >10-day *floor* and no ceiling. Of the rest, the ones that settled more than
> ~90 days ago have been **purged from Kalshi's public API** (they 404 on
> `/markets/{ticker}`, return nothing from
> `/markets?event_ticker=…&status=settled`, and have no candlestick history);
> their outcomes are not recoverable from Kalshi at any price.
>
> Scorable n was 25 on the first run and rose to 61 within the hour as
> `bot/data/`'s settled-market backfill landed, so **read the live count out of
> `reports/evals/fs_replication.json` rather than out of this paragraph.** Its
> ceiling today is the 77 markets that have already resolved; the other 76 arrive
> as they settle, out to 2030. It will never approach the n≥500 gate on its own.
>
> Where it stood at n=61 (FutureSearch's median forecast vs the Kalshi price at
> their own snapshot instant — both genuinely point-in-time, so this comparison
> is clean at any n):
>
> | split | n | Brier(FS median) | Brier(Kalshi price) | ΔBrier [95% CI] |
> |---|---|---|---|---|
> | all resolved | 61 | 0.0636 | 0.0644 | +0.0008 [−0.0082, +0.0099] |
> | resolves < 2026-05-01 | 21 | 0.0394 | 0.0363 | −0.0031 [−0.0107, +0.0045] |
> | resolves ≥ 2026-05-01 | 40 | 0.0762 | 0.0791 | +0.0029 [−0.0103, +0.0161] |
>
> A dead heat, with a CI four times wider than the effect anyone is claiming.
> That is the honest reading of the only public LLM-forecaster-vs-Kalshi data
> that exists, and it is the prior our own forecaster has to argue against.
>
> Two ways to grow it: backfill citable outcomes into
> `fs_resolution_overrides.json` (every entry requires a `source`; the module
> raises without one), or re-run periodically as the 76 open markets settle —
> which only works if `bot/data/` snapshots settled markets on a cadence tighter
> than the 90-day purge.

---

## Rung 3 — Kalshi tournament replay

**Question it answers:** does the strategy make money on our own market data,
with fills walked against real books and fees charged per contract?

This rung lives in `bot/backtest/` (engine, fills, fees, risk, metrics) and is
not implemented in this package. What rung 3 owes the ladder:

- the full SYNTHESIS §2.3 metric block: net P&L after fees **and inference cost**,
  the three-number Brier block, calibration + refinement decomposition, drawdown
  and concentration, capacity at 1×/3×/10× depth, trade stats, robustness
  (time split, category split, fees ×1.5, inference ×2), and cost ratios;
- SPEC §7's graduation gates: positive P&L on the held-out split, top-5 markets
  under 60% of profit, survives fee stress;
- the simulated fill rate, checked against the 40–50% sanity band, with any maker
  fill rate above 60% flagged;
- number **(c)** of the three-number block, fed back into rung 2's `score()`.

---

## Findings this package reports upward

Things measured here that are someone else's to fix. Reported, not patched —
`bot/backtest/`, `bot/data/` and `bot/strategies/` are owned elsewhere.

1. **A backtest run that does not pass a `FeeSchedule` over-charges taker fees by
   ~2×.** `bot/backtest/fees.py` now carries both paths: the exact model
   (`trade_fee_centicents` — one ceiling per order to $0.0001, per-series
   `fee_multiplier`) and the legacy per-contract ceiling kept for backward
   compatibility. On the FutureSearch book they come out at **$2,235 vs $4,652,
   a 2.08× gap**; a single 1,000-contract order at 50¢ costs $17.50 exact and
   $20.00 legacy. Rung 1 measures both every run. The remaining ask is that
   every real-data run construct a `FeeSchedule` (from the store or the bundled
   snapshot) rather than inheriting the legacy default, and that the eventual
   `/series/fee_changes` pull make the multiplier point-in-time rather than
   as-of-today.
2. **`research/futuresearch.md` §7's claim that the 153 Kalshi questions "all
   resolved months ago" is false** — 76 are still open, out to 2030, and the
   old settlements are purged. See rung 2a. The doc should be corrected; the
   eval it recommends is real but roughly 20× smaller than advertised, and the
   "n=153 head-to-head against a published AI forecaster *and* the market" it
   promises is not available on this data.
3. **Kalshi's ~90-day settled-market purge is a standing data-loss risk.** Any
   outcome not snapshotted within the window is unrecoverable from the public
   API. Worth a scheduled settled-market sweep in `bot/data/`.
4. **FutureSearch's fee-free methodology costs 261 bps.** Kalshi taker fees on
   their own resolved book are $2,235 on an $85,610 cost basis — their published
   +$27,798 is really +$25,563. Their stated edge threshold is 2 percentage
   points; the fee drag alone is comparable. Any strategy of ours inheriting a
   2-point threshold is trading for the exchange.

---

## Licensing — read before anything touches money

| Asset | Licence | Allowed here | Not allowed |
|---|---|---|---|
| ForecastBench datasets | CC BY-SA 4.0 | internal evals, rung 2 | redistribution without attribution + share-alike |
| **Bench to the Future (BTF-2 / BTF-3)** | **CC BY-NC** | internal evals, model/prompt selection | **any live-money decision path** |
| FutureSearch position + forecast dumps | published marketing data, no explicit licence | internal evals, rungs 1 and 2a | redistribution |

**The non-commercial clause is a hard boundary.** BTF datasets (and anything
derived from them — a fitted calibration curve, a selected prompt, a threshold
tuned on BTF questions) may inform *model and prompt selection* during
development. They must not appear in, or be fitted into, a component that places
or sizes a live-money order. If a rung-2 artifact would end up as a coefficient
in the trading path, it needs re-deriving on data we are licensed to trade on.

ForecastBench's CC BY-SA is share-alike, not non-commercial: fine to use, but any
*published* derivative must carry attribution and the same licence.

---

## Files

| file | what |
|---|---|
| `scoring.py` | all the math: Brier, Brier Index, log loss, paired ΔBrier + CI, power, tiers, the three-number block, gates, calibration, concentration |
| `report.py` | `reports/evals/<name>.{json,md}` emission |
| `fs_trade_replay.py` | rung 1 |
| `forecastbench.py` | rung 2 — also the `questions()` / `score()` API for the forecaster |
| `fs_replication.py` | rung 2a |
| `fs_resolution_overrides.json` | hand-curated outcomes for purged Kalshi markets; every entry needs a citable `source` |
| `ladder.py` | runs rungs 0 → 2a in order |
| `test_*.py` | rung 0's share of the self-tests |

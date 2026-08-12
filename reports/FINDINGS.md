# Session findings — autonomous prototype build, 2026-08-11/12

*The orchestrator's synthesis of the full ~10-hour run: what was built, what
was learned, what made money in backtest, and what died honestly. Written for
Chris & Griffin. Every number below traces to a report under `reports/` or a
research file under `research/`.*

## 1. What exists now

A complete, tested research-to-backtest stack:

- **Data**: `bot/data/` pulls Kalshi's public REST *and* its undocumented
  unauthenticated `/historical/*` archive tier. Local SQLite: 344k markets
  (July 2021 → Aug 2026), 6.4M candlesticks, 9.5M tick trades, tick-structure
  metadata on 100% of markets. Key traps documented in `bot/data/NOTES.md`
  (three different retention horizons; `_fp`/`_dollars` field names; 2022
  midterms live under `category=Politics`, not `Elections`; 80% of the
  market flood is auto-generated parlay spam).
- **Benchmark**: `bot/backtest/` implements `SPEC.md` — the deterministic win
  condition. Point-in-time replay (no-lookahead enforced structurally and
  tested), exact per-series centicent fee engine, maker-queue fill model
  calibrated on fill rates (never P&L), cancel/replace, quarter-Kelly caps,
  three-number Brier block, capacity/fee/inference stress. Externally
  validated: reproduces FutureSearch's published 128-position live book to
  **$0.41**, and the ForecastBench market baseline to 4 decimals.
- **Forecaster**: `bot/forecaster/` — cost-tiered (Haiku screen → Sonnet
  dossier → multi-pass judge, geometric-mean-of-odds aggregation), leak-proof
  point-in-time retrieval (GDELT with double date gates, Wikipedia revisions,
  VoteHub/FEC/ALFRED), deterministic replay cache, real inference cost charged
  into P&L ($0.095/question measured).
- **Strategies**: seven families built and backtested under identical rules,
  each with pre-registered kill criteria (`bot/strategies/`, `reports/`).
- **Evals**: `bot/evals/` — the external-reconciliation ladder that gates
  trust in the harness.

`python -m pytest bot/ -q`: 600+ tests.

## 2. The kill ledger (train split; held-out touched only by the tournament)

| Strategy | Result | Why |
|---|---|---|
| **FLB R5-endgame** — maker-buy the 90–98¢ side in the final 6–24h | **SURVIVES** → tournament champion candidate | +5.4–6.3%/episode (GWU benchmark: +2.6%), fills 16–30% (honest band), fee-stress immune. Caveat: zero losing episodes in-sample → mean is an upper bound |
| FLB R2-maker (rest bids ≥50¢) | KILLED | Its +$262 was a fill-model artifact; honest queue model → −$162 |
| FLB R1-taker (buy 70–97¢) | KILLED | 85% win rate, but taker fees + tail losses eat it |
| Weather ground-truth (bias-corrected ensemble vs strikes) | KILLED | On 1,480 markets the market's Brier beats ours (Δ −0.022, CI < 0) in all 12 configs. Kalshi weather prices already embed the ensembles |
| Panic-wick ladders R4 (the Michigan thesis) | **PARKED** (positive, n-limited) | 136-episode census: 11% adverse selection, R4 5/5 hits, +17.8% median. Michigan replay: caught the 74¢ wick, +20.3%. Only 9 fillable episodes exist in ALL available history (structural — Kalshi listed no candidate markets pre-Oct-2024) → below the pre-registered n≥10 bar. Paper-trade Nov 2026 midterms |
| Panic R4b (cheap-NO convexity) | KILLED | Measured arm rate 3.8% vs ~10% breakeven; Michigan's 8.67× was the lucky draw |
| Swing-fade (general, Chris's hypothesis) | KILLED (with a real answer) | 510k-episode census: ≥10¢/24h swings are frequent (~339/wk) and 60–74% half-revert, BUT 53–72% settle *with* the move (information, not panic), and the reversion is quote-only — no prints to exit into. The tails (panic-dip / spike-fade) have 3–6× lower adverse selection — Chris right about *where* — but engine-honest fading still loses except eco spike-fade (parked, concentration-flagged) |
| Structure: overround NO-sets | DEMOTED to monitoring | Fee-clearing overround: 0.75/week, median fillable **$0.98**. Fees destroy 98.6% of the raw signal. (Original research — nobody had published this) |
| Structure: cross-instrument consistency | DEMOTED | Kalshi ladders internally coherent: 0 violations in 8,936 event-hours |
| Structure: cross-venue divergence | KILLED | 23 rule-audited pairs: median gap 1¢ (inside one tick); divergence-trading loses vs baseline |
| LLM forecaster (pooled) | KILLED as a trading strategy | Rung 2 (n=120, Sonnet-judged, leak-audited): parity with market (Δ −0.004, NS); **no-retrieval ablation = full pipeline** (+0.0017); rung 3 replay: −$433 net, negative under all stresses |
| LLM hits-based selective | **MECHANISM DEMONSTRATED**, edge unproven | Fires 3/88, one true hit: KXCLAYTONCONF 94¢ vs our 18% — a resolution-timing wedge found for the documented right reason — paid +14.4×. Not FLB in disguise (the always-fade null loses on every accounting). But n=1, the Haiku screen filtered out real upsets, and the mispricing evaporated in <5h (execution needs a freshness gate) |

## 3. The big lessons (what we'd tell our past selves)

1. **Fill simulation is where backtests lie.** Three separate strategies
   looked profitable until the maker-queue model landed. Any prediction-market
   backtest that assumes fills at mid or fills on every touch is fiction.
   Corollary: "the price reverted" ≠ "you could have exited" — 87 of 99
   adverse swing entries crossed the retrace target on *quotes* with no tape
   to sell into.
2. **The market price is the strongest model in the room.** Our forecaster,
   FutureSearch's scaffold, and every ForecastBench bot land at parity-or-worse
   with the price. Retrieval added nothing over the judge's prior on
   benchmark questions. Scaffolding ≈ bare frontier model (their own
   leaderboard: Δ0.0023, p=0.054); a model *generation* is worth 5× the
   scaffold. Edge, if it exists, is in selection: thin, research-heavy,
   asymmetric markets where consensus is lazy — demonstrated once at +14.4×,
   not yet proven repeatable.
3. **Inference cost is a non-issue at this scale; liquidity is the binding
   constraint.** $0.095/question vs $0.63 break-even; meanwhile top-of-book
   depth on mid-tier markets is $100–$3k and nearly uncorrelated with
   cumulative volume.
4. **The documented anomalies are real but tiny after fees.** Favorite-
   longshot bias survives our honest replay only in its endgame form.
   Overrounds exist but pay pennies. Cross-venue gaps are one tick.
5. **Pre-registration works.** Every strategy carried kill criteria written
   before results; the tournament protocol was committed before any held-out
   run. This is why the numbers above are believable.

## 4. Tournament results

*(Placeholder — filled by `reports/tournament/FINAL.md` when Round 4
completes: champion, integration variants kept, adversarial audit verdicts,
and the single held-out evaluation against the graduation gates.)*

## 5. What we'd do next (post-session roadmap)

1. **Paper-trade R5-endgame live** on the Kalshi demo environment (auth keys
   needed — human task), with the tournament-frozen config.
2. **Stand up the panic-ladder paper trader before Nov 2026 midterms** — the
   parked strategy with the best per-episode economics.
3. **Hits-based forecaster v2** (pre-specified in `reports/hits/ANALYSIS.md`):
   larger sample, freshness-gated taker entry at decision time, screen redesign
   (the current screen rejected 22 of 26 real upsets).
4. **Start the forward data moat now**: order-book snapshotter (no historical
   books exist anywhere), Cleveland Fed nowcast (no archive), VoteHub, NWS CLI.
   Every day not snapshotted is unrecoverable.
5. **Human tasks**: Kalshi API keys (demo + live), fee-schedule PDF download
   (429s from this container), FRED/ALFRED + api.data.gov keys.

## 6. Honest overall assessment

The session's strongest claim is *negative space*: six of eight strategy
families died under honest fills and fees, which is exactly what the research
predicted (markets are efficient where liquid; anomalies are small). The
surviving edges are (a) a modest, well-evidenced, capacity-limited endgame
harvest, (b) a rare but high-quality event-night pattern awaiting its n, and
(c) one demonstrated 14× forecasting hit whose repeatability is the single
most valuable open question. The infrastructure to answer that question —
honestly, cheaply, reproducibly — is the real deliverable.

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
| Panic-wick ladders R4 (the Michigan thesis) | **PARKED → now breakeven-at-best** (see §4b) | 136-episode census: 11% adverse selection, R4 5/5 hits, +17.8% median, Michigan replay +20.3%. But only 9 fillable episodes in all history, and the Wisconsin out-of-sample dip-and-die (−100%) erased the cumulative P&L. Paper-trade Nov 2026 midterms only with a much stronger episode gate |
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

## 4. Tournament results (complete — `reports/tournament/FINAL.md`)

The four-round tournament ran exactly as pre-registered, and it *changed the
answer twice* — which is the point of running it:

**Round 1 (full-history championship)** exposed the R5-endgame train pass as
a window artifact: on 2021→2024 train data, all three R5 windows LOSE
(weather episodes dominate the unfiltered universe and weather endgame
favorites are fairly priced). Economics (+4.12%/ep, SE 0.41) and Politics
cells stayed positive.

**Round 2 (integration)** produced the integrated champion: **ia_econ_only**
(R5-6h, maker-join 90–98¢, Economics category only) — the only net-positive
variant on full-history train (+$144, fee-stress positive, rising capacity
curve, fills in the honest band).

**Round 3 (adversarial audits)**: one VIOLATION (the category filter is
selected on train outcomes — winner's curse; plus a volume-filter form
defect worth exactly $0.00) and two MINOR panels. Remediation was executed
and pre-committed *before* the held-out run: the train number was formally
reclassified as a biased upper bound, the DB snapshot pinned, and a
never-contaminated CLEAN sub-window breakout pre-committed.

**Round 4 (the single held-out evaluation)** — the only number that counts:

| config | held-out episodes | net P&L after fees | per-episode mean ± SE | fee ×1.5 | top-5 share | gates |
|---|---|---|---|---|---|---|
| **ia_econ_only** | 309 (195 clusters) | **+$1,642.14** | +2.36% ± 1.30% | +$1,638 | 9.9% | **PASS 5/5 → GRADUATES** |
| ib_swing_filter | 1,158 | −$2,035.13 | −1.40% ± 0.87% | moot | n/a | FAIL → killed |

Honest degradation was real and is reported: in-sample +4.12%/ep fell to
+2.36%/ep held-out, with 9 losing episodes including two ~−100%. The verdict
survives the pre-committed CLEAN sub-window (+$662.07, +1.04%/ep, 197
episodes never touched by any earlier pull). Caveats that go with the
graduation: the equal-weighted CI grazes zero; ~80% of profit sits in one
series family (AAA gas-price ladders); capacity is real but modest.

**What graduates to paper trading:** R5-6h Economics-only, maker-join
90–98¢, final 6 hours, exact per-series fees, quarter-Kelly, depth caps —
the tournament-frozen config in `reports/tournament/round4/`.

## 4b. The Wisconsin out-of-sample coda (2026-08-11, `reports/case-wisconsin/`)

The night before the session ended, the Wisconsin Dem gubernatorial primary
handed us a free out-of-sample test of the founding thesis, and it was the
*adverse* draw: Hong (95¢, market overconfident vs polls that justified
60–80%) dipped, partially reverted to 79¢ — live-indistinguishable from
Michigan — then died. Settled NO. Replaying the FROZEN rules: the R4 panic
ladder filled fully and lost −100% (−$499.50); one Wisconsin erased Michigan
plus all five train wins (cumulative R4 P&L across every simulated fill ever:
≈ +$18 on ~$3.1k — statistically zero, dip-die rate now 2/11 ≈ 18%, above
R4's ~15% economic breakeven). **The panic-ladder thesis is now
breakeven-at-best on all available evidence.** The genuinely capturable edge
that night was *pre-event*: Crowley YES / Hong NO at ~5¢ — the
fade-the-overconfident-favorite forecasting expression, the same mechanism as
the hits-forecaster's +14.4× Clayton trade. Twice demonstrated, still
unproven at n. Our pipeline missed it because of a close-time anchoring bug
(markets close in November; the primary was in August) — fixed post-hoc as
infrastructure (see `reports/hits/ANALYSIS.md` v2 section), with no new
performance claims attached.

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

The session's strongest claim is *negative space*: most strategy families
died under honest fills and fees, which is exactly what the research
predicted (markets are efficient where liquid; anomalies are small). What
survived the full gauntlet — pre-registered kill criteria, adversarial
audits, a single held-out evaluation, and an out-of-sample election night:

1. **One graduated strategy**: Economics-only endgame favorite-buying,
   +$1,642 held-out net on $10k with 5/5 gates passed — real, modest,
   capacity-limited, and dependent on one series family for most of its
   profit. Worth paper-trading; not worth quitting a job over.
2. **One twice-demonstrated mechanism**: pre-event fades of overconfident
   extreme favorites found by targeted forecasting (+14.4× Clayton, and the
   Wisconsin ~20× that our anchoring bug missed). Its repeatability is the
   single most valuable open question this repo can now answer cheaply.
3. **The founding panic thesis, honestly priced**: the dips are real, the
   ladders fill, and the adverse tail (Wisconsin) eats the winners. You
   cannot tell a Michigan from a Wisconsin mid-panic; you *might* be able to
   tell them apart pre-event, which routes back to #2.

The infrastructure to keep answering these questions — honestly, cheaply,
reproducibly, with fills and fees that don't lie — is the real deliverable.

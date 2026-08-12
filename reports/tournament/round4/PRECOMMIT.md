# ROUND 4 — pre-commitments and audit remediation (written BEFORE any held-out replay)

*2026-08-12, after the Round-3 audit verdicts (1 VIOLATION, 2 MINOR_ISSUES),
before the engine has touched a single held-out market. Everything in this
file is frozen now; the held-out numbers will be reported against it as-is.*

## Round-3 audit remediation (required before proceeding)

**F1 CONFIRMED (settlement-outcome circularity) — remediation = Round 4 itself.**
The champion's category set `("Economics",)` was derived from Round-1 *train*
win-rate-vs-price tables, so the train +$144.21 (+4.12%/ep, zero losses) is
in-sample by construction and is hereby reclassified as a **biased upper
bound, not an estimate**. No harness change can fix a selection-on-outcome
issue; the only valid estimate of the champion's edge is the single held-out
run below. FINAL.md will report train-vs-held-out degradation explicitly and
will not quote the train number as an expectation.

**F2 CONFIRMED (held-out contamination) — remediation pre-committed here.**
The tournament held-out window (settlements 2024-08-02..2026-08-11) contains
the entire FLB research pull on which the R5 family was selected and the
maker-queue depth calibrated. Remedies, frozen before the run:

1. **DB snapshot pinned** (see below); the 2025 trades backfill is complete
   and idle (row counts sampled 20 s apart: identical; WAL 0 bytes; no
   writer process).
2. **Contamination breakout pre-committed.** Every episode table in Round 4
   is reported twice: full held-out, and split into
   - **CLEAN sub-window**: settle_ts in (1722556901, 1779708760] —
     2024-08-02T00:01:41Z .. 2026-05-25T11:32:40Z, never seen by any
     research, selection, or calibration step;
   - **CONTAMINATED sub-window**: settle_ts in [1779708761, 1786483329] —
     the FLB pull span 2026-05-25T11:32:41Z .. 2026-08-11, on which the R5
     family was selected (132 flb-train tickers backtested repeatedly, 146
     flb-test tickers pulled) and depth_windows=1.0 calibrated.
   Episodes on the 132 flb-train tickers (all 132 are in held-out) are
   additionally flagged ticker-by-ticker. **Graduation is decided on the
   full held-out numbers per protocol, but FINAL.md must state whether the
   verdict survives on the CLEAN sub-window alone; a config whose positive
   P&L exists only in the contaminated sub-window does not graduate.**

**F3 PLAUSIBLE channel (close_ts anchoring) — post-run check pre-committed.**
For every market any Round-4 config trades: report `can_close_early`,
close_time vs expected_expiration_time, and whether the close corresponds to
a rule-named scheduled public event (FOMC/CPI/EIA-class). Episodes in
markets with *unscheduled* early closes are flagged and the P&L they carry
is quantified. (On train all 29 champion trades were verified benign.)

**F4 CONFIRMED in form (lifetime-volume universe filter is future info) —
config fixed and train re-verified.** Remediation run (before held-out):
`ia_econ_only` re-run on the train Economics universe rebuilt with
`min_volume=0` (the F4-fixed selection). Result recorded in
`REMEDIATION.md`; expected delta $0.00 because the filter excludes zero
train Economics markets (auditor's finding, re-verified: 386 tickers
identical with and without the filter). Held-out exposure, quantified
before running: the F4-fixed (min_volume=0) universe adds **1** market to
the champion's 2,169 Economics held-out markets and **4,213** (weather-
dominated) to the runner-up's 48,247. Round 4 runs on the **pinned**
universe definition (protocol-binding; the volume filter is part of the
frozen Round-1 UniverseConfig) and FINAL.md discloses this residual: for
the champion it is 1/2,170 markets (immaterial); for the weather-heavy
runner-up it remains a live caveat carried into its verdict.

**Round-2 reporting error (fill-rate band metric) — fixed here.** The
maker-queue model was calibrated on the CONTRACT-level fill rate
(reports/flb/QUEUE_IMPACT.md "Metric choice"). Round 4 quotes the
**maker contract fill rate** as the band metric for the ≤60% gate (order-
level rate also reported, labeled as such).

## Pinned DB snapshot (F2 remedy 1)

- file: `data/kalshi.db`, 4,738,936,832 bytes, mtime 2026-08-12 13:57 UTC;
  `-wal` 0 bytes.
- rows: markets = 344,138; trades = 9,611,621; candlesticks = 6,489,037.
- max trades.created_time = 2026-08-12T13:36:18.73007Z.
- two samples 20 s apart identical → backfill idle; no pull/backfill
  process running.

## Held-out universe (pinned, reproduced exactly)

- Re-selecting with the frozen Round-1 `UniverseConfig` and splitting at the
  pinned `split_ts=1722556901` with settle_ts ≤ pinned `t1=1786483329`
  reproduces the pinned counts exactly: TRAIN 7,103 (0 drift),
  **HELD-OUT 48,247 = pinned n_test**. 129 markets settled after t1
  (post-Round-1 settlements) are excluded by the t1 pin.
- `universe_heldout.json` (48,247 tickers,
  sha256 190dce95bd079e082ae4272772b7a319d4b991d2a398a0cf9cb4b6fe64ee7570).
- `universe_heldout_econ.json` (2,169 Economics tickers,
  sha256 1470ca703c1aec4a4a072206721fc06... see file meta) — the champion's
  pre-filtered universe: `ia_econ_only` carries `categories=("Economics",)`
  inside the strategy, so non-Economics markets generate zero intents/fills
  by construction; replaying them would only burn hours (verified byte-
  identical on train by the Round-3 overfit audit). The runner-up
  `ib_swing_filter` trades all categories and runs on the full 48,247.

## The two configurations (frozen; nothing else runs on held-out)

1. **ia_econ_only** (champion): `flb.R5Endgame(window_s=21600, band=(90,98),
   vol_frac=0.25, bias_frac=0.013, use_replace=True,
   categories=("Economics",))`.
2. **ib_swing_filter** (runner-up): `wrappers.R5SwingFiltered(window_s=21600)`
   (X=20c, T=24h, N=24h census constants; all categories).

Engine: `EngineConfig(fee_schedule=FeeSchedule.load_default(),
maker_queue=MakerQueueConfig(depth_windows=1.0), cancel/replace on,
RiskConfig defaults: $10k bankroll, quarter-Kelly, 5%/market, 10%/event,
80% total deploy)`. Companion runs per SPEC §6, same as Rounds 1–2:
capacity 3×/10× depth multiplier and fee-stress ×1.5. One base evaluation
per config; **no iteration afterward** regardless of outcome.

## Graduation gates (TOURNAMENT_PROTOCOL.md, applied verbatim)

- Net P&L > 0 after fees (inference = $0 for price-only strategies)
- Fee ×1.5 stress P&L > 0
- Concentration: top-5 markets < 60% of profit
- Fill rates within sanity bands (maker ≤ 60%; band metric = CONTRACT-level
  per the remediation above)
- No adversarial-audit violations (F1 stands CONFIRMED against the *train*
  headline; for the held-out verdict the F2 remedy above additionally
  requires the CLEAN-sub-window check to support the full-window sign)

## Execution / restart plan (frozen)

- Runner: `bot/strategies/tournament/run_round4.py`. Each (config, run-type,
  shard) is one idempotent unit writing
  `reports/tournament/round4/<variant>/shards/<shard>_<run>.json`; completed
  units are detected and skipped on restart. All long runs under `nohup`
  with timestamped logs.
- `ib_swing_filter` (48,247 markets) is sharded into 4 strided alphabetical
  parts (`tickers[i::4]`, i = 0..3) per run-type. Shards are merged by: summing net P&L /
  fees / contract counts, concatenating episode rows (episode stats and
  event-clustered SEs are functions of the episode rows), recomputing
  concentration from merged per-market P&L, and recomputing fill rates from
  merged order/contract numerators and denominators. Validity condition
  (checked and reported): markets couple only through bankroll/cash and the
  80% total-deploy cap; each shard runs with the full $10k bankroll, so the
  merge is exact iff no shard ever binds the total-deploy/no-borrow clamps
  and realized P&L never moves available cash enough to change a Kelly
  clamp. We report each shard's max concurrent deployed premium and the
  count of total-deploy/no-borrow clamp events; if any shard shows a
  binding clamp, the merged number is flagged as approximate and the
  monolithic run is rerun overnight instead of iterated on.
- Timing is calibrated on the F4 train remediation run before launching.

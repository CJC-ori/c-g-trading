# Round-3 VIOLATION remediation record (executed BEFORE the held-out run)

Audit verdict: 1 VIOLATION (F1 confirmed, F2 confirmed, F3 plausible-channel,
F4 confirmed-in-form) + 2 MINOR_ISSUES panels. Remediation per finding:

## F4 (universe lifetime-volume filter = future info at decision time) — FIXED, delta $0.00

- Fix: rebuild the champion's train universe with `min_volume=0` (the only
  harness/config element the auditor proved defective in form). Re-verified
  the auditor's claim: the F4-fixed selection yields the **identical 386
  train Economics tickers** (the volume>=1000 filter excluded zero
  Economics markets; its exclusions were 6,638 weather + 1 World).
- Re-run (2026-08-12 14:07Z, `run_round4.py`, same engine/fees/queue):
  - base: net **+14,421c = +$144.21**, 29 episodes, 91 fills — episode
    table byte-identical to the published Round-2 champion artifact
    (`reports/tournament/round2/ia_econ_only/episodes.csv`, 0 mismatches).
  - fee-stress x1.5: net **+14,311c = +$143.11** — identical to published.
- **Champion still passes train after the F4 fix; delta = $0.00.** Held-out
  residual exposure quantified in PRECOMMIT.md (1 extra market of 2,170 for
  the champion; 4,213 for the weather-heavy runner-up — disclosed as a live
  caveat in its verdict, universe kept pinned per protocol).

## F1 (settlement-outcome circularity of the category filter) — remediated by reclassification + Round 4

No harness fix exists for selection-on-outcome. Remediation: the train
+$144.21/+4.12%/ep headline is reclassified as an in-sample-selected upper
bound (winner's curse across 4 categories) and is not quoted as an
expectation anywhere in Round 4 / FINAL.md. The single held-out run is the
only estimate of edge; train-vs-held-out degradation is reported explicitly.

## F2 (held-out contamination by the 2026-05-25+ FLB pull) — remediated by pre-committed breakout + DB pin

See PRECOMMIT.md: DB snapshot pinned (backfill idle, WAL empty, two samples
identical), CLEAN vs CONTAMINATED sub-window breakout and 132-flb-train-
ticker flags pre-committed before the run, with the rule that a positive
verdict must survive on the CLEAN sub-window.

## F3 (close_ts anchoring channel) — post-run audit pre-committed

Traded held-out markets are checked for unscheduled early closes
(can_close_early, close vs expected expiration, rule-named calendar events);
flagged episodes' P&L quantified. Results in FINAL.md.

## Round-2 reporting error (fill-rate band metric) — fixed

Round 4 quotes the maker CONTRACT-level fill rate against the <=60% gate
(the metric the queue model was calibrated on). For the record, the train
champion's rates are: contract-level 26.68% (band metric), order-level
44.62% (reported as such, not as the band metric).

## Diagnostics retained

Train remediation run diagnostics (also the shard-merge validity baseline):
clamp reasons {kelly: 29, per-market-cap: 11}, zero total-deploy/no-borrow
clamps, max concurrent deployed $1,357.30 of the $8,000 total-deploy cap.

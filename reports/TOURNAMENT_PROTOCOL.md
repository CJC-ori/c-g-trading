# Tournament protocol — pre-registered before any held-out evaluation

*Written 2026-08-12, after all train-split results, before ANY strategy has
touched the held-out 40%. Per Chris's instruction: variants compete on
identical harness settings, a judge panel ranks them, losers' demonstrated
strengths are folded into the winner, an adversarial verifier attacks each
champion, and only final champions get a single held-out evaluation.*

## Entrants and their train-split status

| # | Strategy | Train verdict | Enters tournament? |
|---|---|---|---|
| 1 | FLB R5-endgame (6/12/24h windows) | PASS (+5.4–6.3%/ep, honest fills 16–30%) | **Yes — presumptive champion** |
| 2 | FLB R2-maker | Flipped negative under queue model | No (killed) — its *stand-down-near-close* insight already lives in R5 |
| 3 | FLB R1-taker | Killed (fees + tails) | No |
| 4 | Weather ground-truth | Killed (market Brier wins, all configs) | No |
| 5 | Panic R4 ladders | PARKED (5/5 hits, n=9 < 10 bar, structural) | Overlay candidate only (see integration) |
| 6 | Panic R4b convexity | Killed (arm rate 3.8% < 10% breakeven) | No |
| 7 | Swing fade (general) | Killed (quote-only reversion, 38% adverse) | No; census carries filters forward |
| 8 | Eco spike-fade | PARKED (+$562/90, fails concentration) | Portfolio-addition candidate |
| 9 | Structure R3/consistency/R7 | Demoted to monitoring / killed | No |
| 10 | LLM forecaster (pooled) | Killed at rung 3 (−$433 net, ablation=full) | No |
| 11 | LLM hits-based selective | Pending (running now) | Conditional on its train verdict |

## Round structure

**Round 1 — full-history championship run.** All entrants (R5 in its three
windows + surviving conditional entrants) rerun on the FULL backfilled train
universe (2021-07 → t_split), identical settings: exact FeeSchedule, maker
queue depth_windows=1.0, cancel/replace on, $10k bankroll, quarter-Kelly.
The train-period results reported per SPEC §6 (all metrics, all stresses).
This is the binding test the hardening agent deliberately deferred.

**Round 2 — integration.** Fold losers' demonstrated strengths into the
Round-1 winner as *pre-named* variants (no free-form search):
- I-a: R5 + category filter (drop categories whose R5 win-rate-vs-price curve
  sat below breakeven on train; from reports/flb/ANALYSIS.md).
- I-b: R5 + swing-census adverse-selection filter (exclude entries within N
  hours after a ≥20¢/24h adverse swing — swings are information).
- I-c: R5 + panic-night stand-down (skip endgame entries in the final hours of
  scheduled election-night windows where R4-class wicks occur; avoids being
  run over by the panic).
- I-d: R5 portfolio + parked R4 overlay (R4 trades its 9-episode class live;
  capacity additive, risk caps shared).
- I-e: R5 + eco spike-fade portfolio addition.
Each integration variant runs on train only. Keep any that improves net P&L
without worsening fee-stress survival or concentration; else discard.

**Round 3 — adversarial verification.** Independent verifier agents attack the
champion + kept integrations on train artifacts only: lookahead audit (decision
inputs vs data timestamps), fill-optimism audit (fill rates vs sanity bands,
queue assumptions), overfit audit (parameter provenance — every parameter must
trace to a pre-registered source, not to P&L iteration), concentration/regime
audit (is P&L one event or one month?). Any CONFIRMED violation kills or
reverts the variant.

**Round 4 — the single held-out evaluation.** The champion and AT MOST two
runner-up configurations get ONE run each on the held-out 40%. Numbers are
reported as-is, pass or fail. No iteration afterward: if held-out fails, the
honest conclusion is "no graduating strategy this session," not another lap.

## Graduation gates on held-out (from SPEC §7, fixed now)

- Net P&L > 0 after fees (and inference where applicable)
- Fee ×1.5 stress P&L > 0
- Concentration: top-5 markets < 60% of profit (hits-based entrants exempt
  per SPEC's hits clause but must show ≥3 independent hits)
- Fill rates within sanity bands (maker ≤60%)
- No adversarial-audit violations

## What "winning" means for the final report

The deliverable is the best-graduating configuration + the full kill ledger.
A parked-but-positive strategy (R4) ships as a paper-trading plan for Nov
2026, not as a live allocation.

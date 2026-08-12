# ROUND 4 — the single held-out evaluation (2026-08-12)

Executed per TOURNAMENT_PROTOCOL.md Round 4 and the pre-commitments frozen in
`PRECOMMIT.md` BEFORE any held-out replay. Audit remediation record:
`REMEDIATION.md` (F4 config fix re-verified on train, delta $0.00; DB snapshot
pinned; contamination breakout pre-committed; fill-rate band metric corrected
to contract-level). Runner: `bot/strategies/tournament/run_round4.py`.
Reproduce (from repo root):

```
python -m bot.strategies.tournament.run_round4 --variant ia_econ_only \
    --tickers-file reports/tournament/round4/universe_heldout_econ.json \
    --run {base,cap3,cap10,stress} --out reports/tournament/round4
python -m bot.strategies.tournament.run_round4 --variant ib_swing_filter \
    --tickers-file reports/tournament/round4/universe_heldout.json \
    --run base --shard {0..3} --n-shards 4 --out reports/tournament/round4
python -m bot.strategies.tournament.run_round4 --variant <v> --merge
```

## Setup (pinned)

- Held-out = 48,247 markets settling in (split_ts=1722556901, t1=1786483329]
  under the frozen Round-1 UniverseConfig — reproduces the pinned n_test
  EXACTLY (0 drift; 129 post-Round-1 settlements excluded by the t1 pin).
  Ticker lists + sha256 in `universe_heldout.json` /
  `universe_heldout_econ.json` (2,169 Economics markets for the champion,
  whose category filter makes non-Economics markets structurally untradeable).
- Engine: exact FeeSchedule.load_default(), maker queue depth_windows=1.0,
  cancel/replace on, RiskConfig defaults ($10k, quarter-Kelly, 5%/10%/80%),
  fee stress x1.5. ONE base evaluation per config; no iteration.
- DB snapshot pinned in PRECOMMIT.md (trades=9,611,621, backfill idle).

## Results — champion `ia_econ_only` (R5-6h, band 90–98c, Economics only)

| metric | train (Round 2, in-sample-selected) | HELD-OUT |
|---|---|---|
| episodes (event clusters) | 29 (21) | **309 (195)** |
| net P&L after fees | +$144.21 | **+$1,642.14** |
| per-episode mean ± clustered SE | +4.12% ± 0.41% | **+2.36% ± 1.30%** |
| 95% CI | [+3.33%, +4.92%] | **[−0.18%, +4.91%]** |
| dollar-weighted return (basis) | +3.62% ($3,985) | **+2.63% ($62,467)** |
| losing episodes | 0 | **9** (worst −$499.70, −100.5%) |
| fee ×1.5 stress | +$143.11 | **+$1,638.17** |
| capacity 1×/3×/10× | +144/+171/+174 | **+1,642/+1,730/+1,725** |
| top-5 share of net | 35.0% | **9.9%** |
| maker CONTRACT fill rate (band metric) | 26.7% | **34.5%** (order-level 30.7%) |
| fees | — | $8.42 (per-series exact; FED/CPI/gas series charge ~0 maker fees) |

Distribution: median +4.17%, p25 +3.02%, min −100.5%, max +11.1%, 97.1%
positive. 1,006 maker orders, 0 taker, 65,442 contracts. P&L by series:
AAA-gas ladders (KXAAAGASW/D/M) +$1,319.73 on 175 episodes ≈ 80% of net;
FED/FOMC-family +$273.23; CPI-family +$112.61; losers concentrated in
KXPAYROLLS (−$293.05 on 3 Jan-2026 payroll-surprise strikes) and 2 gas-ladder
blowups. The two ~−100% episodes the train sample warned about DID occur;
the book absorbed them.

### F2 contamination breakout (pre-committed)

| slice | n | net | mean/ep |
|---|---|---|---|
| CLEAN (settle 2024-08-02 .. 2026-05-25) | 197 | **+$662.07** | +1.04% |
| CONTAMINATED (FLB-pull span 2026-05-25+) | 112 | +$980.07 | +4.69% |
| … of which the 132 flb-train tickers | 6 | +$51.78 | +7.18% |

The pre-committed rule ("a positive verdict must survive on the CLEAN
sub-window alone") is satisfied: CLEAN net +$662.07 > 0, top-5 share within
CLEAN = 24.5% < 60%, 8/197 losers. Honest flag: 60% of held-out profit and
the fatter +4.69%/ep mean sit in the contaminated 2.5-month window — which is
also where Kalshi volume exploded and where the 2025-26 trades backfill gives
the best fill fidelity, so selection contamination and genuine
regime/data-quality effects are confounded there. The graduation case rests
on the CLEAN number.

### F3 close-anchor audit (pre-committed)

All 309 traded markets are can_close_early=1. 296 episodes are in series
whose settlement is a scheduled data release / calendar decision (AAA gas
readings, FOMC/CB calendars, BLS/BEA releases, weekly stats) — close anchored
ex-ante. 13 episodes sit in threshold/event series where unscheduled early
close is possible (KXMUSKNW, KXNURSESTRIKE, KXMAJORPROTEST, KXUSDEBTMON,
KXCREDITC, KXTRUEV, KXTOP3WEALTH, KXDEBTGROWTH25): +$91.07 = 5.5% of net,
and 12 of the 13 in fact closed exactly at their scheduled calendar
timestamp (one, KXDEBTGROWTH25-25DEC31-38, closed at an off-schedule instant;
it carries +$1.35). Voiding all 13 leaves +$1,551.07 — verdict unchanged.
Detail: `ia_econ_only/f3_close_anchor_audit.json`.

### F4 residual

The F4-fixed (min_volume=0) universe would add exactly 1 market to the
champion's 2,169 (quantified pre-run in PRECOMMIT.md); immaterial.

## Results — runner-up `ib_swing_filter` (R5-6h + adverse-swing filter, all categories)

| metric | train | HELD-OUT |
|---|---|---|
| episodes (clusters) | 81 (69) | **1,158 (767)** |
| net P&L after fees | −$78.93 | **−$2,035.13** |
| per-episode mean ± SE [CI] | −4.81% ± 3.67% | **−1.40% ± 0.87% [−3.11%, +0.32%]** |
| dollar-weighted (basis) | −1.23% ($6,443) | **−0.92% ($222,158)** |
| maker CONTRACT fill rate | 11.7% | **22.6%** |
| top-5 share | n/a (net ≤ 0) | n/a (net ≤ 0) |
| fee ×1.5 / capacity | −$80.03 / rising | **CANCELLED** (see below) |

Per category: Climate and Weather −$3,031.74 (339 eps) and Financials
−$1,519.99 (69) bury the positives (Economics +$1,415.73 on 276,
Politics +$530.57, Elections +$403.33). CLEAN sub-window −$2,232.04;
contaminated +$196.91. The swing filter again fails to save unfiltered R5:
the weather endgame book is structurally negative out of sample too.

**Companion-run cancellation (documented deviation):** after all 4 base
shards completed, the remaining cap3/cap10/stress shard-units (~40 CPU-hours)
were cancelled — base already fails the primary gate, fee ×1.5 strictly
raises costs for a maker-only buy-and-hold book and cannot flip a negative
net, and the capacity curve gates no decision. This cancels diagnostics of a
failed config; it is not an extra evaluation. `COMPANION_RUNS_CANCELLED.txt`.

## Shard-merge validity (ib ran as 4 strided shards; ia single-process)

Kelly sizing uses the static configured bankroll, so markets couple ONLY via
the no-borrow / total-deploy clamps. Across every shard and run: zero
`no-borrow` and zero `total-deploy-cap` clamp events; max concurrent deployed
premium $2,102–$3,382 per shard vs the $8,000 cap. The merge is therefore
exact, not approximate. Clamp counts and deployment maxima per shard are in
each `report.json` under `merge_diagnostics`.

## Graduation gates (protocol, verbatim; band metric = maker CONTRACT rate)

| gate | ia_econ_only | ib_swing_filter |
|---|---|---|
| Net P&L > 0 after fees | **PASS** (+$1,642.14) | **FAIL** (−$2,035.13) |
| Fee ×1.5 stress P&L > 0 | **PASS** (+$1,638.17) | **FAIL** (base negative; stress cannot be positive — runs cancelled) |
| Top-5 < 60% of profit | **PASS** (9.9%; CLEAN-only 24.5%) | n/a — FAIL upstream |
| Maker fill ≤ 60% | **PASS** (34.5% contract-level) | **PASS** (22.6%) — moot |
| No adversarial-audit violations | **PASS** (remediations executed pre-run; pre-committed CLEAN-sub-window check satisfied: +$662.07 > 0) | FAIL upstream |

**Verdict: `ia_econ_only` passes all five gates and graduates to paper
trading. `ib_swing_filter` fails and is killed.**

## Honest caveats on the graduating number (stated, not walked back)

1. The event-clustered per-episode 95% CI is [−0.18%, +4.91%] — it grazes
   zero on equal-weighted episodes. Net P&L, the gate metric, is
   unambiguously positive, and the dollar-weighted return is +2.63%.
2. Train-to-held-out degradation is real: +4.12%/ep (in-sample-selected,
   zero losses) → +2.36%/ep with 9 losses; on the never-seen CLEAN window
   alone, +1.04%/ep. The F1 warning (winner's-curse train number) was
   correct in direction.
3. 80% of held-out profit is one series family (AAA gas price ladders) —
   below the top-5 market-level gate, but a family-level concentration a
   paper-trading plan must watch.
4. 60% of profit sits in the F2-contaminated 2026-05-25+ window; the
   graduation case is carried by the CLEAN +$662.07.

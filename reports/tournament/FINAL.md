# TOURNAMENT FINAL — conclusive document (2026-08-12)

Four rounds ran exactly as pre-registered in `reports/TOURNAMENT_PROTOCOL.md`
(written before any held-out touch): Round 1 full-history championship,
Round 2 pre-named integrations, Round 3 adversarial audits (1 VIOLATION +
2 MINOR_ISSUES panels, remediated before Round 4 — see
`round4/REMEDIATION.md`), Round 4 the single held-out evaluation
(`round4/ROUND4.md`, pre-commitments in `round4/PRECOMMIT.md`).

## Champion and held-out result

**Champion: `ia_econ_only`** — `flb.R5Endgame(window_s=21600, band=(90,98),
vol_frac=0.25, bias_frac=0.013, use_replace=True, categories=("Economics",))`
on the SPEC harness (exact per-series fees, maker queue depth_windows=1.0,
cancel/replace, $10k quarter-Kelly, 5%/10%/80% caps). Mechanism: in the final
6 hours of an Economics market, maker-join the bid of whichever side rests at
90–98c and hold through the scheduled resolving event; edge = the documented
favorite-longshot bias at the endgame, harvested passively.

Single held-out run (48,247-market held-out split, champion trades its 2,169
Economics markets; settlements 2024-08-02 → 2026-08-11):

- **Net +$1,642.14 after fees** on $62,467 deployed (dollar-weighted +2.63%);
  fee ×1.5 stress **+$1,638.17**; capacity 1×/3×/10× = 1,642/1,730/1,725.
- 309 episodes / 195 event clusters; mean **+2.36%/ep**, clustered SE 1.30%,
  95% CI [−0.18%, +4.91%]; 97.1% winners; 9 losses including two ~−100%
  (−$499.70 worst) — the tail the zero-loss train sample could not show.
- Top-5 markets 9.9% of profit; maker contract fill rate 34.5% (≤60% gate,
  band metric per the Round-3 reporting fix).
- Pre-committed contamination breakout: CLEAN never-seen sub-window
  (2024-08 → 2026-05-25) **+$662.07, +1.04%/ep, top-5 24.5%**; the
  FLB-pull-contaminated tail (2026-05-25+) +$980.07. The pre-registered rule
  — the verdict must survive on CLEAN alone — is satisfied.
- Close-anchor (F3) audit: 95.8% of episodes in scheduled-release series;
  the 13 threshold-series episodes carry +$91.07 (5.5% of net) and 12/13
  closed on their scheduled timestamp anyway.

**All five graduation gates PASS → `ia_econ_only` graduates to paper trading
on live markets (demo env), the first and only strategy of this session to
do so.**

## Runner-up

**`ib_swing_filter`** (R5-6h all-category + ≥20c/24h adverse-swing entry
filter): held-out **−$2,035.13** on 1,158 episodes (mean −1.40%/ep, CI
[−3.11%, +0.32%]); weather −$3,031.74 and Financials −$1,519.99 overwhelm the
positive Economics/Politics/Elections cells; negative on the CLEAN sub-window
too. Fails the first gate; capacity/fee-stress companion runs were cancelled
as moot (documented). **Killed.** Its one out-of-sample lesson: unfiltered
R5-endgame remains structurally negative outside Economics, confirming the
category restriction is what carries the champion — while the Economics cell
was +$1,415.73 even inside this failing config.

## Train-vs-held-out degradation (fair account)

The Round-3 audit CONFIRMED the train headline was selected on its own
outcomes (F1). Held-out behaved accordingly: per-episode mean fell 43%
(+4.12% → +2.36%), the zero-loss profile broke (9 losses, two −100%-class),
and on the never-seen CLEAN window the mean is +1.04%/ep. Net P&L scaled up
15× only because the held-out span offers 10× the qualifying Economics
markets. Family concentration is the real live risk: ~80% of held-out profit
is AAA-gas-price ladders (passes the market-level gate at 9.9%, but it is one
data-generating process). The equal-weighted CI grazing zero means the edge
is proven at the portfolio/dollar level, not at per-episode-certainty level.

## Audit remediation (what was fixed before the number above was produced)

- **F4** (lifetime-volume universe filter = future info): config fixed
  (min_volume=0), train re-run byte-identical (+$144.21, 29/29 episodes,
  delta $0.00); held-out residual = 1 market of 2,170, disclosed.
- **F2** (held-out contamination): DB snapshot pinned (backfill idle),
  CLEAN/CONTAMINATED breakout + 132-flb-ticker flags pre-committed and
  reported; verdict survives on CLEAN.
- **F1** (circularity): train number reclassified as in-sample upper bound;
  never quoted as an expectation; Round 4 is the only estimate.
- **F3** (close_ts anchoring): pre-committed post-run audit ran; flagged
  P&L +$91.07 (5.5%), verdict robust to voiding it entirely.
- **Reporting**: fill-rate gate quoted on the CONTRACT-level metric the
  queue model was calibrated on (34.5% held-out; 26.7% train).

## What graduates, and the kill ledger

**Graduates to paper trading (Nov 2026 window, demo env):**

1. `ia_econ_only` — live allocation candidate after paper confirmation.
   Paper-trade plan: quote exactly per the frozen config; track (a) realized
   maker fill rate vs the 34.5% simulated (kill if >60%), (b) per-family P&L
   with AAA-gas ladders broken out, (c) any 95c-class loss vs the modeled
   ~1/35 episode rate.
2. Panic R4 ladders — PARKED per protocol (5/5 hits, n=9; its episode class
   only exists post-split); ships as a paper-trading plan, not an allocation.

**Killed (full ledger):** FLB R1-taker, R2-maker, unfiltered R5-endgame
(train), weather ground-truth, panic R4b convexity, swing-fade general,
eco spike-fade (as portfolio leg, Round 2), structure R3/consistency/R7,
LLM forecaster pooled, LLM hits-based (no train pass), integrations I-c/I-d
(no-ops on train), I-e (hurts), and — Round 4 — `ib_swing_filter`.

## Artifacts

- `round4/PRECOMMIT.md`, `round4/REMEDIATION.md` — frozen before the run.
- `round4/universe_heldout*.json` — pinned held-out tickers + sha256.
- `round4/<variant>/report.json`, `episodes.csv`,
  `f3_close_anchor_audit.json`, `shards/*.json` (per-unit raw outputs incl.
  merge diagnostics), `logs/`.
- Runner: `bot/strategies/tournament/run_round4.py` (idempotent shard units;
  merge documented and validated exact — zero bankroll-coupling clamps).

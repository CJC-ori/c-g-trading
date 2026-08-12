# ROUND 3 — Overfitting / concentration / regime audit of `ia_econ_only`

Adversarial verifier, 2026-08-12. Scope per TOURNAMENT_PROTOCOL.md Round 3:
parameter provenance (every parameter must trace to a pre-registered source,
not P&L iteration), plus concentration/regime attack on the integrated
champion `ia_econ_only` = `R5Endgame(window_s=6h, band=(90,98), vol_frac=0.25,
bias_frac=0.013, use_replace=True, categories=("Economics",))`. Train
artifacts only; held-out 40% untouched. Sources verified: research/SYNTHESIS.md,
bot/strategies/flb.py docstring, reports/flb/ANALYSIS.md, TOURNAMENT_PROTOCOL.md,
ROUND1.md/ROUND2.md, run_round2.py/wrappers.py, per-variant episodes.csv, git log.

**Verdict: NO CONFIRMED provenance violation — the champion survives Round 3 —
but with three material adverse findings (F1–F3) that cap what a Round-4 pass
can honestly claim, and two documented protocol deviations (D1–D2).**

---

## 1. Parameter provenance chain (reconstructed document-by-document)

Git order confirms the pre-registration sequence: protocol committed
(`559cca6 Pre-register tournament protocol`) BEFORE Round 1 (`2283b74`) and
Round 2 (`94a8817`). ANALYSIS.md param freeze predates the tournament runs.

| parameter | value | pre-registered source | P&L-iterated? |
|---|---|---|---|
| endgame windows | 6/12/24h sweep | research/SYNTHESIS.md ("in the final 6/12/24h") — research phase, pre-backtest | No (but see D1 on picking 6h) |
| band | 90–98c | SYNTHESIS.md ("buy (maker) the side priced 90–98¢"); flb.py default | No |
| maker-join, hold to resolution | — | SYNTHESIS.md R5 spec | No |
| vol_frac | 0.25 | SYNTHESIS.md R2/R5 sizing ("≤20–25% of trailing volume"); ANALYSIS.md frozen table | No |
| bias_frac | 0.013 | flb.py docstring, frozen 2026-08-11 "before any test-set look": half GWU maker +2.6%/ep because GWU shows maker returns deteriorate at closing prices | No |
| use_replace | True | harness fix (ANALYSIS.md note 1), applied uniformly to all variants | No |
| risk config | $10k, quarter-Kelly, 5%/mkt, 10%/event, 80% | SPEC/harness defaults, unchanged all tournament | No |
| categories | ("Economics",) | protocol I-a (pre-named), rule applied to Round-1 win-rate tables | No raw-P&L iteration, but **train-selected** — see F1 and D2 |

The wrapper code confirms the integrated champion differs from the bare
Round-1 champion in EXACTLY one argument: `categories=("Economics",)`
(run_round2.py line 133–134; every other R5Endgame argument is the frozen
default). The 29 `ia_econ_only` episodes are byte-identical (ticker + P&L)
to the Economics subset of the champion re-run — verified against both
episodes.csv files. No hidden re-tune.

### D1 (deviation, documented): the 6h window was chosen post-hoc as "least-bad"

The protocol's Round 2 assumed a Round-1 *winner*; none passed, and the 6h
window was selected because it lost least on train (-$679 vs -$7.2k/-$10.0k).
That is train-P&L-based selection among the three pre-registered windows —
a protocol gap, not a pre-registered rule. Mitigating facts: (a) the overall
P&L that drove the choice is dominated by weather episodes the final champion
never trades, so the selection is nearly orthogonal to the Economics cell;
(b) 6h is NOT the best Economics window on train: it has the lowest
per-episode return (+4.12% vs 12h +4.65%, 24h +5.25%) and 12h beats it on
net dollars too (+$182.70 vs +$144.21; 24h +$131.88). Whoever was fishing
for the best-looking Economics number would have picked 12h (max $) or 24h
(max %/ep), not 6h.
Verdict: deviation, transparently documented in ROUND2.md, direction not
self-serving. Not a kill.

### D2 (deviation, documented): the I-a rule was evaluated on a different source than the protocol cites

Protocol I-a says "drop categories whose R5 win-rate-vs-price curve sat below
breakeven on train; **from reports/flb/ANALYSIS.md**". Applied literally to
ANALYSIS.md (the 2026-only pull), the rule drops NOTHING — every category
there clears every bin (win rate 1.000 across the board). The implemented I-a
instead applied the rule to the Round-1 full-history tables, and tightened it
to "above breakeven in EVERY bin in EVERY window", which excluded Politics
(Politics cleared all 6h bins on n=3 but failed bins in 12h/24h). Two facts
keep this out of violation territory: (a) the tightening was registered in
ROUND1.md interpretation #3 *before* Round 2 ran (git order confirms); (b) it
REDUCED train P&L — keeping Politics would have reported +$172.09 instead of
+$144.21. A P&L-fisher does not throw away +$27.88. Conservative direction,
documented. Not a kill.

---

## 2. F1 — The headline number is an in-sample-selected estimate (winner's curse)

Economics was kept BECAUSE it was the only category positive on the
2021-07..2024-08 train window, and its +$144.21 / +4.12%/ep is then reported
on the SAME window. That is selection of 1 winner from 4 candidate categories
(Climate/Weather, Economics, Politics, World) on the very data used for the
estimate. The rule was win-rate-vs-breakeven, not raw P&L, and the Economics
cell is strong in all three windows (1.000 win rate, every bin, n=29/39/51) —
but the expected out-of-sample mean is strictly below the in-sample-selected
mean, by construction. The +4.12% must be treated as an upper bound going
into Round 4, not a point estimate. This is the exact failure shape that
already burned this tournament once (2026-pull R5 +6.3% zero-loss → full
history -4.5%). The control is the protocol's own design: Round 4's single
held-out run is the only estimate that counts.

## 3. F2 — 29 episodes are ~18 independent bets, and zero losses is exactly what NO edge predicts

Reconstructed from episodes.csv settlement dates:

- FED-* and FEDDECISION-* markets on the same FOMC meeting are the SAME
  real-world outcome under different event_tickers (e.g. FED-23JUN-T5.25 +
  FEDDECISION-23JUN-H0 + FEDDECISION-23JUN-H25 = one June-2023 decision;
  likewise 23MAY, 24JUN, 24JUL). Intra-event YES/NO pairs (FED-22MAR-T0.25
  yes + FED-22MAR-T0.5 no, etc.) are also one bet on one outcome.
- Unique real-world outcomes: **15 FOMC meetings + CPI-21DEC + GASM-22JUL +
  OIL-22JUL = 18**, not the 21 event clusters the SE was computed on. The
  clustered SE (and the [+3.33%, +4.92%] CI) is overstated-confidence: it
  clusters on event_ticker, which splits single FOMC decisions in two.
- Statistical power: average entry 96.5c. If market prices were PERFECTLY
  FAIR (zero edge), P(0 failures in 18 outcomes) = 0.965^18 ≈ **0.53**. The
  zero-loss record is the modal outcome under the null. The train sample
  therefore CANNOT distinguish "+4.12% edge" from "zero edge" from "mildly
  negative edge"; the entire economic case rests on the GWU favorite-bias
  prior, not on anything this train run demonstrated. The CI is conditional
  on zero failures and is not a valid CI for expected return: one failure at
  the 96.5c average costs ~-100% of an episode (~-$130 of the +$144 net,
  i.e. ~90% of total P&L; at 2024's 97.8c entries, worse).

## 4. F3 — Same-outcome exposure evades the 10%/event risk cap (concentration by mechanism)

The 10%-per-event cap keys on event_ticker, so FED-XX and FEDDECISION-XX on
the same FOMC meeting are capped independently. Measured combined basis on
single decisions:

| real-world outcome | combined basis | episodes | share of $10k bankroll |
|---|---|---|---|
| Jun-2024 FOMC (FED-24JUN + FEDDECISION-24JUN) | **$720.30** | 3 | 7.2% |
| Jun-2023 FOMC (FED-23JUN + FEDDECISION-23JUN) | $443.00 | 3 | 4.4% |
| Sep-2022 FOMC | $411.44 | 2 | 4.1% |
| May-2023 FOMC | $303.64 | 3 | 3.0% |

A single surprise FOMC (and June 2023 — the "skip" — and Sep 2024 — 50-vs-25
— were genuinely debated meetings) loses ≈ the combined basis: **-$443 to
-$720, i.e. 3.1x–5.0x the champion's entire +$144.21 net P&L**. The top-5
*market* concentration gate (35% < 60%) passes only because the gate's unit
is the market; by mechanism, **87.7% of net P&L ($126.52) is FED+FEDDECISION**
— this is functionally a single-trade-type strategy: sell certainty into
FOMC announcements, n≈15 meetings in 37 months. Recommendation (must-fix
before any live/Round-4-graduated deployment, does not change frozen params):
add a same-outcome exposure aggregation (series-family + settlement-date) to
the risk layer, and state Round-4 sensitivity in advance: one failed favorite
≈ -$130 to -$720 depending on stacking.

## 5. Year-by-year / quarter-by-quarter P&L (regime + GWU decay check)

Computed from ia_econ_only/episodes.csv (settlement-dated):

| year | net P&L | episodes | $/episode | dollar-weighted return | avg entry price |
|---|---|---|---|---|---|
| 2021 (Jul–Dec, in-window) | $0.00 | 0 | — | — | — |
| 2022 | +$75.33 | 13 | $5.79 | +4.09% | 96.0c |
| 2023 | +$49.06 | 11 | $4.46 | +3.94% | 96.1c |
| 2024 (to Aug 2) | +$19.82 | 5 | $3.96 | **+2.21%** | **97.8c** |

| quarter | P&L | n | quarter | P&L | n |
|---|---|---|---|---|---|
| 2022Q1 | +$8.93 | 3 | 2023Q2 | +$34.74 | 6 |
| 2022Q2 | +$19.33 | 3 | 2023Q3 | +$3.31 | 2 |
| 2022Q3 | +$38.49 | 5 | 2023Q4 | +$2.41 | 1 |
| 2022Q4 | +$8.58 | 2 | 2024Q1 | $0.00 | 0 |
| 2023Q1 | +$8.60 | 2 | 2024Q2 | +$14.43 | 3 |
| 2023Q3+Q4+2024Q1 combined | +$5.72 | 3 | 2024Q3 (to 8/2) | +$5.39 | 2 |

Reading:

- **No losing quarter** — but with zero losses possible only until the first
  failed favorite, that is F2 restated, not robustness.
- **Monotonic decay on every axis**: dollar-weighted return 4.09% → 3.94% →
  2.21%; $/episode 5.79 → 4.46 → 3.96; episode density 13/yr → 11/yr →
  ~8.6/yr annualized; average entry price drifting 96.0c → 97.8c. The 2024
  book is buying near-certainty at 97–98c, where gross margin is ~2% and a
  single failure is -100%: the risk/reward is thinning exactly in the
  direction of the GWU decay warning (GWU measured 2021-2025, weakening in
  2025). 2022Q3 + 2023Q2 alone are 51% of total P&L (the rate-hike-cycle
  regime of maximal FOMC-threshold uncertainty, which has ended).
- **2025-26 cannot be tested from train logs** (train ends at split_ts
  2024-08-02; everything later is held-out and was not touched). The only
  out-of-window glimpse — the earlier 2026-only pull (ANALYSIS.md, n=7
  Economics 6h episodes, +6.45%/ep) — is directionally NOT weaker, but it is
  a different, provisional universe and proves nothing. Flag for Round 4:
  the held-out window (2024-08 → 2026-08) is the Fed *cutting/holding*
  regime; expect fewer, higher-priced, thinner-margin episodes than the
  train-era hike cycle (the 2024 row above is the leading edge of exactly
  that). If Round 4 shows a materially weaker per-episode return than
  +4.12%, that is the base case, not a surprise.

## 6. Does the integrated champion differ from the bare champion in a way that smells like train-set fitting?

The only diff is `categories=("Economics",)` (code-verified; episodes
byte-identical to the champion's Economics subset). The category choice is
train-derived (F1) but via a pre-named variant with a breakeven rule, applied
in the direction that REDUCED train P&L (D2), on a category whose positivity
is consistent across all three pre-registered windows. No parameter smells of
raw P&L iteration. The 6h window choice (D1) is the weakest link in the
chain, and it too was not self-serving for the reported cell.

## 7. Minor notes

- ROUND2.md quotes "maker fill rate 45%" (order-any-fill rate, 0.446, inside
  the 40–50% band); the contract-weighted maker fill rate is 26.7%
  (report.json). Both within sanity; the table should name the metric.
- OIL/GASM episodes show fees = 0 (plain `quadratic` series) and FED/CPI
  episodes charge maker fees — matches the fee-engine claim in ROUND2.md #3.
- Engine-internal midpoint split (report.json): pre-2023-05 +$103.94 (18
  mkts) vs post +$40.27 (11 mkts) — both positive, consistent with §5 decay.

## 8. Round-3 ruling

- **`ia_econ_only`: SURVIVES (no CONFIRMED violation).** Provenance traces;
  deviations D1/D2 are documented and conservative. Eligible for Round 4
  under the protocol.
- **Binding caveats to attach to any Round-4 pass** (pre-stated now so they
  cannot be negotiated after the number is seen): (1) the train +4.12% is an
  in-sample-selected upper bound (F1); (2) the strategy has demonstrated no
  statistical edge over fair pricing — 18 independent outcomes, zero losses,
  P≈0.53 under the null (F2); (3) one surprise FOMC costs 3–5x total net
  under current risk caps — a same-outcome exposure cap is a must-fix before
  live deployment (F3); (4) held-out regime is the post-hike era where the
  train-side trend (§5) is already decaying toward 97-98c entries at ~2%
  margins.
- `ib_swing_filter` (runner-up): no provenance issue found (X=20c/T=24h/N=24h
  are protocol-verbatim + census-frozen constants, wrappers.py docstring);
  it is negative on train and graduates nothing by itself — no further
  overfit exposure beyond what ROUND2.md already states.

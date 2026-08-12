# ROUND 3 — Fill-optimism audit of the integrated champion (ia_econ_only)

*Adversarial verification per reports/TOURNAMENT_PROTOCOL.md Round 3.
Audited 2026-08-12 against train artifacts only (held-out 40% untouched).
Auditor: independent Round-3 subagent. Everything below is recomputed from
the raw tape in `data/kalshi.db` and a deterministic re-run of the exact
Round-2 command; nothing is taken from the Round-2 reports on faith.*

**Champion under attack:** `ia_econ_only` = R5Endgame(window_s=6h,
band=(90,98), vol_frac=0.25, bias_frac=0.013, use_replace=True,
categories=("Economics",)), exact FeeSchedule.load_default(), maker queue
depth_windows=1.0, universe pinned to `reports/tournament/round1/universe.json`
(TRAIN = 7,103 tickers, split_ts = 1722556901). Claimed: +$144.21 net,
29/29 winning episodes, maker fill rate "45%, inside the 40–50% band".

## Verdict summary

**No CONFIRMED fill-integrity violation. The champion survives this audit
— with one confirmed reporting error and two quantified optimism bounds
that shave but never flip the P&L.**

| # | finding | severity | P&L impact |
|---|---|---|---|
| 1 | Every one of the 91 fills verified against the raw tape: qualifying print exists at the exact fill second, fill ≤ 25% of qualifying print volume, no zero-volume-candle fills, no quote-only fills, no never-printed prices. Top-10 P&L fills individually clean (all matched 100% genuine-direction prints, 4–21× fill size). | clean | none |
| 2 | ROUND2.md quotes the ORDER fill rate (45%) against the 40–50% band; the calibrated band metric is the CONTRACT rate, which is **26.7% — below the band** (champion's own report.md says so). Under-filling, so conservative, but the "inside the band" claim is false as written. | CONFIRMED (reporting) | none (conservative direction) |
| 3 | The fill/queue model is direction-blind: buyer-initiated prints fill resting bids. 395 of 4,132 contracts (9.6%) filled at seconds with only wrong-direction prints. Voiding all suspect fills: **+$136.81 survives** (−5.1%); 27/27 remaining episodes still all winners. | PLAUSIBLE optimism, quantified | −$7.40 (5.1%) |
| 4 | Queue-depth guess (1× recent volume) is the softest assumption; the QUEUE_IMPACT calibration insensitivity claim (fill *rate* flat on [0.75,3]) does not pin down champion *P&L*: queue×3 → +$65.65 (−54%), queue×5 → +$49.59 (−66%), strict-direction+queue×5 → +$49.59. **Sign never flips.** | PLAUSIBLE sensitivity | up to −$94.62 (66%) without sign flip |
| 5 | Zero-loss record is NOT a fill-model artifact: all 65 orders — filled AND unfilled — were on sides that won. The single in-tape losing favorite (PROLLS-24JAN) was skipped by the pre-registered trailing-volume sizing rule at a decision point with 0 prior-hour volume, not by the queue. | clean (with a fragility note) | none |
| 6 | Disbelieve maker fills entirely (taker cross at entry hour, full taker fees, no fill where no ask): **+$81.03 survives** (−44%), all 29 episodes individually non-negative. | robustness bound | −44% worst case for full fill-model disbelief |

Recommendation to Round 4: PASS the champion to the held-out run, but (a)
report the contract-level fill rate as the band metric, (b) carry the
queue×3 number (+$65.65-equivalent haircut, −54%) as the honest
sensitivity band alongside the headline, and (c) keep the n=29/zero-loss
fragility caveat — the PROLLS near-miss shows a −100% episode was one
hour of volume away on train.

## 1. Reproduction

`python -m bot.strategies.tournament.run_round2 --variants ia_econ_only`
equivalent re-run (same EngineConfig, pinned universe, full JSONL event
log kept this time) reproduces the published artifacts **exactly**:

| metric | published (round2/ia_econ_only) | this re-run |
|---|---|---|
| net P&L | +14,421c | +14,421c |
| episodes | 29 | 29 |
| maker orders | 65 | 65 |
| maker contracts filled | 4,132 | 4,132 |
| order / contract fill rate | 44.6% / 26.7% | 44.6% / 26.7% |

Determinism holds; the audit below is performed on this run's event log
(91 fill events) and `data/kalshi.db` directly. The 2025 trades backfill
was idle during both runs (last `pull_log` entry finished 04:43Z; Round 2
ran 12:41Z+).

## 2. Fill rates recomputed from the event log — and a metric mislabel

Recomputed independently from the engine's JSONL event log + orders table
(not from `flb_analysis.fill_stats`):

- **Maker order fill rate: 44.6%** (29 of 65 maker orders got ≥1 fill).
- **Maker contract fill rate: 26.7%** (4,132 of 15,489 post-clamp ordered
  contracts filled).
- Taker orders: 0 (pure-maker strategy, as designed).

**Finding (reporting, CONFIRMED): ROUND2.md quotes the wrong metric
against the sanity band.** The table column "maker fill rate 45%" and
interpretation #2 ("45% — inside the SPEC 40-50% sanity band") use the
ORDER-level rate. But the queue model's own calibration doc
(`reports/flb/QUEUE_IMPACT.md`, "Metric choice") explicitly rejects the
order-level rate as lifecycle-dominated and calibrates the
**contract-level** rate to the FutureSearch 40–50% anchor — and the
champion's own `report.md` prints "Simulated maker fill rate: **26.68%**
(sanity band 40-50%)". On the calibrated metric the champion is **below**
the band, not inside it.

Direction of the error: under-filling, i.e. P&L-conservative for a
strategy whose fills all won — this does NOT inflate the +$144. It also
still clears the hard ≤60% graduation gate. But the "fill rate in the
sanity band" line in ROUND2.md is not a validation the champion is
entitled to, and Round-4 reporting must quote 26.7% (contract) as the
band metric, or state that the band is not met from below. The 40–50%
anchor itself comes from FutureSearch quoting mid-book on politics
markets, a different regime from joining 90–98c favorites in the final
6h; treat the band as a coarse plausibility check only, not as evidence
the fill model is right in this regime.

## 3. Per-fill tape verification (every fill, not a sample)

All **91 fill events** (65 orders, 29 markets) were re-checked against
the raw `trades`/`candlesticks` rows, independently of the engine
(`audit_fills.py` → `audit_out.json`):

- **A — tape event exists:** every fill timestamp matches ≥1 trade print
  at/through the order's yes-space limit at that exact second. **0
  violations.**
- **B — volume caps:** per (order, second), filled contracts ≤
  floor(0.25 × qualifying print volume at that second). **0 violations.**
- **C — quote-only / zero-volume-candle fills:** none. All 29 traded
  markets have real trade tape in the entry window, so the engine's fill
  stream was trades, never candles; synthesized quote-only candles carry
  volume=0 structurally and produced no fills anywhere in the run.
- **E — price-never-printed:** for every maker fill, the market's tape
  printed at or through the limit; no fill at a price the tape cannot
  support. **0 violations.**
- Independent queue replay of all 65 orders from
  `queue_ahead_at_placement` + the tape reproduces the engine's fill
  counts **order-for-order (0 mismatches)** — the fills are what the
  model says they are; the question is only whether the model itself is
  too generous (sections 5–6).
- Opportunity lifetime (per order): median 1,509s, min 9s — far above the
  ~5s "not ours to trade" bar; these are not colocation-only fills.

## 3b. Filled vs unfilled orders (adverse-selection probe)

If the queue model were quietly suppressing exactly the fills that lose,
unfilled orders would sit on losing sides. They do not: **filled orders
29 won / 0 lost; unfilled orders 36 won / 0 lost.** Every side R5 quoted
on train won its market; the model's un-fills cost profit, they did not
hide losses.

## 4. Top-10 largest-P&L fills vs the raw tape

Method: per-fill P&L = count × (100 − entry) − fee (all 29 markets settled
for the entered side), ranked; each fill then checked against the
`trades` rows at its exact timestamp: qualifying print present, fill ≤
floor(0.25 × qualifying print volume) at that second, queue position at
placement consistent with the tape volume between placement and fill.

| fill | P&L | prints at fill second (yes-price, count, taker) | 25% cap | genuine vol at second |
|---|---|---|---|---|
| FED-23MAR-T5.00 no 192@96 | +$7.55 | 4¢×400 y, 5¢×1425 y, 4¢×1000 y, … | 1,024 | 4,100 |
| FED-22MAY-T1 no 116@94 | +$6.84 | 6¢×1000 y | 250 | 1,000 |
| FEDDECISION-23MAY-H0 no 116@94 | +$6.84 | 7¢×328 y, 7¢×2672 y | 750 | 3,000 |
| FED-22JULY-T2.25 yes 218@97 | +$6.43 | 95¢×1832 n, 96¢×601 n, 95¢×2064 n, … | 1,249 | 5,000 |
| OIL-22JUL11-N100 no 125@95 | +$6.25 | 5¢×2205 y, 5¢×500 y, 5¢×50 y | 688 | 2,755 |
| OIL-22JUL11-N100 no 115@95 | +$5.75 | (same second as above) | 688 | 2,755 |
| FED-23JUN-T5.25 no 181@97 | +$5.33 | 3¢×156 y, 3¢×1404 y | 390 | 1,560 |
| FED-22SEP-T3.25 no 48@90 | +$4.72 | 10¢×300 y | 75 | 300 |
| FED-22NOV-T3.75 yes 111@96 | +$4.36 | 96¢×665 n, 94¢×140 n, 97¢×100 n, … | 482 | 1,931 |
| FEDDECISION-23JUN-H25 no 111@96 | +$4.36 | 4¢×835 y, 4¢×1111 y, 4¢×54 y | 498 | 2,000 |

Every top-10 fill sits well inside its per-second cap (fill is 4–21× 
smaller than the qualifying tape at that second) and — decisively — every
one matched **100% genuine-direction prints** (taker on the opposite
side, i.e. real sellers hitting our resting bid / real buyers lifting our
NO-side offer). The largest-P&L fills are the *cleanest* in the run. The
direction-suspect volume (next section) is concentrated in smaller,
later fill events.

## 5. Direction-blind fills: the one real optimism in the model

The fill simulator treats **every** print at/through a resting order's
limit as fillable volume, ignoring `taker_side`. A resting bid is only
genuinely hit by seller-initiated flow; a buyer-initiated print at/below
our bid means the ask crossed our level — it does not mechanically fill
us. In the champion's final-6h windows, 29% of band-qualifying tape
volume is wrong-direction under this test, and **395 of 4,132 filled
contracts (9.6%) filled at seconds where every qualifying print was
wrong-direction** (FED-22SEP-T3.00: 258, FED-23MAY-T5.00: 110,
FEDDECISION-23MAY-H25: 27).

Strict replay (only genuine-direction prints consume queue or fill;
identical lifecycle, queue and caps otherwise; replay verified 0-mismatch
against the engine in loose mode):

| model | maker contracts | net P&L | episodes |
|---|---|---|---|
| engine (as published) | 4,132 | **+$144.21** | 29, all winners |
| strict direction-aware | 3,757 (90.9%) | **+$136.81** | 27, all winners (FED-22SEP-T3.00 and FEDDECISION-23JUN-H0 lose their fills entirely) |

**Voiding every suspect fill costs $7.40 (5.1%).** Not a kill. (Caveat in
the champion's favor, for honesty: a buyer-initiated print at/below a
resting bid usually implies the bid *would* have been hit by the same
marketable flow under price priority, so full voiding is the adversarial
extreme, not the expected case.)

## 6. Queue-depth stress (the softest assumption)

The store has no book sizes; the model guesses displayed depth ahead of a
new order as 1× the recent per-window traded volume (champion orders:
queue₀ 23–18,513 contracts, median 1,163). `QUEUE_IMPACT.md` shows the
calibrated *fill rate* is flat for depth_windows ∈ [0.75, 3] — but that
was measured on R2-maker's universe, and fill-rate flatness does **not**
pin down this champion's P&L. Static replay of all 65 orders with the
placement queue scaled (adversarial lower bound — ignores the extra
re-quoting a live strategy would do when unfilled):

| queue multiplier | contracts | net P&L |
|---|---|---|
| ×1 (as published) | 4,132 | +$144.21 |
| ×3 | 1,616 (39%) | **+$65.65** |
| ×5 | 1,250 (30%) | **+$49.59** |
| ×3 + strict direction | 1,500 (36%) | +$59.64 |
| ×5 + strict direction | 1,250 (30%) | +$49.59 |

**P&L is 3–5× more sensitive to the queue guess than the "calibration
insensitivity" language suggests (−54% at ×3, −66% at ×5), but the sign
never flips and per-episode returns stay positive** (fewer contracts at
the same winning prices). The +$144 headline should be read as the
optimistic end of a [+$50, +$144] fill-model band.

## 7. The zero-loss record: was it fill-model luck?

The 29/29 win record is the headline risk (Round 2's own caveat). If the
fill machinery had quietly *unfilled* would-be losers, the record would be
an artifact. I scanned ALL 386 settled train Economics markets for
favorites that qualified for the band in the final 6h and LOST:

- **By candle-close quotes at decision times: exactly one loser
  qualified** — `PROLLS-24JAN-T299999` ("above 299,999 jobs in January
  2024", NO quoted 92–94 in the final hours, resolved YES — the Jan-2024
  payrolls blowout). R5 did NOT enter it. Why: at its only in-window
  decision point (12:00Z candle), trailing 1-h volume was **0**, so
  `size = int(0.25 × 0) = 0` — no order. The market's entire final-session
  volume (1,113 contracts, incl. a 1,000-lot at 13:20Z) arrived in the
  last 24 minutes before the 13:25Z close, *between* hourly decision
  points. This is honest point-in-time behavior of the pre-registered
  sizing rule, **not** a queue/fill artifact — a live bot with the same
  hourly clock and sizing rule would have stood aside identically.
- **By in-band trade prints in the final 6h: three more losers printed in
  band** (`CPICORE-22OCT-T0.3`, `GAS-22NOV14-T3.75`, `CPI-22AUG-T0.0`),
  but in all three the *book at decision times* was never in the 90–98
  band (GAS: bid 7/ask 25 — band prints were a one-second sweep wick;
  CPI-22AUG: bid 16/ask 18 at the last decision; CPICORE: bid 81/ask 85
  at the last data before the announcement, in-band prints only in the
  final 23 minutes). R5 quotes off the book at hourly decision points and
  could not have entered any of them. CPICORE additionally has **no
  candles at all for its final ~8 hours** (store gap), so the engine had
  no decision points there — a data-coverage caveat, but the pre-gap book
  (81–85) was far out of band, so the skip is almost certainly correct
  anyway.

**Conclusion: the zero-loss sample is a property of the tape + the
pre-registered sizing rule, not of fill-model optimism.** But note how
thin the margin is: one hour of ≥4 contracts of prior volume in PROLLS
and the champion buys a −100% episode (~−$23 at the 25%-of-volume size,
more at Kelly size) into the January-2024 payrolls print. The Round-2
caveat ("one 95c failure costs ~−$130") had a live near-miss in train.

## 8. Fee-model realism note (adjacent finding)

The exact fee engine resolves per-series configs from a **2026-08-11
snapshot** (`series_fees.json`, `_meta.notes`: "point-in-time resolution
is a known gap"). The champion's FED/CPI/FEDDECISION markets resolve to
`KXFED`/`KXCPI`/`KXFEDDECISION` = `quadratic_with_maker_fees`, so 2022–24
maker fills are charged today's maker fee (coefficient 0.0175,
itself flagged as triangulated/unverified). Maker fees on these series
almost certainly postdate the 2022–23 fills being charged for them, so
the direction is conservative (P&L understated by the maker-fee line);
the KXOIL/KXGAS-MONTH episodes correctly charge zero maker fee under the
snapshot. The unverifiable residual is whether any multiplier was ever
*higher* historically. No action
required, but Round 4 should keep the ×1.5 fee stress for exactly this
reason.

## 9. What survives if the fill model is simply disbelieved

Strongest robustness bound: replace every maker entry with a taker cross
at the same decision instant (pay the ask instead of joining the bid,
full taker fee, zero entry where the book showed no ask). Recomputed from
candle quotes at each episode's entry hour:

| aggregate | maker (claimed) | taker counterfactual |
|---|---|---|
| net P&L, 29 episodes | **+$144.21** | **+$81.03** |

(Per-episode recomputation: same contracts, entry at the candle-close ask
at the entry hour, exact quadratic taker fee, and $0 credited where the
book showed no ask — FED-24JUN-T5.25, FEDDECISION-23NOV-H25 and
FEDDECISION-24JUN-C25 had ask = 100/none, so they contribute nothing
rather than an assumed fill. Every one of the 29 episodes remains
individually non-negative as a taker.)

**Even with zero credit for maker fills, the Economics-endgame edge stays
positive on train.** The maker fill model is worth ~44% of the claimed
P&L, not 100% of it.

## Files

- Re-run + event log: scratchpad `rerun_champion.py` →
  `ia_econ_only_events.jsonl`, `fills.json`, `orders.json`, `intents.json`
- Tape audit: scratchpad `audit_fills.py` → `audit_out.json`
- Strict direction-aware replay + queue×{1,3,5} stress: scratchpad
  `strict_replay.py` → `strict_replay_out.json` (loose replay verified
  0-mismatch against the engine before the stressed variants were read)
- Loser-scan and taker-counterfactual queries: inline in the audit
  session transcript (read-only against `data/kalshi.db`)

Nothing was committed; the held-out 40% was never touched.

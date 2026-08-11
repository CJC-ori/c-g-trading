# P-5 price-only structure scanner — results and kill verdicts

**Built 2026-08-11 by the structure-scanner engineering agent.** Covers
SYNTHESIS §1 P-5's three sub-signals: **R3** (multi-outcome overround),
**cross-instrument consistency** (binary control markets vs their seat
ladders), and **R7** (Kalshi↔Polymarket divergence). Everything is
price-only: zero inference cost, no contamination window, full history
usable (SPEC §1).

Code: `bot/strategies/structure/` (58 tests, all passing). Raw outputs:
`reports/structure/*.json`. Data pulled by this package:
`data/structure/structure.db` (2.99 M Kalshi candles / 16,612 tickers) and
`data/polymarket/` (992 k Polymarket 1-minute midpoints) — both covered by
`.gitignore`'s root-anchored `/data/`.

---

## 0. Headline — the R3 answer (unpublished original research)

**Nobody has published Kalshi's multi-outcome overround frequency. Here it
is, measured over 2,871 settled mutually-exclusive events / 377,092
event-hours / 13,529 legs, Mar 2025 – Aug 2026.**

> ### A tradeable, fee-clearing overround appears on Kalshi **0.75 times per week**.
> **274 event-hours out of 377,092 (0.073%)**, on **65 of 2,871 events
> (2.3%)**. Median net edge when it fires: **+0.73¢ per $1 set**
> (mean +1.56¢, p90 +3.59¢, max +34.8¢).
> **Median volume-capped fillable size: $0.98** (25% of the thinnest leg's
> trailing-24 h volume). Only **6 of the 274 firing hours — all in one
> single event — could absorb $200.**

Two supporting facts make that number mean something:

1. **The raw signal is 71× more common than the tradeable one.** Σ best-bid
   YES exceeds 100¢ in **19,646 event-hours (5.21%)**. Fees and the 1¢
   buffer destroy **98.6%** of them. The Kalshi taker fee on an n-leg set
   is n separate `0.07·P(1−P)` charges — on a 10-leg event that is ~4–7¢
   of fee against an overround that is usually 1–3¢. *The overround exists;
   the fee is bigger than it.*
2. **The literal rule in SYNTHESIS is not the rule you can trade** (below).

### ⚠ Microstructure correction to SYNTHESIS R3 — load-bearing

SYNTHESIS says: *"when Σ best-ask YES > 100¢ + total fees + 1¢ buffer, buy
the full NO set."* That is **not executable**, and taken literally it fires
in **73.7% of all event-hours**.

Kalshi's book exposes YES bids and NO bids only; the API's own words
(`research/kalshi-api.md` §7): *"a bid for yes at price X is equivalent to
an ask for no at price (100−X)"*. So the reported `yes_ask` **is** 100 −
best NO bid, and:

```
price to BUY NO on leg i  = 100 − yes_bid_i          (the NO ask)
NO-set cost               = 100n − Σ yes_bid_i
NO-set payoff             ≥ (n−1)·100                (= n·100 if no leg wins)
NO-set gross P&L          ≥ Σ yes_bid_i − 100        <-- BID sum, not ask sum
```

Σ ask > 100¢ is simply *"the book has a spread"*: for coherent mids it
equals 100 + Σ spreads. Scanning for it measures the spread, not an edge.
The executable condition is **Σ best-BID YES > 100¢ + fees + buffer**.
`overround.py` computes both and labels the ask version
`literal_ask_sum_diagnostic`; a unit test pins the distinction
(`test_no_set_uses_bids_not_asks`). Every number in this report uses the
executable version.

### Data-quality result: the `mutually_exclusive` flag is trustworthy

Of the 2,896 settled multi-outcome events with ≥2 settled legs,
**2,816 resolved exactly one YES and 80 resolved zero — never two.** The
NO-set payoff floor of (n−1)·100 held in 100% of observed events (the 80
zero-YES events pay n·100, strictly better). Those same 80 are why the
**YES-set** (underround) variant is only riskless on *structurally*
exhaustive events; `is_exhaustive_by_structure` proves exhaustiveness from
contiguous open-ended `between`/`less`/`greater` strike ladders only, and
never from candidate fields — a write-in outcome is always possible.

### R3 detail

| | executable NO-set | YES-set (exhaustive events only) | literal ask-sum (diagnostic) |
|---|---|---|---|
| events scanned | 2,871 | 806 | 2,871 |
| event-hours | 377,092 | 43,814 | 377,092 |
| firing hours | 274 (0.073%) | 58 (0.13%) | 277,949 (73.7%) |
| firing events | 65 (2.3%) | 7 (0.87%) | — |
| **firing events/week** | **0.75** | 0.15 | — |
| median net edge | +0.73¢ | +1.29¢ | — |
| max net edge | +34.8¢ | +13.8¢ | — |
| median fillable (same-hour vol) | $0.00 (80.7% zero) | $0.00 (94.8% zero) | — |
| median fillable (trailing-24 h vol) | $0.98 | $0.00 | — |
| p90 / max fillable (24 h) | $62.76 / $716 | $0.08 / $0.15 | — |

**By category (firing hours):** Elections 245, Politics 17, Economics 6,
Science & Tech 3, Financials 2, Weather 1. **By event:** Elections 51 of the
65. Overwhelmingly candidate-field and margin-of-victory events with 7–10
legs (10-leg events supply 115 of 274 firing hours).

**By time:** 2025-03 ×1, 2026-02 ×4, 2026-04 ×18, **2026-05 ×189**,
2026-06 ×57, 2026-07 ×5. The signal is a *primary-season artifact*: it
concentrates in the weeks when many thin candidate-field events trade at
once. This is not a stationary edge.

**Persistence:** 125 episodes; **median 1 hour**, p90 4 h, max 33 h. Half
the firings are single-hour states.

**Robustness (nothing tuned on the test window — see §4):**

| run | firing events/week | median net | median fillable (24 h) |
|---|---|---|---|
| staleness 1 h (no forward-fill) | 0.66 | +1.27¢ | $2.98 |
| staleness 6 h | 0.74 | +1.10¢ | $2.09 |
| **staleness 24 h (headline)** | **0.75** | **+0.73¢** | **$0.98** |
| staleness 72 h | 0.76 | +0.74¢ | $0.80 |
| fees ×1.5 | 0.33 | +0.79¢ | $1.45 |

The conclusion is identical at every setting, and the fee-stress run alone
halves the frequency — a strategy one fee change from nonexistent.

### R3 as an actual strategy (harness replay)

`NoSetSweep` (riskless set, executed leg by leg, IOC takers) and
`BracketFade` (R3's higher-capacity clause) were replayed through
`bot/backtest` on the 71 flagged events, hourly, exact per-series fees,
$10 k bankroll:

| strategy | split | net P&L | fills | events traded | events + | median event P&L |
|---|---|---|---|---|---|---|
| NoSetSweep | all | **+$146.74** | 59 | 35 | 18 | +$0.16 |
| NoSetSweep | train (first 60% by close) | +$146.26 | 40 | 21 | 12 | +$0.70 |
| NoSetSweep | **test (last 40%)** | **+$0.48** | 19 | 14 | 6 | **−$2.29** |
| BracketFade | all | +$12.08 | 5 | 4 | 4 | +$2.10 |
| BracketFade | test | +$1.30 | 1 | 1 | 1 | +$1.30 |

**85% of the whole-sample profit is one event** (`KXCANCOALITION-30`,
+$124.34); the held-out split nets **+$0.48 on a $10,000 bankroll** with a
*negative* median event. Fee-free arithmetic says the set is riskless — but
the harness executes it leg by leg (the engine hands a strategy one market
at a time and rejects cross-ticker intents), so a partial fill leaves a
partial set, and a partial NO-set is a directional bet. That leg risk is
real, not an artifact: nobody can lift 10 legs atomically on Kalshi, and
Kalshi has no negRisk-style convert function.

### R3 verdict → **DEMOTE TO MONITORING** (both kill criteria fire)

SYNTHESIS: *"overround-after-fees frequency < 1 event/week **or** median
fillable size < $200 → demote to monitoring."*

* frequency **0.75 events/week < 1** ✗
* median fillable **$0.98 ≪ $200** ✗ (97.8% of firings can't absorb $200)

Both fail, independently, at every robustness setting. Keep `scan_r3.py` as
a cheap daily monitor (it is a screen for genuinely mispriced thin events
and it feeds P-1); do not allocate capital to it.

---

## 1. Cross-instrument consistency

Two mechanical relations, both derived from Kalshi's own strike metadata —
no title matching anywhere.

### (A) Monotone threshold ladders — a clean null

For `greater`/`greater_or_equal` ladders, `YES(s₂) ⊆ YES(s₁)` when
`s₁ < s₂`, so `bid(s₂) > ask(s₁) + fees` is riskless (buy YES low strike +
NO high strike, payoff ≥100¢ in every state).

**Result: 0 violations in 8,936 event-hours across 235 ladder events**
(1,401 in the universe; 235 had complete leg candles after a top-250
by-volume pull). Kalshi's ladders are internally coherent. Do not build
this.

### (B) Binary control markets vs their seat ladders

Four pairs exist in our data. The control mapping is the load-bearing part
and is read off the venue's own rules, not guessed — Kalshi settles Senate
control on *"the party identification of the President pro tempore"*, so a
50-50 Senate is organised by the Vice-President's party. This is exactly
the trap `research/ground-truth.md` §3.5 flags (the Silver-Bulletin
"9-point Senate edge" was a resolution-rule mismatch). Where a ladder
bucket straddles the majority threshold, the ladder **brackets** the binary
rather than pinning it, and the implied price becomes an interval.

| pair | mapping | hours | % outside band | median abs divergence | max |
|---|---|---|---|---|---|
| `CONTROLS-2024-R` vs `RSENATESEATS-25` | R ≥ 50 (VP tiebreak) — exact | 431 | 77.5% | 4.5¢ | 9.5¢ |
| `CONTROLH-2024-R` vs `RHOUSESEATSSMALL-25` | R ≥ 218; "218 or less" straddles ⇒ interval | 44 | 0.0% | 0.0¢ | 0.0¢ |
| `CONTROLS-2026-D` vs `KXDSENATESEATS-27` | D ≥ 51 — exact | 4,132 | 66.3% | 0.7¢ | 9.5¢ |
| `CONTROLH-2026-D` vs `KXDHOUSESEATS-27` | D ≥ 218; bracket 218-221 inside ⇒ exact | 3,559 | 37.7% | 0.0¢ | 18.9¢ |

**Coherence history:** the binary sits outside the ladder-implied band a
lot (38–78% of hours) but usually by well under 1¢ — i.e. by less than the
ladder's own aggregated spread. Persistent (≥2 h, ≥2¢) divergence episodes:
10 on senate-2024, 26 on senate-2026 (median 10 h, median peak 2.7¢), 43 on
house-2026 (median 9 h, median peak 4.5¢).

**Riskless combinations, fresh quotes only** (every leg quoted in the same
hour — forward-filled quotes are how phantom arbs are manufactured):

| pair | fresh hours | "buy binary, sell ladder" positive | max net | **median fillable $** | max fillable $ |
|---|---|---|---|---|---|
| senate-2024-R | 123 | 6 | +1.81¢ | **$0.00** | $11.50 |
| house-2024-R | 8 | 0 | — | $0.00 | $0.00 |
| senate-2026-D | 1,969 | 231 (11.7%) | +4.40¢ | **$0.00** | $1.63 |
| house-2026-D | 880 | 75 (8.5%) | +11.76¢ | **$0.00** | $3.47 |

The *converse* direction (buy the ladder, sell the binary) was **never**
positive in any pair, in any hour — the ladder's ask side is always too
expensive. And ~96% of the positive hours have zero fillable size: the
"arb" is a wide-spread illusion on thin ladder legs. This is the same
shape as R3.

**Divergence trading (harness replay, gate 4¢ / persist 2 h / exit <1¢),
vs the direction-shuffled no-signal control on the identical trigger
times:**

| pair | divergence | no-signal control | fills |
|---|---|---|---|
| senate-2024-R (settled) | **+$94.56** | +$81.46 | 9 |
| house-2024-R (settled) | −$0.80 | −$1.10 | 1 |
| senate-2026-D (unsettled) | −$3.96 | −$3.96 | 16 |
| house-2026-D (unsettled) | −$247.32 | −$155.14 | 138 |

The only positive number is **one market, nine fills**, and the no-signal
control captured 86% of it — that is a direction-agnostic "trade the 2024
Senate market" P&L, not evidence the ladder told us anything. The unsettled
2026 pairs realise losses and their open positions cannot be scored.

### Consistency verdict → **MONITORING ONLY**

Kalshi's own instruments are coherent to within their spreads. The
mechanical relations are worth running as a live incoherence alarm (they
cost nothing and would catch a genuine listing error), but there is no
capital case: fillable size ~$0 on the riskless leg and n=1 settled
observation on the directional one.

---

## 2. R7 — cross-venue divergence (Kalshi vs Polymarket)

### 2.1 Pair map: 23 pairs, 12 independent resolutions, 100% structurally verified

Title matching was never used. `research/oss-arb.md` §4.2 measured what it
does on exactly this family — "Bitcoin above $100,000" vs "…$110,000"
scores **1.000**, "Fed cuts 25 bps in September" vs "…50 bps" scores 0.817
— so every pair is matched on (underlying quantity, exact strike, exact
resolution date, resolution source in both venues' rules text), recorded
verbatim in `pairs.py`, and re-checked mechanically by `audit_pair`:

1. both records exist and carry the recorded ticker/slug;
2. the Kalshi rules text contains the exact strike token
   (`"Hike of 0bps"` / `"Cut of 25bps"`);
3. the Polymarket description contains the exact resolution token
   (`"September 2025 meeting"`) — the check that stops a September market
   being paired with an October one;
4. both venues settle on the same UTC calendar day;
5. Gamma's outcome agrees with the CLOB `tokens[].winner` (catches
   mid-dispute UMA rows);
6. the two venues' realized outcomes agree.

**All 23 pairs passed all six checks** (`reports/structure/r7_pair_audit.json`).

* **Fed decision, 11 FOMC meetings May 2025 – Jul 2026 × 2 legs = 22.**
  Kalshi `KXFEDDECISION-<YYMON>-{H0,C25}` vs Polymarket
  `fed-decision-in-<month>` legs "No change" / "25 bps decrease". Only
  these two legs are mapped: Kalshi's outer buckets are ">25 bps" while
  Polymarket's are "50+ bps", which coincide only under Polymarket's
  round-up-to-25 rule — a residual difference that is documented rather
  than papered over.
* **Nobel Peace Prize 2025 — Donald Trump = 1.** Same person, prize year,
  announcing body (Norwegian Nobel Committee), same day; both resolved NO.

**Rejected candidates, recorded so the rejection is auditable:** LA mayor
2026 (Kalshi = first round closing 2026-06-08, Polymarket = general to
2026-12-31); NYC mayor 2025 and PRES-2024 (Kalshi resolves on the winner's
*party*, Polymarket on the *person* — co-extensive in fact, not in rules);
Khamenei-out (no shared strike date); government shutdown (Kalshi "shut
down **on** Jan 31" vs Polymarket "shutdown **by** <date>" — point-in-time
vs cumulative).

### 2.2 The divergence distribution (original measurement)

**500,061 aligned Kalshi-minute / Polymarket-minute observations across the
23 verified pairs:**

| statistic | value |
|---|---|
| median \|Kalshi mid − PM mid\| | **1.0¢** |
| p95 | 3.0¢ |
| p99 | 4.0¢ |
| max | 16.5¢ |
| median Kalshi top-of-book spread | 1¢ |
| minutes with gap > 2¢ | 41,471 (8.29%) |
| minutes with gap > 3¢ | 10,366 (2.07%) |
| **minutes with gap > 4¢ (the R7 gate)** | **2,863 (0.573%)** |
| minutes with gap > 6¢ | 100 (0.020%) |
| minutes with gap > 8¢ | 10 (0.002%) |

**The two venues agree to within one Kalshi tick, essentially always.** The
documented "2–8¢ gaps on liquid pairs at high-news moments" exist only in
the 0.02% tail, and the pair-level signed means are anti-symmetric between
the two legs of a meeting (`fed-25may-h0` +2.32¢ / `fed-25may-c25` −2.27¢),
which is a **normalisation** difference (Kalshi listed 5 buckets, Polymarket
4, in 2025), not a tradeable dislocation.

### 2.3 Backtest: divergence-following vs the no-signal control

Harness replay, 1-minute clock, maker-style entries at the near touch,
exact Kalshi per-series fees, gate 4¢ / persist ≥10 min / exit <1¢ (all
SYNTHESIS's numbers, none fitted), $10 k bankroll per pair:

| run | net P&L | fills | signals | markets + | maker fill rate |
|---|---|---|---|---|---|
| **divergence, all 23 pairs** | **−$193.01** | 1,249 | 264 | 1/23 | 0.91 |
| no-signal control (direction shuffled) | −$254.45 | 1,426 | 187 | 0/23 | 0.97 |
| divergence, train (13 pairs → 2025-12-10) | −$193.01 | 1,249 | 264 | 1/13 | 0.91 |
| **divergence, test (10 pairs, 2026-01-28 →)** | **$0.00 — zero signals** | 0 | 0 | 0/10 | — |
| divergence, fees ×1.5 | −$265.62 | 1,249 | — | 0/23 | 0.91 |
| divergence, depth ×3 / ×10 | −$354.14 / −$354.74 | 976 / 782 | — | 0–1/23 | 0.90 |

All P&L comes from 5 of 23 markets, all 2025 (`25MAY-H0` −$74.13,
`25DEC-H0` −$56.01, `25DEC-C25` −$39.44, `25SEP-H0` −$26.03, `25MAY-C25`
+$2.60). **The held-out 40% produced no trades at all** — the 2026 pairs
never diverged by 4¢ for 10 minutes.

Three honesty flags on these numbers:

* **The maker fill rate is 0.90–0.97, far above SPEC §3's ">60% means the
  fill model is lying" flag.** Our 1-minute candles carry quote OHLC but
  not trade high/low, so the maker-through test is too generous. The fill
  model is therefore *optimistic* — and the strategy still loses money,
  which makes the negative result stronger, not weaker. Any *positive*
  result from this pipeline would have to be discarded.
* Median **opportunity lifetime is 33,240 s (9.2 h)** — this is not a
  latency-race signal; it would be genuinely ours to trade if it existed.
  It doesn't.
* **Polymarket fee regimes:** every 2025 pair traded under Polymarket's
  fee-free regime and the 2026 pairs under `economics_fees` (rate 0.05,
  taker-only, read from each market's own `feeSchedule`). Since R7 as
  specified trades **only the Kalshi leg** (Polymarket is the reference
  price, not a hedge — two-leg cross-venue arb was already priced out in
  `research/oss-arb.md` §5–6), Polymarket fees never enter the P&L, and
  Polymarket's `p` is a maker-side midpoint with makers paying zero, so the
  reference itself is unaffected. **Realized-fee and current-schedule P&L
  are therefore identical: −$193.01.** (If the strategy is ever extended to
  a two-leg version, the 2025 pairs' P&L would need re-charging at the
  current schedule; the per-market rates are stored in
  `data/polymarket/pm_markets.json`.)

### R7 verdict → **KILL as a strategy; keep the pair map + client**

SYNTHESIS: *"R7: divergence-following on the curated pairs fails to beat
the no-signal baseline, **or** the pair audit can't produce ≥10 truly
identical pairs → kill."*

* pair audit: **23 verified pairs / 12 independent resolutions ≥ 10** ✓ (the
  audit gate passes)
* beats the baseline: divergence −$193.01 vs control −$254.45 — nominally
  $61 "better", but **both are losses**, the entire difference sits inside
  one market (`25MAY-H0`), and the held-out split has **n = 0 trades**. That
  is not beating a baseline; that is losing slightly less. ✗

Kill it as a capital strategy. The underlying finding is the valuable part
and it is now measured rather than assumed: **Kalshi and Polymarket price
structurally identical contracts within ~1¢ of each other 95% of the time**,
which retires the cross-venue thesis for this class of market. Keep
`polymarket.py` and `pairs.py` — the client is the only Polymarket data
path in the repo, and the pair map is reusable as a live-monitoring
watchlist and as ground truth for any future cross-venue claim.

---

## 3. What this changes for the portfolio

1. **P-5 does not graduate.** None of the three sub-signals clears SPEC §7
   (net P&L > 0 on the held-out split, non-concentrated, fee-stress
   survivable). R3 → monitoring, consistency → monitoring, R7 → killed.
2. **The recurring cause is the same in all three: the fee is bigger than
   the incoherence, and the depth is smaller than the fee.** Σbid > 100¢
   happens 5.2% of the time and survives fees 0.07% of the time; the
   consistency arbs fire 8–12% of hours with $0 fillable; the venues agree
   to 1¢. Every "riskless" price-only edge in this family dies on the same
   two lines.
3. **Two corrections to propagate into SYNTHESIS/SPEC:**
   * R3's rule must be stated on the **bid** sum, not the ask sum (§0).
   * The engine's per-market strategy interface cannot express an atomic
     multi-leg order. Any future set-arb strategy needs either a
     cross-market intent path in the harness or an explicit leg-risk model;
     ours uses the latter and pays for it.
4. **Reusable assets left behind:** a complete hourly quote history for
   every settled multi-outcome Kalshi event leg (2.49 M hourly + 0.50 M
   1-minute candles over 16,612 tickers — the main pull had only 302
   tickers), a self-contained Polymarket client, and a structurally audited
   cross-venue pair map.

---

## 4. Train/test discipline

* **Nothing was tuned on the last 40% by time.** Every threshold used here
  is SYNTHESIS's own (Σ > 100¢ + fees + 1¢ buffer; 4¢ gate; ≥10 min
  persistence; exit <1¢) or a harness default (25% depth cap, ¼-Kelly,
  $10 k bankroll).
* **R3:** the scan is a measurement and runs over the whole history; the
  *strategy* replay reports train (first 60% of flagged events by close
  date) and test (last 40%) separately, one evaluation each. Staleness and
  fee-stress variants are robustness reporting, not model selection — the
  headline setting (24 h) was fixed before any result was seen, and the
  verdict is identical at 1 h, 6 h, 24 h, 72 h and ×1.5 fees.
* **R7:** split by resolution date — train = the 13 pairs resolving through
  2025-12-10, test = the 10 pairs resolving 2026-01-28 onward. One
  evaluation on test. (It produced zero trades, so there was nothing to
  overfit to even if we had wanted to.)
* **Consistency:** the control mappings come from the venues' published
  resolution rules; the only free parameter reuses R7's 4¢ gate.
* Tuning log and file map: `bot/strategies/structure/README.md`.

## 5. Reproduce

```bash
python -m bot.strategies.structure.pull_kalshi            # ~7 min, 2.3M candles
python -m bot.strategies.structure.scan_r3                # the headline
python -m bot.strategies.structure.run_r3                 # R3 strategies
python -m bot.strategies.structure.scan_consistency --pull --pull-ladders 250
python -m bot.strategies.structure.run_consistency
python -m bot.strategies.structure.pull_r7                # audit + both venues
python -m bot.strategies.structure.run_r7                 # R7 vs control
python -m pytest bot/strategies/structure -q              # 58 tests
```

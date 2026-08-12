# Hits-based selective forecasting — do big (3x+) mispricings exist where we can find them?

Run 2026-08-12. Code: `bot/forecaster/hits.py` (candidate screen + Haiku
mispricing screen + pipeline orchestration), `bot/strategies/hits/`
(trade rule, nulls, runner, tests). Machine-readable: `results.json`;
trade table fragment: `trades-table.md`; deterministic replay:
`hits-llm-cache.jsonl` (352 entries). Work files:
`data/forecaster-cache/hits/` (own cache namespace; the rung-2/3 caches
were not touched).

**Motivation.** Rung 2 (reports/forecaster/RUNG2.md) showed the pipeline is
market-parity on the average question, and worse than the market exactly
where it disagrees. FutureSearch's entire live P&L came from ~5 asymmetric
trades (research/futuresearch.md §4). This experiment tests the surviving
hypothesis: *selective* forecasting confined to markets where a 3x+ payoff
is structurally on offer, with an explicit null separating forecaster skill
from longshot-bias harvesting (futuresearch.md §4.3's warning).

## Design (parameters frozen before any forecast or engine run)

1. **Candidate screen** (price-only, `data/kalshi.db` settled binaries):
   YES price at D ≤ 15c or ≥ 85c (cheap side pays ≥ 5.7x); volume ≥ 2,000
   contracts; lifetime ≥ 14d; D = close − 10d (inside the 7–14d
   pre-resolution band ⇒ ≥ 7d to close at decision); categories
   Politics/Elections/World/Economics/Companies (no sports/crypto/
   climate/financials); one candidate per event (highest volume).
2. **Windows** (judge-cutoff rules, SPEC §1): **A** = closes
   2026-02-01..05-31, judge Sonnet 5 (Jan-2026 cutoff); **B** = closes
   2026-06-15..08-05, judge Opus 5 (May-2026 cutoff — window B starts
   06-15 so the earliest D = 06-05 postdates the judge cutoff, which the
   bare "resolves June+" rule would not guarantee). Dossier model is
   Sonnet 5 in both windows; the Haiku screen model's cutoff (Feb 2025)
   predates everything.
3. **Haiku mispricing screen** (adapted from `bot/forecaster/screen.py`,
   fail-closed): FutureSearch's insider/methodology screens plus a third
   screen — PASS only if a concrete research-resolvable path to the
   unexpected outcome can be named; REJECT structural certainty.
4. **Forecast** the screened survivors (seeded sample, budget 88) with the
   full P-4 pipeline (PIT retrieval → dossier → 2-pass judge) at D.
5. **Trade rule**: enter maker-first iff |forecast − price| ≥ 15 points AND
   payoff-if-right ≥ 3x (entry ≤ 25c) — i.e. fade the extreme, buy the
   cheap side. Hold to resolution. Exact per-series fees, maker queue
   model, inference (incl. amortized screen cost) charged to P&L.
6. **Nulls**: (i) market-price-as-forecast — same rule, must never fire;
   (ii) **always-fade** — buy the cheap side of *every* candidate with the
   same entry mechanics and minimum-conviction sizing: the FLB null.

## Funnel

| stage | window A | window B |
|---|---|---|
| settled, vol ≥ 2k, category ok | 5,151 | 3,827 |
| lifetime ≥ 14d | 2,886 | 2,180 |
| hourly-candle price available at D | 1,217 | 653 |
| **extreme at D (candidates)** | **833** | **487** |
| event-deduped | 292 | 231 |
| Haiku screen PASS | 72 | 50 |
| forecasted (seed 13) | 48 | 40 |

Screen verdicts on 523: 122 pass; rejects = structural_certainty 219,
insider 106, other 57, fin_price 10, moral_hazard 6, crypto 2, sports 1,
parse failures 0. Cost: $3.09 (Haiku, batched).

Base rates the whole exercise lives on: the cheap side of a deduped
candidate wins **26/523 = 5.0%** of the time (median cheap price ~3c) —
extremes are usually right, and (given the ~4c average price) roughly
break-even to fade before fees. There is no free FLB harvest here in either
direction; only selection can make money.

## Forecasts (n = 88, zero errors, zero parse-failure no-trades)

| | Brier(ours) | Brier(market@D) | paired ΔBrier (+ = we win) |
|---|---|---|---|
| pooled n=88 | 0.0274 | 0.0214 | −0.0060 [−0.0308, +0.0189] — parity, market ahead on point estimate |
| window A (Sonnet judge, n=48) | 0.0428 | 0.0185 | −0.0243 (se 0.0149) |
| window B (Opus judge, n=40) | 0.0088 | 0.0248 | **+0.0160** (se 0.0212) |
| fired subset (n=5, engine) | — | — | −0.0316 [−0.517, +0.454] — n far too small |

Cost: $10.72 for 88 full-pipeline forecasts ($0.090/q Sonnet-judged,
$0.161/q Opus-judged), + $3.09 screen ⇒ $14.24 charged into the engine
(amortized 4c/market). CostLedger from exact usage blocks, standard API
prices.

## The trade rule at D — the forecast-level answer

Applying the frozen rule analytically at the decision anchor (taker at the
D price, equal-$ stakes):

| ticker | w | mkt@D | our p | side bought | entry | payoff | result | outcome |
|---|---|---|---|---|---|---|---|---|
| KXCLAYTONCONF-26JUN11-JUN27 | B | 93.5c | 18% | NO | 6.5c | 14.4x | no | **HIT +14.4x** |
| KXFARRERBYELECTION-26DEC31-LP | A | 0.5c | 48% | YES | 0.5c | 199x | no | miss −1x |
| KXTXRSEN2ND-26MAR03-2-JCOR | A | 0.5c | 83% | YES | 0.5c | 199x | no | miss −1x |

**Fires 3 of 88; 1 hit; equal-$ P&L +12.4 units per 1 unit/trade
(+4.1x avg per trade).** The same equal-$ accounting for the nulls:
market-as-forecast fires 0 times (by construction); always-fade loses
−0.73/unit avg on the same 88 markets and −0.17/unit avg on all 523
candidates. The selective rule's economics are the opposite sign of the
FLB null's.

Thesis per trade (from the judges' documented reasoning; full premortems in
`results.json`):

- **HIT — Clayton confirmed as DNI before Jun 27 (94c YES, we said 18%).**
  Both passes independently found the market was pricing "Clayton will be
  DNI" while the contract required *Senate confirmation within 10 days* —
  no SSCI hearing was on the calendar in the pre-D evidence; base rates for
  cabinet-level confirmation inside 10 days from no-hearing are low. Screen
  path: "Senate committee votes; if opposition emerges, confirmation
  fails." Market collapsed 92c→25c within ~5 hours after D; resolved NO.
  The forecast was right for the stated reason: a timing/definition wedge,
  exactly the FutureSearch hit profile.
- **miss — Farrer by-election Liberal win (0.5c YES, we said 48%).** The
  causal-chain pass (p=0.78) bought a single "One Nation surging" headline;
  the outside-view pass said 20%; passes divergent (flagged). Market right.
- **miss — Cornyn finishes 2nd in TX primary (0.5c YES, we said 83%).**
  Dossier polls had Paxton 1st / Cornyn 2nd; Cornyn in fact finished 1st
  (its sibling market KXTXSENRPRIMARYMOV-…-JCOR-P1 sat at 99c and resolved
  YES). The 0.5c was informed; our dossier was stale. Market right.

The two misses cost 1 unit each at ~200x-payoff prices; the hit paid 14.4x.
This is the hits-based shape working as intended — **but n(hits) = 1**, and
SPEC §7's hits-based clause requires ≥ 3 independent documented hits.
One 14x hit cannot be distinguished from luck.

## Engine runs (maker-first, exact fees, maker queue, daily decision ticks)

| run | intents | markets traded | fills | net P&L | after inference |
|---|---|---|---|---|---|
| **hits strategy** | 5 | 2 | 81 contracts | −$2.82 | **−$17.06** |
| hits, fees ×1.5 | 5 | 2 | 81 | −$2.82 | −$17.06 |
| hits, inference ×2 | 5 | 2 | 81 | −$2.82 | −$31.30 |
| null: market-as-forecast | **0** | 0 | 0 | $0.00 | $0.00 |
| null: always-fade (523 candidates) | 522 | 328 | 271k contracts | **−$3,552.65** | −$3,552.65 |
| null: always-fade (the 88 forecasted) | 88 | 43 | 217 fills | **−$1,917.93** | −$1,917.93 |

- The engine fired 5 (not 3) because the implementation retries the rule at
  each daily tick until it quotes: two extra trades (KXDENMARKGAIN,
  KXPERUPRES1RMOV-P2) fired at *drifted* prices days after D, and the
  Clayton trade fired on the **wrong side** on Jun 25 — after the market
  had already collapsed through our forecast (18%) to 0.5c, the stale
  forecast now read "YES underpriced". All 5 lost. **Lesson recorded: a
  forecast must carry a freshness window; a 15-point disagreement with a
  price that has moved *through* your number is not your trade.** (Not
  patched post-hoc — the frozen-rule engine result stands as run.)
- Monetization reality: the Clayton mispricing lived **< 5 hours** past our
  anchor (D 03:59 UTC, crash ~08:00–09:00, first daily tick after D 09:00).
  A maker-first daily-cadence strategy captured none of it. Opportunity
  lifetime for fired trades: median 18h, p25 10h.
- Fill honesty: hits maker fill rate 3.7% of ordered contracts (resting
  1c bids on dead longshots almost never fill). The always-fade-523 run's
  maker fill rate is 64% — **above the 60% sanity flag, so its P&L
  magnitude must not be trusted**; its *sign* is safe (every cheap-side
  fill on these candidates has negative EV — more fills, more loss), and
  the analytic at-D numbers above corroborate the direction with no fill
  model at all.
- Market-as-forecast fired 0 trades, as designed — all firing comes from
  forecast-vs-price disagreement, not entry mechanics.

## The key honesty check (rung-2 kill-criterion (b) shape)

Rung 2 said: where we disagree with the market, the market is right more
often. Did filtering to thin/asymmetric/research-heavy candidates flip
that?

**Mostly no, with one bright spot.** Pooled over the 88 selected extremes
the market is still ahead (ΔBrier −0.0060, NS). On the fired subset the
point estimate is still negative (−0.032, n=5, CI ±0.5 — uninformative).
Trade-count evidence: the rule fired 3 times at D and was right about the
direction of mispricing once (33%); the two confident misses were at
199x-priced markets where the market's 0.5c was better information than our
dossier. What *did* flip is the economics: at asymmetric prices a 33% hit
rate is hugely profitable (+4.1x/trade equal-$) where rung-2's mid-range
disagreements were not. And window B — the only window judged by a model
with zero training overlap concern *and* fresher retrieval relevance —
shows the pipeline ahead of the market (+0.016, NS).

Separation from FLB: the always-fade null loses on every accounting
(engine −$3.5k/−$1.9k; analytic −0.17 to −0.73 per unit). The selective
strategy's positive analytic P&L is therefore **not** longshot-bias
harvesting wearing a hat — there is no longshot premium to harvest in this
universe (cheap sides win 5.0%, priced ~4c). Whatever the +4.1x/trade is,
it came from the forecast layer, not the candidate class.

Screen post-mortem (pre-registered role: concentrate mispricing): it
**failed at recall** — upsets among screen-passers 4/122 (3.3%) vs 26/523
(5.0%) baseline; it rejected 22 of the 26 real upsets (9 insider, 8
structural_certainty, 5 other). Its value was cost control only. The
forecast gate did the real filtering: it correctly declined to fire on
85/88 correctly-priced extremes and caught 1 of the 2 upsets present in its
sample (missed KXDENMARKGAIN: forecast 10% vs market 12.5%, resolved YES).

## Leak discipline

- Retrieval ledger `data/forecaster-cache/hits/ledger.jsonl`: 1,022 rows —
  930 PASS, 56 QUARANTINE (never reached a model), 36 fetch failures.
  Blocking audit (`retrieval.audit_ledger`): **0 violations, 0
  date-inconsistency flags.** Both date gates active (client `seendate < D`
  + publisher `datePublished`/`dateModified`); no live search (subagent
  tools disabled).
- Judge cutoffs: Sonnet 5 (Jan-2026) for window A (closes Feb–May);
  Opus 5 (May-2026) for window B with min D = 2026-06-05 > cutoff. Settled
  results never appear in any prompt (screen prompt carries title/price/
  days-to-close only; `Candidate.result` is excluded by construction —
  tested).
- Every LLM response cached under `data/forecaster-cache/hits/llm/`; the
  352 entries behind the reported numbers are exported to
  `reports/hits/hits-llm-cache.jsonl` for $0 deterministic replay.

## Caveats

1. **n(hits) = 1.** The headline +14.4x is one trade. The SPEC hits-based
   graduation bar (≥3 independent hits) is not met; this experiment is
   evidence of *mechanism*, not of edge. ~30 more upset-containing samples
   (≈ 1,300 more forecasts at the observed 2.3% upset density) would be
   needed to see 3 expected hits at the observed catch rate.
2. **Candidate set conditioned on candle coverage** (42%/30% of otherwise
   eligible markets have a price at D). Coverage is an artifact of which
   series earlier pull jobs fetched; measured outcome skew between covered
   and uncovered is small (35.1% vs 34.0% YES in A; 33.2% vs 28.6% in B)
   but not zero.
3. **The one hit was barely monetizable.** The mispricing decayed in
   hours; maker-first entry at extremes almost never fills (3.7%); taker
   entry at D would have paid, but only a strategy that trades *immediately
   at forecast time* — with a freshness gate — could have caught it. That
   variant is specified by this report's lesson, deliberately not run
   post-hoc on the same data.
4. Window A's negative ΔBrier (−0.0243, se 0.0149) echoes rung-2's warning:
   near an extreme, our dossier is more often stale than the price is
   wrong. The costly failure mode is *confident disagreement with an
   informed 0.5c* (both misses), which no calibration rail currently
   blocks; a "who knows more, me or this price?" check belongs in the judge
   prompt for any follow-up.
5. Always-fade engine magnitudes carry a flagged fill model (64% maker
   fill); use the analytic at-D numbers for the null's size, the engine run
   for its sign.

## Verdict

Does selective forecasting find real 3x+ mispricings, or is it FLB in
disguise? **It is not FLB in disguise** — the always-fade null loses money
in this universe on every accounting, so nothing here is harvested from the
candidate class itself. **And the pipeline did find exactly the kind of
mispricing the thesis predicts** — a 94c market pricing the wrong
proposition (identity vs. 10-day confirmation timing), identified for the
right documented reason, worth 14.4x. But one hit in 88 forecasts, a pooled
Brier still at market parity, a screen that filtered out most of the real
upsets, and a demonstrated inability to monetize the hit through
maker-first execution mean the honest summary is: **mechanism demonstrated,
edge unproven, execution unsolved.** The follow-up that would settle it is
specified above (bigger sample over more windows + forecast-freshness
gate + taker entry at D); it should be run before any capital, paper or
real, chases this.

---

## v2 anchoring — event-date decision anchors (post-hoc infrastructure fix, 2026-08-12)

**This section documents an infrastructure fix, not a performance claim.**
No new forecasts were run, no held-out evaluation was performed, and every
frozen number above stands exactly as run. What changed is the screen's
*plumbing*, motivated by two independent post-freeze findings:

1. **Wisconsin (reports/case-wisconsin/ANALYSIS.md §3):** the frozen anchor
   D = `close_time` − 10d made the screen structurally blind to the
   KXGOVWINOMD-26 nominee markets — pre-event their `close_time` was
   2026-11-03 (the general-election date), so D = 2026-10-24 never landed
   before the 2026-08-11 primary and a ~20x pre-event fade (Hong NO at
   ~4–5c) was invisible by construction.
2. **F3 close-anchor audit
   (reports/tournament/round4/ia_econ_only/f3_close_anchor_audit.json):**
   the same shape in econ — `can_close_early` markets whose data-determined
   outcome fixes long before the listed close.

### Design (`bot/forecaster/event_anchor.py`)

The decision-relevant **event date** is derived from market metadata, with
provenance stored, in strict priority order:

1. `explicit_text` — dates parsed from `rules_primary`/title/subtitle
   (month-name, ISO, m/d/y, day-month-year; yearless dates resolve to the
   close year; "Jun 2026" month-year forms are guarded against parsing as
   a day). Among parsed dates the latest one inside the market's life
   (open .. close+1d] is taken.
2. `election_calendar` — for election markets with no explicit date (the
   Wisconsin shape), the public **statutory** 2026 primary calendar
   (partial table; runoffs/specials excluded; unknown states fall through)
   plus the general-election date. These dates are ex-ante public-calendar
   knowledge fixed by statute.
3. `expected_expiration` — the market's own `expected_expiration_time`
   from the raw JSON, when it precedes `close_time` (an upper bound on the
   event date).
4. `activity` — volume-concentration signature from hourly candles.
   **Outcome-adjacent** (trading concentrates when the event happens): it
   is flagged `outcome_adjacent=True`, `anchored_decision_ts` refuses to
   anchor on it, and the screen never enables it — live diagnostics only.

Anchor rule: D = event − 10d whenever a (non-outcome-adjacent) event date
exists and precedes `close_time` by more than 10 days; otherwise the
frozen D = close − 10d, unchanged. `enumerate_candidates(anchoring="event")`
turns it on; the default `anchoring="close"` reproduces the frozen screen
byte-for-byte (verified: 487/487 window-B candidates, zero drift).

**Freshness gate** (the lesson recorded in the engine section, now
implemented): a forecast anchored at D is *stale* when |now − D| > f hours
(f = 24 by default — a round pre-specified default, not tuned on any
result), and entry re-validates the price gap at execution: it must still
clear the 15-point gate **and point the same way it did at D** — a price
that moved *through* the forecast (the Clayton wrong-side entry) is
refused. Wired into `HitsStrategy(freshness_h=...)`, **default off**, so
the frozen engine runs reproduce unchanged; unit tests cover the exact
Clayton pattern.

### Parse rate (measured, deterministic; also asserted in tests)

- Deterministic 1-in-8 sample of the settled Politics/Elections/World/
  Economics/Companies universe (vol ≥ 2k, closes 2026-02-01..08-05,
  n = 1,320): **event date found for 62.4%** (`explicit_text` 586,
  `election_calendar` 190, `expected_expiration` 48, none 496); the anchor
  actually *moves* (event precedes close by > 10d) for 103 (7.8%).
- Full window-B universe (n = 3,827): parse rate 61.2%
  (`explicit_text` 1,853, `election_calendar` 490).

### Wisconsin regression test (bot/forecaster/test_event_anchor.py)

On the real KXGOVWINOMD-26 rows with the documented pre-event close
(2026-11-03): every market derives event date **2026-08-11**
(`election_calendar: primary_2026:wisconsin`) ⇒ **D = 2026-08-01,
ten days pre-primary**, where the frozen anchor put D = 2026-10-24
(post-primary, blind). An end-to-end fixture test reconstructs the
pre-event snapshot (real metadata + real hourly candles, pre-event close)
and proves `enumerate_candidates` returns FHON as a candidate at
D = Aug 1 (95c, cheap NO ≈ 5c) under `anchoring="event"` while returning
**nothing** under the frozen `anchoring="close"`.

### Candidate-set delta, window B (screen only — no forecasts, no P&L)

| | frozen (close-anchored) | v2 (event-anchored) |
|---|---|---|
| lifetime-ok universe | 2,180 | 2,180 |
| anchors moved | — | 333 (15.3%) |
| price available at D | 653 | 585 |
| **extreme at D (candidates)** | **487** | **431** |
| event-deduped | 231 | 196 |

- **All 333 moved anchors had a frozen D *after* the derived event date**
  (this is by construction: the anchor only moves when the event precedes
  close by > 10d). The frozen screen was, for these markets, screening
  *post-event* prices — mostly election complexes whose `close_time` is a
  certification date weeks after the vote (CA primary complex: frozen
  D ≈ Jul 1 for a Jun 2 primary).
- **57 candidates disappear** (all were post-event extremes at the frozen
  D — e.g. KXVOTEPRIMARY/KXCAPRIMARY/KXPRIMARYMOV series): at the
  corrected pre-event D, 55 have no hourly candle (the store's coverage
  concentrates in the last ~15 days of life — caveat 2 gets *worse* at
  earlier anchors) and 2 are genuinely not extreme yet. **1 appears**
  (KXPRIMARYMOV-SENATEMED-GPLA-P35). Net: 487 → 431.
- **KXGOVWINOMD:** still outside frozen window B under any anchoring —
  window membership is close-keyed and B was frozen to closes ≤ 08-05
  (realized WI close 08-12; pre-event 11-03). In a report-only extended
  window B′ (closes 06-15..08-12), all 11 KXGOVWINOMD markets enter the
  candidate set with D = 2026-08-02, FHON at 95.2c ⇒ cheap NO ≈ 4.8c
  (~20x if right). Note the honest nuance: in the *current post-settlement
  DB* that D comes from the realized early close (now only ~1d after the
  primary), so close-anchoring happens to coincide with event-anchoring;
  it is the **live / pre-event** case — where close_time still reads
  2026-11-03 — that the event anchor fixes, as the fixture test proves.

### Caveats

1. Post-hoc infrastructure fix: parameters of the *fix* (source priority,
   the k = 10d reuse, f = 24h) were chosen for consistency with the frozen
   design, not tuned on outcomes; still, any future run using v2 anchoring
   is a **new experiment** and must not be compared against the frozen
   numbers as if same-protocol.
2. The candidate-set delta above describes screen *composition* only.
   Nothing here says the 431 v2 candidates forecast better or trade
   better — that is exactly the follow-up experiment this report already
   pre-specified, and it remains unrun.
3. The statutory election calendar is partial (2026 only, well-known
   statutory dates only); a wrong or missing entry degrades gracefully to
   the next source. `expected_expiration_time` in the current DB was
   re-fetched post-settlement for some markets and may differ from its
   pre-event value; it sits last in priority partly for this reason.
4. Candle coverage binds harder at pre-event anchors (653 → 585 priced
   markets in B): early-determination markets need earlier-life candle
   pulls before v2 anchoring can screen them at scale.

# ROUND 3 — Adversarial lookahead/leakage audit of the integrated champion

*Verifier: independent Round-3 audit agent, 2026-08-12. Target: `ia_econ_only`
(R5Endgame 6h, band 90-98, vol_frac 0.25, bias_frac 0.013, cancel/replace,
categories=("Economics",)) + runner-up `ib_swing_filter`, per
reports/TOURNAMENT_PROTOCOL.md Round 3. Charter: prove or refute that any
decision input — universe construction, parameters, filters, episode
detection — uses information from at or after decision time t, from the
held-out window, or from settlement outcomes. Train artifacts only; the
held-out 40% was not touched by this audit.*

## Verdict summary

**The replay engine and the trade-level dataflow are clean** — attacked from
source and verified by exact reproduction plus five end-to-end hand-traces
against raw DB rows (§2, §3). **But two leakage findings are CONFIRMED at the
pipeline level**, both above the engine:

| # | Finding | Class | Status | Materiality |
|---|---|---|---|---|
| F1 | I-a category filter is derived from train settlement outcomes and scored on the same train sample | settlement-outcome circularity | **CONFIRMED** | The +$144.21 / +4.12%/ep / zero-loss headline is an in-sample selection maximum, not an out-of-sample estimate. Effectively 100% of the champion's reported edge quality rides on it; one 95c-class failure (~-$130) erases ~90% of net. |
| F2 | The tournament held-out window (settlements 2024-08-02 → 2026-08-11) is partially contaminated: the champion family (R5) was selected, its zero-loss Economics behavior observed, and the maker-queue depth_windows=1.0 calibrated, all on the 2026-05-25..2026-08-11 pull — which sits INSIDE the tournament held-out window | held-out contamination | **CONFIRMED** | Does not touch train P&L; degrades Round 4. ~95 days of the held-out span (including 132 repeatedly-backtested markets, 7 R5-6h Economics episodes with known-positive outcomes) were seen before the protocol froze. Round-4 numbers on that sub-window are partially pre-known. |
| F3 | `MarketInfo.close_ts` comes from the DB's post-hoc `close_time`, which for `can_close_early` markets embeds early-determination closes; R5's "final 6h" window is anchored on it | formal lookahead channel | **PLAUSIBLE in general, empirically benign for all 29 champion trades** | 15/29 episodes (FED monthlies, $92.79 of $144.21) closed weeks before `expected_expiration_time` — but each close is the market's rule-named public event (FOMC decision date printed in `rules_primary`; CPI release; EIA report), knowable ex ante. Zero champion trades anchor on an outcome-driven close. Unpatched, this channel is live for Round 4 categories where early closes are not calendar-scheduled. |
| F4 | Universe volume filter (lifetime volume >= 1000) is future information at decision time | future-conditioned selection | **CONFIRMED in form, REFUTED in effect for the champion** | All 386 settled train Economics markets with candles have volume >= 1000 — the filter excluded zero Economics markets. All 29 traded markets had 17k-1.3M contracts already printed before entry (min 17,455). Sub-1000 exclusions are 6,638 weather + 1 World market, none reachable by the Economics-only champion. |
| F5 | Universe conditions on settlement (settled-only; train membership = settled <= split_ts) | survivorship channels | **REFUTED (empty) on this boundary** | Zero otherwise-eligible markets closed pre-split but settled post-split (no delayed-settlement/dispute survivorship). Zero otherwise-eligible non-settled (void/zombie) markets closed in the train window with volume >= 1000 and candles. |
| F6 | Fee schedule is a present-day snapshot applied to 2021-2024 (`FeeSchedule.load_default()`, disclosed in ANALYSIS.md) | anachronism, not lookahead | noted | Champion is 100% maker; it is CHARGED $2.34 maker fees on KXFED/KXCPI under today's `quadratic_with_maker_fees`, which mostly postdates the trades — conservative (overcharges). No champion series has today-zero/historically-positive fees. |

**Recommended disposition** (protocol Round-3 clause: any CONFIRMED violation
kills or reverts): the engine needs no revert. F1 cannot "revert" the variant
(reverting to unfiltered r5_endgame_6h = -$679, already FLAGGED); it means
the train number must be treated as a biased upper bound and the variant
survives only as the *hypothesis* Round 4 exists to test — which the Round-2
verdict's caveats already half-say. F2 requires an amendment BEFORE Round 4:
report the held-out run with the 2026-05-25..2026-08-11 sub-window (at
minimum the 132 flb-pull train tickers) broken out separately, and treat only
the never-seen 2024-08..2026-05 span as genuinely out-of-sample. Without that
breakout, a Round-4 "pass" is not a clean pass, because "held-out untouched"
(UNIVERSE.md, ROUND1.md, ROUND2.md) is only true of engine runs, not of
information flow.

---

## 1. What was attacked

- `bot/backtest/engine.py` (view slicing, decision clock, fill replay,
  cancel/replace, settlement), `dataport.py` (DB adapter, unit conversion,
  settlement vocabulary), `fills.py` (maker qualification, queue model),
  `risk.py` (clamp inputs), `types.py` (MarketView surface).
- `bot/strategies/flb.py` R5Endgame decision inputs; `tournament/wrappers.py`
  I-b filter; `tournament/run_round2.py` (universe pinning, variant
  factories, verdict rule).
- Universe construction: `bot/backtest/universe.py` + the frozen
  `reports/tournament/round1/universe.json` and `reports/tournament/UNIVERSE.md`.
- Parameter/filter provenance: `reports/flb/ANALYSIS.md` (frozen params),
  `reports/flb/QUEUE_IMPACT.md` (queue calibration), ROUND1/ROUND2 tables.
- Raw `data/kalshi.db` rows (markets incl. `raw_json.expected_expiration_time`,
  `rules_primary`, candlesticks, trades) for the empirical checks.

## 2. Engine dataflow: attacked and cleared

Checked at source, each a plausible lookahead vector:

1. **View slicing.** `_build_view` gives candles with `end_ts <= t` (a candle
   ending exactly at t is fully elapsed) and trades strictly `< t`. Decision
   times are candle end times built from the same series. No path hands a
   strategy any record at > t; `MarketInfo` carries no result/settlement
   field. `test_engine.py` asserts the property; verified independently here.
2. **Self-fill at t impossible.** Maker fills require `trade.ts >
   order.maker_from_ts` (strict); taker windows are `(placed_ts, t+period]`.
   A print at exactly t can inform the decision (via the candle close) but
   can never fill the order placed at t.
3. **Queue/depth inputs are past-only.** `queue_ahead` = ceil(1.0 x mean of
   the last 3 candle volumes ending <= t); trailing size volume = the candle
   ending at t. Verified numerically in all five hand-traces.
4. **Risk clamp inputs**: p_hat, book estimate at t, past window volume,
   current exposure. Nothing future.
5. **Settlement flow**: payouts occur at `settled_ts` events; `effective_end_ts
   = min(close, settled_ts)` truncates the decision clock — uses a future
   timestamp only to STOP trading (exchange-halt equivalent), never to trade.
6. **Diagnostics that do use the future tape** (`opportunity_lifetime_s`) are
   computed engine-side after order registration and are not exposed to the
   strategy object.
7. **Conversion conservatism** (dataport): bid floors / ask ceils, maker
   candle rule strictly-through, volumes floored — all directions push
   against the strategy.

Also re-ran the exact champion config (pinned universe, exact FeeSchedule,
default MakerQueueConfig) restricted to the 386 Economics train tickers
(non-Economics markets are structurally inert under the category filter):
**net +14,421c, fees 234c, 4,132 contracts, 65 orders — byte-identical to
`reports/tournament/round2/ia_econ_only/report.json`.** Determinism and
reproducibility hold.

## 3. Five champion trades traced end-to-end by hand

Method: from the reproduced event log, every `order_placed` / `fill` /
`settlement` event was re-derived independently from raw `data/kalshi.db`
rows: placement must be an hourly candle end inside `(close-6h, close)`; the
limit must equal the re-derived point-in-time best bid of the joined side
(floor/ceil rules); the order must be maker-only in band 90-98; requested
size must equal `int(0.25 x volume of the candle ending at t)`;
`queue_ahead` must equal `ceil(mean of last-3 candle volumes)`; every fill
must match a raw trade print strictly after placement at-or-through the
limit, with count == min(remaining, floor(0.25 x (print - remaining
queue))), walking prints in (ts, rowid) order; totals must reproduce
episodes.csv; settlement must match `markets.result`.

| trade | placement (UTC) | window check | book@t (derived) | fills | settlement |
|---|---|---|---|---|---|
| FED-22SEP-T3.00 yes@98 x373 | 2022-09-21 13:00 (4.9h to close) | candle end, in final 6h | bid/ask 98/99, join 98, maker | 7 prints 17:13-17:34, per-print queue sim reproduces engine sequence 111/22/125/35/25/5/50 exactly | yes, +746c gross, 13c fee (= ceil of 0.0175 x 373 x .98 x .02) |
| CPI-21DEC-T0.8 no@92 x17 | 2022-01-12 00:00 (5.0h) | ok | yes-space 7/8, join ask 8 (sell-YES@8 = buy-NO@92), maker | 3/10/4 across 00:12-03:43, all post-placement, cap respected | no, +136c, 3c fee |
| OIL-22JUL11-N100 no@95 x252 | 3 replaced orders 18:00/19:00/20:00 | ok; replaces rejoin queue at back | join 10 -> 7 -> 5 (yes-space), maker each time | 115/125/12 at 20:52:12 vs 2,755-contract qualifying burst, queue 1,745 consumed first | no, +1,260c, 0 fee (KXOIL quadratic: no maker fee) |
| FED-24JUN-T5.25 yes@98 x505 | 2024-06-12 13:00 (4.9h) | ok | bid/ask 98/100, join 98, maker | 97/62/138/13/83/112 across 14:08-15:13, per-print queue math exact | yes, +1,010c, 18c fee |
| FEDDECISION-23MAY-H25 yes@93 x92 | 2023-05-03 13:00 (4.9h) | ok | bid/ask 93/94, join 93, maker | 10 fills 13:58-14:15; same-second print sequence 8/1/6/23/1/1 reproduced per-print | yes, +644c, 11c fee |

All five: no fill precedes its order; no order at/after close; every fill
maps to a real print at-or-through the limit; queue-and-25%-cap arithmetic
reproduces the engine per print; episode totals and results match the CSV
and the DB. (Initial per-timestamp aggregation flagged two apparent cap
breaches; per-print re-simulation in (ts, rowid) order resolved both as
aggregation artifacts of same-second prints, matching the engine exactly.)

## 4. F1 — the category filter is settlement-outcome-derived, scored in-sample (CONFIRMED)

The champion IS the filter: `categories=("Economics",)`. That set was chosen
(ROUND2.md, notes) as "categories above breakeven in EVERY price bin in
EVERY window" **in the Round-1 win-rate-vs-price tables** — tables computed
from the settlement outcomes of the very train run the filter was then
scored on. Win rate 1.000 in every Economics bin is the *selection
criterion*, so the zero-loss, +4.12%/ep result is guaranteed by
construction on this sample; the +$144.21 is max-over-categories of an
in-sample statistic, not an unbiased edge estimate.

Aggravating detail: the protocol's own pre-registered source for I-a was
"reports/flb/ANALYSIS.md". In ANALYSIS.md's r5_endgame_6h table **every
category clears breakeven in every bin** (13 episodes, 100% positive, 2026
pull) — applied as written, I-a would have dropped nothing. The
Economics-only set exists only under the Round-1-tables reading. The reading
was registered in ROUND1.md interpretation #3 before Round 2 ran (verified),
so this is disclosed judgment, not silent tuning — but it moves the
derivation fully in-sample.

Consequences the Round-2 verdict already gestures at, stated sharply:
n=29 with zero losses means the clustered SE (+/-0.41%) measures dispersion
of winners only; the failure tail is unobserved; a single 95c-class failure
is about -$130 against +$144 net. FED-monthly episodes alone are $92.79 of
the $144.21. The train number cannot support a "keep" beyond "hypothesis
for Round 4."

## 5. F2 — the held-out window is not pristine (CONFIRMED)

Timeline of information flow:

- The FLB family was developed, parameters frozen, and its train verdict
  ("PASS +5.4-6.3%/ep" — the reason R5 entered the tournament as presumptive
  champion) produced on the 2026 pull: settlements 2026-05-25 .. 2026-08-11,
  split 2026-07-11 (ANALYSIS.md header).
- The maker-queue parameter depth_windows=1.0 was calibrated (on fill rate,
  not P&L — but still on data) against the frozen FLB R2 universe from the
  same 2026 pull (fills.py docstring, QUEUE_IMPACT.md).
- The tournament then redefined the split over full history: held-out =
  settlements after 2024-08-02. The ENTIRE 2026 pull — including its
  132-market train half that was backtested repeatedly and drove champion
  selection — lies inside the new held-out window.

So Round 4's window contains ~95 days of markets whose R5 outcomes are
already known to be positive (7 Economics R5-6h episodes among them) and
whose results caused R5 to be the champion. This is selection contamination
of the held-out evaluation, and the tournament documents' "held-out 40%
untouched/never touched" claims (UNIVERSE.md, ROUND1.md, ROUND2.md) are
true only in the narrow sense that no engine run consumed test tickers.

Required remedy (pre-commit before Round 4, consistent with SPEC §8):
Round-4 reports must break out (a) markets settling 2026-05-25..2026-08-11
(and at minimum the 132 flb-train tickers) from (b) the never-seen
2024-08-02..2026-05-25 span, and the pass/fail reading should lean on (b).
The 2025 trades backfill (closed 2024-11..2026-01, still appending) also
sits in this window — Round 4 should pin a DB snapshot hash.

## 6. F3 — close_ts anchoring (PLAUSIBLE channel, benign for these 29 trades)

`close_ts` is parsed from `markets.close_time` in a post-settlement DB
snapshot. Kalshi updates `close_time` when a market closes early
(`can_close_early` = 1 on 28/29 champion markets), so in general "final 6h
before close_ts" can anchor on a time that was only knowable ex post — a
genuine lookahead channel for window-triggered strategies.

Empirical check on every champion trade: 15 FED monthlies closed 2-12 days
before `expected_expiration_time` — but each `rules_primary` names the
specific FOMC meeting date ("...following the Federal Reserve's June 15,
2022 meeting..."), i.e. the close was calendar-scheduled at listing;
FEDDECISION closes sit 10 minutes before same-day expected expiration
(scheduled); CPI closes the night before the release; GASM/OIL close at
20:59Z (4:59pm ET) on the EIA report date. No champion trade anchors on an
outcome-driven close. Entries are 1-5h pre-announcement and positions are
held THROUGH the announcement (e.g. FED-22SEP entered 13:00-17:34Z, FOMC
18:00Z, settled 02:38Z next day) — the event risk is real, so the P&L is
not manufactured by the anchor. For Round 4 (categories with genuinely
unscheduled early closes) this channel must be re-checked or the window
re-anchored on ex-ante fields.

## 7. F4/F5 — universe filters: future-conditioned in form, empty in effect here

- Volume >= 1000 is lifetime volume (future info at early decision points).
  Measured: every one of the 386 settled train Economics markets with
  candles clears 1000; all 29 traded markets had 17k+ contracts printed
  BEFORE first entry (min 17,455; median ~186k). The filter removed 6,638
  weather + 1 World market — unreachable by the Economics-only champion.
  For the runner-up ib_swing_filter (weather-heavy), this filter IS a live
  future-information channel and its train number should carry that caveat.
- Settled-only + settle<=split membership: zero otherwise-eligible markets
  closed pre-split but settled post-split; zero non-settled (void/zombie)
  markets closed in the train window with volume >= 1000 and candles. No
  survivorship on this boundary.
- Candle-presence filter is a local-dataset property (pull coverage), not
  outcome-conditioned; noted, not a leak.
- All 29 episode tickers verified in the pinned universe.json train list
  with settlement <= split_ts (largest margin case FEDDECISION-24JUL settled
  2024-07-31, 2 days inside).

## 8. Runner-up ib_swing_filter (brief)

The I-b swing detector was re-read at source: census-identical scan over the
strategy's own MarketView hourly candles, reference window [tau-T, tau),
block window (t-24h, t]; all inputs end at candle closes <= t. Constants
(20c/24h/24h) trace to the census config frozen before the census ran. No
lookahead found in the filter itself. Its exposure to F4 (weather volume
filter) and F3 (weather close schedules are calendar-driven; benign) noted
above; it remains net-negative on train and is not a graduation candidate.

## 9. Score for the protocol's Round-3 gate

- Lookahead in decision inputs (engine/strategy/wrappers): **REFUTED** —
  none found; verified by construction, by tests, and by five independent
  hand-traces that reproduce the engine exactly from raw data.
- Universe construction using future information: **CONFIRMED in form,
  measured immaterial for the champion's category and trades** (F4/F5).
- Decision inputs using settlement outcomes: **CONFIRMED — the I-a category
  set itself** (F1). Pre-registered mechanism, in-sample derivation;
  reported train P&L is a selection-biased upper bound.
- Use of the held-out window: **CONFIRMED — upstream of the tournament**
  (F2): champion-family selection and queue calibration consumed data that
  now sits in the held-out window; Round 4 needs the sub-window breakout
  remedy to remain meaningful.

Bottom line: nothing here shows the +$144.21 was *mechanically* fabricated —
the trades are real prints, honestly queued, genuinely at risk through
their events. What the audit kills is the *evidential weight* of the number:
it is an in-sample selection maximum measured on 29 loss-free episodes, and
the "single held-out confirmation" the protocol relies on is itself
partially pre-seen. Round 4 can still be informative if and only if the F2
breakout is pre-committed.

# P-3 event-night panic-wick resting ladders — episode taxonomy + train backtest

**2026-08-12 (rev 2 — re-run on the extended tape: tick trades to 2021-06-30 for
12,970 additional markets, full 2024-election-night 1-min candles, pull_log 24-25;
census, fills and P&L were re-generated and are IDENTICAL to rev 1 — see §5.1 for
why the extra 3.5 years add zero qualifying episodes).** Code: `bot/strategies/panic/` (episodes.py discovery, strategy.py R4/R4b,
provider.py, run_backtest.py). Engine: `bot/backtest/` with the exact per-series fee
schedule, maker-queue fill model and tick quantization. Outputs:
`reports/panic/{episodes.json, train_results.json, michigan_replay.json}`.
Brief: `research/SYNTHESIS.md` §1 P-3; verified Michigan timeline: `docs/viability.md` §2.

## 0. Discipline

- **Parameters frozen before results** (full log: `strategy.py` PARAMS_RATIONALE).
  Every number (rungs 85/80/75c, qualification >=95c 24h avg, exit at pre-2c, NO entry
  <=3c, NO exit ladder 15/20/25c, 1% stake, 10% assumed arm rate) comes verbatim from
  SYNTHESIS P-3 / viability §2, both written before this code existed. Nothing was tuned
  on backtest output. Caveat stated up front: the rung prices themselves were chosen in
  SYNTHESIS *knowing* Michigan's 74c trough, so the Michigan replay is in-sample by
  construction; the train set below measures generalization.
- **Time split by episode window start:** train = first 60% (2024-11-05 -> 2026-05-13,
  81 episodes), held-out = last 40% (2026-05-19 -> 2026-08-04, 55 episodes), untouched
  except the single sanctioned Michigan case study (§4), which counts toward nothing.
- **Point-in-time:** strategies re-derive the >=95c qualification from their own
  `MarketView` (data strictly < t); the episode file supplies only window locations.
  Known residual: window *location* uses realized volume timing as a stand-in for the
  public event calendar (no machine-readable historical calendar exists); admission
  never uses prices, so the dip taxonomy is not selection-biased on price action.
- **Amendment log:** episodes.py's WIP anchor ("max-volume hour in the final 10 days of
  life") silently dropped the whole 2024 general night — PRES-2024-* settled Jan 20 and
  SENATE*-24 Jan 3, months after election night — and picked up their settlement-week
  churn instead. Fixed 2026-08-12 (anchor = max-volume hour over covered life, per-hour
  MAX across hourly/1-min/tick sources) *before any strategy backtest was run*; this was
  a coverage fix decided from data-coverage inspection, not from results.
- **Extended-tape re-run (rev 2):** after the deep backfill the full census and both
  backtests were re-run with the byte-identical frozen configuration. Episode set
  (136), taxonomy, train fills and P&L all reproduced exactly; no parameter, gate or
  universe rule was changed in response to the extended data.

## 1. Episode taxonomy (the adverse-selection measurement)

Universe: settled binary Elections/Politics markets, lifetime volume >=1k, time-weighted
YES >=95c over the 24h entering a scheduled event window (window = max-volume hour -6h,
+24h; admitted when >=3 distinct events cluster on the ET date or the date is a known
event night). **Full covered history: 136 episodes across 29 event nights, 2024-11-05 ->
2026-08-04**. The extended tape (trades to 2021-06-30) confirms the earlier window is
an absence of qualifying *markets*, not a data gap — §5.1.

| Class | Definition | n | share |
|---|---|---|---|
| no-dip | never <=85c in window, settled YES | 127 | 93.4% |
| **dip-and-revert** | touched <=85c, settled YES (Michigan shape) | **8** | 5.9% |
| **dip-and-die** | touched <=85c, settled NO (market was right) | **1** | 0.7% |
| died-without-dip | settled NO without printing <=85c in window | 0 | 0% |

- Dip rate: 9/136 = **6.6%** of qualifying episodes.
- **Adverse-selection rate, episode level: 1/9 = 11.1%** of dips died (kill threshold 35%).
- The 9 dip episodes (full history; split marked):

| ET date | Ticker | Class | min c | vol <=85c | data | split |
|---|---|---|---|---|---|---|
| 2024-11-05 | PRESPARTYOR-24-D | revert | 85 | 58 | trades | train |
| 2024-11-05 | PRESPARTYME1-24-D | revert | 71 | 1,878 | trades | train |
| 2024-11-05 | SENATEWY-24-R | revert | 65 | 160 | trades | train |
| 2025-01-24 | KXVOTEHEGSETH-26-TT | revert | 5 | 53,764 | 1min | train |
| 2025-01-24 | KXSECDEF-26DEC31-PH | revert | 40 | 8,662 | trades | train |
| 2026-03-11 | KXNEPALHOUSE-26MAR05-RSP | revert | 50 | 23,843 | hourly | train |
| 2026-06-23 | KXUTPRIMARY-02R26-BMOO | revert | 2 | 862 | hourly | heldout |
| 2026-08-04 | KXSENATEMID-26-AELS | revert | 74 | 715,392 | trades | heldout |
| 2026-08-04 | KXMIPRIMARY-08R26-AHAS | **die** | 1 | 1,871 | 1min | heldout |

Note the shape: when a >=95c favorite dips through 85c inside a scheduled event window it
has historically reverted 8 times out of 9 — but three of the reverts went *very* deep
first (5c, 2c, 40c): "no stop-loss, sized for total loss" is load-bearing, and the one
death (KXMIPRIMARY-08R26-AHAS, a Michigan-primary bracket on the same night the Senate
market reverted) is exactly the adverse-selection case the counterparty-with-precinct-data
story predicts.

## 2. R4 dip-side ladder — train results (81 episodes, exact fees, maker-queue on)

Per-episode engine runs, $10k bankroll each, harness risk caps (per-market 5% => ~$500
max ladder premium). Elections/Politics charge **zero maker fees**; all R4 entries and
exits filled as makers.

| | value |
|---|---|
| Episodes run / entered (>=1 ladder fill) | 81 / **5** |
| Hit rate among entered (net > 0) | **5/5** |
| Adverse selection at fill level (entered & settled NO) | **0/5 = 0%** |
| Total net P&L | **+$402.92** |
| EV per entered episode | **+$80.58** |
| EV per episode (all 81) | +$4.97 |
| Fee stress x1.5 | +$402.92 (unchanged — all-maker, 0c maker fee) |
| Simulated maker fill rate (orders) | 17/258 = 6.6% (<=60% sanity flag: OK) |

Entered episodes (all dip-and-revert; the 6th train dip, SENATEWY-24-R, produced **no
fill** — 160 contracts printed <=85c, too thin to reach a queued order):

| Ticker | contracts | deployed | net | return | exit |
|---|---|---|---|---|---|
| PRESPARTYOR-24-D | 2 | $1.70 | +$0.18 | +10.6% | resting ask 96c |
| PRESPARTYME1-24-D | 626 | $499.50 | +$104.62 | +20.9% | resting ask |
| KXVOTEHEGSETH-26-TT | 626 | $499.50 | +$88.94 | +17.8% | resting ask (dipped to 5c first) |
| KXSECDEF-26DEC31-PH | 626 | $499.50 | +$82.68 | +16.6% | resting ask (dipped to 40c) |
| KXNEPALHOUSE-26MAR05-RSP | 626 | $499.50 | +$126.50 | +25.3% | held to YES settlement |

Distribution, payoff-when-hit (return on deployed): min +10.6%, median +17.8%, max
+25.3%. Loss-when-wrong: **no observation on train** — the honest statement is that the
one historical death sits in the held-out window and a filled ladder there loses
~100% of deployed premium (~-$500 at cap).

Caveats: (i) two of the five wins rode through 40c and 5c marks before reverting —
mark-to-market drawdown near -90% is inside the observed path, (ii) the
opportunity-lifetime metric (median ~seconds) is not meaningful for pre-placed resting
ladders — the SPEC §3 "<5s" flag targets taker signals; the whole point of R4 is that
the order rests *before* the wick, (iii) the maker-queue model estimates displayed
depth from trailing traded volume because no historical order books exist.

## 3. R4b cheap-NO convexity — train results

Entry: buy NO <=3c in the 24h pre-window (taker, fees charged — ~7-10% of stake at these
prices); rest NO asks 15/20/25c; ride to settlement otherwise. Stake 1% of bankroll.

| | value |
|---|---|
| Episodes entered (NO acquired <=3c) | **26** |
| Panic exits (>=1 ladder fill) | **1** (KXNEPALHOUSE: +$5.20) |
| Bleed-to-zero episodes (honest count) | **25** — total -$302.49 |
| Total net P&L | **-$297.29** |
| EV per entered episode | **-$11.43** |
| Measured arm rate | 1/26 = **3.8%** |
| Breakeven arm rate at observed ~8.7x payoff (Michigan, §4) | ~10% |
| Fee stress x1.5 | -$306.79 |

The frozen sizing assumed a 10% panic-arm rate; the measured train rate is 3.8%. The
lottery structure works exactly as viability §2 described — total loss is the base case
— and at the historical arm rate the ticket price exceeds the prize. Note the simulated
3c entries are integer-cent and therefore an *upper bound* on cost versus the real
0.1c-tick book (real entries at 2.0-2.9c were available — the harness's whole-cent
convention is conservative by up to ~17% of stake here, per the dataport unit decision);
that discount does not close a -$11.43/episode gap.

## 4. Michigan replay (sanctioned held-out case study — counts toward nothing)

`KXSENATEMID-26-AELS`, 2026-08-04, replayed through the engine with tick trades
(26k prints), exact fees, maker queue on. Full trace: `michigan_replay.json`.

**R4 — yes, the ladder caught the wick.** All three rungs filled during the panic leg:
196 @ 85c (02:25:52 UTC), 208 @ 80c, **222 @ 75c — filled inside the ~3-minute 74c
trough** the thesis was founded on. Full 626-contract position exited at the resting
96c ask (pre-avg 98.19 - 2) by 04:07 UTC, ~100 minutes after first fill.
**Net +$101.27 on $499.50 deployed = +20.3%**, fees $0.19. This matches viability §2's
ex-post arithmetic (+23.7% buying 77c/selling 97c) within the ladder's blended prices.

**R4b — the convexity leg paid 8.7x.** 3,333 NO bought at 2-3c pre-event ($68.40 incl.
taker fees, entries from 19:01 UTC Aug 3); the full position exited on the panic:
1,111 each @ 15c / 20c / 25c, last fill 03:18 UTC — inside the trough minute.
**Net +$593.32 = +8.67x on stake.** (Viability's 11-14x assumed selling everything at
NO~26c; the frozen ladder banks thirds earlier — the difference is the price of not
knowing the trough depth ex ante.)

## 5. Data gaps (verified against `data/kalshi.db`, pull_log rowid <=23)

1. **RESOLVED (rev 2), with a structural answer: 2021-2023 election nights do not
   exist on Kalshi.** The backfill (pull 25: tick trades for 12,970 more markets,
   reaching 2021-06-30) was re-censused with the frozen gates. Pre-Nov-2024 the
   qualifying universe contains 1,520 settled Elections/Politics markets with data;
   1,410 fail the >=95c pre-event gate, 95 fail data/volume gates, and only **15 pass
   market-level gates — each a singleton on its own date** (debt-ceiling deadlines,
   confirmation votes, a debate, approval brackets), so the frozen cluster/known-night
   rule rejects all of them. There are no 2022-midterm, GA-runoff or 2021/2023
   governor-night episodes because **Kalshi listed no candidate election markets before
   Oct 2024** (CFTC litigation; markets settling around 2022-11-08 are CPI/approval/
   legislation series only). rev 1's premise that pulling 2022 tick history would add
   election-night episodes was wrong.
   **Singleton sensitivity (taxonomy-level only, admitted nothing):** re-classing the
   15 rejected windows adds **zero fillable episodes** — 14 no-dip, 1 died-without-dip
   (ASYLUMCASES-24JUN-14000, settled NO without ever printing <=85c: no R4 fill; it
   would have been a hold-to-settlement windfall for R4b's NO ticket, noted for honesty
   and NOT added — its outcome is already known and the frozen universe rule stays).
2. 1-min candles cover the final-72h of 4,318 election/politics markets (pull 19) plus
   the full 2024 election night for 581 tickers (pull 24); tick trades for 708 + 40
   selected markets plus the 12,970-market deep backfill (pull 25). Six of nine dip
   episodes replay on tick trades or 1-min candles; two (NEPALHOUSE, UTPRIMARY) only on
   hourly candles — their fills use the conservative strictly-through candle rule. The
   re-run on this richer tape reproduced rev 1's fills exactly.
3. No historical order books (SYNTHESIS §3 risk 3): maker queue position is estimated
   from trailing traded volume (calibrated on the FLB fill-rate anchor, never on P&L).
4. Sub-cent (0.1c) execution is not representable in the integer-cent harness; per the
   dataport convention every rounding direction chosen is pessimistic for us (R4b entry
   cost overstated, candle lows ceiled so ladders under-fill).

## 6. Kill verdicts (criteria fixed in SYNTHESIS P-3 before any backtest)

| Criterion | Threshold | Measured | Verdict |
|---|---|---|---|
| (a) adverse selection | fills dominated by non-reverting wicks (>~35%) | episode level 1/9 = **11.1%** full history; fill level 0/5 train | **PASS** (small n) |
| (b) enough data | <10 episodes with simulated fills over full history -> park | **9 fillable episodes** (<=85c) full history — unchanged on the extended 2021-2026 tape (+0 from 3.5 extra years, §5.1); 5 filled on train, max possible <=9 incl. held-out | **FIRES -> PARK** |
| (c) economics | EV per episode <= 0 | R4: **+$80.58**/entered (+$4.97/episode); R4b: **-$11.43**/entered | R4 pass, **R4b KILL** |

**R4 (dip-side ladder): PARK until the Nov 2026 midterms, per criterion (b) — now
final on the data question.** Every measurable sign is positive — 5/5 train hits at
+10.6..+25.3%, 0% fill-level adverse selection, fee-stress-immune (zero maker fees),
and the held-out-window Michigan case study confirms the mechanism end-to-end — but
nine fillable episodes remain below the pre-registered 10-episode bar, and the extended
tape closes the escape hatch rev 1 hoped for: the bar **cannot** be cleared by more
history, because Kalshi's election-market class only exists from Oct 2024 (§5.1). The
evidence deficit is structural until new event nights occur. Action: keep the discovery
+ strategy code warm and paper-trade the ladder live on 2026-11-03 — the midterms are
the first night that can move n materially (dozens of >=95c favorites in one window,
like 2024-11-05's 30 episodes).

**R4b (cheap-NO convexity): KILL as a standalone strategy, per criterion (c).** The
measured arm rate (3.8%) is ~1/3 of breakeven (~10%) at the observed payoff; 25 of 26
tickets bled to zero exactly as the honest accounting predicted. The Michigan 8.7x is
real but does not pay for the bleed. Revisit only as an *overlay* on nights where R4 is
already armed and capacity-constrained — never as a standing program.

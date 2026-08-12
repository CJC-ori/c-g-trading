# ROUND 2 — Integration variants (pre-named, TOURNAMENT_PROTOCOL.md)

Generated 2026-08-12 12:59Z by `python -m bot.strategies.tournament.run_round2`. Base = Round-1 least-bad config `r5_endgame_6h` (no Round-1 variant passed). Universe/boundary pinned to `reports/tournament/round1/universe.json` (split_ts = 1722556901 = 2024-08-02T00:01:41Z; TRAIN = 7,103 markets; held-out 40% untouched). Harness: exact FeeSchedule.load_default(), maker queue depth_windows=1.0, cancel/replace on, $10k bankroll, quarter-Kelly, fee stress x1.5.

## Comparison table

| variant | episodes (clusters) | net P&L | per-episode mean +/- clustered SE [95% CI] | maker fill rate | fee x1.5 P&L | capacity 1x/3x/10x | top-5 share | verdict |
|---|---|---|---|---|---|---|---|---|
| r5_endgame_6h | 215 (181) | $-679.17 | -4.49% +/- +2.19% [-8.79%, -0.20%] | 12% | $-680.27 | -679/-615/-591 | n/a (net<=0) | reference (champion re-run) |
| ia_econ_only | 29 (21) | $+144.21 | +4.12% +/- +0.41% [+3.33%, +4.92%] | 45% | $+143.11 | +144/+171/+174 | 35% | **KEEP**: net P&L improves vs champion ($+144.21 vs $-679.17) |
| ib_swing_filter | 81 (69) | $-78.93 | -4.81% +/- +3.67% [-12.00%, +2.38%] | 15% | $-80.03 | -79/-15/-4 | n/a (net<=0) | **KEEP**: net P&L improves vs champion ($-78.93 vs $-679.17) |
| ic_panic_standdown | 215 (181) | $-679.17 | -4.49% +/- +2.19% [-8.79%, -0.20%] | 12% | $-680.27 | -679/-615/-591 | n/a (net<=0) | DISCARD: net P&L does NOT improve vs champion ($-679.17 vs $-679.17) |
| id_r4_overlay | 215 (181) | $-679.17 | -4.49% +/- +2.19% [-8.79%, -0.20%] | 12% | $-680.27 | -679/-615/-591 | n/a (net<=0) | DISCARD: net P&L does NOT improve vs champion ($-679.17 vs $-679.17) |
| ie_eco_spikefade | 216 (181) | $-980.38 | -4.87% +/- +2.23% [-9.24%, -0.50%] | 12% | $-988.03 | -980/-1,002/-977 | n/a (net<=0) | DISCARD: net P&L does NOT improve vs champion ($-980.38 vs $-679.17) |

## Keep/discard (pre-registered rule: improve net P&L without worsening fee-stress survival or top-5 concentration)

- **ia_econ_only** — KEEP: net P&L improves vs champion ($+144.21 vs $-679.17); fee x1.5 P&L $+143.11 (champion $-680.27); top-5 share 35% <= 60%
- **ib_swing_filter** — KEEP: net P&L improves vs champion ($-78.93 vs $-679.17); fee x1.5 P&L $-80.03 (champion $-680.27); top-5 share n/a (net <= 0)
- **ic_panic_standdown** — DISCARD: net P&L does NOT improve vs champion ($-679.17 vs $-679.17); fee x1.5 P&L $-680.27 (champion $-680.27); top-5 share n/a (net <= 0)
- **id_r4_overlay** — DISCARD: net P&L does NOT improve vs champion ($-679.17 vs $-679.17); fee x1.5 P&L $-680.27 (champion $-680.27); top-5 share n/a (net <= 0)
- **ie_eco_spikefade** — DISCARD: net P&L does NOT improve vs champion ($-980.38 vs $-679.17); fee x1.5 P&L $-988.03 (champion $-680.27); top-5 share n/a (net <= 0)

## Interpretation (Round-2 verdict)

1. **The champion re-run reproduces Round 1 exactly** (-$679.17, 215
   episodes, same CI) — determinism holds and the still-appending 2025
   trades backfill did not move train-side fills, as predicted (it
   postdates split_ts).
2. **I-a (Economics-only) is the integrated champion going to Round 3.**
   It is the only variant that is net-positive on the full-history train:
   +$144.21 net, fee x1.5 +$143.11 (> 0), capacity curve RISING
   (+144/+171/+174 at 1x/3x/10x), top-5 concentration 35% < 60%, maker
   order fill rate 45% — inside the SPEC 40-50% sanity band (the
   champion's 12% was a weather artifact; Economics endgame quotes fill
   at the anchor rate). On its face it clears every SPEC §7 train-side
   gate for a price-only strategy.
3. **I-a's honest caveats, stated before Round 3/4:** (a) n = 29 episodes
   across 21 event clusters — BELOW the n>=30 bar Round 1 used for
   championship, and small in absolute terms; (b) **zero losing episodes
   in the sample** (min +1.95%), so the mean is an upper bound and the
   clustered CI reflects only winner dispersion — the same warning shape
   that preceded R5's Round-1 failure. The difference this time: this IS
   the full 2021-07..2024-08 history, not a 95-day pull, and the win-rate
   curve sits above breakeven in every price bin (1.000 win rate on 29
   FED/CPI/OIL-class favorites). One 95c failure would cost ~-100% of an
   episode (~-3.4% on the mean) and roughly -$130 of the +$144 net.
   (c) The category label is the only filter: 29 episodes are dominated
   by FED/CPI-family series, which DO charge maker fees — charged exactly
   by the fee engine.
4. **I-b (swing filter) is kept as the runner-up.** It removes 133 of the
   178 weather episodes (the filter binds almost exclusively in weather —
   exactly the 'swings are forecast updates' census finding) and cuts the
   loss 8.6x (-$78.93 vs -$679.17) with a rising capacity curve, but it
   is still negative and its CI spans zero. It graduates nothing by
   itself; it survives to Round 3 per the pre-registered keep rule and
   documents that the residual R5 loss is swing-adjacent weather entries.
5. **I-c never bound on train** (byte-identical results): no train-side
   R5-6h entry fell on a scheduled election-night date — the Politics
   losses Round 1 saw in 12h/24h windows are outside the 6h champion's
   entry set, and all discovered R4 wick windows post-date the split.
6. **I-d is a structural no-op** (0 train-side R4 episodes; the whole
   discovered class is 2024-11-05+). Its live thesis is untested on this
   boundary, not refuted — it stays PARKED as a paper-trading plan per
   the protocol's final-report rule, but it cannot ride Round 3/4.
7. **I-e actively hurts** (-$980.38): on the pinned universe the
   SwingFade eco leg entered 16 markets and turned the Economics book
   from +$144.21 into -$157.00 (one extra episode, -$301 swing). The
   parked +$562 result came from its own 875-market census universe and
   per-market fresh bankrolls; under shared caps on the tournament
   universe the cell is a money-loser. Discarded on the pre-registered
   rule.

**Round-3 slate: champion = `ia_econ_only`; kept runner-up =
`ib_swing_filter`. Everything else discarded.**

## r5_endgame_6h

*champion re-run: final 6h, maker-join bid on 90-98c side (Round-1 least-bad config)*

- Episodes: 215 across 181 events; mean -4.49%, clustered SE +2.19%, 95% CI [-8.79%, -0.20%]. Distribution: min -100.00%, median +3.09%, max +11.11%; 92% positive.
- Capital: $+12,177.83 deployed, net $-679.17 (-5.58% dollar-weighted).

| category | episodes | mean return | clustered SE | net P&L |
|---|---|---|---|---|
| Climate and Weather | 178 | -5.68% | +2.56% | $-781.91 |
| Economics | 29 | +4.12% | +0.41% | $+144.21 |
| Politics | 3 | +3.82% | +0.95% | $+27.88 |
| World | 5 | -17.29% | +20.69% | $-69.35 |

## ia_econ_only

*I-a: R5-6h restricted to categories above breakeven in every Round-1 price bin/window = Economics only*

- Episodes: 29 across 21 events; mean +4.12%, clustered SE +0.41%, 95% CI [+3.33%, +4.92%]. Distribution: min +1.95%, median +3.65%, max +10.92%; 100% positive.
- Capital: $+3,985.45 deployed, net $+144.21 (+3.62% dollar-weighted).

| category | episodes | mean return | clustered SE | net P&L |
|---|---|---|---|---|
| Economics | 29 | +4.12% | +0.41% | $+144.21 |

## ib_swing_filter

*I-b: R5-6h, entries blocked within 24h of a >=20c/24h adverse (side-space) swing (census X20_T24)*

- Episodes: 81 across 69 events; mean -4.81%, clustered SE +3.67%, 95% CI [-12.00%, +2.38%]. Distribution: min -100.00%, median +3.09%, max +10.92%; 91% positive.
- Capital: $+6,442.59 deployed, net $-78.93 (-1.23% dollar-weighted).

| category | episodes | mean return | clustered SE | net P&L |
|---|---|---|---|---|
| Climate and Weather | 45 | -9.58% | +5.95% | $-180.32 |
| Economics | 29 | +4.12% | +0.41% | $+144.21 |
| Politics | 3 | +3.82% | +0.95% | $+27.88 |
| World | 4 | -22.38% | +25.89% | $-70.70 |

## ic_panic_standdown

*I-c: R5-6h, stand down in Politics/Elections on scheduled event nights + discovered R4 windows*

- Episodes: 215 across 181 events; mean -4.49%, clustered SE +2.19%, 95% CI [-8.79%, -0.20%]. Distribution: min -100.00%, median +3.09%, max +11.11%; 92% positive.
- Capital: $+12,177.83 deployed, net $-679.17 (-5.58% dollar-weighted).

| category | episodes | mean return | clustered SE | net P&L |
|---|---|---|---|---|
| Climate and Weather | 178 | -5.68% | +2.56% | $-781.91 |
| Economics | 29 | +4.12% | +0.41% | $+144.21 |
| Politics | 3 | +3.82% | +0.95% | $+27.88 |
| World | 5 | -17.29% | +20.69% | $-69.35 |

## id_r4_overlay

*I-d: champion portfolio + parked R4 overlay (additive capacity, shared per-market caps)*

- Episodes: 215 across 181 events; mean -4.49%, clustered SE +2.19%, 95% CI [-8.79%, -0.20%]. Distribution: min -100.00%, median +3.09%, max +11.11%; 92% positive.
- Capital: $+12,177.83 deployed, net $-679.17 (-5.58% dollar-weighted).

| category | episodes | mean return | clustered SE | net P&L |
|---|---|---|---|---|
| Climate and Weather | 178 | -5.68% | +2.56% | $-781.91 |
| Economics | 29 | +4.12% | +0.41% | $+144.21 |
| Politics | 3 | +3.82% | +0.95% | $+27.88 |
| World | 5 | -17.29% | +20.69% | $-69.35 |

- R4 overlay: 0 train-side episodes, 0 entered, net $+0.00. R4 episodes restricted to the pinned Round-1 TRAIN tickers (settlement <= split_ts). All 136 discovered R4 episodes have window_start >= 2024-11-05 > split_ts (2024-08-02): the R4 episode class does not exist in the train-side tape, so the overlay is a structural no-op on this boundary.

## ie_eco_spikefade

*I-e: portfolio = R5-6h + parked eco spike-fade (SwingFade eco_spike_fade cell), one engine run, shared bankroll/risk caps*

- Episodes: 216 across 181 events; mean -4.87%, clustered SE +2.23%, 95% CI [-9.24%, -0.50%]. Distribution: min -101.74%, median +3.09%, max +19.25%; 92% positive.
- Capital: $+12,959.38 deployed, net $-980.38 (-7.57% dollar-weighted).

| category | episodes | mean return | clustered SE | net P&L |
|---|---|---|---|---|
| Climate and Weather | 178 | -5.68% | +2.56% | $-781.91 |
| Economics | 30 | +1.10% | +3.67% | $-157.00 |
| Politics | 3 | +3.82% | +0.95% | $+27.88 |
| World | 5 | -17.29% | +20.69% | $-69.35 |

- Leg overlap: R5 quoted in 1511 markets, SwingFade in 16; both legs active in 6 markets (replace-semantics interference scope).

## Notes

- Same pinned universe/boundary/harness as Round 1; per-variant outputs in reports/tournament/round2/<variant>/ (report.json/report.md/extras.json/episodes.csv).
- I-a derivation: 'drop categories whose win-rate-vs-price curve sat below breakeven' evaluated on the Round-1 tables; only Economics cleared every bin in every window (Politics cleared 6h bins on n=3 episodes but failed 12h/24h — the robustness reading registered in ROUND1.md interpretation #3 before Round 2 ran).
- I-b constants (X=20c, T=24h, N=24h) are census X20_T24 + its 24h fill window, pre-registered in wrappers.py before the run; detection is point-in-time from the strategy's own MarketView.
- I-c uses the ex-ante KNOWN_EVENT_NIGHTS calendar plus discovered R4 windows; all discovered windows post-date split_ts, so on train only the calendar binds.
- I-d: all 136 discovered R4 episodes start 2024-11-05 or later (> split_ts 2024-08-02): the parked R4 class has zero train-side episodes, so the overlay is a structural no-op on this boundary — it cannot improve train net P&L.
- The held-out 40% was never touched.
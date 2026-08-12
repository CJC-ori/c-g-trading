# ROUND 1 — Full-history championship run (FLB R5-endgame family)

Generated 2026-08-12 09:09Z by the Round-1 tournament agent. Pre-registered protocol: `reports/TOURNAMENT_PROTOCOL.md`. Universe + split frozen in `reports/tournament/UNIVERSE.md` and `reports/tournament/round1/universe.json` (pinned via `--universe-file`).

## Binding boundary

- Settlement span: 2021-07-19 04:01:00Z .. 2026-08-11 21:22:09Z (1850 days).
- **Train/test boundary (60% of the full settled window, binding for all later rounds): split_ts = 1722556901 = 2024-08-02 00:01:41Z.**
- Universe: 55350 settled non-sports/non-crypto/non-parlay markets (12954 events), volume >= 1000, hourly candles, close_time > 2021-07-01. TRAIN = 7103 markets; HELD-OUT = 48247 (untouched; Round 4 only).
- Harness: exact per-series FeeSchedule (centicent model), maker queue ON (depth_windows=1.0), cancel/replace ON, $10k bankroll, quarter-Kelly, fee stress x1.5. Frozen R5 params from reports/flb/ANALYSIS.md — no re-tuning.

## Comparison table

| variant | episodes (clusters) | net P&L | per-episode mean +/- clustered SE [95% CI] | dollar-weighted | maker order fill rate | fill rate (contracts vs ordered) | fee x1.5 P&L | capacity 1x/3x/10x | top-5 share | max drawdown | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| r5_endgame_6h | 215 (181) | $-679.17 | -4.49% +/- +2.19% [-8.79%, -0.20%] | -5.58% | 12% | 7% | $-680.27 | $-679.17/$-615.04/$-591.02 | n/a | $+914.63 | FLAGGED: mean episode return <= 0; fee x1.5 P&L <= 0 |
| r5_endgame_12h | 727 (595) | $-7,224.90 | -10.19% +/- +1.51% [-13.14%, -7.23%] | -17.99% | 16% | 10% | $-7,226.11 | $-7,224.90/$-7,547.06/$-7,516.56 | n/a | $+7,296.81 | FLAGGED: mean episode return <= 0; fee x1.5 P&L <= 0 |
| r5_endgame_24h | 846 (634) | $-9,999.52 | -10.54% +/- +1.42% [-13.32%, -7.76%] | -23.06% | 20% | 13% | $-9,999.24 | $-9,999.52/$-9,950.47/$-9,948.63 | n/a | $+10,019.83 | FLAGGED: mean episode return <= 0; fee x1.5 P&L <= 0 |
| baseline_hold_favorite | 347 (319) | $-1,399.37 | -13.92% +/- +2.40% [-18.62%, -9.23%] | -15.34% | n/a | 36% | $-1,424.39 | $-1,399.37/$-2,308.14/$-2,535.32 | n/a | $+1,509.91 | baseline (reference) |

## Champion (pre-registered criteria)

**No variant passes** all pre-registered criteria (n>=30 + all sanity flags).
- r5_endgame_6h: FLAGS: mean episode return <= 0; fee x1.5 P&L <= 0
- r5_endgame_12h: FLAGS: mean episode return <= 0; fee x1.5 P&L <= 0
- r5_endgame_24h: FLAGS: mean episode return <= 0; fee x1.5 P&L <= 0

## r5_endgame_6h

*final 6h, maker-join bid on 90-98c side*

- Episodes: 215 across 181 events; mean -4.49%, clustered SE +2.19%, 95% CI [-8.79%, -0.20%]. Distribution: min -100.00%, median +3.09%, max +11.11%; 92% positive.
- Capital: $+12,177.83 deployed, net $-679.17 (-5.58% dollar-weighted).
- Top-5 markets (n/a of net): DCEIL-23JUN1 $+20.80, HIGHCHI-22MAR25-T49 $+19.08, HIGHCHI-23APR24-B52.5 $+15.45, OIL-22JUL11-N100 $+12.60, HIGHNY0-21JUL17-T90 $+10.74.
- Worst single-market loss: $-328.51.
- Opportunity lifetime: median 2h over 2444 qualifying markets.

| category | episodes | mean return | clustered SE | net P&L |
|---|---|---|---|---|
| Climate and Weather | 178 | -5.68% | +2.56% | $-781.91 |
| Economics | 29 | +4.12% | +0.41% | $+144.21 |
| Politics | 3 | +3.82% | +0.95% | $+27.88 |
| World | 5 | -17.29% | +20.69% | $-69.35 |

Win-rate vs entry price by category (contract-weighted; breakeven = price + paid fee):

| category | entry price | contracts | markets | win rate | breakeven | above? |
|---|---|---|---|---|---|---|
| Climate and Weather | 90c | 177 | 6 | 0.102 | 0.900 | no |
| Climate and Weather | 91c | 624 | 9 | 0.216 | 0.910 | no |
| Climate and Weather | 92c | 125 | 4 | 0.960 | 0.920 | YES |
| Climate and Weather | 93c | 118 | 7 | 1.000 | 0.930 | YES |
| Climate and Weather | 94c | 19 | 2 | 1.000 | 0.940 | YES |
| Climate and Weather | 95c | 207 | 10 | 0.599 | 0.950 | no |
| Climate and Weather | 96c | 1035 | 10 | 0.926 | 0.960 | no |
| Climate and Weather | 97c | 2614 | 73 | 0.998 | 0.970 | YES |
| Climate and Weather | 98c | 2483 | 57 | 0.905 | 0.980 | no |
| Economics | 90c | 51 | 1 | 1.000 | 0.902 | YES |
| Economics | 92c | 17 | 1 | 1.000 | 0.922 | YES |
| Economics | 93c | 92 | 1 | 1.000 | 0.931 | YES |
| Economics | 94c | 464 | 4 | 1.000 | 0.941 | YES |
| Economics | 95c | 252 | 1 | 1.000 | 0.950 | YES |
| Economics | 96c | 964 | 7 | 1.000 | 0.961 | YES |
| Economics | 97c | 881 | 6 | 1.000 | 0.971 | YES |
| Economics | 98c | 1411 | 8 | 1.000 | 0.980 | YES |
| Politics | 95c | 126 | 1 | 1.000 | 0.950 | YES |
| Politics | 96c | 520 | 1 | 1.000 | 0.960 | YES |
| Politics | 98c | 39 | 1 | 1.000 | 0.980 | YES |
| World | 90c | 93 | 1 | 0.000 | 0.900 | no |
| World | 94c | 179 | 1 | 1.000 | 0.940 | YES |
| World | 97c | 45 | 1 | 1.000 | 0.970 | YES |
| World | 98c | 113 | 2 | 1.000 | 0.980 | YES |

## r5_endgame_12h

*final 12h, maker-join bid on 90-98c side*

- Episodes: 727 across 595 events; mean -10.19%, clustered SE +1.51%, 95% CI [-13.14%, -7.23%]. Distribution: min -100.00%, median +3.09%, max +11.11%; 85% positive.
- Capital: $+40,160.28 deployed, net $-7,224.90 (-17.99% dollar-weighted).
- Top-5 markets (n/a of net): HIGHMIA-24JAN10-B73.5 $+27.54, DCEIL-23JUN1 $+26.30, GASM-22JUL11-A4.60 $+24.20, HIGHNY-22JUN18-T74 $+20.80, HIGHCHI-22MAR25-T49 $+19.08.
- Worst single-market loss: $-499.70.
- Opportunity lifetime: median 2h over 3859 qualifying markets.

| category | episodes | mean return | clustered SE | net P&L |
|---|---|---|---|---|
| Climate and Weather | 674 | -10.57% | +1.59% | $-6,839.52 |
| Economics | 39 | +4.65% | +0.40% | $+182.70 |
| Politics | 7 | -40.93% | +18.01% | $-77.42 |
| World | 7 | -25.05% | +19.39% | $-490.66 |

Win-rate vs entry price by category (contract-weighted; breakeven = price + paid fee):

| category | entry price | contracts | markets | win rate | breakeven | above? |
|---|---|---|---|---|---|---|
| Climate and Weather | 90c | 2533 | 55 | 0.467 | 0.900 | no |
| Climate and Weather | 91c | 2040 | 49 | 0.538 | 0.910 | no |
| Climate and Weather | 92c | 2554 | 61 | 0.570 | 0.920 | no |
| Climate and Weather | 93c | 2361 | 54 | 0.706 | 0.930 | no |
| Climate and Weather | 94c | 3335 | 58 | 0.707 | 0.940 | no |
| Climate and Weather | 95c | 4385 | 64 | 0.472 | 0.950 | no |
| Climate and Weather | 96c | 3818 | 56 | 0.829 | 0.960 | no |
| Climate and Weather | 97c | 9248 | 152 | 0.976 | 0.970 | YES |
| Climate and Weather | 98c | 5274 | 125 | 0.933 | 0.980 | no |
| Economics | 90c | 102 | 2 | 1.000 | 0.902 | YES |
| Economics | 91c | 37 | 1 | 1.000 | 0.912 | YES |
| Economics | 92c | 76 | 2 | 1.000 | 0.921 | YES |
| Economics | 93c | 92 | 1 | 1.000 | 0.931 | YES |
| Economics | 94c | 472 | 6 | 1.000 | 0.941 | YES |
| Economics | 95c | 796 | 4 | 1.000 | 0.950 | YES |
| Economics | 96c | 912 | 8 | 1.000 | 0.961 | YES |
| Economics | 97c | 881 | 6 | 1.000 | 0.971 | YES |
| Economics | 98c | 1412 | 9 | 1.000 | 0.980 | YES |
| Politics | 91c | 43 | 1 | 0.000 | 0.910 | no |
| Politics | 95c | 526 | 1 | 1.000 | 0.950 | YES |
| Politics | 96c | 25 | 1 | 1.000 | 0.960 | YES |
| Politics | 97c | 73 | 2 | 0.000 | 0.970 | no |
| Politics | 98c | 261 | 2 | 1.000 | 0.980 | YES |
| World | 90c | 265 | 2 | 0.234 | 0.900 | no |
| World | 91c | 361 | 1 | 0.000 | 0.910 | no |
| World | 94c | 179 | 1 | 1.000 | 0.940 | YES |
| World | 97c | 45 | 1 | 1.000 | 0.970 | YES |
| World | 98c | 113 | 2 | 1.000 | 0.980 | YES |

## r5_endgame_24h

*final 24h, maker-join bid on 90-98c side*

- Episodes: 846 across 634 events; mean -10.54%, clustered SE +1.42%, 95% CI [-13.32%, -7.76%]. Distribution: min -100.00%, median +5.26%, max +11.11%; 84% positive.
- Capital: $+43,372.42 deployed, net $-9,999.52 (-23.06% dollar-weighted).
- Top-5 markets (n/a of net): HIGHNY-21NOV08-T63 $+32.49, HIGHNY-23JAN20-B50.5 $+30.51, HIGHCHI-22MAR25-T49 $+19.08, HIGHNY-22SEP10-T86 $+17.64, HIGHNY-22JUN05-B77.5 $+17.00.
- Worst single-market loss: $-499.80.
- Opportunity lifetime: median 2h over 4302 qualifying markets.

| category | episodes | mean return | clustered SE | net P&L |
|---|---|---|---|---|
| Climate and Weather | 777 | -11.11% | +1.50% | $-9,020.87 |
| Economics | 51 | +5.25% | +0.38% | $+131.88 |
| Politics | 10 | -38.24% | +13.13% | $-623.61 |
| World | 8 | -21.66% | +17.13% | $-486.92 |

Win-rate vs entry price by category (contract-weighted; breakeven = price + paid fee):

| category | entry price | contracts | markets | win rate | breakeven | above? |
|---|---|---|---|---|---|---|
| Climate and Weather | 90c | 3987 | 98 | 0.561 | 0.900 | no |
| Climate and Weather | 91c | 3794 | 83 | 0.696 | 0.910 | no |
| Climate and Weather | 92c | 3166 | 79 | 0.629 | 0.920 | no |
| Climate and Weather | 93c | 3243 | 81 | 0.748 | 0.930 | no |
| Climate and Weather | 94c | 3455 | 84 | 0.788 | 0.940 | no |
| Climate and Weather | 95c | 5358 | 89 | 0.548 | 0.950 | no |
| Climate and Weather | 96c | 4393 | 74 | 0.781 | 0.960 | no |
| Climate and Weather | 97c | 7962 | 93 | 0.841 | 0.970 | no |
| Climate and Weather | 98c | 5095 | 96 | 0.813 | 0.980 | no |
| Economics | 90c | 153 | 3 | 1.000 | 0.902 | YES |
| Economics | 91c | 191 | 4 | 1.000 | 0.912 | YES |
| Economics | 92c | 175 | 4 | 1.000 | 0.921 | YES |
| Economics | 93c | 241 | 5 | 1.000 | 0.931 | YES |
| Economics | 94c | 212 | 5 | 1.000 | 0.941 | YES |
| Economics | 95c | 357 | 4 | 1.000 | 0.950 | YES |
| Economics | 96c | 420 | 8 | 1.000 | 0.961 | YES |
| Economics | 97c | 477 | 8 | 1.000 | 0.971 | YES |
| Economics | 98c | 447 | 10 | 1.000 | 0.980 | YES |
| Politics | 91c | 43 | 1 | 0.000 | 0.910 | no |
| Politics | 94c | 531 | 1 | 0.000 | 0.940 | no |
| Politics | 95c | 98 | 1 | 1.000 | 0.950 | YES |
| Politics | 96c | 25 | 1 | 1.000 | 0.960 | YES |
| Politics | 97c | 110 | 2 | 0.000 | 0.970 | no |
| Politics | 98c | 773 | 4 | 1.000 | 0.980 | YES |
| World | 90c | 265 | 2 | 0.234 | 0.900 | no |
| World | 91c | 361 | 1 | 0.000 | 0.910 | no |
| World | 94c | 179 | 1 | 1.000 | 0.940 | YES |
| World | 97c | 45 | 1 | 1.000 | 0.970 | YES |
| World | 98c | 300 | 3 | 1.000 | 0.980 | YES |

## baseline_hold_favorite

*null baseline: taker-buy YES 90-97c once, hold*

- Episodes: 347 across 319 events; mean -13.92%, clustered SE +2.40%, 95% CI [-18.62%, -9.23%]. Distribution: min -105.68%, median +4.91%, max +118.10%; 80% positive.
- Capital: $+9,123.28 deployed, net $-1,399.37 (-15.34% dollar-weighted).
- Top-5 markets (n/a of net): HIGHNY-22JAN08-T29 $+11.99, HIGHCHI-21AUG30-T83 $+10.31, HIGHNY-22JUL17-T84 $+9.49, FED-23MAR-T4.75 $+9.37, HIGHCHI-21OCT21-T59 $+9.37.
- Worst single-market loss: $-97.21.
- Opportunity lifetime: median 4h over 5732 qualifying markets.

| category | episodes | mean return | clustered SE | net P&L |
|---|---|---|---|---|
| Climate and Weather | 277 | -16.38% | +2.84% | $-1,142.47 |
| Economics | 50 | -0.07% | +3.70% | $-80.03 |
| Politics | 19 | -15.77% | +7.74% | $-184.35 |
| World | 1 | +8.13% | n/a | $+7.48 |

## Interpretation (Round-1 verdict)

1. **The R5 train-split PASS did not survive full history.** On the 2026-only
   95-day pull, R5 was +5.4-6.3%/episode with zero losing episodes. On the
   full 2021-07..2024-08 train window every R5 window is decisively negative
   (6h: -4.5%/ep, CI [-8.8%, -0.2%]; 12h: -10.2%/ep; 24h: -10.5%/ep), and the
   24h window loses essentially the whole $10k bankroll. The zero-loss 2026
   sample was exactly the upper bound the train analysis warned about: the
   full-history sample contains the missing failure tail (-100% episodes,
   8-16% of episodes negative).
2. **The losses are structural, not one bad event: pre-2025 weather ladders.**
   The 2021-2024 train universe is dominated by Climate and Weather strike
   ladders (HIGHNY/HIGHCHI/HIGHMIA...): 83-92% of episodes in every window.
   The win-rate-vs-price curve shows weather favorites at 90-96c winning far
   below breakeven (e.g. 24h window: 0.55-0.79 win rates vs 0.90-0.96
   breakeven). A 90-98c "favorite" on a same-day temperature strike is not a
   settled fact, it is a coin with fat tails, and joining the bid there is
   adverse selection against forecast updates.
3. **Economics is the only survivor — in every window.** Economics episodes
   are positive in all three windows (+4.1% to +5.3%/episode, clustered SE
   ~0.4%, n=29/39/51) and its win-rate curve sits at 1.000, above breakeven in
   every price bin, in every window. This is the demonstrated strength that
   Round 2's pre-registered I-a variant (category filter) exists to fold in.
   Politics/World endgame entries are negative (election/geopolitical wicks —
   the I-c panic stand-down insight).
4. **The baseline confirms the market regime, not a harness artifact.**
   HoldFavorite (taker, no maker machinery) loses -13.9%/episode on the same
   universe with the same weather-dominated composition — long-shot bias on
   the NO side of pre-2025 weather ladders is simply brutal against favorites.
5. **Fill sanity holds.** Maker order fill rates 12-20% (<= 60% flag bar);
   fee x1.5 moves P&L by pennies (maker fees are tiny); capacity is flat —
   the losses are not a size artifact.

Per the pre-registered protocol, Round 2 integration variants (especially
I-a category filtering) proceed from the strongest R5 configuration; the
honest headline is that **unfiltered R5-endgame is dead on full history**,
and any surviving strategy must earn its pass through the pre-named
integration variants, the adversarial audit, and the single held-out run.

## Notes

- Full SPEC reports per variant: `reports/tournament/round1/<variant>/report.md` (+ report.json, extras.json, episodes.csv).
- 2022 midterms are category=Politics in this DB; both Politics and Elections are in-universe (only Sports/Crypto excluded).
- A 2025 trades backfill (closed 2024-11..2026-01) may still have been appending during this run; those trades postdate split_ts, so train-side results are unaffected.
- The held-out 40% (settlements after split_ts) was never run.

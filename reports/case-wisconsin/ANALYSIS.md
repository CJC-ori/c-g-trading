# Wisconsin 2026-08-11 — the dip that DIED (out-of-sample case study)

**2026-08-12.** Event: Wisconsin Democratic gubernatorial primary, Kalshi series
`KXGOVWINOMD` (FHON = Francesca Hong, DCRO = David Crowley), siblings
`KXGOVWINOMR` (R side), `KXWIDGOV2ND` (2nd place), `KXPRIMARYMOV-GOVWINOMD26-*`
(margin), `KXVOTEPRIMARY-GOVWINOMD26*` (vote share). Hong led the final public
polls; Crowley won 39.8%–39.4% ([NBC](https://www.nbcnews.com/politics/2026-election/francesca-hong-david-crowley-democratic-primary-wisconsin-governor-rcna592056),
[Fox9](https://www.fox9.com/news/wisconsin-governor-primary-results-democrat-2026)).
Markets settled 2026-08-12 11:23 UTC — **after every strategy in this repo was
frozen**, so this is a genuine out-of-sample event. Nothing below was used to
tune anything; no parameter, gate, or threshold was changed.

**Data provenance (all pulled 2026-08-12 ~13:45–14:00 UTC via `python -m
bot.data.pull`, public endpoints):** Elections market metadata refresh
(by-series; captured settlements), 1-min candles 08-09→08-12 and hourly candles
07-13→08-12 for the WI complex + the Aug-11 primary universe (WI/MN/CT/VT
series), full trade tape for the 7 `KXGOVWINOMD` candidates + `KXWIDGOV2ND`
(69,926 prints) + the night's 6 other qualifying markets (710 prints). Engine
replays: `reports/case-wisconsin/{wisconsin_replay.json, night_replay.json}`.

---

## 1. Pre-election: the market was NOT "with the polls" — it was far ahead of them, on the wrong side

Final 24h before the event window (time-weighted, from 1-min candles):

| market | pre-election price | poll picture |
|---|---|---|
| Hong (`-FHON`) | **95.3c avg**, 94.5–97.4 range on Aug 11 | Marquette (late Jul): Hong 38%, large plurality, Crowley single digits, ~1/3 undecided ([WaPo](https://www.washingtonpost.com/elections/2026/08/11/wisconsin-governor-primary-election-results-live-francesca-hong-david-crowley-run/), [Wisconsin Watch](https://wisconsinwatch.org/2026/08/wisconsin-gubernatorial-primary-democratic-voters-still-split-election-hong-crowley-roys-brennan/)) |
| Crowley (`-DCRO`) | **4.9c** (3.4–5.6 final days) | Crowley internal (PPP, Aug 4–5, public Aug 10): race "wide open" ([Spectrum](https://spectrumnews1.com/wi/milwaukee/news/2026/08/10/election-primary-polling-voting)) |
| "Crowley finishes 2nd" | 97c | — |
| "Hong margin ≥15%" (`FHON-P57`) | **38c** | a ~10-pt poll lead with 1/3 undecided |
| "Hong margin 9–12%" / "12–15%" | 21c / 16c | — |
| "Hong margin 0–3%" | 8c | — |
| "Crowley margin 0–3%" (`DCRO-P1`) | **5c** | ← the bracket that happened |

Two Michigan patterns repeated exactly: (i) the winner market priced the poll
leader far above what the polls justified (95% on a 38%-with-1/3-undecided
plurality — a boring poll model says maybe 60–80%); (ii) the margin brackets
put the modal outcome at a double-digit Hong blowout (≥9% ≈ 75c of combined
mass) in a race that ended −0.4% for her.

**VoteHub check (hits-pipeline ground truth, `created_at` < any pre-event D):**
`api.votehub.com/polls` carries **zero WI Democratic-primary head-to-head
polls** — no `gubernatorial-primary` type exists; the only Hong/Crowley rows are
*general-election* matchups vs Tiffany (Marquette 07-23: Hong 44–44; RMG 07-30:
Hong 44–43, Crowley 46–43). The "~10-point poll lead" was visible only in press
coverage of Marquette, not in our structured poll feed. And unlike Michigan,
**the market was NOT more right than the polls here**: polls implied a genuinely
uncertain race (leader ~38%, 1/3 undecided, a public internal claiming wide-open);
the market's 95.3% was overconfidence in the poll leader, and the poll-implied
read (Crowley worth ~15–25c, not 5c) was the better forecast.

## 2. Election night, minute by minute (UTC; polls closed 01:00 = 8pm CT)

From the tick tape (18,185 prints / 5.23M contracts on FHON inside the window):

| UTC (Aug 12) | Hong `FHON` | Crowley `DCRO` | note |
|---|---|---|---|
| 00:00–01:00 | 94–96 | 4–6 | pre-close plateau |
| 01:00–01:10 | 94–96 | 4–19 | first returns |
| 01:11:59 | **first ≤90** | — | collapse begins 12 min after poll close |
| 01:12:54 / 01:13:03 | **first ≤85 / ≤80** | ≥10 at 01:12 | R4 rungs 1–2 fill here |
| 01:19:45 | **first ≤75** | ≥25 at 01:19 | R4 rung 3 fills; ladder complete by 01:19:50 |
| 01:25–01:32 | 50 → 25 | 51 → 75 | straight through the ladder zone |
| 01:58:46 | **trough 13c** | 87–88 (01:45–02:00) | first leg down done |
| 02:17–02:29 | bounce 47→**79** | **crash 88→26; 3.0c flash print 02:24** (100k contracts that minute) | mid-count whipsaw — Milwaukee vs outstate |
| 02:30–03:45 | chops 37–76 | chops 22–78 | two hours of genuine uncertainty |
| 03:40–04:12 | second collapse → 12–15 | first ≥90 04:12:33 | |
| 04:10–06:10 | 5–19 | 80–94 (44c flash low ~05:00) | Hong briefly 19 at 06:10 |
| 06:15–06:30 | **0.2–0.5** | **99.7** | race called |
| 11:18 / 11:23 | closes / settles **NO** | settles **YES** | Hong NO, Crowley YES |

The shape vs Michigan: Michigan was 98.5 → 74 → 100 (dip-and-revert). Wisconsin
was 95.3 → 13 → **79** → 0 (dip, *partial* revert to 79, then die). Both
candidates' books whipsawed violently mid-count — Crowley printed 88, then 3c,
then 100, inside five hours. A dip-buyer had no way to tell 01:20's wick from
Michigan's; the counterparties selling Hong at 80 in minute 13 of the count were
right this time.

## 3. Frozen-strategy replay (read-only; exact engine, frozen params)

### Gates — the episode DOES qualify and the night IS admitted
- Hong's 24h pre-window time-weighted avg = **95.34c** (coverage 24h) — clears
  the frozen ≥95c gate *by 0.34c*. Window (frozen anchor rule, max-volume hour
  −6h/+24h): Aug 11 19:00 → Aug 12 11:18 UTC, ET date 2026-08-11.
- 2026-08-11 is **not** in `KNOWN_EVENT_NIGHTS` (frozen list has 08-04 and
  11-03). Admission came from the cluster rule: **7 distinct qualifying events**
  on the date (WI gov-D Hong 95.3, WI-07-D Clark 95.0, CT-04-R 96.3, MN gov-D
  99.8, MN-07-D 98.4, MN-08-D 95.8, MN Sen-R 99.4). The discovery machinery
  finds the night without manual labeling. Crowley's own market (4.9c) never
  qualifies for anything — R4/R4b only ever trade the ≥95c favorite's book.

### R4 panic ladder — armed, filled, and killed: **−$499.50 (−100.0%)**
Engine replay (tick fills, maker queue, exact fees; `wisconsin_replay.json`):
all three rungs filled inside 7 minutes of the collapse — 196 @ 85c (01:12:54),
208 @ 80c (01:19:22), 222 @ 75c (01:19:45–50); 626 contracts, $499.50 deployed
(the 5%-bankroll cap), all maker, zero fees. The resting exit at pre-avg−2 =
93c was never touchable: the best print after the first fill was **88.0c at
01:13:31**; the later bounce topped at 79. No stop-loss by design → rode 79 →
0 → settled NO. **Total loss of deployed premium.** Depth was no excuse: 4.93M
contracts printed ≤85c in the window (vs Michigan's 715k).

The other night episodes (`night_replay.json`): WI-07-D Clark dipped to 48c and
reverted → R4 filled 114 contracts, **+$13.62**; the five MN/CT favorites never
dipped ≤85 (min 95–99.6) → ladders rested unfilled, $0. **Whole-night R4 total:
−$485.88.**

### R4b cheap-NO convexity — the ticket it needed was never for sale
On the one market where the NO lottery paid 20x+ (Hong NO: ~4.7c → 100), the
frozen entry (NO ≤ 3c, ioc when YES bid ≥ 97) **never triggered**: max YES bid
in the 24h entry window was 96.5c; only ~6.2k contracts printed at YES ≥97
(NO ≤3) all window — crumbs even for a resting order. R4b's only entry on the
night was MN gov-D at 1c, which bled to zero: **−$1.25**. So the strategy that
was *built* for "underdog spikes from nowhere" watched the biggest such spike
in the dataset from the sidelines, gated out by its own frozen 3c limit —
while its 25-of-26-tickets-bleed base case ticked one more bleed. The KILL
verdict on R4b stands reinforced from both directions.

### Hits-based forecaster screen — structurally blind to this event
The frozen screen anchors D = `close_time` − 10d. Pre-settlement this market's
`close_time` was **2026-11-03** (nominee markets carry the general-election
date; our own pre-event DB snapshot proves it), so ex ante the market never
entered the 7–14-day pre-resolution band before the primary — and its realized
close (08-12) also falls outside frozen window B (close ≤ 08-05). **The frozen
pipeline could not have seen this event, full stop.** That is a real coverage
gap: early-determination markets (every nominee market that resolves on a
public primary date months before `close_time`) are invisible to close-anchored
screening, even though the resolution date was public-calendar knowledge.

Counterfactual with the same criteria at D = effective-resolution − 10d
(Aug 2): FHON passes every candidate gate (96.1c ≥ 85c extreme, volume ≫ 2k,
lifetime ≫ 14d, top-volume market of its event; cheap side NO at ~3.9c pays
~25x). The trade rule fires iff forecast ≤ 81%. VoteHub supplies no primary
polls, so the dossier would have leaned on press retrieval (Marquette 38% +
1/3 undecided was public by D). A boring poll-based read of 60–80% clears the
15-point disagreement bar → buy NO ~4–5c → **~20–25x realized**. But that is a
*hindsight-assisted counterfactual through two unfrozen gates* (the D-anchor
fix and the Haiku screen verdict); it is evidence the hits mechanism *aims at*
exactly this shape, not evidence our system would have caught it.

## 4. Taxonomy update — the adverse-selection tail landed

Wisconsin adds 7 out-of-sample episodes to the panic census (frozen gates,
zero changes): 5 no-dip, 1 dip-and-revert (WI-07-D, min 48c), **1 dip-and-die
(Hong, min 0.1c)**.

| | pre-WI (rev 2) | + Wisconsin night |
|---|---|---|
| episodes | 136 | 143 |
| dips (≤85c) | 9 | 11 |
| dip-and-die | 1 | **2** |
| episode-level adverse selection | 11.1% | **18.2%** (2/11) |
| kill threshold (frozen) | 35% | not breached |

But the pre-registered 35% threshold is the wrong yardstick for R4's actual
payoff profile: wins pay +10.6–25.3% of deployed (median ~+18%), a die costs
−100%. Breakeven die-rate ≈ 0.18/1.18 ≈ **15%**. The observed episode-level
rate is now **18.2%**, and the cumulative simulated P&L across *every* R4 fill
ever run (train +$402.92, Michigan case study +$101.27, WI night −$485.88) is
**≈ +$18 on ~$3.1k total deployed — statistically zero**. One Wisconsin erased
five train wins plus Michigan, exactly the "one wrong dip-buy erases 3–8
winners" arithmetic in `docs/viability.md` §5. R4's PARK verdict survives on
the letter of the kill criteria; its economics no longer have a positive point
estimate. Any revival at the Nov midterms needs either (a) a live
count-vs-baseline model that can tell Milwaukee-still-out from
race-actually-lost (viability §4's adverse-selection warning, now measured),
or (b) an exit/payoff structure that isn't short-100-to-make-18.

## 5. Verdict — was there a-priori tradeable profit for OUR frozen rules?

**No.** Ex post the night was littered with 20x prints (Crowley YES 4.9c→100,
Hong NO 4.7c→100, Crowley-margin-0-3 5c→99, NO on Hong≥15% 62c→100). Ex ante,
under our frozen rules as actually coded: the only strategy that fired lost its
full stake (R4, −$499.50; −$485.88 for the night including the one small
revert win), R4b couldn't buy its ticket at the frozen 3c limit, and the hits
screen was structurally unable to look at the event. The a-priori edge that
*did* exist — visible in public polling by Aug 2 — was the **pre-event
forecasting expression**: the market held a 38%-plurality-1/3-undecided poll
leader at 95c and her double-digit-blowout brackets at 75c of mass; fading that
overconfidence (favorite-NO / underdog-YES / tight-margin brackets at extreme
prices) is the P-4/hits family, whose mechanism this event now demonstrates for
the *second* time (Michigan margins, Wisconsin winner+margins) but whose edge
remains unproven (1 documented hit, n far below the graduation bar) and which
our frozen implementation would have missed for a mundane engineering reason
(close-time anchoring). Wisconsin's real lesson is the one the census was built
to measure: election-night dips are not free money — the market's excursions
carry exactly the informed flow the Michigan write-up warned about, the
dip-and-die rate is now at R4's economic breakeven, and the two strategy
families this repo froze — buy-the-panic and fade-the-extreme — just gave
opposite-signed answers on the same night. The night was a huge profit
opportunity for a *forecaster*; it was a wipeout for a *dip-buyer*; and we
currently have proof of neither edge in our own hands.

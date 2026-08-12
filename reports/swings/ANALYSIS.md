# Swing census + SwingFade — is the general "big temporary swings are tradeable" hypothesis true?

**2026-08-12.** Code: `bot/strategies/swings/` (census.py scan, strategy.py SwingFade,
run_backtest.py, 33 unit tests incl. engine-level strategy tests, all passing).
Engine: `bot/backtest/` exact per-series fees, maker queue ON, tick quantization.
Outputs: `reports/swings/{census.json, episodes.jsonl.gz, train_cells.json, train_results.json}`.
Hypothesis under test (Chris): prediction markets have semi-frequent large temporary
swings that are tradeable — *beyond* the narrow panic case (`reports/panic/ANALYSIS.md`:
>=95c favorites entering scheduled event nights).

## 0. Discipline

- **Census definitions frozen before any strategy run** (census.py module docstring):
  swing = move of >=X cents within T hours from a sliding-window reference extreme,
  X in {10,15,20,30}, T in {1,6,24}; honesty gates = >=$500 traded notional in the
  trigger window + two-sided book at ref and trigger; cooldown = one displacement,
  one episode. Reversion at U in {6,24,72}h + by settlement. Fade depths {0,5,10}c,
  exit at the 50% retrace, no stop-loss.
- **Shortlist rule registered inside census.py before results** (`SHORTLIST_RULE`):
  cells ranked by revert-rate x frequency x capturable-$, gated at
  settled-with-move <=35%, n>=30, spread <=10c.
- **Strategy parameters frozen from TRAIN-window census structure only**
  (`strategy.py` PARAMS_RATIONALE; train = episodes before t_split = **2026-03-20**,
  the 60th percentile of episode times — same episode-order convention as panic).
  The held-out 40% has never been run by the strategy; `arm_before=t_split` makes a
  train run structurally unable to open positions in it.
- Tail cells (Chris's two named shapes) were added to the census output **before**
  the full run: `panic_dip` (down-swing from ref>=90c) and `spike_fade` (up-swing
  from ref<=15c), plus `mid_range` (30-70c) as the comparison group.

## 1. Census headline — swings are frequent, and they revert

Scan: 47,628 settled markets, 3.07M hourly candles, 2021-07-17 -> 2026-08-11
(265.8 calendar weeks, ~18,932 covered market-weeks). **510,771 episodes** total
(an episode can appear in several (X,T) configs by construction; per-config rows
are independent scans).

| config | episodes | /calendar-wk | revert50 <=24h | revert50 by settle | **settled WITH move** | d0 fade median (gross) |
|---|---:|---:|---:|---:|---:|---:|
| X10_T1  | 35,131 | 132 | 70.0% | 72.6% | 58.8% | +1.0c |
| X10_T6  | 68,331 | 257 | 73.6% | 77.3% | 54.3% | +2.0c |
| X10_T24 | 90,130 | 339 | 73.8% | 78.5% | 53.4% | +2.5c |
| X15_T6  | 50,002 | 188 | 69.5% | 72.8% | 58.5% | +3.0c |
| X20_T6  | 38,032 | 143 | 66.0% | 68.8% | 62.7% | +3.0c |
| X20_T24 | 52,589 | 198 | 67.0% | 70.7% | 60.4% | +4.5c |
| X30_T1  |  9,586 |  36 | 58.2% | 59.9% | 72.1% | -3.0c |
| X30_T24 | 33,235 | 125 | 60.5% | 63.7% | 66.7% | +2.0c |

Read this carefully, because both halves of Chris's hypothesis live in it:

- **"Semi-frequent large swings": TRUE, and then some.** Even at the 30c/1h extreme
  there are ~36 liquid swings per week on our (partial) tape; 10-20c swings happen
  dozens-to-hundreds of times a week. Two thirds revert halfway within 24h.
- **"…and tradeable by fading": mostly FALSE, because the swings are information.**
  The *unconditional adverse-selection rate* — fraction of swing markets that settled
  on the move's side of the 50% retrace line — is **53-72%, rising with swing size**.
  The bigger and faster the move, the more likely it was right. A market that swings
  and reverts intraweek still, more often than not, *settles* where the swing pointed.
  Median gross fade P&L at instant-taker entry (d0) is a few cents at best and negative
  for large X; that's before fees and before the exit-fill haircut in §4.

The paradox "70% revert yet 55% settle with the move" is not a contradiction: paths
oscillate (you often get a 50% retrace exit *before* settlement), but money held to
settlement is on the wrong side. Trend-moves-that-were-information dominate
panic-overshoots overall.

## 2. The two tails vs the middle (Chris's named cells, first-class)

X10_T24, full tape (`census.json .configs.X10_T24.tail_cells`; train-only economics in
`train_cells.json`):

| cell (X10_T24, train window) | n | /wk | settled-with-move | d5 maker-fill EV/contract (gross) | d5 death rate | median capturable @25% | spread@trigger | t-to-50% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **pol_panic_dip** (Pol+Elec, ref>=90, down) | 621 | 2.5 | **11.3%** | **+7.6c** | 11.4% | 1,192 ct | 8c | 3h |
| **pol_spike_fade** (Pol+Elec, ref<=15, up) | 969 | 4.0 | **29.8%** | +2.9c | 19.0% | 1,612 ct | 7c | 3h |
| **eco_spike_fade** (Econ, ref<=15, up) | 397 | 1.6 | **12.1%** | **+7.2c** | 12.3% | 1,149 ct | 7c | 3h |
| weather_spike_fade | 8,907 | 36.5 | 29.2% | **-2.3c** | 25.7% | 1,732 ct | 6c | 2h |
| weather_panic_dip | 900 | 3.7 | 28.9% | **-6.8c** | 35.7% | 853 ct | 14c | 1h |
| mid_range (all categories, 30-70c) | 24,573 | 100.8 | **66.5%** | +4.3c | 25.2% | 1,753 ct | 5c | 3h |

Chris's tail intuition is **directionally right about adverse selection**: both tails
carry 3-6x lower settled-with-move than the mid-range. When a >=90c favorite dips or a
<=15c longshot spikes, the pre-swing consensus usually survives. And it is right that
these are the only census cells that pass the pre-registered shortlist gates.

But the money-weighted census answer splits the tails by category:

- **Weather (the volume king: 36 spike-fades/week) is EV-negative in both tails.**
  Weather swings are forecast updates — information, not panic. Not tradeable, full stop.
- **Politics/Elections + Economics tails are census-EV-positive** (+3 to +8c/contract
  gross at maker depth) with 11-30% adverse selection. These three cells — and only
  these — were frozen into SwingFade. (Yearly cuts are stable-positive 2024-2026,
  noisy/thin before.)
- The mid-range's superficially positive +4.3c median is a path artifact: 66% of
  those markets settle with the move; the census's optimistic exit assumption (§4)
  is what keeps it positive on paper. It fails the 35% gate by miles — correctly.
- Spike-fade sell-side entries sit in the tapered 0.1c tick zone; the integer-cent
  harness makes those entries strictly conservative (dataport rounding is against us).

## 3. Tradeability ("price swung" vs "you could have been filled")

From the census (train window, X10_T24 frozen cells): median **capturable volume at
the d5 rung ~1,100-1,600 contracts** per episode at the 25% cap (comfortably above the
~$500 harness per-market cap — capacity is not the binding constraint at our size);
**spread at trigger 7-8c** (wide: the d0 taker entry is EV-negative in *every* cell —
paying the spread to chase is a losing trade everywhere); **median time to 50%
retrace 3h** (the opportunity is hours-long, not seconds — median engine opportunity
lifetime 3600s, nowhere near the <5s colocated-bot flag); census d5 print-through
fill rates 47-61%, engine maker-queue order fill rate **41%** (inside the SPEC 40-50%
sanity band).

The brutal part is the **exit**, quantified in §4: reversion of the *mid* is not
reversion you can sell into.

## 4. SwingFade train backtest (exact fees, maker queue, per-market $10k)

875 train markets (union of the three cells' census markets; strategy re-derives all
triggers point-in-time), 1,182 arm events, 478 markets entered, fresh $10k bankroll
each, harness caps (~$500/market). `train_results.json`.

| cell | entered | hit rate | **adverse selection** | net P&L | EV/entered | fee x1.5 net |
|---|---:|---:|---:|---:|---:|---:|
| pol_spike_fade | 279 | 71% | **38.4%** | **-$5,625** | -$20.16 | -$5,718 |
| pol_panic_dip | 171 | 71% | **38.6%** | **-$2,399** | -$14.03 | -$2,444 |
| eco_spike_fade | 90 | 81% | 24.4% | **+$562** | +$6.24 | +$485 |
| ALL (cells overlap) | 478 | 75% | 31.6% | -$3,449 | -$7.21 | -$3,642 |

Shape: winners are many and small (n=359, avg **+$123**); losers are few and total
(n=119, avg **-$400**, mostly full loss of the deployed cap). Fees are immaterial
($397 total; Politics/Elections charge zero maker fees, Economics maker fees are cents).

**Why the engine is so much worse than the census cells (+7.6c/contract -> -$14/market):**

1. **The exit is the mirage.** Of 151 adverse entered markets, 99 rode to settlement
   with zero exit fills — and in **87 of those 99 the census says the mid DID cross
   the 50% retrace target within 72h**. The quote mid reverted; no trade ever printed
   at/through our resting exit, so the "reversion" was unsellable. The census's
   exit-at-mid-cross assumption (declared up front) flattered every cell; the engine's
   print-based fills are the honest accounting. This single mechanism converts
   census-positive cells into engine-negative ones.
2. **Per-episode EV is not per-market EV.** Markets re-arm after each closed episode
   (census cooldown semantics), so a market keeps fading until it hits the episode
   that kills it. Fill-weighted adverse selection (38%) is far above episode-weighted
   (11-30%): the deadly episodes are exactly the ones that print through the deep
   rungs and fill you in full.

## 5. Kill criteria (thresholds fixed in the brief before the run)

| criterion | threshold | pol_spike_fade | pol_panic_dip | eco_spike_fade |
|---|---|---|---|---|
| adverse selection | >35% -> kill | 38.4% **KILL** | 38.6% **KILL** | 24.4% pass |
| EV per entered | <=0 -> kill | -$20.16 **KILL** | -$14.03 **KILL** | +$6.24 pass |
| enough fills | too few -> park | 279 (ample) | 171 (ample) | 90 (ok) |
| fee stress x1.5 | <=0 -> kill | (already dead) | (already dead) | +$485 pass |
| concentration (SPEC §7) | top-5 <60% of profit | — | — | **FAIL** (top-5 wins $1.4k vs $562 net; 5 full losses of -$500 dominate the tail) |

**Verdict: KILL the general SwingFade in Politics/Elections (both tails).
KILL everything in Weather and the mid-range (never reached the strategy stage —
failed the census gates). PARK eco_spike_fade** — the only cell that passes
adverse-selection, EV and fee-stress gates — because its net edge (+$562 on 90
markets over ~4.7 years of train tape, ~0.4 fills/week) is thin, top-5-concentrated,
and one extra full-loss market flips it negative. Do not touch the held-out split
for it; revisit only if (a) the 2025 tape hole is backfilled and the cell stays
positive, and (b) exit fill realism improves (e.g. minute data for Economics
release windows).

## 6. Comparison to the narrow panic result

The panic study (reports/panic/ANALYSIS.md) found +$403 on train, 0% fill-level
adverse selection, and PARKED only for sample size (9 fillable episodes). This census
is the missing denominator for it: generalizing panic's shape to *any* >=90c
Politics/Elections market dipping >=10c in 24h — no scheduled-event window, no >=95c
24h-average qualification, rungs near the trigger instead of deep at 85/80/75 —
takes adverse selection from ~11% (episode level) to **38.6% at the fill level** and
flips EV negative. **Panic's narrowness is load-bearing**: the scheduled-event-night
condition (a known resolution moment where a deep wick is mechanically a panic, not
news arriving days early) plus deep rungs is what filtered information out of the
fade. The general hypothesis does not survive its own census; the narrow one remains
parked on its own terms.

## 7. Data caveats, stated plainly

1. **The tape is a sample, not the exchange.** Hourly candles exist for 47.6k of 238k
   settled markets: full history for weather (`KXHIGH*`), deep series for
   Politics/Elections/Economics, volume-filtered pulls elsewhere; many
   Politics/Elections markets have only final-15-days hourly coverage (pull phase d).
   Swings/week are therefore **lower bounds**, and the census is biased toward
   late-life (86% of X20_T24 episodes are within 48h of close). Sports was never
   swept historically (deliberate).
2. **Hourly grain misses intra-hour wicks.** Michigan's 3-minute 74c trough shows up
   here only as its hourly low. The panic study covers the minute-grain event-night
   subclass; this census cannot rule swings in/out *inside* an hour.
3. **Episode counts are per-config**; one physical displacement appears in up to 12
   (X,T) rows. Cross-config totals must not be summed. Within a config the cooldown
   guarantees no double-counting.
4. **Census fade accounting is optimistic by construction** (exit at mid-cross,
   fill on any print-through without queue) — which is exactly why the engine pass
   exists, and §4.1 measures the gap it creates (87/99 adverse rides had a
   mid-only "reversion").
5. 2025 is thin in the store generally (known pull gap, bot/data/NOTES.md); census
   year cuts for 2025 rest on fewer markets in some categories.
6. Marks are bid/ask mids where no trade printed (~46% of hourly candles per
   dataport convention; bot/data/NOTES.md: 54.3% of hourly candles carry a trade price) — the standard for this store; the notional and two-sided
   gates keep pure quote-drift out of the episode set.

## 8. The honest answer to Chris's question

**Are big tradeable swings semi-frequent?** Big swings: yes, absolutely — hundreds
per week even on our partial tape, and ~70% revert halfway within a day. *Tradeable*
swings: no, not in general. Most swings are information (53-72% settle with the move,
worse the bigger the move); the mean-reversion you see in the mid is often not
executable (no prints at the retrace); and honest maker-queue, print-based
accounting flips every Politics cell negative. Where exactly does anything survive?
Only in the extreme tails of already-decided markets — >=90c favorites and <=15c
longshots in Politics/Economics — and even there, only the narrow, condition-rich
panic variant (scheduled event night + deep rungs) or the parked Economics
spike-fade shows a real, small edge. The exchange's fast money is, on this evidence,
mostly right when it moves fast.

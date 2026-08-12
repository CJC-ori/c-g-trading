# P-2 Weather ground-truth trading — pipeline + backtest analysis

**Engineering agent output. §1 written 2026-08-11 (~23:45Z); §2–§4 rewritten
2026-08-12 (~01:30Z) on the FULL backfilled Kalshi data.** Code:
`bot/groundtruth/` (station map, ACIS + Open-Meteo clients, CLI parser, bias
fits, snapshotter) and `bot/strategies/weather/` (day-ahead + intraday
strategies, backtest runner). Data caches: `data/weather/` (gitignored via
the root-anchored `/data/` entry). Reports here regenerate with
`python -m bot.groundtruth.analyze_weather`,
`python -m bot.strategies.weather.run_backtest` and
`python -m bot.strategies.weather.run_backtest --grid`.

**Headline: the P-2 weather thesis is dead on full data.** The partial-data
run's small positive P&L was a coverage artifact; on 13× the market
universe the day-ahead variant loses the entire $10,000 bankroll (ruin on
2026-06-16, 25 days into a 50-day train window) and still loses the
probability score to the market with a 17× larger sample. Every one of the
12 sensitivity-grid cells fires the same kill criterion. Nothing is frozen
for the tournament.

Every number below is **train-split only** (first 60% of target dates,
2026-05-21..2026-07-09). The held-out 40% (2026-07-10..2026-08-10, 594
markets) has never been loaded into the engine.

**Harness note (changed since the previous run):** the runner now passes
`EngineConfig(fee_schedule=FeeSchedule.load_default())` — the exact
per-series centicent fee model, not the legacy per-contract-ceil path — and
inherits the engine's new maker queue-position model (enabled by default).
Both are strictly more honest than the previous run's settings. Neither
changed the verdict: see §2.5 for the attribution run.

---

## 1. Ground-truth findings (stable, full-data)

### 1.1 Station map — 20 city series, not 12

`settlement_sources[].url` on every KXHIGH* series carries the CLI issuing
office + location (`site=LOT&product=CLI&issuedby=MDW`), which resolves the
station exactly — no guessing from `rules_primary` needed. The research's
12 cities plus **BOS, HOU, LV, MIN, NOLA, OKC, SATX, SFO**. Traps
confirmed: Chicago = **Midway KMDW** (not O'Hare), Houston = **Hobby
KHOU** (not IAH), Dallas = **KDFW**, DC = **KDCA**, NYC = **KNYC Central
Park**. Full map with ACIS-verified coordinates: `bot/groundtruth/weather.py::STATIONS`
(cross-checked against the DB by `station_map_from_db`, tested).

### 1.2 Open-Meteo previous-runs API does NOT cap at 92 days

`past_days` up to **365 verified fully non-null** (tested 92/150/183/270/365
on 2026-08-11; a 120 attempt hit a proxy timeout, not an API limit). SYNTHESIS
§1 P-2's "~92-day window" bound is obsolete — the point-in-time forecast
window is ≥1 year. Consequence used here: **bias/σ were fit on
2025-08-12..2026-05-20, strictly disjoint from the backtest window.**

### 1.3 Ensemble variables

`ensemble-api` daily `temperature_2m_max` (local calendar day) maps 1:1 to
the settlement quantity. `gfs025` = 31 members, `ecmwf_ifs025` = 51 members,
poolable in one call. Hourly per-member `temperature_2m` also works
(suffixed `_member01.._ncep_gefs025`) but daily-max is the clean variable.

**Added 2026-08-12:** the *previous-runs* API also accepts `models=`, and
with two or more models it returns per-model suffixed variables
(`temperature_2m_previous_day1_gfs_seamless`, `..._ecmwf_ifs025`), fully
non-null over 365 days. This is the only point-in-time-safe way to get a
multi-model historical forecast and is what the §2.4 sensitivity grid uses
(`wx.PREV_RUNS_MODELS`). Two findings from it: Open-Meteo's default
`best_match` is **bit-identical to `gfs_seamless`** at all 20 US stations,
and `ecmwf_ifs025`'s 0.25° cell is badly displaced for coastal stations
(LAX daily maxima 89–93 °F in August vs GFS's 75–77 °F — an inland cell).
The backtest itself uses the previous-runs *deterministic* lead-1/lead-2
forecasts (the only point-in-time-safe historical product) with a Gaussian
whose σ is the measured station residual; the live snapshotter captures the
full ensemble daily.

### 1.4 Bias fits vs research's measured offsets

Pre-window fit (station − forecast, lead-1, n=282/city, °F). Research
compared archived model *analysis* to station on May–Aug 2026; ours is the
lead-1 *forecast*, so sign/magnitude agreement is the test (replication
column computed on the research's own window, grid−station convention):

| City | research grid−station | ours (lead-1, same window) | sign agrees |
|---|---|---|---|
| NYC KNYC | +1.54 | **+2.20** (n=97) | yes |
| Miami KMIA | −1.19 | **−1.69** | yes |
| LAX KLAX | −0.60 | **−0.90** | yes |
| Chicago KMDW | −0.58 | **+0.85** | **no — seasonal flip** |

Chicago's offset flips sign between the cool-season fit window (+1.61) and
summer (−0.9): a frozen per-city constant is systematically wrong by >2 °F
there. The runner therefore uses **walk-forward daily re-fitting** (pre-window
prior of strength 30 + settled window residuals through D−2 only — still
point-in-time). Pre-window per-city offsets/σ for all 20 cities:
`reports/weather/groundtruth.json::bias_fits` (lead-1 offsets range −1.1
(LV) to +2.7 (AUS); residual σ 2.3–3.4 °F; seasonal harmonic kept where it
beat the flat fit).

### 1.5 "Max already set by 4 PM" — measured (SYNTHESIS flagged unverified)

ACIS hourly obs (~i:51 LST, DST-corrected cutoffs, verified against IEM
ASOS) vs settled daily max, 2024-06-01..2026-08-10, n≈790/city:

- **Exactly set by 4 PM local: only ~18–35%** (PHX 0.18 … CHI 0.35). The
  strong form of the prior is **false**.
- **Within 1 °F of final by 4 PM: 63–86%.** (Hourly obs undersample the
  continuous max, so truth lies between the two columns.)
- Warm season is systematically later-peaking than cool season (e.g. LV
  warm 0.20 vs cool 0.53 exact).

Full table: `groundtruth.json::max_by_wall_hour` (also 5 PM / 6 PM
cutoffs). Consequence: the intraday variant cannot treat the 4 PM
max-so-far as final — it must price the remaining rise, which we did via
per-city empirical rise PMFs (`rise_pmf_4pm`, P(rise=0) ≈ 0.33–0.5,
P(rise≥2) ≈ 0.08–0.2).

---

## 2. Backtest on the FULL backfilled universe (train split only)

Setup unchanged from the previous run except where noted: maker-first
resting orders (weather series are `quadratic`, maker fee 0), threshold =
fee + buffer, `p_hat` = fair prob, harness Kelly/depth caps, fills replayed
against the actual trade tape (trades are the fill stream; hourly decision
clock synthesized from prints where the store lacks candles —
`bot/strategies/weather/dataport_ext.py`). **New:** exact centicent fee
schedule (`FeeSchedule.load_default()`) and the engine's maker
queue-position model, both on.

### 2.0 Coverage: before vs after the backfill

| | prior run (2026-08-11 23:45Z) | this run (2026-08-12 01:30Z) |
|---|---|---|
| KXHIGH* tickers with 60-min candles (all dates) | 18 | **7,986** |
| KXHIGH* tickers with trade tapes (all dates) | 451 (`pull_log` id=18) | **1,265** |
| In-window settled markets **usable** (candles or tape, target ≥ 2026-05-21) | 110 | **1,480** |
| … as a share of the 9,840 in-window settled listed markets | 1.1 % | **15.0 %** |
| Target dates in the usable universe | ≈70 | **82** (2026-05-21..2026-08-10) |
| Train split (first 60 % of dates) | 42 dates / 61 markets | **50 dates / 886 markets** (2026-05-21..2026-07-09) |
| Held-out 40 % (never loaded) | 28 dates | **32 dates / 594 markets** (2026-07-10..2026-08-10) |
| Cities with a trade in the train split | 8 | **18** |
| Brier-scored looks (pooled, deduped, spread ≤ 20¢) | 104 | **1,772** |

Composition also de-skewed: the prior universe was ~55 % LAX; the full one
is LAX 399 / NY 268 / MIA 228 / CHI 195 markets and 16 further cities
(1,480 total). LAX is now 27 % of the universe, not 55 %.

Still incomplete: candles cover 15 % of the in-window settled ladder, and
coverage is uneven per city (LAX 78 % of that city's strikes, NY 55 %, MIA
49 %, CHI 47 %, and 1–10 % for the 16 thin cities). The thin cities are
therefore represented by their most-traded strikes. That biases *toward*
liquid, well-priced strikes — i.e. against us if anything, but the direction
is not large enough to change any verdict below.

### 2.1 Day-ahead variant (lead-2 on D−1, lead-1 on D morning)

| Metric | partial data (110 mkts) | **full data (1,480 mkts)** |
|---|---|---|
| Net P&L (train, after fees) | +$83.66 | **−$10,000.00 — total bankroll ruin** |
| Fees paid | $0 (all maker) | $0 (all maker; exact fee model on) |
| Orders / contracts filled | 58 / 11,871 | 368 / 193,425 |
| Notional traded | — | $81,503 (net = −12.3 % of notional) |
| Simulated maker fill rate | 98 % ⚠ | 89 % ⚠ (still over SPEC §3's 60 % flag) |
| Max drawdown | $720 | **$11,107 = 100 % of peak equity** |
| Avg stated edge at entry | — | +17.8¢/contract (realised: **−5.2¢/contract**) |
| Calibration ECE (all strikes) | 0.095 | 0.094 |
| Fee stress ×1.5 | +$83.66 | −$10,000 (maker fee is 0 either way) |
| Capacity 1× / 3× / 10× depth | +$84 / −$378 / −$376 | −$10,000 / −$4,402 / −$4,071 |

**Ruin trajectory** (cash at the first decision of each day, from
`data/weather/backtest_logs/events_dayahead.jsonl`): $9,940 on 05-20 →
$4,307 on 05-22 → $1,642 on 05-23 → oscillates $700–$4,000 through May and
early June → $213 on 06-13 → **$0 from 06-16 onward.** The account is dead
for the last 24 of the 50 train dates, so the −$10,000 is a *floor*, not an
estimate: −100 % is the worst a fully-collateralised book can print, and P&L
after 06-16 is unmeasured. Two consequences: (i) the number understates how
bad the strategy is, and (ii) P&L comparisons between configurations below
are path-dependent noise — the Brier block is the signal.

**Three-number Brier block** (ours vs market on the same strike outcomes, first
scoreable look per (ticker, phase), books wider than 20¢ excluded):

| Scope | n | Brier ours | Brier market | Δ (market − ours) | 95 % CI | verdict |
|---|---|---|---|---|---|---|
| Pooled | 1,772 | 0.2057 | 0.1835 | **−0.0222** | [−0.0308, −0.0136] | market wins |
| Phase d1 (day before) | 886 | 0.2069 | 0.1882 | −0.0186 | [−0.0290, −0.0082] | market wins |
| Phase d0am (morning of) | 886 | 0.2046 | 0.1788 | −0.0258 | [−0.0395, −0.0121] | market wins |
| Traded subset | 368 | 0.2322 | 0.2055 | −0.0267 | [−0.0472, −0.0062] | market wins |

The point estimate improved slightly vs the partial run (−0.031 → −0.0222)
but the confidence interval tightened by a factor of ~3 and still excludes
zero by a wide margin. On the traded subset — the markets we actually chose,
where selection should help us most — we lose by *more* than pooled. The
partial-run finding that "d0am loses less than d1" **reverses** on full data
(d0am −0.0258 vs d1 −0.0186); it was sample noise.

**Calibration (all strikes, n=1,772, ECE 0.094)** — the mechanism of the loss:

| p̂ bin | n | mean p̂ | observed freq |
|---|---|---|---|
| [0.0,0.1) | 379 | 0.049 | **0.211** |
| [0.1,0.2) | 541 | 0.158 | 0.233 |
| [0.2,0.3) | 599 | 0.236 | 0.292 |
| [0.3,0.4) | 157 | 0.335 | 0.382 |
| [0.4,0.5) | 34 | 0.447 | 0.559 |
| [0.5,0.6) | 15 | 0.554 | 0.133 |
| [0.6,0.7) | 20 | 0.651 | 0.350 |
| [0.7,0.8) | 7 | 0.739 | 0.429 |
| [0.8,0.9) | 12 | 0.835 | 0.750 |
| [0.9,1.0) | 8 | 0.956 | **0.375** |

A textbook over-sharp distribution: every low bin under-predicts, every high
bin over-predicts. Our Gaussian is too narrow in the tails, so the strategy
systematically sells cheap "impossible" strikes that then settle YES 21 % of
the time. That is exactly the trade that produces +17.8¢ of *stated* edge and
−12.3¢ of realised P&L. Note this is **not** simply a σ-too-small problem in
the usual sense: the fitted pre-window σ (2.3–3.4 °F) is if anything *larger*
than the realised in-window forecast error sd (pooled 2.61 °F, per city 1.1–3.8).
The miss is in the shape — the residuals have fatter tails than a Gaussian —
plus the point-in-time staleness of a ≥24 h-old deterministic run.

**Per-city net P&L, full data** (train split; ordered by P&L):

| city | markets | dates | contracts | net P&L | partial-data P&L |
|---|---|---|---|---|---|
| KXHIGHTATL | 14 | 8 | 9,457 | +$1,520 | — |
| KXHIGHDEN | 2 | 1 | 954 | +$838 | — |
| KXHIGHTHOU | 2 | 2 | 1,255 | +$198 | — |
| KXHIGHTDAL | 2 | 2 | 1,724 | +$184 | — |
| KXHIGHTLV | 2 | 1 | 1,961 | +$50 | +$2 |
| KXHIGHTSATX | 2 | 1 | 1,718 | −$38 | — |
| KXHIGHTPHX | 6 | 3 | 3,200 | −$89 | — |
| KXHIGHPHIL | 1 | 1 | 1,000 | −$120 | — |
| KXHIGHTDC | 1 | 1 | 1,000 | −$170 | — |
| KXHIGHTSEA | 5 | 5 | 2,783 | −$250 | +$13 |
| KXHIGHTBOS | 7 | 6 | 4,095 | −$271 | −$7 |
| KXHIGHTMIN | 1 | 1 | 769 | −$500 | −$3 |
| KXHIGHCHI | 56 | 25 | 25,855 | −$750 | −$12 |
| KXHIGHNY | 49 | 25 | 29,290 | −$759 | −$201 |
| KXHIGHTSFO | 11 | 10 | 5,243 | −$926 | — |
| KXHIGHMIA | 57 | 27 | 34,053 | −$1,378 | +$76 |
| KXHIGHAUS | 19 | 14 | 11,583 | −$1,533 | — |
| **KXHIGHLAX** | 92 | 30 | 57,485 | **−$6,008** | **+$216** |
| **total** | 329 | 33 | 193,425 | **−$10,000** | +$84 |

**LAX flips from the single profitable city (+$216) to the single worst
loser (−$6,008)** once its full strike ladder (399 markets vs a handful) is
present. 13 of 18 cities lose; the five winners are all ≤14 markets on ≤8
dates. There is no city-level survivor.

### 2.2 Intraday 4 PM variant (max-so-far + rise PMF)

| Metric | partial data | **full data** |
|---|---|---|
| Net P&L (train) | −$638.16 | **−$6,270.85** |
| Orders / contracts filled | — | 140 / 46,903 |
| Simulated maker fill rate | 100 % ⚠ | **37 %** (inside SPEC's 40–50 % band region, no flag) |
| Brier pm4 | n=61: ours 0.106 vs 0.0023, Δ −0.104 | **n=886: ours 0.1215 vs 0.0248, Δ −0.0967 [−0.1108, −0.0825]** |
| Brier traded subset | n=10: ours 0.343 vs 0.013 | n=140: ours 0.2496 vs 0.1453, Δ −0.1043 [−0.1572, −0.0513] |
| Calibration ECE | — | 0.055 |
| Capacity 1× / 3× / 10× | — | −$6,271 / −$5,563 / −$5,288 |

The verdict is unchanged and now overwhelming (n=886 instead of 61). The
market's own 4 PM Brier is **0.0248** — by late afternoon the book has
essentially resolved the outcome (live obs plus the ~4:32 PM CLI
intermediate are public), and our ACIS-hourly reconstruction of the 4 PM
max-so-far is strictly *less* informed than the price. The "trade the
already-determined max" thesis inverts: the determination is in the price
before we can act on it. Note this is the one variant whose fills got
markedly more honest under the queue model (100 % → 37 %) and it still loses
$6.3k.

### 2.3 Capacity — measured against the market, not against our bankroll

Kill criterion (d) as coded measures **our own filled premium per day per
city**, which on a $10,000 bankroll (and, after 06-16, a dead account) is
bounded by our sizing, not by the market. Day-ahead deployed $137/day/city;
intraday $28. Both are under the $200 bar, so (d) "fires" — but that number
answers the wrong question. Measuring the market directly, from the 60-min
candle tape over the train window (covered strikes only, so a lower bound):

| city | city-days | median traded $/day | 25 % depth cap |
|---|---|---|---|
| KXHIGHLAX | 50 | $88,077 | $22,019 |
| KXHIGHNY | 48 | $63,136 | $15,784 |
| KXHIGHMIA | 50 | $35,308 | $8,827 |
| KXHIGHCHI | 47 | $35,056 | $8,764 |
| KXHIGHDEN | 5 | $31,282 | $7,820 |
| KXHIGHAUS | 18 | $14,274 | $3,568 |
| KXHIGHTSFO | 20 | $12,331 | $3,083 |
| KXHIGHTSEA | 19 | $8,374 | $2,093 |
| KXHIGHTATL | 17 | $7,704 | $1,926 |
| KXHIGHTPHX | 16 | $5,427 | $1,357 |
| KXHIGHTDAL | 12 | $5,427 | $1,357 |
| KXHIGHTBOS | 11 | $6,115 | $1,529 |
| KXHIGHTMIN | 5 | $1,719 | **$430** (thinnest measured) |
| **all cities pooled** | 342 | **$32,776** | **$8,194** |

Even the thinnest city clears the $200/day/city bar by 2×, and the four
liquid cities clear it by 40–110×. **The weather ladder has ample depth; the
strategy has no edge.** Criterion (d) is resolved as a bankroll/sizing
artifact of the runner's own metric, in both the partial and the full run.

### 2.4 Sensitivity grid — edge buffer × forecast source (TRAIN ONLY)

`python -m bot.strategies.weather.run_backtest --grid` →
`reports/weather/sensitivity_dayahead.json`. Forecast sources come from the
previous-runs API's per-model variables (`models=gfs_seamless,ecmwf_ifs025`,
verified 2026-08-12 to return fully non-null suffixed series); bias offsets
and σ are refit per source, so each cell is self-consistent and
point-in-time. `best_match` is included as the baseline and turns out to be
**bit-identical to `gfs_seamless`** at every US station — a free validation
that the plumbing selects what it claims to.

| forecast source | buffer | net P&L | contracts filled | markets traded | Brier ours | Δ (mkt − ours) | 95 % CI | ECE | kill (b) |
|---|---|---|---|---|---|---|---|---|---|
| gfs | 1¢ | −$10,000 | 174,718 | 315 | 0.2057 | −0.0222 | [−0.0308, −0.0136] | 0.094 | FIRED |
| gfs | 2¢ | −$10,000 | 193,425 | 329 | 0.2057 | −0.0222 | [−0.0308, −0.0136] | 0.094 | FIRED |
| gfs | 3¢ | −$10,000 | 176,991 | 312 | 0.2057 | −0.0222 | [−0.0308, −0.0136] | 0.094 | FIRED |
| ecmwf | 1¢ | −$5,413 | 332,809 | 554 | 0.2103 | −0.0268 | [−0.0359, −0.0178] | 0.109 | FIRED |
| ecmwf | 2¢ | −$5,329 | 325,833 | 527 | 0.2103 | −0.0268 | [−0.0359, −0.0178] | 0.109 | FIRED |
| ecmwf | 3¢ | −$10,000 | 237,750 | 396 | 0.2103 | −0.0268 | [−0.0359, −0.0178] | 0.109 | FIRED |
| **both** | **1¢** | **−$4,722** | 385,783 | 625 | **0.2033** | **−0.0198** | [−0.0278, −0.0119] | **0.088** | FIRED |
| both | 2¢ | −$10,000 | 218,367 | 344 | 0.2033 | −0.0198 | [−0.0278, −0.0119] | 0.088 | FIRED |
| both | 3¢ | −$10,000 | 209,442 | 335 | 0.2033 | −0.0198 | [−0.0278, −0.0119] | 0.088 | FIRED |
| best_match | 1¢ | −$10,000 | 174,718 | 315 | 0.2057 | −0.0222 | [−0.0308, −0.0136] | 0.094 | FIRED |
| best_match | 2¢ | −$10,000 | 193,425 | 329 | 0.2057 | −0.0222 | [−0.0308, −0.0136] | 0.094 | FIRED |
| best_match | 3¢ | −$10,000 | 176,991 | 312 | 0.2057 | −0.0222 | [−0.0308, −0.0136] | 0.094 | FIRED |

Readings:

- **Every cell loses money and every cell fires kill criterion (b).** The
  best pooled Δ in the grid (−0.0198, GFS+ECMWF consensus at 1¢) still has a
  CI entirely below zero. The kill is not a configuration artifact.
- The edge buffer does not move the Brier at all (it gates *trading*, not
  `p_hat`) — identical Δ within each source, exactly as it should be. Its
  effect on P&L (−$4,722 at 1¢ vs −$10,000 at 2–3¢ for "both") is pure ruin
  path-dependence, not a signal: a wider buffer trades less, hits ruin on a
  different date, and stops. Do not read the buffer column as tuning.
- The multi-model consensus is genuinely the best *forecast* (Brier 0.2033,
  ECE 0.088, vs 0.2057/0.094 for GFS alone and 0.2103/0.109 for ECMWF
  alone) — averaging two models helps, as expected. It is not remotely
  enough: it closes ~11 % of the gap to the market.
- ECMWF's 0.25° grid cell for coastal stations is badly displaced (its LAX
  daily maxima run 89–93 °F in August against GFS's 75–77 °F). The
  walk-forward bias offset absorbs the ~13 °F level error, which is why the
  ECMWF cells still trade at all, but its worse ECE (0.109) shows the
  residual scatter is larger.

**Nothing is frozen for the tournament.** The task's condition ("if
day-ahead survives on full data") is not met. For the record, the
best-by-train cell is `--ensemble both --buffer 1`; if the tournament
protocol requires an entry to exist, that is the config to use, and its
held-out result should be read as a confirmatory kill test, not as a
strategy. The runner's defaults are deliberately left at
`--ensemble best_match --buffer 2` so the reported baseline reproduces.

### 2.5 Attribution — was it the data or the harness?

The prior run used the legacy fee path and the pre-hardening maker fill
model. To separate "full data changed the answer" from "the honest harness
changed the answer", the day-ahead variant was re-run on the **full** data
with the **legacy** settings (`fee_schedule=None`,
`MakerQueueConfig(enabled=False)`):

| | legacy harness, full data | honest harness, full data |
|---|---|---|
| Net P&L | −$10,000 (ruin) | −$10,000 (ruin) |
| Contracts filled | 184,051 | 193,425 |
| Maker fill rate | 90 % | 89 % |
| Brier pooled Δ | −0.0222 [−0.0308, −0.0136] | −0.0222 [−0.0308, −0.0136] |
| Capacity 3× / 10× | +$5,828 / +$5,991 | −$4,402 / −$4,071 |

**The data changed the answer, not the harness.** The Brier block is
identical to the last digit (it scores model vs market and never touches
fills), and both harnesses reach ruin at 1×. The only place the hardening
bites is the capacity curve, where the legacy every-print fill model turns a
$4.4k loss at 3× depth into a $5.8k *profit* — a good illustration of why
the queue model was needed. Fees are $0 in every weather run (all fills are
maker on `quadratic` series), so wiring the exact centicent schedule changed
no weather number; it matters for P-1's taker variants.

Day-ahead's maker fill rate stays at 89 % even with the queue model on,
above SPEC §3's 60 % flag. That is arguably defensible here — our orders are
tiny relative to a ladder trading $33k/city-day, the median opportunity
lifetime is 5 h (p25 2 h, p75 16 h) and the mean holding period is 29 h, so
there is a lot of tape to be filled by — but it still biases P&L *upward*,
which only strengthens the kill reading.

### 2.6 Kill-criteria verdicts — partial vs full data

| Criterion | partial-data verdict | **full-data verdict (day-ahead)** | **full-data verdict (intraday)** |
|---|---|---|---|
| (a) post-fee P&L ≤ 0 over ≥60 days × ≥6 cities | not fired (P&L > 0; 42 days) | **not formally fired** — P&L is −$10,000 across 18 cities but only 33 traded days, short of the 60-day bar. Direction is unambiguous: total ruin. | same: −$6,271, 33 days, 15 cities |
| (b) fails to beat market Brier | FIRED (Δ −0.031, n=104) — *flagged as a possible coverage artifact* | **FIRED, and NOT a coverage artifact.** n went 104 → 1,772 (17×), LAX share 55 % → 27 %, and Δ = −0.0222 with CI [−0.0308, −0.0136]. All 12 grid cells fire it too. **Resolved.** | **FIRED** (Δ −0.0967, n=886, CI [−0.111, −0.083]) |
| (c) P&L concentrated in one city/regime | not fired (LAX 71 % of positive P&L, borderline) | **not fired, for a degenerate reason** — nothing is concentrated because almost everything loses (13 of 18 cities negative; the top positive city is ATL at 54 % of the small positive pool). The partial-data concern that LAX carried the strategy is resolved in the harshest way: LAX is the biggest loser. | not fired (losses everywhere) |
| (d) < $200/day/city deployable | FIRED (~$9/day/city) — *flagged as a coverage artifact* | **FIRED as coded ($137/day/city) but the criterion is measuring us, not the market.** Direct tape measurement: median $32,776 traded per city-day, $8,194 at the 25 % depth cap, and even the thinnest city clears $430. **Resolved: this is a bankroll/sizing artifact, not a market-capacity finding.** Capacity is real; edge is not. | FIRED as coded ($28/day/city); same resolution |

**Verdict: P-2 weather is killed.** Both variants lose decisively to the
market's own probabilities on a 17×-larger sample, across all four forecast
sources and all three edge buffers tried, with the day-ahead variant
reaching bankroll ruin halfway through the train window. The two verdicts
the prior agent flagged as possibly coverage-driven are now resolved in
opposite directions: **(b) survives the coverage fix and is the real kill**;
**(d) does not survive it and should be disregarded** — the market has
ample depth.

### 2.7 What would (still) change the verdict

Ordered by expected value, and all of them are *new research*, not tuning:

1. **Tail shape, not σ.** The calibration table is the whole story: 5 %
   forecasts settle YES 21 % of the time. The fitted σ is already slightly
   *wider* than the realised in-window error sd (2.3–3.4 °F fitted vs 2.61 °F
   pooled realised), so simply inflating σ is not the fix — the residual
   distribution has fatter tails than a Gaussian and probably heteroskedastic
   ones. A Student-t or empirical-residual-kernel likelihood, conditioned on
   a volatility proxy (ensemble spread, frontal passage), is the honest
   next model.
2. **Live ensemble spread per day.** `bot.groundtruth.snapshot` is already
   capturing gfs025 (31 members) + ecmwf_ifs025 (51 members) daily; a
   per-day spread-driven σ is only testable forward, since no historical
   per-member previous-runs product exists. The grid above is the closest
   backward-testable proxy and it moved Δ by only +0.0024.
3. **A longer window is NOT available in the obvious direction.** The DB now
   holds KXHIGH candles back to 2024-10 (7,986 tickers), which is tempting —
   but the previous-runs API caps at `past_days=365`, i.e. 2025-08-12, and
   the bias/σ fit must sit strictly before the test window. The current
   split (fit 2025-08-12..2026-05-20, test 2026-05-21+) is already close to
   the maximum. A shorter fit window (e.g. fit through 2026-01-31, test
   2026-02-01..2026-08-10) would roughly triple the test universe at the cost
   of a weaker fit; worth doing if anyone revisits this thesis.
4. **Point-in-time handicap remains real, and remains a lower bound on the
   gap.** Previous-runs lead-1 is ~24 h stale at the morning-of decision; a
   live trader would use the overnight 00Z run. The measured Δ is therefore
   a *lower bound* on how badly this specific pipeline loses — but the gap
   would have to close by 0.022 Brier, roughly the entire benefit that the
   multi-model consensus delivered ten times over, for the thesis to live.

---

## 3. Live forward data (running)

`python -m bot.groundtruth.snapshot` captured (and can re-capture, cron-able)
into `data/weather/snapshots/2026-08-11T224737Z/`: all 20 cities' CLI
product texts (parsed max-so-far + time), ensemble daily-max members
(gfs025+ecmwf_ifs025), and Kalshi open markets + order books (240 books).
No historical order books exist anywhere — daily runs of this script are
the only way to ever have them.

## 4. Harness / data issues — status after the 2026-08-12 rerun

1. ~~**Engine uses the legacy per-contract ceil fee path**~~ — **fixed by the
   harness agent.** `EngineConfig.fee_schedule` now exists and the runner
   passes `FeeSchedule.load_default()`. No effect on any weather number
   (all fills are maker on `quadratic` series, fee 0); it matters for P-1.
2. ~~**Maker fill model has no queue position**~~ — **fixed by the harness
   agent** (`fills.MakerQueueConfig`, enabled by default). Effect measured
   in §2.5: intraday's fill rate dropped 100 % → 37 %; day-ahead's only
   98 % → 89 %, still above SPEC §3's 60 % flag, because our orders are tiny
   against a $33k/city-day ladder and rest for hours. Verdicts unaffected;
   the residual bias is in our favour, so the kill stands.
3. **Risk-layer depth pre-clamp** (`_recent_window_volume`, mean of last 3
   candles) still zeroes intents in quiet hours even when the order would
   rest for days, double-counting with the per-print fill caps. On the
   partial universe this drove criterion (d); on the full universe (d) is
   dominated by bankroll ruin instead (§2.3). Still worth fixing for other
   strategies.
4. **The runner's criterion (d) is mis-specified.** It measures our own
   filled premium per day per city, so it conflates market depth with our
   bankroll and our (lack of) edge — and it "fires" hardest exactly when the
   strategy is losing so much it cannot deploy. A depth criterion should be
   measured off the tape (as in §2.3), independent of the strategy's P&L.
   Left as-is here so the number stays comparable to the prior run; flagged
   for whoever owns the criteria.
5. **Data (resolved for this window).** Weather candles went 18 → 7,986
   tickers and trade tapes 451 → 1,265 markets, giving 1,480 usable
   in-window markets (15 % of the 9,840 in-window settled ladder) vs 110
   before. The remaining 85 % is mostly deep-OTM strikes on thin cities.
   Extending the window backwards is capped by Open-Meteo's
   `past_days=365` previous-runs limit (≈2025-08-12), not by Kalshi
   coverage — candles reach back to 2024-10. See §2.7 item 3 for the
   shorter-fit-window option.

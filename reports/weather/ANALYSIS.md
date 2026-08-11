# P-2 Weather ground-truth trading — pipeline + backtest analysis

**Engineering agent output, 2026-08-11 (~23:45Z).** Code: `bot/groundtruth/`
(station map, ACIS + Open-Meteo clients, CLI parser, bias fits, snapshotter)
and `bot/strategies/weather/` (day-ahead + intraday strategies, backtest
runner). Data caches: `data/weather/` (gitignored via the root-anchored
`/data/` entry). Reports here regenerate with
`python -m bot.groundtruth.analyze_weather` and
`python -m bot.strategies.weather.run_backtest`.

**Coverage caveat:** the Kalshi weather pull (concurrent data agent) was
still in progress at run time. This backtest ran on **110 settled
in-window markets with trade tapes (61 in the train split), heavily
LAX-weighted** — of ~9,500 eventually expected. Rerun the runner as
coverage grows; every number below is train-split-only (first 60% of
target dates, 2026-05-21..2026-07-12; the last 40% is untouched for the
tournament).

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

## 2. Backtest (train split only; partial universe — see caveat)

Setup: maker-first resting orders (weather = `quadratic`, maker fee 0),
threshold = fee + 2¢ buffer, p_hat = fair prob, harness Kelly/depth caps,
fills replayed against the actual trade tape (trades are the fill stream;
hourly decision clock synthesized from prints where the store lacks
candles — `bot/strategies/weather/dataport_ext.py`).

### 2.1 Day-ahead variant (lead-2 on D−1, lead-1 on D morning)

| Metric | Value |
|---|---|
| Net P&L (train, after fees) | **+$83.66** (fees $0 — all-maker) |
| Orders / contracts filled | 58 / 11,871 (order fill rate 98% ⚠) |
| Max drawdown | $720 |
| Brier pooled (n=104) | ours 0.1425 vs market 0.1119 → **Δ −0.031 [−0.057, −0.004] — market wins** |
| Brier traded subset (n=58) | ours 0.158 vs market 0.128 → Δ −0.030 [−0.063, +0.003] |
| Calibration ECE (all strikes) | 0.095 |
| Fee stress ×1.5 | +$83.66 (unchanged; zero maker fees) |
| Capacity 1×/3×/10× depth | **+$84 / −$378 / −$376** — negative at size |

Per-city net P&L ($): LAX **+216**, MIA +76, SEA +13, LV +2, MIN −3,
BOS −7, CHI −12, **NYC −201**. Ex-LAX the strategy loses money.

### 2.2 Intraday 4 PM variant (max-so-far + rise PMF)

| Metric | Value |
|---|---|
| Net P&L (train) | **−$638.16** |
| Brier pm4 (n=61) | ours 0.106 vs market **0.0023** → Δ −0.104 [−0.158, −0.050] |
| Traded subset (n=10) | ours 0.343 vs market 0.013 — catastrophic |

By late afternoon the market has effectively converged to the outcome
(live obs + the 4:32 PM CLI are public); our 4-PM-hourly-obs
reconstruction is *less* informed than the market for the rest of the
session, not more. The "trade the determined max" thesis inverts: the
determination is already in the price.

### 2.3 Kill-criteria verdicts (SYNTHESIS P-2, on train evidence so far)

| Criterion | Day-ahead | Intraday 4 PM |
|---|---|---|
| (a) post-fee P&L ≤ 0 over ≥60 days × ≥6 cities | not fired (P&L>0; 42 days so far) | P&L ≤ 0 but only 42 days — formally short of the 60-day bar; direction is clear |
| (b) fails to beat market Brier | **FIRED** (Δ −0.031, CI excludes 0) | **FIRED** (Δ −0.104) |
| (c) P&L concentrated in one city/regime | not fired (top city 71% of positive P&L — borderline; ex-LAX negative) | fired (losses everywhere; SEA/MIA dominate) |
| (d) depth < $200/day/city deployable | **FIRED** (~$9/day/city filled at 25% cap on this thin partial universe) | FIRED |

**Verdict as of this coverage: the day-ahead Gaussian-around-bias-corrected-
deterministic-forecast pipeline does NOT beat the market's own probabilities
(kill criterion b) and its small positive P&L is LAX-concentrated and
capacity-fragile. The intraday 4 PM variant is killed outright.** Honest
caveats in both directions:

- The universe is 55% LAX and misses most thin-city markets (trades pull
  incomplete) — criterion (d) especially is a data-coverage artifact, not
  yet a market measurement ($1.4M/day series turnover says capacity exists).
- Our point-in-time constraint (previous-runs lead-1 is ~24 h stale by the
  morning-of decision) handicaps us vs a live trader who would use the
  overnight 00Z run and the actual multi-model ensemble spread rather than
  a fitted Gaussian. The measured Δ is a lower bound in that sense.
- 98–100% maker fill rates exceed SPEC §3's 40–60% sanity band — resting
  at bid+1 for a market's whole life with no queue modeling is generous;
  real fills would be fewer and more adverse. This biases P&L *up*, so it
  strengthens, not weakens, the kill reading.

### 2.4 What would change the verdict

1. Full-universe rerun when the data agent finishes (same command).
2. Ensemble-spread σ per day instead of constant per-city σ (sharpness is
   where Brier is lost; our σ≈3 °F Gaussian is under-confident on calm
   days, over-confident on volatile ones).
3. Day-ahead-only entries (d1 beats d0am is false — d1 Δ −0.037 vs d0am
   Δ −0.025; both lose, but morning-of loses less; live 00Z-run data would
   help d0am most).

---

## 3. Live forward data (running)

`python -m bot.groundtruth.snapshot` captured (and can re-capture, cron-able)
into `data/weather/snapshots/2026-08-11T224737Z/`: all 20 cities' CLI
product texts (parsed max-so-far + time), ensemble daily-max members
(gfs025+ecmwf_ifs025), and Kalshi open markets + order books (240 books).
No historical order books exist anywhere — daily runs of this script are
the only way to ever have them.

## 4. Harness / data issues observed (reported, not fixed — per brief)

1. **Engine uses the legacy per-contract ceil fee path** (`fees.py` exact
   `FeeSchedule`/centicent model exists but `EngineConfig` has no
   `fee_schedule` field and `_fee_for` never calls it). For weather this
   only matters for taker fills (maker=0 either way); the per-contract
   ceiling overstates tail-strike taker fees ~2–6× vs the centicent model.
   Conservative, but P-1's taker variants will feel it.
2. **Maker fill model has no queue position**: a resting order at bid+1
   fills on any print at/through it (25%-capped). Our 98–100% fill rate
   trips SPEC §3's own >60% suspicion flag. Consider a
   probability-of-fill haircut or queue model for maker-heavy strategies.
3. **Risk-layer depth pre-clamp** (`_recent_window_volume`, mean of last 3
   candles) zeroes intents in quiet hours even when the order would rest
   for days — it double-counts with the per-print fill caps and largely
   drove criterion (d) above on thin cities.
4. **Data**: weather trades pull covered only 451 of ~9.5k in-window
   markets at run time (`pull_log` id=18); candles 18 tickers. Rerun
   `run_backtest` when `bot/data`'s pull completes. Trade tapes also
   reach back to 2024 weather markets, and previous-runs goes back ≥365
   days — a much longer backtest window than SYNTHESIS assumed is
   available once markets/candles are backfilled (needs a second
   previous-runs pull with larger `past_days` for a pre-2025-08 fit
   window).

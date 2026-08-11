# Ground-truth data sources that beat news aggregation

**Written 2026-08-11 by a research agent. Every load-bearing claim carries a URL.
Claims marked ⚠ are unverified or inferred.** All live probes were run from this
container on 2026-08-11 and the raw responses are quoted inline.

Audience: engineering agents building the bot in the next few hours. The
recommendations at the end are ordered so you can start coding from the top.

---

## 0. TL;DR — the five things that change what you should build

1. **Kalshi publishes its own ground-truth map.** `GET /trade-api/v2/series`
   returns, for all 12,659 series, a `settlement_sources` array with the exact
   agency name **and URL** Kalshi settles against. You do not have to guess which
   data source matters — Kalshi tells you. §1.

2. **Weather is the highest-value category, and it is not close.** Ground truth is
   a physics model you can query for free; settlement is a specific NWS station
   report you can also query for free; and there are ~12 cities × ~365 days × 12
   strikes ≈ **>4,000 independent resolutions per year**, versus ~40 for the whole
   2026 election calendar. That is the difference between a backtest with
   statistical power and an anecdote. §2.

3. **I measured a persistent, city-specific bias between the gridded weather model
   everyone uses and the NWS station Kalshi actually settles on** — NYC Central
   Park **+1.54 °F**, Miami **−1.19 °F**, LAX **−0.60 °F**, Chicago Midway
   **−0.58 °F** over 97 days. On markets with 1 °F-wide strikes, correcting this
   bias *is* the edge. §2.4.

4. **`fee_type` varies by series and some series charge maker fees.**
   `KXCPIYOY` and `KXFEDDECISION` are `quadratic_with_maker_fees`; weather and
   elections are plain `quadratic`. Any strategy premised on "rest limit orders,
   pay no fee" is invalid on CPI/Fed series. §6.2.

5. **A model-vs-market gap is usually a definition gap.** Silver Bulletin has
   Democrats at 57.3% for the Senate; Kalshi `CONTROLS-2026-D` trades at 48¢.
   That looks like a 9-point edge. It is mostly **not**: Kalshi settles on the
   party of the President pro tempore on Feb 1 2027, so a 50-50 Senate resolves
   *Republican*, and Kalshi's own seat-distribution market implies P(D≥51)=47%,
   almost exactly the 48¢ price. §3.5. **Reconcile the resolution rule before you
   ever treat a gap as edge.**

### Ranking: ground-truth quality × liquidity × mispricing likelihood

| # | Category | GT quality | Liquidity (measured) | Mispricing | Backtest n/yr | Verdict |
|---|---|---|---|---|---|---|
| **1** | **Daily city temperature** | **Very high** — deterministic NWP + exact station settlement | **~$1.4M/day** across 12 cities | **Medium-high** — measurable station bias; retail-heavy | **>4,000** | **Build first** |
| **2** | **Weekly/monthly econ prints** (jobless claims, CPI) | High — official nowcasts + component data | $181k/24h (CPIYOY); $6k/24h (claims) | Medium | ~64 (claims) + ~12 (CPI) | Build second |
| **3** | **Elections — downballot & derivative** (margins, turnout, seat counts) | Medium — polls are noisy; FEC spending is hard data | $0.6–8M cumulative per race | **High** — thin, neglected, definitional traps | ~40 (one shot) | High ceiling, low n |
| 4 | Elections — headline control markets | Medium | $21.7M cum (CONTROLH) | **Low** — priced by pros, consistent internally | 2 | Skip as primary |
| 5 | Fed decisions | Very high — but it *is* the futures market | $1M/24h (FEDDECISION) | **Very low** | ~8 | Skip; use as a sanity oracle |
| 6 | Entertainment / awards | Low-medium — no clean pre-release GT | Low | Medium | ~50 | Deprioritize |
| — | Sports | High | Highest | Very low | — | Skipped per brief |

---

## 1. The meta-discovery: Kalshi hands you the ground-truth map

`GET https://api.elections.kalshi.com/trade-api/v2/series` (unauthenticated, works
from this container) returns every series with these fields:

```
category, title, frequency, fee_type, fee_multiplier,
settlement_sources: [{name, url}, ...],
contract_terms_url, additional_prohibitions
```

Live probe, 2026-08-11 — 12,659 series returned in one call with `limit=1000`
(the endpoint ignores the limit and returns everything; note `/series/` with a
trailing slash 301-redirects, use `/series`):

| Category | Series |
|---|---|
| Sports | 3,176 |
| Entertainment | 2,500 |
| Politics | 2,145 |
| **Elections** | **1,553** |
| Financials | 803 |
| **Economics** | **620** |
| Mentions | 407 |
| Science and Technology | 299 |
| **Climate and Weather** | **293** |
| Crypto | 271 |
| Companies | 173 |
| World | 143 |
| Health | 96 |
| Commodities | 77 |
| Social, Transportation, Exotics, Education | 103 |

Example settlement sources pulled live:

```
KXHIGHNY      → NWS Climatological Report
                https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC
KXCPIYOY      → Bureau of Labor Statistics    https://www.bls.gov/cpi/
KXFEDDECISION → Federal Reserve               https://www.federalreserve.gov
CONTROLS      → Library of Congress           https://www.congress.gov/
SENATETX      → United States Congress        https://www.congress.gov/
```

**Action for the data layer:** pull `/series` once, persist it, and key every
strategy off `settlement_sources[].url`. This is the authoritative
market→ground-truth join and it costs one HTTP request.

Also pull `rules_primary` from `/markets/{ticker}` — it names the exact
measurement site. This is how I discovered Kalshi's Chicago market settles on
**Midway (KMDW), not O'Hare** (§2.3). Getting this wrong silently poisons a
whole strategy.

### 1.1 API gotchas that will cost you an afternoon

- **Field names are `*_dollars` and `*_fp`, not `volume`/`open_interest`.** A
  market object has `volume_fp`, `volume_24h_fp`, `open_interest_fp`,
  `liquidity_dollars`, `yes_bid_dollars`, `yes_ask_dollars`,
  `yes_bid_size_fp`, `yes_ask_size_fp`, `last_price_dollars`. Values are
  **strings**. My first liquidity sweep returned all zeros because I read
  `m["volume"]`, which does not exist. Verified against the full key list of a
  live market object.
- **`GET /markets` without `series_ticker` is useless for discovery.** Cursor
  paging returned 200,000 open markets across 200 pages and had not terminated;
  the ordering is dominated by multi-leg parlay series (`KXMVECROSSCATEGORY`,
  `KXMVESPORTSMULTIGAMEEXTENDED`), which have zero volume and zero OI. Always
  filter by `series_ticker`.
- **Candlesticks cap at 5,000 bars per request.**
  `/series/{s}/markets/{t}/candlesticks?start_ts&end_ts&period_interval` returns
  `400 {"details":"requested time range with candlesticks: 9600, max
  candlesticks: 5000"}` for a 400-day range at `period_interval=60`. Chunk your
  pulls.
- **Rate limiting is real.** Sustained per-series polling started returning
  `429` after ~25 requests in quick succession. Sleep ~0.5–1 s between calls.

---

## 2. Category 1 — Weather (build this first)

### 2.1 Why it wins

Every other category on Kalshi requires you to forecast *human* behavior with
noisy proxies. Daily temperature is the one category where the ground truth is
produced by a deterministic physical model that is (a) free, (b) queryable, (c)
archived so you can reconstruct what it said at any past moment, and (d) settled
against a published instrument reading rather than a judgment call.

And the sample size is decisive. **Weather gives you thousands of independent
resolutions per year; the 2026 midterms give you one night.** For a deterministic
backtest that must distinguish edge from luck, this is the whole ballgame.

### 2.2 Measured liquidity (live, 2026-08-11)

Daily high-temperature series, summed over the ~12 open strikes per city. Because
these markets open the morning before, `cumVol ≈ vol24h` — read the first column
as **turnover per city per day**:

| Series | City | cum vol ($) | vol 24h ($) | OI ($) | top-of-book YES bid ($) |
|---|---|---|---|---|---|
| KXHIGHLAX | Los Angeles | 645,115 | 627,699 | 381,368 | 127,960 |
| KXHIGHMIA | Miami | 156,799 | 151,988 | 100,066 | 53,731 |
| KXHIGHCHI | Chicago | 135,941 | 130,889 | 71,865 | 94 |
| KXHIGHTPHX | Phoenix | 70,422 | 67,046 | 45,903 | 171 |
| KXHIGHAUS | Austin | 67,646 | 64,433 | 51,321 | 34 |
| KXHIGHTATL | Atlanta | 64,687 | 62,591 | 54,117 | 17,607 |
| KXHIGHPHIL | Philadelphia | 51,521 | 47,495 | 28,502 | 2,764 |
| KXHIGHNY | New York | 48,431 | 37,406 | 35,353 | 46,553 |
| KXHIGHTSEA | Seattle | 41,113 | 37,716 | 25,060 | 35 |
| KXHIGHTDC | Washington DC | 40,843 | 39,556 | 23,126 | 5,444 |
| KXHIGHDEN | Denver | 27,947 | 26,227 | 21,811 | 96 |
| KXHIGHTDAL | Dallas | 24,835 | 21,924 | 19,554 | 27 |

**~$1.4M/day of turnover across the twelve.** Note the huge spread in
top-of-book depth: LAX/NYC/MIA/ATL show $17k–$128k resting on the YES bid,
while Chicago/Austin/Dallas/Seattle show under $100. Depth is bursty and must
be measured per market at decision time, never assumed. Fee model is plain
`quadratic`, `fee_multiplier=1` for all Climate and Weather series.

Additional weather series exist and are thinner: `KXRAINNYCM` (monthly NYC
precipitation, $22.5k cum), monthly snowfall series for ~12 cities
(`KXNYCSNOWM`, `KXBOSSNOWM`, `KXCHISNOWM`, …), `KXHEATAPHX` (weekly Phoenix
heat), `KXDROUGHTLEVEL` (settles on the U.S. Drought Monitor), `KXHURRICANE`
and `KXNAMEDSTORM` (settle on NOAA).

### 2.3 The settlement source — exact, and machine-readable

Kalshi settles daily temperature markets on the **NWS Climatological Report
(Daily)**, the "CLI" product. `rules_primary` for `KXHIGHNY-26AUG12-T90`, pulled
live:

> "If the highest temperature recorded in **Central Park, New York** for August
> 12, 2026 as reported by the **National Weather Service's Climatological Report
> (Daily)**, is greater than 90°, then the market resolves to Yes."

Kalshi's own `rules_secondary` explicitly warns that other sources disagree —
which is precisely the mispricing thesis:

> "Not all weather data is the same. While checking a source like AccuWeather or
> Google Weather may help guide your decision, the official and final value used
> to determine this market is the highest temperature as reported by the
> corresponding NWS Climatological Report (Daily)… Preliminary NWS reporting and
> measurement methods may be subject to underlying rounding and conversion
> nuances."

**Station mapping, extracted live from each series' `rules_primary`:**

| Series | Site named in the rules | Likely station ID ⚠ |
|---|---|---|
| KXHIGHNY | Central Park, New York | KNYC |
| KXHIGHCHI | **Chicago Midway, IL** | KMDW (**not** KORD) |
| KXHIGHAUS | Austin Bergstrom | KAUS |
| KXHIGHMIA | Miami International Airport | KMIA |
| KXHIGHLAX | Los Angeles Airport, CA | KLAX |
| KXHIGHPHIL | Philadelphia International Airport | KPHL |
| KXHIGHDEN | Denver, CO | KDEN |
| KXHIGHTDC / TPHX / TSEA / TATL / TDAL / TBOS / TLV / TMIN / TNOLA / TOKC / TSATX / TSFO / THOU | city name only in `rules_primary`; the series `settlement_sources[].name` disambiguates (e.g. "NWS Climatological Report Dallas FW", "NWS Climatological Report Chicago Midway") | ⚠ confirm each from `settlement_sources` before trading |

⚠ The station IDs are my inference from the site names; confirm each by
cross-checking the `settlement_sources[].url` CLI issuing office/location code.

**Live access — NWS product API** (no key; send a `User-Agent` per NWS policy):

```
GET https://api.weather.gov/products/types/CLI/locations          → 629 locations
GET https://api.weather.gov/products/types/CLI/locations/NYC      → product list
GET https://api.weather.gov/products/{uuid}                       → productText
```

Probe result: `locations/NYC` returned 14 products; the most recent two were
issued `2026-08-11T20:32Z` and `2026-08-11T06:27Z` — i.e. **CLI is issued at
least twice a day**, an intermediate report in the afternoon and a final one
after midnight local. The fetched text:

```
CLIMATE REPORT / NATIONAL WEATHER SERVICE NEW YORK, NY / 432 PM EDT TUE AUG 11 2026
...THE CENTRAL PARK NY CLIMATE SUMMARY FOR AUGUST 11 2026...
VALID TODAY AS OF 0400 PM LOCAL TIME.
WEATHER ITEM   OBSERVED TIME   RECORD YEAR NORMAL DEPARTURE LAST
 TODAY
  MAXIMUM         85    126 PM 102    1944  84      1       89
  MINIMUM         72    614 AM  56    1962  70      2       72
```

**This is a tradeable signal on its own.** The ~4 PM ET intermediate CLI publishes
the max-so-far *and the clock time it occurred*. On most summer days the daily max
occurs between 1 PM and 4 PM, so by 4:32 PM ET the day's outcome is close to
determined while the market still has ~7.5 hours of trading (last trading time is
11:59 PM ET per the `early_close_condition`). A parser over this product plus a
"probability the max is still exceeded after time T" model is the single most
concrete strategy in this document. ⚠ The claim that the max is usually set by
4 PM is a meteorological prior I did **not** verify; measure it from CLI history
before sizing.

⚠ Caveat: `api.weather.gov/products` retains only a rolling window (14 products
for NYC ≈ one week). It is a **live** source, not an archive. For history, use
ACIS (§2.5).

### 2.4 The measured edge: gridded model vs. settlement station

This is the finding to act on. I compared **Open-Meteo's archived model analysis**
of daily max temperature against **ACIS station observations** (the same values
the NWS CLI reports) for 2026-05-01 → 2026-08-05, n=97 days per city:

| City / station | n | bias (grid − station) | MAE | sd |
|---|---|---|---|---|
| NYC — KNYC Central Park | 97 | **+1.54 °F** | 1.67 | 1.29 |
| Chicago — KMDW Midway | 97 | −0.58 °F | 1.20 | 1.55 |
| LAX — KLAX | 97 | −0.60 °F | 0.87 | 0.96 |
| Miami — KMIA | 97 | −1.19 °F | 1.39 | 1.21 |

Read this carefully: for New York the gridded model runs **1.54 °F hotter** than
the Central Park thermometer Kalshi settles on, and the residual scatter around
that bias is only 1.29 °F. Kalshi's strikes are 1 °F apart. **A trader using a
weather app's number without station calibration is systematically wrong by more
than one strike in NYC.** Fitting a per-city bias (and ideally a seasonal one) is
cheap, mechanical, and directly monetizable.

Sources for the two legs:
- Grid: `https://historical-forecast-api.open-meteo.com/v1/forecast` (Open-Meteo,
  free, no key) — <https://open-meteo.com/en/docs/historical-forecast-api>
- Station: RCC-ACIS `StnData` (§2.5)

### 2.5 Forecast skill — how much uncertainty is actually left

Using Open-Meteo's **previous-runs API**, which serves the forecast *as it stood
N days earlier* — the point-in-time-safe source you need for an honest backtest:

```
GET https://previous-runs-api.open-meteo.com/v1/forecast
    ?latitude=..&longitude=..
    &hourly=temperature_2m,temperature_2m_previous_day1,
            temperature_2m_previous_day3,temperature_2m_previous_day5
    &temperature_unit=fahrenheit&timezone=America/New_York
    &past_days=92&forecast_days=1
```

Verified working. Daily-max error of the forecast issued N days earlier,
n=93 days ending 2026-08-11:

| City | MAE day-1 | sd day-1 | MAE day-3 | sd day-3 | MAE day-5 | P(\|err\|≤1 °F) day-1 |
|---|---|---|---|---|---|---|
| NYC Central Park (grid) | 2.14 | 2.83 | 3.23 | 3.62 | 4.50 | 0.42 |
| Chicago Midway (grid) | 2.50 | 3.22 | 3.68 | 4.53 | 4.45 | 0.27 |
| LAX (grid) | 1.99 | 2.40 | 2.94 | 3.16 | 2.82 | 0.29 |

(Runs for Miami / Austin / Phoenix timed out and were not completed — rerun them.)

⚠ Important: this compares Open-Meteo's own later analysis against its earlier
run, so it measures *model-vs-model* skill. The error against the **station** is
the convolution of this with the grid-station residual from §2.4. Budget roughly
sd ≈ √(2.8² + 1.3²) ≈ 3.1 °F for a day-ahead station forecast, after removing the
per-city bias. Measure it directly rather than trusting my arithmetic.

**Practical consequence:** with a day-ahead sd of ~3 °F against 1 °F strikes, a
single deterministic run is nowhere near enough — but it also means the market
genuinely has something to price, and a well-calibrated distribution is worth
real money. Use the **ensemble** endpoint for the distribution rather than
inventing a Gaussian:

```
GET https://ensemble-api.open-meteo.com/v1/ensemble
    ?latitude=..&longitude=..&daily=temperature_2m_max&models=gfs025
    &temperature_unit=fahrenheit&timezone=America/New_York&forecast_days=3
```
Verified working; returns `temperature_2m_max_member01..NN` — an empirical
distribution you can bias-correct per city and integrate directly against
Kalshi's strike ladder.

### 2.6 Weather source summary

| Source | URL | Key? | Probe | Role |
|---|---|---|---|---|
| NWS product API (CLI) | `api.weather.gov/products/types/CLI/locations/{loc}` | No (UA required) | **200** | **Settlement truth + intraday max-so-far** |
| NWS gridpoint forecast | `api.weather.gov/gridpoints/{wfo}/{x},{y}/forecast/hourly` | No | **200** | Official US forecast; free |
| Open-Meteo forecast | `api.open-meteo.com/v1/forecast` | No | **200** (intermittent TLS timeouts via proxy — retry) | Multi-model deterministic |
| Open-Meteo ensemble | `ensemble-api.open-meteo.com/v1/ensemble` | No | **200** | **Probability distribution** |
| Open-Meteo previous-runs | `previous-runs-api.open-meteo.com/v1/forecast` | No | **200** | **Point-in-time backtest forecasts** |
| Open-Meteo historical-forecast | `historical-forecast-api.open-meteo.com/v1/forecast` | No | **200** | Archived analyses |
| Open-Meteo ERA5 archive | `archive-api.open-meteo.com/v1/archive` | No | **200** | Long climatology |
| **RCC-ACIS** | `POST data.rcc-acis.org/StnData` and `/MultiStnData` | No | **200** | **Station history matching CLI** |

ACIS is the workhorse for backtesting. One `MultiStnData` POST returns every
Kalshi city for a date:

```bash
curl -X POST https://data.rcc-acis.org/MultiStnData -H "Content-Type: application/json" \
 -d '{"sids":"KNYC,KLAX,KMDW,KMIA,KAUS,KDEN,KPHL,KDCA,KPHX,KSEA,KATL,KDFW",
      "date":"2026-08-10","elems":["maxt","mint","pcpn"]}'
```
→ `KNYC 85/72/0.10`, `KLAX 79/70/0.00`, `KORD 87/72/T`, … Note `"T"` (trace) and
`"M"` (missing) are returned as strings; handle them. Docs:
<https://www.rcc-acis.org/docs_webservices.html>

> ⚠ **ECMWF direct:** I did not verify a free, unauthenticated ECMWF IFS feed.
> Open-Meteo redistributes IFS (`models=ecmwf_ifs025`) but my probes of that
> parameter timed out through this container's proxy; the default multi-model
> endpoint worked. ECMWF's own Open Data service
> (<https://www.ecmwf.int/en/forecasts/datasets/open-data>) is the direct route
> and is worth a probe, but Open-Meteo is the pragmatic choice today.

---

## 3. Category 2/3 — Elections

### 3.1 Measured liquidity (live, 2026-08-11)

Headline control markets — deep, and I argue efficient (§3.5):

| Series | cum vol ($) | OI ($) | top-of-book ($) |
|---|---|---|---|
| CONTROLH (House control) | 21,681,811 | 13,204,287 | 144,800 |
| KXBALANCEPOWERCOMBO | 9,446,411 | 5,751,167 | 48,140 |
| CONTROLS (Senate control) | 6,681,735 | 3,341,710 | 17,735 |
| KXDSENATESEATS (seat count) | 1,992,620 | 1,490,315 | 4,956 |
| KXHOUSEPOPVOTEMARGIN | 831,091 | 622,863 | 1,859 |
| KXHOUSETURNOUT | 77,488 | 39,791 | 285 |

Individual Senate races — this is where the neglect lives:

| Series | cum vol ($) | OI ($) | top-of-book ($) |
|---|---|---|---|
| SENATETX | 7,966,365 | 5,366,395 | 139,476 |
| SENATEME | 3,770,160 | 2,045,511 | 12,023 |
| SENATENC | 1,359,582 | 1,181,798 | 1,831 |
| KXMESENATEPERSON | 1,905,229 | 1,028,687 | 13,929 |
| SENATEMI | 1,294,060 | 979,834 | 16,168 |
| SENATEIA | 1,030,000 | 881,819 | 102,118 |
| SENATEGA | 1,028,253 | 734,597 | 176 |
| SENATENE | 586,962 | 423,043 | 10,581 |
| SENATESC | 630,334 | 390,727 | 1,550 |
| SENATEAK | 513,303 | 365,706 | 66 |
| SENATEMT | 393,640 | 228,239 | 17,127 |
| SENATEKS | 256,521 | 191,637 | 1,279 |
| SENATENH | 162,622 | 109,323 | 2,998 |
| …then a long tail: SENATEMS/MN/ID/LA/AR/SD/TN/WV/NJ/NM/CO/VA/WY/AZ all under $100k | | | |

Governor races: `GOVPARTYOH` $971k cum, `GOVPARTYCA` $1.24M, `GOVPARTYTX` $598k,
`GOVPARTYGA` $529k, `GOVPARTYPA` $409k, then ~30 states in the $15k–$300k band.
Kalshi also lists ~125 individual `HOUSE{ST}{N}` district series and 92
`*PRIMARY*` series.

**The neglected-market thesis is visible in the data**: SENATEGA has $1.03M of
cumulative volume but **$176** resting on the top of the book; SENATEAK $513k
cumulative, **$66** on the book. Depth, not volume, is the binding constraint.

### 3.2 FEC — OpenFEC API (verified live)

Base: `https://api.open.fec.gov/v1/`. Docs: <https://api.open.fec.gov/developers/>

**Key signup:** `DEMO_KEY` works but is capped at **40 calls/hour**. The API's own
429 body states the tiers verbatim:

> "You have exceeded your rate limit of 40 calls per hour for the DEMO_KEY, 1000
> calls per hour for a personal key, or 120 calls per minute for an upgraded key.
> You can either try again later, sign up for a personal key at
> <https://api.data.gov/signup/>, or email apiinfo@fec.gov to upgrade your key."

**Get a personal key from <https://api.data.gov/signup/> immediately** — it is
free, instant, and 25× the limit. Pass as `?api_key=`.

Verified endpoints:

| Endpoint | Probe | What it gives | Update cadence |
|---|---|---|---|
| `/candidates/?election_year=2026` | **200**, 4,269 candidates | Candidate registry: `candidate_id`, `incumbent_challenge` (I/C/O), `district`, `candidate_status`, `first_file_date`, `has_raised_funds` | On filing |
| `/schedules/schedule_e/?cycle=2026` | **200**, 26,875 records | **Independent expenditures** — `expenditure_date`, `expenditure_amount`, `candidate_name`, `support_oppose_indicator` (S/O), `filing_date`, `office_total_ytd` | **24/48-hour** (see below) |
| `/efile/filings/?sort=-receipt_date` | **200**, 652,588 filings | **Raw filings as received**, `receipt_date` timestamped to the second | **Real-time** |
| `/schedules/schedule_a/` | 429 on DEMO_KEY | Itemized individual contributions | Quarterly/monthly reports |
| `/candidates/totals/`, `/committees/totals/` | not probed ⚠ | Aggregate receipts/disbursements/cash-on-hand | Report periods |

**The signal with real timing value is Schedule E.** Independent expenditures must
be reported on a fast clock, per the FEC's own rules:

- **48-hour reports**: through the 20th day before an election, each time IEs
  aggregate to **$10,000 or more** for a given election, due within 48 hours —
  by 11:59 PM ET on the second day after the ad was publicly distributed.
- **24-hour reports**: after the 20th day but more than 24 hours before the
  election, each time IEs aggregate to **$1,000 or more**, due within 24 hours.

Sources: <https://www.fec.gov/help-candidates-and-committees/filing-pac-reports/24-hour-reports/>,
<https://www.fec.gov/help-candidates-and-committees/making-independent-expenditures/48-hour-reports-independent-expenditure-filers/>,
and the 2026 calendar at
<https://www.fec.gov/help-candidates-and-committees/dates-and-deadlines/2026-reporting-dates/24-and-48-hour-reports-independent-expenditures-periods-main-page-2026/>

So in the final three weeks of a race, **every $1,000+ of outside money becomes
public within 24 hours**. Late deployment of outside money into a race is one of
the strongest observable signals that internal polling has moved — and it is
structured, timestamped, and free, while news coverage of the same fact lags by
days or never happens for a downballot race. The README's Michigan story ($98M in
ad spend, mostly against El-Sayed) is exactly this data.

**Two data-quality traps, both observed live:**

1. **`expenditure_date` is filer-entered and contains garbage.** My probe sorted
   by `-expenditure_date` and the top two rows were dated **2029-05-19** and
   **2027-07-07** — obvious typos, filed 2026-05-26 and 2026-07-11 respectively.
   **For point-in-time discipline, key off `filing_date` / `receipt_date`, never
   `expenditure_date`.** This is a lookahead-bug generator.
2. `load_date` was `null` on the Schedule E rows I sampled. Do not depend on it.

### 3.3 Polling — VoteHub is the one with a real API (verified live)

`https://votehub.com/polls/api/` is behind Cloudflare (403 to both WebFetch and
curl), but **the API host itself is open**:

```
GET https://api.votehub.com/polls        → 200, no key, 5,445 rows
GET https://api.votehub.com/polls?poll_type=us-senator → 200
```

Schema (one row):

```json
{"id":"us-202chaf0ea6e94","poll_type":"us-senator","sample_size":915,
 "population":"lv","url":"https://carolinaforward.org/news/the-august-2026-carolina-forward-poll/",
 "created_at":"2026-08-10","start_date":"2026-08-03","end_date":"2026-08-06",
 "pollster":"Change Research",
 "answers":[{"choice":"Roy Cooper","pct":50.0},{"choice":"Michael Whatley","pct":43.0}],
 "seat_name":null,"sponsors":["Carolina Forward"],"internal":false,
 "partisan":"DEM","subject":"2026 North Carolina"}
```

Coverage: date range **2018-11-12 → 2026-08-06**. Composition by `poll_type`:
approval 2,931 · favorability 1,033 · generic-ballot 533 · **us-senator 327** ·
**governor 307** · presidential-primary 187 · us-representative 68 ·
attorney-general 37 · proposition-50 11 · mayor 11.

**Why this is the right source: `created_at` is separate from `end_date`.** That
gives you the ingestion timestamp, which is what point-in-time discipline
actually requires — a poll fielded 2026-08-03/06 only entered the dataset on
2026-08-10, and a backtest that uses it on 08-07 is cheating. Very few free
polling feeds expose this. Combined with `pollster`, `partisan`, `internal`, and
`sponsors`, you can replicate a quality-weighted average yourself.

Quirks: `seat_name` was `null` on all 327 Senate rows — **the race is encoded in
`subject`** (e.g. `"2026 North Carolina"`). Parse `subject`, not `seat_name`.
No `/averages`, `/forecast`, `/docs`, or `/openapi.json` endpoint exists (all 404);
`/polls/averages` 500s. You get raw polls and build the average yourself.

VoteHub's own descriptions of the averages and the 2026 model:
<https://votehub.com/2026/01/22/our-2026-polling-averages-explained/>,
<https://votehub.com/wp-content/uploads/2026/05/2026_votehub_midterm_methodology.pdf>,
<https://votehub.com/2026-forecast/>. They state the API is free for researchers
and developers.

**The other aggregators, ranked by usability:**

| Source | Access | Probe | Verdict |
|---|---|---|---|
| **VoteHub** | `api.votehub.com/polls`, free, no key | **200** | **Use this.** Only one with a clean API + `created_at` |
| **Split Ticket** | HTML pages, free | **200** (browser UA) | **Use for WAR features** (§3.4). Scrapable |
| Silver Bulletin | natesilver.net, subscription | 200 landing, model paywalled ⚠ | Best-regarded model; no API found. Use headline numbers as a sanity check only |
| RealClearPolitics | realclearpolling.com | **403** (Imperva bot wall, both API and HTML paths) | Effectively closed. Do not build on it |
| FiveThirtyEight | **shut down March 2025** | legacy CSVs 302-redirect | **Dead.** Its polling databases went dark with the site |

538's shutdown is confirmed by Nieman Lab
(<https://www.niemanlab.org/2025/03/fivethirtyeight-is-shutting-down-as-part-of-broader-cuts-at-abc-and-disney/>),
which also reports its public polling databases shut down with it; the NYT picked
up the poll-tracking work
(<https://www.niemanlab.org/2025/03/the-new-york-times-picks-up-the-shuttered-fivethirtyeights-poll-tracking-database/>).
The successors are Nate Silver's Silver Bulletin and G. Elliott Morris's Strength
in Numbers / FiftyPlusOne.

⚠ The `github.com/fivethirtyeight/data` archive is the standard historical
backtest corpus but I could **not** verify it from this container — the GitHub
REST API returns 403 through the proxy. Use the `mcp__github` tools (e.g.
`get_file_contents`) rather than raw HTTP to reach it.

### 3.4 Academic / quantitative election features

**Split Ticket's Wins Above Replacement (WAR)** is the most directly usable
candidate-quality metric, and it is public. Data repository (probe: **200** with a
browser UA, 151 KB HTML):
<https://split-ticket.org/data-repository/>

Available WAR datasets, each its own page:

| Dataset | URL |
|---|---|
| Full WAR database | <https://split-ticket.org/full-wins-above-replacement-war-database/> |
| 2024 House WAR | <https://split-ticket.org/2024-house-wins-above-replacement-war/> |
| 2024 Senate WAR | <https://split-ticket.org/2024-senate-wins-above-replacement-war/> |
| 2022 House / Senate WAR | <https://split-ticket.org/posts/2022-house-war/> · <https://split-ticket.org/posts/2022-senate-war/> |
| 2020 House WAR | <https://split-ticket.org/posts/2020-house-wins-above-replacement-war/> |
| 2018 House WAR | <https://split-ticket.org/posts/2018-house-wins-above-replacement-war/> |
| 2016 House / Senate WAR | <https://split-ticket.org/2016-house-wins-above-replacement-war/> · <https://split-ticket.org/2016-senate-wins-above-replacement-war/> |

Method, per Split Ticket: WAR uses demographics, incumbency, partisanship and
financial data to quantify how much a candidate over- or under-performs a
replacement-level candidate in the same seat. Their 2026 model applies
demographic swing, then adjusts for incumbency and prior candidate
overperformance via WAR, blended with a statistical model trained on the last
four cycles plus polling averages
(<https://split-ticket.org/category/modeling/>,
<https://www.theargumentmag.com/p/split-ticket-2026-midterms-model>). Their
2026 House candidate-quality analysis:
<https://split-ticket.org/2025/08/06/candidate-quality-and-the-democrats-2026-house-playingfield/>

**Feature set to build for a downballot race model** (each element maps to a
source verified above):

| Feature | Source | Verified |
|---|---|---|
| Quality-weighted poll average + house effects | `api.votehub.com/polls` (`pollster`, `partisan`, `internal`, `population`, `sample_size`) | ✅ |
| Candidate quality (WAR) | Split Ticket WAR database | ✅ (pages reachable) |
| Incumbency | FEC `/candidates/` → `incumbent_challenge` = I/C/O | ✅ |
| Fundraising / cash on hand | FEC candidate & committee totals | ⚠ not probed |
| **Outside money, 24/48h latency** | FEC `/schedules/schedule_e/` | ✅ |
| Filing velocity (new committees, IE bursts) | FEC `/efile/filings/` `receipt_date` | ✅ |
| Presidential approval (fundamentals) | `api.votehub.com/polls?poll_type=approval` (2,931 rows) | ✅ |
| Economic fundamentals | FRED CSV (§4.1) | ✅ |
| Seat partisan lean / prior results | ⚠ not sourced here — MIT Election Data & Science Lab or Daily Kos Elections are the usual corpora | ⚠ |

**Primaries specifically.** The academic literature is consistent that primaries
are much harder than general elections and that the general-election playbook
transfers badly:

> "In primary elections, partisan metrics are far less predictive than in general
> elections; primary elections experience lower voter turnout with greater outcome
> variability; and there is often a lack of highly predictive data such as
> comprehensive polling and substantial fundraising figures."

The standard pre-primary feature set is **polls + finances (money raised and cash
reserves) + elite endorsements**, often combined with early-state results to
forecast later contests. See the PS: Political Science & Politics 2024 forecasting
symposium
(<https://www.cambridge.org/core/journals/ps-political-science-and-politics/article/introduction-to-forecasting-the-2024-us-elections/3B0CD7678126F613C38AA011D3C034E2>,
issue index at
<https://www.cambridge.org/core/journals/ps-political-science-and-politics/issue/3E205E2F4FA5CB45F6AC0E919FE0AD06>),
Algara et al. on approval, party brands and candidate quality
(<https://doi.org/10.1177/20531680251394444>), and a Senate-primary dataset paper
(<https://www.sciencedirect.com/science/article/pii/S235234092600315X>).

One non-obvious feature worth testing: **donor-network structure beats raw
fundraising**. A Stanford CS224W study found network metrics over the donor graph
(e.g. PageRank) predicted primary success better than fundraising totals alone
(<https://snap.stanford.edu/class/cs224w-2015/projects_2015/Predicting_primary_election_results_through_network_analysis_of_donor_relationships.pdf>).
FEC Schedule A gives you the donor edges to rebuild this. ⚠ Old (2015) and not
replicated by me — treat as a research lead, not a fact.

Kalshi has 92 `*PRIMARY*` series, and primaries are exactly the "too small for
professional capital" niche the README identifies. But note the low-n problem:
they are one-shot events, so a primary strategy cannot be validated on a
deterministic backtest in the way weather can.

### 3.5 The definition trap — worked example, and why headline markets look efficient

As of 2026-08-11, Silver Bulletin's model gives Democrats **57.3% for the Senate**
and **86.6% for the House** (reported at
<https://www.dailykos.com/stories/2026/8/11/800082925/community/nate-silvers-midterm-election-model-has-dems-favored-to-take-back-the-senate-57-3-and-house-86-6/>
and <https://bluevirginia.us/2026/08/nate-silvers-midterm-election-model-has-dems-favored-to-take-back-the-senate-57-3-and-house-86-6/>;
model page <https://www.natesilver.net/p/nate-silver-2026-midterm-election-polls-model>
⚠ paywalled, so these are secondary reports of the number).

Live Kalshi prices, same day:

```
CONTROLH-2026-D   bid 0.84  ask 0.85   OI $5,535,417
CONTROLH-2026-R   bid 0.15  ask 0.16   OI $7,667,151
CONTROLS-2026-D   bid 0.48  ask 0.49   OI $1,522,467
CONTROLS-2026-R   bid 0.50  ask 0.51   OI $1,819,096
```

House: model 86.6% vs market 84–85%. Within fees and noise. No trade.

Senate: model 57.3% vs market 48–49%. **That looks like an 8–9 point edge on a
$3.3M-OI market.** It almost certainly is not. Kalshi's `rules_secondary` for
`CONTROLS-2026-D`, pulled live:

> "This market may be determined early based on a consensus of media calls
> projecting control of the U.S. Senate… Otherwise, victory will be determined by
> **the party identification of the President pro tempore of the Senate on
> February 1, 2027**."

The President pro tempore is chosen by the majority; at 50-50 the Vice President
breaks the organizing tie, so **a 50-50 Senate resolves Republican and Democrats
need 51 seats**. Now check Kalshi's own seat-count market `KXDSENATESEATS-27`,
which is internally consistent with that reading:

```
ABOVE52 0.20 | 52 0.12 | 51 0.15 | 50 0.15 | 49 0.14 | 48 0.11 | 47 0.06 | 46 0.03 | 45 0.017 | BELOW45 0.074
```

P(D ≥ 51) = 0.20 + 0.12 + 0.15 = **0.47** ≈ the 0.48 bid on `CONTROLS-2026-D`.
P(D ≥ 50) = 0.62. The market is coherent across its own instruments; the apparent
gap is a boundary-condition mismatch with whatever "wins the Senate" means in the
model.

**Three lessons for the strategy layer, all mechanical:**

1. **Parse `rules_primary` and `rules_secondary` into an explicit resolution
   predicate before comparing any external forecast to a price.** A gap that
   survives this check is a candidate; a gap that does not is a bug.
2. **Cross-instrument consistency is a free, price-only signal.** The binary
   control market and the seat-count ladder must agree. When they diverge, one is
   stale — and this needs no external data, so it backtests cleanly on any period.
   Same for `KXBALANCEPOWERCOMBO` ($9.4M cum) against `CONTROLH` × `CONTROLS`.
3. **Headline markets are efficient; treat them as a calibration oracle, not a
   target.** Trade the thin races and derivative markets instead.

---

## 4. Category — Economic data releases

### 4.1 The access story is better than expected

**FRED works with no API key** via the graph CSV endpoint — this is the single
most useful discovery in this section, since the documented JSON API hard-fails
without a key:

```
GET https://api.stlouisfed.org/fred/series/observations?series_id=CPIAUCSL&file_type=json
    → 400 {"error_message":"Variable api_key is not set."}

GET https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF
    → 200  observation_date,DFF / 1954-07-01,1.13 / ...
```

Verified keyless pulls, last observation as of 2026-08-11:

| Series | Meaning | Last value |
|---|---|---|
| `EFFR` | Effective fed funds rate | 2026-08-10 → 3.63 |
| `SOFR` | Secured overnight financing rate | 2026-08-10 → 3.63 |
| `UNRATE` | Unemployment rate | 2026-07-01 → 4.1 |
| `PAYEMS` | Nonfarm payrolls (level, thousands) | 2026-07-01 → 158,858 |
| `CPIAUCSL` | CPI-U, SA | 2026-06-01 → 332.568 |
| `ICSA` | Initial jobless claims, SA | 2026-08-01 → 199,000 |

⚠ FRED is a **mirror**, not the release. It updates after the agency publishes,
and it silently revises history in place. For point-in-time backtesting you need
**ALFRED** (`https://alfred.stlouisfed.org/`), FRED's vintage archive, which
preserves what each series looked like on each past date. ⚠ Not probed — verify
whether ALFRED also has a keyless CSV path. Using FRED's current values in a
backtest of a past release is a classic revision-lookahead bug.

**BLS public API v2 works with no key** (capped at 25 requests/day unregistered;
register free for 500/day ⚠ limit figure not verified this session):

```
GET https://api.bls.gov/publicAPI/v2/timeseries/data/CUUR0000SA0
    → 200 {"status":"REQUEST_SUCCEEDED", ... "year":"2026","period":"M06","value":"333.952","latest":"true" ...}
```

**DOL's full unemployment-insurance claims history is a single CSV** — 13.3 MB,
state-level weekly, back to 1986:

```
GET https://oui.doleta.gov/unemploy/csv/ar539.csv  → 200, 13,328,461 bytes
"st","rptdate","c1",...  /  "AK","1986-02-15",6,1986-02-08,2048,...
```

This is the component data behind the headline initial-claims print that
`KXJOBLESSCLAIMS` settles on (Kalshi's `settlement_sources` names "Department of
Labor"). ⚠ Note `www.dol.gov/ui/data.pdf` and `www.bls.gov/schedule/...` both
returned **403** from this container — the agencies bot-block their main sites
while leaving the data hosts open. Use `oui.doleta.gov` and `api.bls.gov`.

**Cleveland Fed inflation nowcast** —
<https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting>.
Fetched live 2026-08-11; the page states nowcasts are refreshed **every business
day around 10:00 a.m. Eastern**. Current values as fetched:

| Horizon | CPI | Core CPI | PCE | Core PCE |
|---|---|---|---|---|
| Current month | 0.35% | 0.20% | 0.34% | 0.27% |
| Next month | 0.09% | 0.21% | 0.19% | 0.27% |

⚠ **No CSV/JSON/API download link is exposed on the page**, and my guesses at
`/api/inflation-nowcasting` and a media JSON path returned the HTML shell / 404.
The page directs data inquiries to `public.information@clev.frb.org`. So: the
numbers are scrapeable daily, but **there is no historical nowcast archive you can
pull**, which means you cannot backtest a nowcast-vs-market strategy without
first accumulating your own daily snapshots. **Start logging it today** — it costs
one cron job and it is the only way this becomes backtestable later.

Related nowcasts, both reachable (200) but not parsed this session ⚠:
Atlanta Fed GDPNow
(<https://www.atlantafed.org/-/media/documents/cqer/researchcq/gdpnow/RealGDPTrackingSlides.pdf>)
and the NY Fed Staff Nowcast
(<https://www.newyorkfed.org/research/policy/nowcast>).

### 4.2 Kalshi econ markets and measured liquidity

| Series | Title | Settles on | cum vol ($) | vol 24h ($) | OI ($) | fee_type |
|---|---|---|---|---|---|---|
| KXCPIYOY | Inflation (YoY) | BLS | 1,255,639 | 181,462 | 840,513 | **quadratic_with_maker_fees** |
| KXCPI | CPI | BLS | 1,134,975 | 342,278 | 729,388 | quadratic |
| KXCPICOREYOY | Core inflation | BLS | 172,763 | 11,702 | 51,252 | quadratic |
| KXCPINDEX | CPI-U index level | BLS – CPI-U | 94,396 | 1,869 | 47,064 | quadratic |
| KXCPICORE | CPI core | BLS | 68,675 | 9,304 | 45,618 | quadratic |
| KXPAYROLLS | Jobs numbers | BLS | 45,912 | 1,127 | 15,561 | quadratic |
| KXGDP | US GDP growth | BEA | 36,714 | 14 | 21,619 | quadratic |
| KXJOBLESSCLAIMS | Weekly initial claims | DOL | 18,549 | 6,141 | 12,959 | quadratic |
| KXNBERRECESSQ | Next recession start | NBER | 515,471 | 604 | 179,106 | quadratic |

Kalshi's economics catalogue is 620 series and much wider than the headline
prints — `CPISHELTER`, `CPIGAS`, `CPIUSEDCAR`, `CPIAPPAREL`, `CPIFOOD`,
`KXAIRFARECPI` (CPI subcomponents, all BLS-settled), `AAAGASD`/`AAAGASW`
(daily/weekly gas prices, settled on AAA), `KXSOFRD` (daily SOFR, settled on the
NY Fed).

**The subcomponent markets are the interesting ones.** CPI shelter and used-car
CPI are far more forecastable from published private data (rent indices, Manheim)
than headline CPI, and they trade thinly against a crowd focused on the headline.
⚠ I did not probe their liquidity — do that before committing.

**A structural note worth flagging:** the catalogue contains `KXCPIDELAY`
("CPI data released"), `KXNFPDELAY` ("Jobs data released"), `KXJOBSRELEASE`
("When will the BLS release a jobs report?"), `KXPAYROLLCANCEL` ("Payrolls
cancelled") and `KXJOBREVISION`. These are markets on **whether the statistical
agency publishes at all**, which implies recent disruption to the release
calendar. Ground truth for these is the BLS release schedule plus agency
announcements — a completely different (and much more tractable) forecasting
problem than the data value itself. ⚠ I could not fetch `bls.gov/schedule` (403);
find an alternate mirror before building on this.

---

## 5. Category — Fed decisions (recommend: use as an oracle, not a target)

**Liquidity is excellent**: `KXFEDDECISION` shows $6.71M cumulative volume,
**$999,586 in 24h**, $5.31M OI, $102,694 top-of-book. `KXRATECUTCOUNT` $6.91M
cumulative / $4.49M OI. `KXFED` $731k / $374k OI.

**But the ground truth is the market.** CME FedWatch is not an independent
forecast — it is a deterministic transformation of 30-Day Fed Funds (ZQ) futures
prices (<https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html>).
Trading Kalshi against FedWatch is trading one liquid market against another
liquid market, with Kalshi's fees on your side of the ledger. The README already
notes Bridgewater's AIA Forecaster underperformed liquid-market consensus; this is
the category where that finding bites hardest.

**Access is also hostile.** Live probe of CME's endpoints returned **403** with:

> "This IP address is blocked due to suspected web scraping activity associated
> with it on this CMEgroup.com page. Use of scripts, software, spiders, robots,
> avatars, agents, tools or other scraping mechanisms is strictly prohibited by
> CME Group's website Data Terms of Use."

**Do not scrape CME.** The official FedWatch APIs
(<https://www.cmegroup.com/market-data/market-data-api/fedwatch-api.html>, EOD API
docs at
<https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457320466/CME+FedWatch+API>)
require an entitled client API ID via CME's onboarding or Global Account
Management. Yahoo Finance `ZQ=F` returned **429** and Stooq returned a JS
bot-wall, so no free ZQ substitute was found this session ⚠.

**Recommendation:** skip Fed markets as a primary strategy. Do use `KXFEDDECISION`
prices as a **calibration oracle** for the backtest harness — a well-specified
harness should show ~zero edge there, and if your strategy claims a large edge on
Fed markets, you have a bug.

One genuinely different sub-market: `KXFEDDISSENT` / `KXFOMCDISSENTCOUNT` (how
many FOMC members dissent). That is a discrete institutional-behavior question
that futures prices do not encode, with ground truth in the FOMC statement and
minutes. ⚠ Liquidity not probed.

---

## 6. Cross-cutting engineering notes

### 6.1 Point-in-time discipline, per source

| Source | The lookahead trap | The safe field |
|---|---|---|
| FEC Schedule E | `expenditure_date` contains filer typos (observed: 2029, 2027 dates filed in 2026) | `filing_date` / `receipt_date` |
| VoteHub | using a poll on its `end_date` | `created_at` (ingestion date) |
| FRED | silent in-place revisions | ALFRED vintages ⚠ verify access |
| Weather forecast | using today's archived analysis as "the forecast" | `previous-runs-api` `*_previous_dayN` |
| NWS CLI | the intermediate report is not final | check `VALID TODAY AS OF <time>` in the text |
| Cleveland Fed nowcast | no archive exists at all | start snapshotting now |

### 6.2 Fees are not uniform — read `fee_type` per series

From the `/series` catalogue:

| Category | `quadratic` | `quadratic_with_maker_fees` | multiplier 0 |
|---|---|---|---|
| Entertainment | 2,493 | 7 | — |
| Politics | 2,140 | — | 5 |
| Elections | 1,550 | — | 3 |
| Financials | 799 | 3 | 1 |
| Economics | 607 | **10** | 3 |
| Climate and Weather | 293 | — | — |

Confirmed individually: `KXHIGHNY` `quadratic`/1 · `CONTROLS` `quadratic`/1 ·
`SENATETX` `quadratic`/1 · **`KXCPIYOY` `quadratic_with_maker_fees`/1** ·
**`KXFEDDECISION` `quadratic_with_maker_fees`/1**.

**Action:** the backtest harness must read `fee_type` and `fee_multiplier` from
the series record rather than hardcoding one formula. A maker-rebate or
resting-limit-order strategy is valid on weather and elections and **invalid on
CPI/Fed series**. Also note `fee_multiplier=0` series exist (12 of them) — free
to trade, worth identifying.

### 6.3 Depth, not volume, is the constraint

Repeatedly across categories, cumulative volume and top-of-book depth are nearly
uncorrelated: SENATEGA $1.03M volume / **$176** on the bid; KXHIGHCHI $136k daily
volume / **$94** on the bid; KXRATECUTCOUNT $6.9M volume / **$64** on the bid.
Meanwhile KXHIGHLAX shows $128k resting. Kelly sizing must be capped by
`yes_bid_size_fp` × price measured at decision time, per the README's constraint 5.

### 6.4 Network notes from this container

- Government data hosts are open: `api.weather.gov`, `api.open.fec.gov`,
  `api.bls.gov`, `fred.stlouisfed.org`, `oui.doleta.gov`, `data.rcc-acis.org`.
- Government **web** front-ends bot-block: `www.dol.gov` 403, `www.bls.gov` 403.
- Commercial walls: `cmegroup.com` 403 (explicit ToS prohibition),
  `realclearpolling.com` 403, `votehub.com` 403 (but `api.votehub.com` open),
  `stooq.com` JS wall, Yahoo Finance 429, `api.github.com` 403 (use `mcp__github`).
- `api.open-meteo.com` gave intermittent TLS handshake timeouts through the proxy
  while `archive-api`, `ensemble-api`, `previous-runs-api` and
  `historical-forecast-api` were reliable. **Retry with backoff; do not treat a
  timeout as absence.**

---

## 7. Recommended build order

1. **Weather bias-correction strategy** (§2). Pull `/series` + `rules_primary` to
   fix the station map; fit a per-city grid→station bias from ACIS + Open-Meteo
   historical-forecast; generate strike probabilities from the Open-Meteo
   ensemble; backtest against Kalshi candlesticks using `previous-runs-api` for
   point-in-time forecasts. **Thousands of resolutions, free data, measured
   +1.54 °F NYC bias, ~$1.4M/day of turnover.** This is the only category where
   you can prove or kill an edge inside this build window.
2. **The 4 PM CLI intraday strategy** (§2.3). Parse the afternoon Climatological
   Report for max-so-far; trade the remaining ~7.5 hours of the session. Highest
   expected edge, needs a live NWS poller and a "max already set?" model.
3. **Cross-instrument consistency monitor** (§3.5). `CONTROLS` vs
   `KXDSENATESEATS`, `CONTROLH` × `CONTROLS` vs `KXBALANCEPOWERCOMBO`. Price-only,
   so it backtests cleanly on any historical period with no contamination risk.
4. **Start the nowcast/poll snapshot cron now** (§4.1, §3.3). Cleveland Fed daily
   at ~10:05 ET; `api.votehub.com/polls` daily. Neither has a usable archive; the
   only way to have one in three months is to start today.
5. **FEC Schedule E monitor for downballot races** (§3.2). Get a `api.data.gov`
   key, poll `/schedules/schedule_e/?cycle=2026` sorted by `filing_date`, alert on
   IE bursts into races where Kalshi shows thin depth. High-ceiling, low-n — build
   it as a signal generator ahead of November, not as a backtestable strategy.

---

## 8. Open questions and unverified items

- ⚠ ALFRED vintage access without a key — required for honest econ backtests.
- ⚠ Exact station IDs for the 12 city series where `rules_primary` gives only a
  city name; resolve from `settlement_sources[].url`.
- ⚠ Whether the daily max is typically set before the 4 PM intermediate CLI —
  the core assumption of build item 2. Measure from CLI history.
- ⚠ Whether `previous-runs-api` supports `past_days` > 92; this bounds the
  point-in-time weather backtest window. If capped, the historical-forecast
  archive plus a lead-time assumption is the fallback.
- ⚠ Liquidity of CPI subcomponent series (`KXCPISHELTER`, `KXCPIUSEDCAR`,
  `KXAIRFARECPI`) and of `KXFEDDISSENT`.
- ⚠ Silver Bulletin / Strength in Numbers programmatic access — no API found; the
  57.3%/86.6% figures here are from secondary reporting, not the model page.
- ⚠ `github.com/fivethirtyeight/data` historical corpus — unreachable via raw HTTP
  from this container; retry through `mcp__github`.
- ⚠ ECMWF Open Data direct access as an alternative to Open-Meteo's IFS mirror.
- ⚠ Entertainment/awards: Kalshi has 2,500 Entertainment series (`KXNETFLIXTOPVIEWSTV`,
  `KXNETFLIXRANKMOVIE`, `KXPUREALBUMS`, box-office and awards markets) and the
  Netflix ones settle on Netflix's own weekly Top 10 report
  (<https://kalshi.com/category/culture/movies>). But there is no pre-release
  structured ground truth comparable to a weather model, liquidity was not
  measured, and the category is judgment-heavy. **Deprioritized, not researched to
  the same depth.**

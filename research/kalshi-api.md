# Kalshi API Cookbook — data layer + backtester

**Written 2026-08-11 by a research agent. Every REST claim below was verified by live
unauthenticated `curl`/`urllib` calls from this container against
`https://api.elections.kalshi.com/trade-api/v2/` on 2026-08-11 (~21:15–22:05 UTC).**
Responses shown are real and truncated. Claims that could *not* be verified empirically
are marked **[UNVERIFIED]**.

---

## 0. TL;DR — the eight things that change how we build

1. **There is a `/historical/*` API and it is the whole ballgame.** The live endpoints
   only carry ~3 months. `GET /historical/trades` returns **tick-level trades back to at
   least Nov 2022**, and `GET /historical/markets/{ticker}/candlesticks` returns
   1-minute OHLC + bid/ask for archived markets — e.g. the full 2024 presidential
   election night for `PRES-2024-DJT`. All of it works **unauthenticated**.
   ([docs](https://docs.kalshi.com/getting_started/historical_data))
2. **Two response schemas.** Live endpoints emit `close_dollars` / `volume_fp` /
   `open_interest_fp`; the `/historical/*` candlestick endpoint emits bare `close` /
   `volume` / `open_interest` with the *same* dollar-string values. The parser must
   accept both.
3. **Candlesticks are the durable archive; trades are a rolling window.** For
   `KXSENATEMID-26-AELS` the live `/markets/trades` endpoint stops at **2026-05-25**
   even though daily candles show 209,764 contracts traded between 2025-04-24 and
   2026-05-17. Use `/historical/trades` for anything older.
4. **`period_interval` ∈ {1, 60, 1440} only, and every request is capped at 5,000
   candles.** That is 3.47 days of 1-min, 208 days of hourly, 5,000 days of daily.
5. **Money is now sub-cent and contracts are fractional.** Prices are decimal-dollar
   strings with up to 6 dp; counts are strings with 2 dp (`"57.51"` contracts).
   Election markets use `price_level_structure: "tapered_deci_cent"` — **0.1¢ ticks
   below 10¢ and above 90¢**. The backtester must read `price_ranges` per market.
6. **Fee model comes from the API, not a guess.** Every series carries `fee_type`
   (`quadratic` | `quadratic_with_maker_fees` | `flat`) and `fee_multiplier`
   (observed: 1, 0.5, 0 — **never >1**). 130 of 12,658 series charge maker fees.
7. **Enumerate settled markets with `min_settled_ts`/`max_settled_ts` + `status=settled`
   + `mve_filter=exclude`** — not `min_close_ts`. Even so, **~40,000 markets settle per
   day**; crawl by series/category, not by date sweep.
8. **Unauthenticated rate limit is the Basic bucket: ~20 req/s.** Measured: 8 workers →
   17.7 rps, 0 errors; 24 workers → 32.6 rps, 10% 429s. No `Retry-After` header.

---

## 1. Base URLs, auth, environments

| Environment | REST base | WebSocket |
|---|---|---|
| Production (recommended) | `https://external-api.kalshi.com/trade-api/v2` | `wss://external-api-ws.kalshi.com/trade-api/ws/v2` |
| Production (also supported, what we use) | `https://api.elections.kalshi.com/trade-api/v2` | `wss://api.elections.kalshi.com/trade-api/ws/v2` |
| Demo | `https://external-api.demo.kalshi.co/trade-api/v2` | `wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2` |

Source: [docs.kalshi.com/getting_started/api_environments](https://docs.kalshi.com/getting_started/api_environments.md).
"Despite the `elections` subdomain, the production Trade API provides access to all
Kalshi markets, not only election-related markets" — confirmed empirically (MLB, crypto,
weather markets all returned).

**All market-data endpoints used in this document work with no credentials.** Verified:
`/exchange/status`, `/series`, `/events`, `/markets`, `/markets/{t}`,
`/markets/trades`, `/markets/{t}/orderbook`, `/markets/orderbooks` (batch),
`/markets/candlesticks` (batch), `/series/{s}/markets/{t}/candlesticks`,
`/historical/cutoff`, `/historical/markets`, `/historical/markets/{t}`,
`/historical/markets/{t}/candlesticks`, `/historical/trades`, `/series/fee_changes`,
`/search/tags_by_categories`, `/milestones`, `/multivariate_event_collections`.
(Note `/markets/orderbooks` declares `security:` in the OpenAPI spec but returned 200
unauthenticated — do not rely on that staying true.)

**Signing (for later, when we have keys):** RSA-PSS/SHA-256 over
`timestamp_ms + METHOD + path`, where *path* is the full path from the API root
**without query string and without host** — e.g. sign `/trade-api/v2/portfolio/orders`
even when calling
`https://external-api.kalshi.com/trade-api/v2/portfolio/orders?limit=5`
([api_environments doc](https://docs.kalshi.com/getting_started/api_environments.md)).

**Full OpenAPI spec:** `https://docs.kalshi.com/openapi.yaml` (324 KB, 93 paths,
`version: 3.27.0` as of 2026-08-11). Downloading it is the fastest way to settle any
parameter question — do that before guessing. Doc index: `https://docs.kalshi.com/llms.txt`.

---

## 2. Units, prices, and tick sizes — read this before writing the fill engine

The API migrated to fixed-point decimal strings. From the OpenAPI spec:

- `FixedPointDollars`: *"US dollar amount as a fixed-point decimal string with up to 6
  decimal places… valid quote intervals for a given market are constrained by that
  market's price level structure."* Example `"0.5600"`.
- `FixedPointCount`: *"Fixed-point contract count string (2 decimals…; referred to as
  `fp` in field names)… Fractional contract values (e.g. `"2.50"`) are supported; the
  minimum granularity is 0.01 contracts."*

So: **parse every price and size as `Decimal(str)`, never float, never "cents as int".**

**Tick size is per market.** Surveyed 1,000 open markets: all were
`price_level_structure: "linear_cent"` with a single range
`{start 0.0000, end 1.0000, step 0.0100}`. But the Michigan Senate primary market is:

```json
"price_level_structure": "tapered_deci_cent",
"price_ranges": [
  {"start":"0.0000","end":"0.1000","step":"0.0010"},
  {"start":"0.1000","end":"0.9000","step":"0.0100"},
  {"start":"0.9000","end":"1.0000","step":"0.0010"}
]
```

This matters directly for the thesis in `README.md`: the "NO at ~1.5–2¢" leg lives in the
0.1¢-tick region, so a limit-order simulator that snaps to whole cents will misprice
exactly the trade we care about. Read `price_ranges` from the market record and quantize
to it.

**Trade direction.** `Trade` carries `taker_outcome_side` (`yes`|`no`) and
`taker_book_side` (`bid`|`ask`), where **`bid ≡ yes`, `ask ≡ no`, always**. The legacy
`taker_side` is deprecated ("will not be removed before May 28, 2026").
([order_direction doc](https://docs.kalshi.com/getting_started/order_direction.md))
For maker-fill simulation: a trade with `taker_book_side: "ask"` means the aggressor
bought NO / sold YES, i.e. it consumed a resting YES bid. That is the signal for
"a resting buy-YES limit at this price would have filled".

`is_block_trade` flags negotiated block trades — **exclude them from fill simulation**
(they never touched the book). Filterable via the `is_block_trade` query param.

---

## 3. Fees — what is verified, what is not

### 3.1 Verified from the API itself

Every series object carries its fee configuration. Real response
(`GET /series?category=Politics&limit=3`):

```json
{"series":[{"category":"Politics","fee_multiplier":1,"fee_type":"quadratic",
  "frequency":"one_off","ticker":"PRESTALK", ... }]}
```

Distribution over **all 12,658 series** (`GET /series?limit=1000`, 15.9 MB, one call):

| `fee_type` | count |
|---|---|
| `quadratic` | 12,528 |
| `quadratic_with_maker_fees` | 130 |
| `flat` | 0 observed (exists in the enum) |

| `fee_multiplier` | count | who |
|---|---|---|
| `1` | 12,625 | everything else, incl. all Elections/Politics |
| `0.5` | 19 | MLB prop series (`KXMLBGAME`, `KXMLBTOTAL`, `KXMLBHR`, …) |
| `0` | 14 | e.g. `KXTRUMPOUT`, `KXGREENLAND`, `KXBTCY`, `KXETHY`, `KXGDPYEAR` |

Series charging maker fees are 107 Sports, 10 Economics, 7 Entertainment, 3 Financials,
2 Crypto, 1 Sci/Tech — including `KXFEDDECISION`, `KXPAYROLLS`, `KXNFLSPREAD`, `KXSB`.
**Zero Elections/Politics series charge maker fees**, and none has a multiplier above 1.

> This **contradicts `docs/prior-art.md` §2**, which says "multiplier 0.07 for most
> categories (higher for premium categories like crypto)". Empirically no series has
> `fee_multiplier > 1`, and two crypto series are at **0**. Fix that line.

The OpenAPI spec defines the semantics:

> `FeeType` is a string representing the series' fee structure. Fee structures can be
> found at https://kalshi.com/docs/kalshi-fee-schedule.pdf. `'quadratic'` is described by
> the General Trading Fees Table, `'quadratic_with_maker_fees'` is described by the
> General Trading Fees Table with maker fees described in the Maker Fees section,
> `'flat'` is described by the Specific Trading Fees Table.
> `FeeMultiplier` is a floating point multiplier applied to the fee calculations.

Fee config can change over time and per event:

```bash
curl -s "https://api.elections.kalshi.com/trade-api/v2/series/fee_changes?show_historical=true"
# {"series_fee_change_arr":[
#   {"fee_multiplier":0.5,"fee_type":"quadratic_with_maker_fees",
#    "scheduled_ts":"2026-08-07T04:59:45.131Z","series_ticker":"KXMLBGAME"}, ... ]}   # 106 entries
```

There is also `GET /events/fee_changes` with `fee_type_override` /
`fee_multiplier_override` (null = cleared, falls back to the series). **For a
point-in-time-honest backtest, resolve the fee config as of the trade timestamp using
`/series/fee_changes?show_historical=true` + `/events/fee_changes`, not today's value.**
The 106-row history only goes back a short way, so **[UNVERIFIED]** whether it covers
2024–2025; for older periods assume the current multiplier and note the assumption.

### 3.2 The PDF: still blocked

`https://kalshi.com/docs/kalshi-fee-schedule.pdf` returned **HTTP 429 with a "Vercel
Security Checkpoint" HTML body (33,789 bytes)** on 5 attempts across 45 minutes, with
browser-like headers, with `curl/8.5`, via `www.`, and via the `WebFetch` tool.
`assets.kalshi.com/docs/…` is 404. `web.archive.org` and `r.jina.ai` are blocked from
this container's proxy. **The PDF remains unfetched — same failure `docs/prior-art.md`
recorded. Someone on a normal browser should download it and drop it in `research/`.**

### 3.3 Formula — triangulated, use with the stated confidence

Consensus of independent secondary sources that cite the PDF:

- **Taker:** `fee = 0.07 × C × P × (1 − P)` per contract-lot, symmetric in `P`,
  **max 1.75¢/contract at P = 0.50**.
  ([pm.wiki, updated 2026-04-21](https://pm.wiki/learn/kalshi-fees-explained);
  [Maker/Taker Math on Kalshi](https://whirligigbear.substack.com/p/makertaker-math-on-kalshi);
  [marketmath.io](https://marketmath.io/platforms/kalshi))
- **Maker:** 25% of taker → `0.0175 × C × P × (1 − P)`, max 0.44¢ at 50¢. Applies only
  to `quadratic_with_maker_fees` series.
- Fee table cited by pm.wiki: 10¢ → 0.63¢ taker / 0.16¢ maker; 50¢ → 1.75¢ / 0.44¢;
  90¢ → 0.63¢ / 0.16¢.

**Confidence: high on the 0.07 coefficient and the quadratic shape** (three independent
sources, and it matches the `quadratic` `fee_type` name and the API's `fee_multiplier`
scaling). **Medium on the exact maker coefficient.**

### 3.4 Rounding — verified from Kalshi's own docs, and it is NOT `round(x, 2)`

From [docs.kalshi.com/getting_started/fee_rounding](https://docs.kalshi.com/getting_started/fee_rounding.md):

- Balances are held to `$0.0001` for direct members, `$0.01` for everyone else.
- Every fill produces three components: **trade fee** = model fee **rounded *up* to the
  nearest `$0.0001`**; **rounding fee** = the amount needed to floor the balance change
  to the member's precision; **rebate** = refund from accumulated rounding, always a
  multiple of `$0.01`.
- `net fee = trade fee + rounding fee − rebate`, always ≥ 0.
- A per-order **fee accumulator** carries across all fills of an order (taker *and*
  subsequent maker fills) so the total converges to the single-fill-equivalent cost.

> This supersedes the `round(0.07·P·(1−P), 2)` formula in `ORCHESTRATION.md` §1. Use:
>
> ```python
> from decimal import Decimal, ROUND_CEILING
> CENTICENT = Decimal("0.0001")
> def trade_fee(count: Decimal, price: Decimal, mult: Decimal, maker: bool) -> Decimal:
>     coef = (Decimal("0.0175") if maker else Decimal("0.07")) * mult
>     raw = coef * count * price * (Decimal(1) - price)
>     return raw.quantize(CENTICENT, rounding=ROUND_CEILING)
> ```
>
> Then apply per-order whole-cent balance flooring + the `$0.01` rebate accumulator if we
> want fill-exact P&L. For a first backtest, the centicent-ceiled trade fee alone is
> within a fraction of a cent per order and is the honest number to quote.

**Caveat:** the worked examples on the fee_rounding page are not consistent with
`0.07·P·(1−P)` (1 contract @ $0.055 → stated fee $0.0085, which implies a coefficient of
~0.163; 0.30 contracts @ $0.50 → $0.0041, implying ~0.055). They appear to be illustrative
numbers for the *rounding* mechanics, not real fee-model outputs. **Do not derive the
coefficient from them.** **[UNVERIFIED]** — resolving this needs the PDF or a real fill.

---

## 4. Hierarchy: series → events → markets, and how categories work

```
Series   (KXSENATEMID)              — the recurring product; owns category, tags, fee config
  └─ Event   (KXSENATEMID-26)       — one real-world occurrence; owns mutual-exclusivity
       └─ Market (KXSENATEMID-26-AELS) — one binary contract; owns price, result, rules
```

### Categories (18, exact counts over all 12,658 series)

`Sports 3175 · Entertainment 2500 · Politics 2145 · Elections 1553 · Financials 803 ·
Economics 620 · Mentions 407 · Science and Technology 299 · Climate and Weather 293 ·
Crypto 271 · Companies 173 · World 143 · Health 96 · Commodities 77 · Social 52 ·
Transportation 38 · Exotics 12 · Education 1`

Tags are the finer grain (`GET /search/tags_by_categories` returns the canonical map):

```json
{"tags_by_categories":{
  "Elections":["US Elections","International elections","House","Senate","Primaries",
               "Governor","Other US Elections","Election Combos","2028","Mixed Combos", ...],
  "Crypto":["BTC","15 min","Hourly","ETH","SOL","DOGE","XRP","BNB","HYPE","ZEC","NEAR","Pre-Market"], ...}}
```

Most-used tags across series: `Soccer 1138 · Music 799 · US Elections 541 ·
Basketball 514 · Awards 480 · Football 467 · Congress 363 · Primaries 359 ·
Music charts 347 · Trump 284`.

**Category/tag live only on the series object** — market and event records do not carry
them. The data layer must join markets → `event_ticker` → `series_ticker` → series to
classify. Build a `series` dimension table first; one `GET /series?limit=1000` call
returns all 12,658 (15.9 MB) in ~4 s.

`frequency` on the series is the cheapest churn filter:
`custom 5422 · one_off 5001 · annual 1357 · monthly 325 · weekly 274 · daily 203 ·
hourly 55 · fifteen_min 19 · quarterly 2`. The `fifteen_min`/`hourly` crypto and weather
series are what generate the 40k-settlements-per-day firehose — exclude them early.

### Finding the Michigan market (worked example)

The series is **`KXSENATEMID`** ("MID" = Michigan Democratic), event **`KXSENATEMID-26`**,
market **`KXSENATEMID-26-AELS`**.

```bash
curl -s "https://api.elections.kalshi.com/trade-api/v2/events?series_ticker=KXSENATEMID&with_nested_markets=true&limit=10"
```

```
KXSENATEMID-26 | Michigan Democratic Senate nominee? | In 2026 | 9 markets
    KXSENATEMID-26-AELS finalized yes  Abdul El-Sayed
    KXSENATEMID-26-HSTE finalized no   Haley Stevens
    KXSENATEMID-26-MMCM finalized no   Mallory McMorrow          ... (9 total)
KXSENATEMID-25 | Who will be the Democratic nominee for Senate in Minnesota? | In 2025
    KXSENATEMID-25-TWAL closed  (no result)  Tim Walz            ... (9 total)
```

> **Gotcha:** the same series ticker `KXSENATEMID` hosts a **Minnesota** event
> (`-25`) and a **Michigan** event (`-26`). Ticker prefixes are not stable semantic keys.
> Always read `title`/`sub_title` off the event, never parse the ticker.

The general-election Michigan market is a *different* series: `SENATEMI`
(`SENATEMI-26-D` / `SENATEMI-26-R`, still `active`, close 2027-11-03).

Full market record (`GET /markets/KXSENATEMID-26-AELS`), real and complete on the fields
that matter:

```json
{"market":{
  "ticker":"KXSENATEMID-26-AELS","event_ticker":"KXSENATEMID-26","market_type":"binary",
  "status":"finalized","result":"yes",
  "open_time":"2025-03-28T14:00:00Z","close_time":"2026-08-05T14:02:38Z",
  "expiration_time":"2026-11-03T15:00:00Z","expected_expiration_time":"2026-11-03T15:00:00Z",
  "occurrence_datetime":"2026-08-05T13:47:57Z",
  "settlement_ts":"2026-08-05T14:32:40.947032Z","settlement_timer_seconds":1800,
  "settlement_value_dollars":"1.0000","notional_value_dollars":"1.0000",
  "can_close_early":true,
  "early_close_condition":"This market will close after Abdul El-Sayed wins the party's nomination.",
  "last_price_dollars":"0.9980","previous_price_dollars":"0.9980",
  "yes_bid_dollars":"0.0000","yes_ask_dollars":"1.0000","no_bid_dollars":"0.0000","no_ask_dollars":"1.0000",
  "volume_fp":"9935560.26","volume_24h_fp":"0.00","open_interest_fp":"4113905.17","liquidity_dollars":"0.0000",
  "price_level_structure":"tapered_deci_cent",
  "price_ranges":[{"start":"0.0000","end":"0.1000","step":"0.0010"}, ...],
  "rules_primary":"If Abdul El-Sayed wins the nomination for the Democratic Party to contest the 2026 Class II Michigan Senate seat, then the market resolves to Yes.",
  "title":"Will Abdul El-Sayed be the Democratic nominee for the Senate in Michigan?",
  "yes_sub_title":"Abdul El-Sayed"}}
```

Note `close_time` (2026-08-05, early close) ≠ `expiration_time` (2026-11-03) — early
close fired. **`settlement_ts` is the timestamp to key settlement off; `close_time` is
when trading stopped.** `rules_primary` is the string to feed the forecast-question
compiler.

---

## 5. Candlesticks — the definitive spec

### Path shapes (two of them)

```
GET /series/{series_ticker}/markets/{market_ticker}/candlesticks   # live markets
GET /historical/markets/{market_ticker}/candlesticks               # archived markets (no series in path)
GET /markets/candlesticks?market_tickers=A,B,C                     # BATCH, live, up to 100 tickers
GET /series/{series_ticker}/events/{event_ticker}/candlesticks     # event-level aggregate
```

Required query params on all: `start_ts`, `end_ts` (Unix seconds), `period_interval`.

**`series_ticker` in the live path is not validated** — passing a mismatched series still
returns the right market's candles. Convenient, but don't rely on it.

### `period_interval` — exactly three legal values

Empirically tested 1, 5, 10, 15, 30, 60, 240, 1440:

```
period_interval=1     HTTP 200
period_interval=60    HTTP 200
period_interval=1440  HTTP 200
period_interval=5|10|15|30|240 →
  HTTP 400 {"msg":"Parameter validation failed for GetMarketCandlesticks:
   Key: 'GetMarketCandlesticksParams.PeriodInterval' Error:Field validation
   for 'PeriodInterval' failed on the 'oneof' tag"}
```

### 5,000-candle hard cap per request

```
1-min over 8 days  → 400 {"details":"requested time range with candlesticks: 11400.000000, max candlesticks: 5000"}
60-min over 500 d  → 400 {"details":"requested time range with candlesticks: 12752.666667, max candlesticks: 5000"}
```

Note the cap is on the *requested range length*, not the returned row count — it fails
before looking at the data. So the chunker must be:

| interval | max window per request |
|---|---|
| `1` | 5,000 min = **3 days 11 h** (use 3 days) |
| `60` | 5,000 h = **208 days** (use 180 d) |
| `1440` | 5,000 days ≈ 13.7 y (one call covers any market's life) |

Batch endpoint: up to **100 tickers**, **10,000 candles total** across all of them
(OpenAPI description). It supports `include_latest_before_start` for a synthetic opening
candle. Response is grouped:

```json
{"markets":[{"market_ticker":"KXSENATEMID-26-AELS","candlesticks":[...]},
            {"market_ticker":"KXSENATEMID-26-HSTE","candlesticks":[...]}]}
```

### Row schema — two variants, same meaning

**Live** (`/series/.../candlesticks`), a real row from Michigan election night:

```json
{"end_period_ts":1785899820,
 "open_interest_fp":"3667351.01",
 "price":{"open_dollars":"0.7600","high_dollars":"0.7700","low_dollars":"0.7400",
          "close_dollars":"0.7600","mean_dollars":"0.7538","previous_dollars":"0.7700"},
 "volume_fp":"4866.38",
 "yes_bid":{"open_dollars":"0.7600","high_dollars":"0.7600","low_dollars":"0.7400","close_dollars":"0.7500"},
 "yes_ask":{"open_dollars":"0.7700","high_dollars":"0.7700","low_dollars":"0.7600","close_dollars":"0.7600"}}
```

**Historical** (`/historical/markets/.../candlesticks`), real row from 2024-11-05:

```json
{"end_period_ts":1730847600,
 "open_interest":"75665260.00",
 "price":{"open":"0.5900","high":"0.5900","low":"0.5800","close":"0.5900","mean":"0.5898","previous":"0.5900"},
 "volume":"35471.00",
 "yes_bid":{"open":"0.5800","high":"0.5800","low":"0.5800","close":"0.5800"},
 "yes_ask":{"open":"0.5900","high":"0.5900","low":"0.5900","close":"0.5900"}}
```

Write one accessor: `pick(d, "close_dollars", "close")`.

### Two parsing rules that will silently corrupt a backtest if missed

1. **`price.close/high/low/mean` are ABSENT when `volume == 0`.** Only
   `price.previous_dollars` is present. Verified: of 471 one-minute candles on
   2026-08-04/05, 22 had zero volume and **0 of those had a `close_dollars` key**.
   The last-trade series must be forward-filled from `previous_dollars`.
   `yes_bid`/`yes_ask` are always present — **use the bid/ask, not the trade price, as
   the tradeable mark.**
2. **Candles are sparse, not contiguous.** They exist only for periods with activity
   (a trade *or* a quote change). Gap histogram over the Michigan election-night window:
   `{1 min: 462, 2 min: 6, 3 min: 2}` — dense during action. But in the quiet window
   2025-08-05 00:00–06:00Z the same market returned **13 candles for 360 minutes**.
   Re-index onto a regular grid with forward-fill before any rolling calculation.

### History depth — verified

| market | first daily candle | note |
|---|---|---|
| `SENATEMI-26-D` | **2024-12-04** | back to market inception |
| `KXSENATEMID-26-AELS` | 2025-03-29 | opened 2025-03-28 |
| `PRES-2024-DJT` (historical endpoint) | **2024-10-05** | Kalshi's election markets launched 2024-10-04 |

1-minute candles also reach inception — `SENATEMI-26-D` returns 1-min rows for
2025-02-15 and 2025-03-15 (2 and 1 rows: the market barely moved), 127 rows for
2025-06-15, 400 for 2025-08-15. **The apparent "no old 1-min data" is sparsity, not
retention.**

### Worked example 1 — the Michigan panic, reproduced

```bash
curl -s "https://api.elections.kalshi.com/trade-api/v2/series/KXSENATEMID/markets/KXSENATEMID-26-AELS/candlesticks?start_ts=1785880800&end_ts=1785909600&period_interval=1"
```
471 candles. Abridged (times UTC; 03:17Z = 11:17 pm ET Aug 4):

```
22:00  close 0.9840  vol      0.01  bid 0.9830  ask 0.9840
02:49  close 0.9200  vol  34333.88  bid 0.9200  ask 0.9250
02:58  close 0.8500  vol  56399.13  bid 0.8400  ask 0.8500
03:14  close 0.7800  vol  55434.01  bid 0.7800  ask 0.8000
03:16  close 0.7700  vol   5031.30  bid 0.7600  ask 0.7700
03:17  close 0.7600  low 0.7400  vol 4866.38  bid 0.7500  ask 0.7600   <-- the 74c trough
03:19  close 0.7600  vol  40418.91  bid 0.7500  ask 0.7600
03:23  close 0.7600  vol   2888.85  bid 0.7600  ask 0.7700
```

This **independently confirms the README's seed story** (98.4¢ → 74¢ trough at 11:17 pm
ET → recovery) from raw API data, and the daily candle for that session shows
`high 0.9990 / low 0.7400 / close 0.9420`. Note the trough print is one minute's `low`
with only ~4.9k contracts — the depth-capped sizing model matters.

### Worked example 2 — 2024 election night, via the historical endpoint

```bash
curl -s "https://api.elections.kalshi.com/trade-api/v2/historical/markets/PRES-2024-DJT/candlesticks?start_ts=1730847600&end_ts=1730876400&period_interval=1"
```
481 one-minute candles. Every 45th:

```
11-05 23:00  close 0.5900  vol  35471  bid 0.58  ask 0.59  oi 75,665,260
11-06 00:30  close 0.5900  vol  41208  bid 0.57  ask 0.59  oi 77,266,084
11-06 01:15  close 0.6700  vol 124195  bid 0.65  ask 0.67  oi 78,252,093
11-06 02:45  close 0.7400  vol  34793  bid 0.73  ask 0.74  oi 80,053,254
11-06 03:30  close 0.8400  vol  70514  bid 0.83  ask 0.84  oi 81,195,325
11-06 04:15  close 0.9200  vol  92169  bid 0.91  ask 0.92  oi 81,304,243
11-06 05:45  close 0.9500  vol 572316  bid 0.94  ask 0.95  oi 81,007,044
11-06 06:30  close 0.9700  vol  51710  bid 0.97  ask 0.98  oi 79,590,777
```

Daily candles: 108 rows, 2024-10-05 → 2025-01-20, 253,463,569 contracts total.
**We can backtest the 2024 election night.** (Contamination discipline still applies —
price-only strategies only, per `ORCHESTRATION.md` §2.)

---

## 6. Trades

### Live endpoint

```
GET /markets/trades?ticker=&min_ts=&max_ts=&limit=&cursor=&is_block_trade=
```

- `limit` max **1000** (`1001` → `400 … 'Limit' failed on the 'lte' tag`).
- `ticker` is **optional** — omitting it gives an exchange-wide firehose (verified;
  newest-first across all markets).
- Ordering is **newest → oldest**; `cursor` walks backwards.
- `min_ts`/`max_ts` filter on `created_time`. Verified: the 03:15–03:20Z window on
  Michigan election night returned exactly 276 trades bounded by
  `2026-08-05T03:15:04.805619Z` … `2026-08-05T03:19:59.076569Z`.

Real row:

```json
{"trade_id":"c0a3ff04-16ec-5d00-a376-925eb9961e30","ticker":"KXSENATEMID-26-AELS",
 "created_time":"2026-08-05T14:01:09.49656Z","count_fp":"57.51",
 "yes_price_dollars":"0.9980","no_price_dollars":"0.0020",
 "taker_outcome_side":"no","taker_book_side":"ask","taker_side":"no","is_block_trade":false}
```

### Depth: the live endpoint is a rolling window — this is the trap

Full pagination of `KXSENATEMID-26-AELS`: **26,400 trades over 27 pages, oldest
`2026-05-25T00:37:29.902238Z`**, cursor exhausted. Same boundary on two other markets:
`SENATEMI-26-D` oldest `2026-05-25T16:52:42Z`; `KXSENATEMID-26-HSTE` oldest
`2026-05-25T04:12:09Z`. Explicit windows in 2025-04, 2025-08 and 2026-01 return **0
trades**.

But the daily candles for the same market show **209,764 contracts traded on 243
distinct days between 2025-04-24 and 2026-05-17**. The trades are not gone — they moved
to `/historical/trades`.

### Historical endpoint — the archive

```
GET /historical/trades?ticker=&min_ts=&max_ts=&limit=&cursor=&is_block_trade=
```

Verified reach:

```bash
# 2024 election night, one hour, all markets
curl -s ".../historical/trades?min_ts=1730764800&max_ts=1730768400&limit=5"
```
```
KXSWINGSTATES24DJT-6   2024-11-05T00:59:59.921713Z  yes 0.1100  count 171.00
KXHIGHCHI-24NOV04-B68.5 2024-11-05T00:59:59.713547Z yes 0.9200  count   1.00
PRESPARTYNJ-24-R       2024-11-05T00:59:59.710065Z  yes 0.0700  count 428.00
POPVOTE-24-R           2024-11-05T00:59:59.149422Z  yes 0.2600  count  38.00
POPVOTEMOV-24-D-B2.5   2024-11-05T00:59:59.114975Z  yes 0.1200  count 221.00
```
```bash
# 2022 midterms
curl -s ".../historical/trades?min_ts=1667865600&max_ts=1667869200&limit=5"
```
```
HIGHNY-22NOV08-B57.5  2022-11-08T00:58:19.074802Z  yes 0.3100  count 12.00
APRPOTUS-22NOV11-B42.0 2022-11-08T00:52:02.939198Z yes 0.2300  count 10.00
```

**Tick history back to at least Nov 2022, unauthenticated, with `min_ts`/`max_ts`
windowing and 1000/page.** This is the single most valuable finding in this document.

### The cutoff, and how to route

```bash
curl -s "https://api.elections.kalshi.com/trade-api/v2/historical/cutoff"
```
```json
{"market_settled_ts":"2026-06-12T00:00:00Z","trades_created_ts":"2026-06-12T00:00:00Z",
 "orders_updated_ts":"2026-06-12T00:00:00Z","market_positions_last_updated_ts":"2026-06-12T00:00:00Z"}
```

Per [the docs](https://docs.kalshi.com/getting_started/historical_data), the cutoffs
"will be regularly updated, advancing forward over time. The target window for live data
is **3 months**." Partitioning applies to *markets, market_candlesticks, trades, orders,
market_positions*; **"Old Events and Series will always still be available through their
original endpoints."**

In practice the two tiers overlap (live trades reached 2026-05-25, ~18 days before the
stated cutoff). **Router rule:** query `/historical/cutoff` at start of a run; for
`ts < cutoff` use `/historical/*`; for `ts ≥ cutoff` use live; when a window straddles it,
query both and dedupe on `trade_id`.

> **Operational consequence:** the cutoff advances. Anything we want long-term must be
> archived locally now. The historical tier appears to be permanent, but that is
> **[UNVERIFIED]** — Kalshi has already demonstrated it will move data out from under us.

---

## 7. Order book (for depth-capped Kelly sizing)

```bash
curl -s ".../markets/SENATEMI-26-D/orderbook?depth=5"
```
```json
{"orderbook_fp":{
  "yes_dollars":[["0.5300","8849.00"],["0.5400","39014.50"],["0.5500","21613.80"],
                 ["0.5600","13340.00"],["0.5700","21582.76"]],
  "no_dollars":[["0.3700","3320.00"],["0.3800","2313.00"],["0.4000","200.00"],
                ["0.4100","2300.00"],["0.4200","14450.44"]]}}
```

Batch form: `GET /markets/orderbooks?tickers=A&tickers=B` (repeated param, max 100),
returns `{"orderbooks":[{"orderbook_fp":{...}}, ...]}`.

**Semantics (from OpenAPI):** *"It returns yes bids and no bids only (no asks are
returned). This is because in binary markets, a bid for yes at price X is equivalent to
an ask for no at price (100−X)."* So the YES ask ladder = `1 − no_dollars` prices,
reversed. Arrays are best-price-last in the observed response — sort explicitly, don't
assume.

**There is no historical order-book endpoint.** Depth for past dates must be
reconstructed from `yes_bid`/`yes_ask` in candlesticks (top-of-book only) plus trade
prints. For the FutureSearch-style ~43% fill realism, the honest approach is:
capture live order books going forward (snapshot job), and for backtests infer executable
size from the trade tape in the same minute rather than pretending we know depth.

---

## 8. Enumerating settled markets and their results

### Status vocabulary — filter values ≠ returned values

**Legal `status` filter values** (OpenAPI enum, confirmed by 400s on everything else):
`unopened`, `open`, `paused`, `closed`, `settled`.

```
status=finalized  → 400 {"details":"invalid status filter"}
status=active     → 400 {"details":"invalid status filter"}
status=determined → 400 {"details":"invalid status filter"}
```

**Values actually returned in the `status` field:** `active`, `closed`, `finalized`
(plus `unopened`/`initialized` presumably). Empirically `status=settled` returns records
whose `status` reads `finalized`, and `status=open` returns records whose `status` reads
`active`. **Never round-trip a returned status back into the filter.**

`result` values observed: `"yes"`, `"no"`, `"scalar"`, and `""` (empty — market closed
without settling, e.g. voided).

### Timestamp filters are mutually exclusive — pick the right one

From the OpenAPI description of `GET /markets`:

| Compatible timestamp filters | Allowed status filters |
|---|---|
| `min_created_ts`, `max_created_ts` | `unopened`, `open`, *empty* |
| `min_close_ts`, `max_close_ts` | `closed`, *empty* |
| **`min_settled_ts`, `max_settled_ts`** | **`settled`, *empty*** |
| `min_updated_ts` | *empty* (requires `mve_filter=exclude`) |

**Use `min_settled_ts`/`max_settled_ts` + `status=settled`** for "what settled in this
window". Using `min_close_ts` instead is what made my first pass look like history had
been deleted.

### The canonical settled-markets query

```bash
curl -s ".../markets?min_settled_ts=1785888000&max_settled_ts=1785974400\
&status=settled&mve_filter=exclude&limit=1000"
```

Full pagination of that one day (2026-08-05) — **40,000 markets and still more pages**:

```
total settled 2026-08-05 (mve excluded): 40000, 40 pages, cursor still non-empty
results: {'no': 25652, 'yes': 14076, 'scalar': 272}
```

Without `mve_filter=exclude` the same day exceeded **200,000** records — the
`KXMVESPORTSMULTIGAMEEXTENDED-*` multivariate combo markets dominate. **Always pass
`mve_filter=exclude` unless you specifically want combos.**

### Pagination contract

`{"cursor": "...", "markets": [...]}`. Empty-string cursor = done. `limit` default 100,
max 1000. **A page can be empty while the cursor is still non-empty** — I hit this and
briefly concluded history was missing. Loop on `cursor`, not on `len(markets)`.

### Markets older than the cutoff

`GET /markets?event_ticker=PRES-2024` returns **0**; so does
`GET /events/PRES-2024?with_nested_markets=true`. The event still exists (series/events
are never archived). The markets are in the historical tier:

```bash
curl -s ".../historical/markets?event_ticker=PRES-2024&limit=20"
```
```
PRES-2024-DJT  finalized  yes  close 2025-01-20T17:03:48Z  volume 262,334,207  Donald Trump
PRES-2024-KH   finalized  no   close 2025-01-20T17:03:48Z  volume 273,312,857  Kamala Harris
PRES-2024-RFK  finalized  no                               volume     224,361  Robert F. Kennedy Jr.
PRES-2024-JS / -CW / -CO   finalized  no                                        (6 total)
```

**`GET /historical/markets` accepts only `limit`, `cursor`, `tickers` (comma list), and
`event_ticker` — "Filters are mutually exclusive" and there are NO date filters.**
Confirmed empirically: passing `min_close_ts`/`max_close_ts` is silently ignored (identical
first page for 2021, 2022, 2023, 2024 and 2025 queries).

> **Therefore the only way to enumerate historical settled markets is:
> series → events → `/historical/markets?event_ticker=…`.** There is no date-ranged
> historical market scan. Plan the crawl accordingly.
>
> `GET /historical/markets/{ticker}` also works for single lookups.

### Recommended enumeration recipe (elections/politics, full history)

```
1. GET /series?limit=1000                          # 1 call, 12,658 series, 15.9 MB
   → keep category in {Elections, Politics} → 3,698 series
2. for each series: GET /events?series_ticker=X&limit=200&with_nested_markets=true
   → ~1.9 events/series in a sample of 8 (15 events, 79 markets from 8 series in 2.4 s)
   → project ~4.6k events, ~30k markets
3. for events whose markets came back empty (archived):
   GET /historical/markets?event_ticker=E&limit=1000
4. for each market: GET .../candlesticks?period_interval=1440  (one call, whole life)
   then 1-min only for the windows the strategy needs (3-day chunks)
5. trades: /markets/trades for ts >= cutoff, /historical/trades for ts < cutoff
```

Measured cost: step 2 runs at ~3.3 req/s single-threaded / ~13 req/s at 8 workers, so the
whole elections+politics event crawl is **~5 minutes**. Daily candles for 54 markets took
4.2 s at 8 workers (22,620 candles, 0 errors) → all ~30k election markets' daily candles
in **~40 minutes**. That is entirely affordable inside the disk budget in
`ORCHESTRATION.md`.

---

## 9. Rate limits — measured, not quoted

Official tiers: independent read/write token buckets refilled per second, most requests
cost 10 tokens; Basic = 200 read tokens/s → **20 reads/s**
([docs.kalshi.com/getting_started/rate_limits](https://docs.kalshi.com/getting_started/rate_limits),
as recorded in `docs/prior-art.md` §2).

Measured from this container, unauthenticated:

| pattern | result |
|---|---|
| 20 sequential requests | 20×200, 6.1 s, **3.3 rps** (latency-bound, ~0.27 s RTT via proxy) |
| 40 sequential requests | 40×200, 13.5 s, **3.0 rps** |
| 8 workers × 80 requests | **80×200, 0 errors, 17.7 rps** |
| 24 workers × 240 requests | 215×200 + **25×429**, 32.6 rps |
| 30 workers × 120 requests | 21×429 |
| 8 workers, 54 candlestick pulls | 0 errors, 12.9 rps |

429 body (no `Retry-After`, no rate headers):

```
HTTP/1.1 429
{"error":{"code":"too_many_requests","message":"too many requests"}}
```

Responses carry `Cache-Control: public, max-age=15`, `x-kalshi-cache-hits`, and are served
via CloudFront — repeated identical queries within 15 s may be cached, so **add a cache-
busting param when benchmarking, and don't mistake a cached 200 for a fresh one.**

**Puller settings: 8 concurrent workers, ~15 rps ceiling, exponential backoff on 429
starting at 1 s.** That is comfortably inside Basic and leaves headroom for a real
account's live trading traffic. Kalshi documents that 429s carry no penalty, but be
polite anyway — this is an unauthenticated shared IP.

---

## 10. Demo environment — signup requirements

- Sign up at **https://demo.kalshi.co/sign-up**. **A production account is NOT required**;
  credentials are completely separate between environments.
- Kalshi's own tutorial says to **use mock information (fake name, address, SSN)** — so no
  real KYC. You need a working email + password.
- The account starts unfunded. Add mock funds with test payment methods: Visa
  `4000 0566 5566 5556`, Mastercard `5200 8282 8282 8210`, Plaid sandbox
  `user_good`/`pass_good`, or testnet crypto faucets.
- API root: `https://external-api.demo.kalshi.co/trade-api/v2`
  (also `https://demo-api.kalshi.co/trade-api/v2`); WS
  `wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2`.
- Demo API keys are generated in the demo account's settings and **only work against demo
  endpoints**. The help article does not cover key generation; the flow is the same
  RSA-keypair flow as production. **[UNVERIFIED — I could not create an account from this
  container.]**
- Kalshi warns: *"The price and behavior of markets in the demo environment may not be
  reflective of those in real markets."* Treat demo as an **order-plumbing test only**;
  never as a source of fills or P&L for the benchmark.

Sources: [demo_env doc](https://docs.kalshi.com/getting_started/demo_env.md),
[help.kalshi.com creating-and-using-a-demo-account](https://help.kalshi.com/en/articles/13823775-creating-and-using-a-demo-account).

**Geographic restrictions on demo: [UNVERIFIED]** — not mentioned in the docs.

---

## 11. Gotcha list (paste this into the puller's docstring)

1. Cursor pagination: **loop until `cursor == ""`, not until a page is empty.**
2. `status=` filter values (`settled`) differ from returned `status` values (`finalized`).
3. Timestamp filters are mutually exclusive; `min_settled_ts` is the one for settlement.
4. Always `mve_filter=exclude` — combos are 80% of the market count.
5. `/historical/markets` has **no date filters**; go via `event_ticker`.
6. Two candlestick JSON shapes (`close_dollars` vs `close`).
7. `price.close` is **missing** when `volume == 0`; forward-fill from `previous`.
8. Candles are sparse; re-index onto a grid before rolling stats.
9. `period_interval` ∈ {1, 60, 1440}; **5,000-candle cap per request**.
10. Daily candles bucket at **04:00 UTC** (= midnight ET), not 00:00 UTC.
11. Series tickers get reused across states/years (`KXSENATEMID-25` = *Minnesota*).
    Never parse semantics out of a ticker; read the event title.
12. Tick size varies (`linear_cent` vs `tapered_deci_cent`); read `price_ranges`.
13. Counts are fractional (min 0.01 contracts); prices up to 6 dp. `Decimal` everywhere.
14. `close_time` ≠ `expiration_time` ≠ `settlement_ts` when `can_close_early` is true.
15. Category/tags live only on the **series**; join through `event_ticker`.
16. Exclude `is_block_trade: true` prints from fill simulation.
17. Responses are CloudFront-cached for 15 s.
18. `limit` max: 1000 for markets/trades/series; 100 tickers for batch endpoints.

---

## 12. Open items / what I could not verify

| Item | Status |
|---|---|
| `kalshi-fee-schedule.pdf` exact tables | **Blocked** — HTTP 429 Vercel checkpoint on 5 attempts, Wayback + jina.ai blocked by this container's proxy. Someone must download it manually. |
| Exact maker-fee coefficient (0.0175?) | Triangulated from 2 secondary sources; the docs' worked examples are inconsistent with any single coefficient. |
| Volume-tier fee discounts (reported ~12.0 → 2.6 bps) | **[UNVERIFIED]** — not visible in any API field. |
| Whether `/historical/*` retention is permanent | **[UNVERIFIED]** — docs imply yes, but archive locally regardless. |
| How far back `/series/fee_changes?show_historical=true` reaches | Only 106 rows returned, newest 2026-08-07. Probably not 2024. |
| Demo API-key generation flow + geo restrictions | **[UNVERIFIED]** — no account created. |
| `flat` fee type behaviour | Enum exists; **0 series currently use it**. |
| Historical order-book depth | **Does not exist.** Must be captured live going forward. |
| `forecast_percentile_history` endpoint (`/series/{s}/events/{e}/forecast_percentile_history`) | Exists in the spec; **not probed.** Possibly interesting for scalar/range markets. |

---

## 13. Corrections to existing repo docs

- `ORCHESTRATION.md` §1 — "taker ≈ round(0.07·P·(1−P),2)/contract": the rounding is
  **ceiling to $0.0001**, not `round(...,2)`, and there is a per-order rounding-fee /
  rebate accumulator on top. See §3.4.
- `docs/prior-art.md` §2 "Fees" — "multiplier 0.07 for most categories (higher for
  premium categories like crypto)": **no series has `fee_multiplier > 1`**; observed set
  is {1, 0.5, 0}, and two crypto series (`KXBTCY`, `KXETHY`) are at **0**. Maker fees
  apply to only **130 of 12,658 series**, none of them Elections or Politics.
- `docs/prior-art.md` §2 "Market data" — the endpoint list omits the entire
  `/historical/*` tier, `/markets/candlesticks` (batch), `/markets/orderbooks` (batch),
  and `/historical/cutoff`. Those change the data-layer design materially.
- `README.md` "The seed example, now verified" — reconfirmed here directly from
  candlesticks (98.4¢ → 74¢ at 03:17Z → recovery, daily candle
  `high 0.9990 / low 0.7400 / close 0.9420`). Correct as written.

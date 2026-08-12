# Polymarket historical-data cookbook

**Compiled 2026-08-11 by a research agent, verified empirically with live `curl` calls from this
container.** Every endpoint, parameter, limit, and error message below was executed against the
live production APIs on 2026-08-11 unless explicitly marked `[UNVERIFIED]` or `[DOCS ONLY]`.
Responses are real and truncated for length.

**Bottom line for the build:**

| Question | Answer |
|---|---|
| Can we reconstruct price time series for RESOLVED markets? | **Yes — 1-minute resolution, complete, back to ~Dec 2022.** But only via chained `startTs`/`endTs` windows of ≤15 days. `interval=max` silently returns only the last 31 days and is **empty** for resolved markets. |
| Is the series a trade tape? | **No — it is the CLOB midpoint**, sampled/forward-filled. Verified exact match against `/midpoint`. No bid/ask, no volume, no trade size. |
| Can we get historical order books? | **No.** `/book` 404s for closed markets. No historical depth anywhere in the public API. Must snapshot live. |
| Can we get historical trade tape? | **Partially.** `data-api /trades` gives the real tape (price, size, side, wallet) but is capped at **offset ≤ 10,000**. For thin markets (< ~$100k volume) that is the entire life of the market. For $100M markets it is the last ~19 hours. |
| Fees | Taker-only, `fee = shares × rate × p × (1 − p)`, rate 0.04–0.07 by category, geopolitics free. Per-market schedule is **published in the Gamma market record** (`feeSchedule`). Makers pay 0 and receive a 15–25% rebate. |
| API keys | **Not needed for any read path used here.** Needed only for order placement / private CLOB ledger. |

---

## 0. Hosts, auth, and the one deprecation you must know

| Host | Purpose | Auth |
|---|---|---|
| `https://gamma-api.polymarket.com` | Market/event metadata, filtering, resolution status, **fee schedules** | none |
| `https://clob.polymarket.com` | Price history, live book/midpoint/spread, market definitions, trading | none for read; L1+L2 for trading |
| `https://data-api.polymarket.com` | Trade tape, holders, open interest, positions | none |
| `wss://ws-subscriptions-clob.polymarket.com/ws/market` | Live book/price streaming | none for market channel |

All three roots are up:

```
$ curl -s https://clob.polymarket.com/       →  "OK"
$ curl -s https://data-api.polymarket.com/   →  {"data":"OK"}
```

**Auth**: read endpoints on Gamma, CLOB market-data, and Data API require no credentials
([docs](https://docs.polymarket.com/developers/CLOB/authentication)). Trading uses two layers —
L1 = EIP-712 signature from the wallet private key (used once to create/derive an API key),
L2 = HMAC-SHA256 request signing with headers `POLY_ADDRESS`, `POLY_SIGNATURE`, `POLY_TIMESTAMP`,
`POLY_API_KEY`, `POLY_PASSPHRASE`. Verified: `GET /trades` on the CLOB host (the private *ledger*
endpoint, distinct from data-api's public tape) returns `401 {"error":"Unauthorized/Invalid api key"}`.

### ⚠️ `GET /markets` on Gamma is deprecated

The offset-paginated `/markets` endpoint still works but ships deprecation headers:

```
$ curl -sD - -o /dev/null "https://gamma-api.polymarket.com/markets?limit=1"
deprecation: true
sunset: Fri, 01 May 2026 00:00:00 GMT
warning: 299 - "use /markets/keyset"
```

The sunset date has already passed. **Build the data layer on `/markets/keyset`.** Offset paging on
`/markets` also hard-fails past ~2,000:

```
offset=2000 -> 200
offset=3000 -> 422   {"type":"validation error","error":"offset too large, use /markets/keyset for deeper pagination"}
```

`limit` is silently clamped to **100** on both (`limit=5000` returns 100 rows).

---

## 1. Gamma API — finding and characterising resolved markets

### 1.1 Full parameter list (from the live OpenAPI spec)

`https://gamma-api.polymarket.com/openapi.json` is public (276 KB, 115 paths). `/markets` and
`/markets/keyset` accept an identical parameter set:

```
id[]  slug[]  archived  active  decimalized  closed  clob_token_ids[]  position_ids[]
condition_ids[]  market_maker_address[]  liquidity_num_min  liquidity_num_max
volume_num_min  volume_num_max  start_date_min  start_date_max  end_date_min  end_date_max
tag_id[]  related_tags  tag_match  cyom  rfq_enabled  combo_status  uma_resolution_status
game_id  sports_market_types[]  rewards_min_size  question_ids[]  include_tag  locale
market_metadata_key  market_metadata_value  limit  offset  order  ascending  after_cursor
```

Dates accept RFC3339 **or** bare `YYYY-MM-DD`. All verified working.

### 1.2 Keyset pagination (the enumeration primitive)

The cursor parameter is **`after_cursor`** (not `cursor` / `next_cursor` — those are silently
ignored and you get page 1 forever; this cost us three probes):

```bash
curl -s "https://gamma-api.polymarket.com/markets/keyset?limit=3&closed=true"
# → {"$schema":"...","markets":[{"id":"12",...},{"id":"17",...},{"id":"18",...}],
#    "next_cursor":"MzMfo31_7wsqXsjvfkCKpxjnLEQksP9BvChtCJBJg9B7InYiOjEsImsi..."}

curl -s "https://gamma-api.polymarket.com/markets/keyset?limit=3&closed=true&after_cursor=<next_cursor>"
# → markets: ['19','20','36']   ✅ advances
```

Filters compose with the cursor (verified with `closed=true&volume_num_min=50000&end_date_min=...`).

### 1.3 The universe available to us

Counting resolved markets in the contamination-safe window (ORCHESTRATION.md constraint #2:
resolution after Feb 2026):

```bash
# closed=true, volumeNum >= 50k, endDate in [2026-02-01, 2026-08-01]
# paged 120 × 100 with after_cursor → 12,000 rows and still not exhausted
```

Composition of the first 12,000 (by `events[0].seriesSlug`):

```
none 2439 | btc-up-or-down-15m 1267 | eth-up-or-down-15m 744 | nba-2026 623
ncaa-cbb 465 | btc-up-or-down-5m 448 | btc-up-or-down-hourly 351
league-of-legends 308 | eth-up-or-down-hourly 259 | atp 256 | wta 219 | elon-tweets 204
```

**Actionable:** the population is dominated by crypto up/down and sports micro-markets. The
FutureSearch-style filter (no sports, no crypto, >10 days horizon) has to be applied via
`tag_id` / `seriesSlug` exclusion or the sample is 80% noise. `feeType` is a fast proxy:
`crypto_15_min` (2,476 rows) and `sports_fees_v2` (659) can be dropped outright.

### 1.4 Resolution / outcome fields — what is authoritative

A **single-market fetch** returns more than the list endpoint. Real response:

```bash
curl -s "https://gamma-api.polymarket.com/markets/2169995"
```
```json
{"id":"2169995",
 "question":"MicroStrategy sells any Bitcoin by May 31, 2026?",
 "slug":"microstrategy-sells-any-bitcoin-by-may-31-2026",
 "startDate":"2026-05-05T23:48:20.102Z",
 "endDate":"2026-07-01T04:00:00Z",
 "closedTime":"2026-06-04 00:34:19+00",
 "closed":true, "active":true, "archived":false,
 "umaResolutionStatus":"resolved",
 "outcomes":"[\"Yes\", \"No\"]",
 "outcomePrices":"[\"0\", \"1\"]",
 "conditionId":"0x3733a1b647e7364095736ab0966465d896a84cf3b6bc1695ca1f26c3239b3868",
 "clobTokenIds":"[\"25714007960293389110960044475283546872601238755063051359394740854408462452120\",
                  \"3192689304828767159232889612891719105504357313659012189260030438494464480574\"]",
 "feesEnabled":true, "feeType":"finance_prices_fees",
 "feeSchedule":{"exponent":1,"rate":0.04,"takerOnly":true,"rebateRate":0.25}}
```

Gotchas, all verified:

* **`outcomes`, `outcomePrices`, `clobTokenIds` are JSON-encoded *strings***, not arrays.
  `json.loads(m['clobTokenIds'])` every time. `positionIds` *is* a real array. Inconsistent by design.
* **`endDate` is the nominal deadline, not when trading stopped.** MicroStrategy: `endDate`
  2026-07-01, `closedTime` 2026-06-04. Anchoring price-history windows on `endDate` returns
  **empty** — this is the single most likely bug in a naive puller. **Always anchor on `closedTime`.**
* `closedTime` is `"YYYY-MM-DD HH:MM:SS+00"` (space separator, 2-digit offset) — not ISO-8601.
  Parse defensively.
* The **list** endpoint returns `umaResolutionStatuses` (plural array, one per UMA question) while
  the **single** endpoint returns `umaResolutionStatus` (singular string). Both exist.
* `uma_resolution_status=resolved` works as a query filter (verified).
* `active:true` + `closed:true` co-occur — `closed` is the settlement flag, `active` is not "tradeable".
* Looking up a resolved market by slug on the list endpoint requires `closed=true`:
  `?slug=<s>` alone returns `[]`; `?slug=<s>&closed=true` returns the row. Or use the clean path
  form `GET /markets/slug/{slug}` which works regardless.

**The authoritative winner** is on the CLOB side:

```bash
curl -s "https://clob.polymarket.com/markets/0x3733a1b647e7364095736ab0966465d896a84cf3b6bc1695ca1f26c3239b3868"
```
```json
{"question":"MicroStrategy sells any Bitcoin by May 31, 2026?","closed":true,
 "accepting_orders":false,"minimum_tick_size":0.001,"minimum_order_size":5,"neg_risk":false,
 "tokens":[{"token_id":"257140079...","outcome":"Yes","price":0,"winner":false},
           {"token_id":"319268930...","outcome":"No",  "price":1,"winner":true}]}
```

`tokens[].winner` + `tokens[].price ∈ {0,1}` is the settlement truth. Cross-check it against Gamma's
`outcomePrices` and reject any market where they disagree — cheap, catches mid-dispute states
(`umaResolutionStatuses: ["proposed","disputed",...]` appears on live rows).

`GET /markets` on the CLOB host enumerates every market ever (cursor-paginated, `count: 1000`
per page, `next_cursor` base64) and is the only place that exposes `enable_order_book` for old
markets — useful to skip the 2020–2022 AMM era that has no price history (§2.4).

---

## 2. CLOB `prices-history` — the workhorse

### 2.1 Signature

```
GET https://clob.polymarket.com/prices-history
  market   (required)  CLOB **token id** (the ERC-1155 position id), NOT conditionId, NOT market id
  startTs  (optional)  unix seconds
  endTs    (optional)  unix seconds
  interval (optional)  max | all | 1m | 1w | 1d | 6h | 1h   ← "1m" is one MONTH, not one minute
  fidelity (optional)  bucket size in MINUTES, default 1
→ {"history":[{"t":<unix s>,"p":<float>}, ...]}
```
([docs](https://docs.polymarket.com/developers/CLOB/timeseries))

### 2.2 The two hard limits (measured, not documented)

**(a) `startTs`/`endTs` window is capped at exactly 1,296,000 s = 15 days**, independent of `fidelity`:

```
window=15d (1296000s) → HTTP 200
window=1300000s       → HTTP 400 {"error":"invalid filters: 'startTs' and 'endTs' interval is too long"}
fidelity=1 / 60 / 10080 at 16d → all 400
```

**(b) `interval=max` and `interval=all` return exactly 31 days**, regardless of market age.
Measured across 40 active markets aged 106–396 days: `max_span = 31.0d` for every single one.
The name is a lie. Do not use `max` for backfill.

Minimum `fidelity` is enforced per interval:

```
interval=1w, fidelity=1  → 400 {"error":"invalid filters: minimum 'fidelity' for '1w' range is 5"}
interval=1m, fidelity=5  → 400 {"error":"invalid filters: minimum 'fidelity' for '1m' range is 10"}
interval=1h/6h/1d        → fidelity=1 accepted
```
With explicit `startTs`/`endTs`, `fidelity=1` is accepted for the full 15-day window (21,600 points).

### 2.3 ⚠️ `interval=max` returns EMPTY for resolved markets

```bash
# MicroStrategy YES token, market resolved 2026-06-04
curl -s ".../prices-history?market=25714007...&interval=max"   → {"history":[]}
curl -s ".../prices-history?market=25714007...&interval=all"   → {"history":[]}
curl -s ".../prices-history?market=21742633...&interval=max"   → {"history":[]}   # Trump 2024
```

Because the interval is anchored to *now* and a closed market has no data in the last 31 days.
**Resolved-market history is only reachable through explicit `startTs`/`endTs`.** A puller that
uses `interval=max` will conclude, wrongly, that Polymarket has no history for settled markets.

### 2.4 Coverage: how far back

Probed the highest-volume closed market in successive windows, anchored on `closedTime`,
`fidelity=60`, 15-day window ending at close:

```
closedTime 2022-09-05  Ethereum Merge (EIP-3675)          pts=0
closedTime 2022-09-15  Ethereum Merge                     pts=0
closedTime 2022-09-21  Fed increase rates                 pts=0
closedTime 2022-11-13  Which party controls the Senate    pts=0
closedTime 2022-11-17  Which party wins the House         pts=0
closedTime 2023-01-02  Will @realDonaldTrump tweet in 2022?   pts=333   ← first data
closedTime 2023-02-13  Super Bowl LVII: Eagles vs Chiefs       pts=287
closedTime 2023-03-23  Arbitrum airdrop by March 31?           pts=340
closedTime 2023-05-29  Will Erdoğan win the 2023 Turkish...    pts=355
closedTime 2024-01-10  Bitcoin ETF approved by Jan 15?         pts=359
closedTime 2024-11-05  Trump 2024 presidency                   pts=360
closedTime 2025-11-05  Mamdani NYC mayoral                     pts=356
closedTime 2026-06-04  MicroStrategy sells any Bitcoin         pts=358
```

**Practical start of coverage: ~December 2022.** Pre-2023 Polymarket was the FPMM/AMM design
(`enable_order_book: false`, `fpmm` address populated) and those markets have **no** CLOB price
series — verified on the 2021 Netanyahu market (0 points across four consecutive windows). Skip
anything with `enableOrderBook != true` or `endDate < 2023-01-01`.

### 2.5 ⚠️ `p` is the MIDPOINT, not the last trade

Verified across the six highest-24h-volume live markets — `prices-history` last point equals
`/midpoint` to the digit, and differs from `/last-trade-price`:

```
market                              hist_last   midpoint   last_trade   bid     ask
Will Adanech Abiebie be the next…    0.0055      0.0055     0.003        0.003   0.008
Will Kai and Speed beat the Mine…    0.215       0.215      0.22         0.21    0.22
Strait of Hormuz traffic returns…    0.0385      0.0385     0.04         0.038   0.039
US x Iran Effective Ceasefire by…    0.955       0.955      0.95         0.95    0.96
Will the Fed increase interest r…    0.395       0.395      0.41         0.39    0.40
Clarity Act (H.R.3633) signed in…    0.215       0.215      0.22         0.21    0.22
```

**Consequences for the backtest harness (important):**

1. The series is **continuous and forward-filled** — 21,600 points per 15-day window at
   `fidelity=1` means every minute has a value even with zero trades. Do not treat a point as
   evidence a trade happened.
2. **Filling at `p` is filling at mid, which constraint #4 forbids.** You must add a spread model.
   The first market above has a 0.003/0.008 book — a 62% half-spread at mid 0.0055. Cheap-longshot
   strategies are exactly where the mid series lies to you most.
3. Recover a spread proxy per market from the Gamma record: `bestBid`, `bestAsk`, `spread`,
   `orderPriceMinTickSize` are all stored on the market row. That is a *current* snapshot, not
   point-in-time — flag it as an approximation. The honest version is: charge
   `max(observed_spread/2, 1 tick)` on every taker fill, and require resting limit orders
   (maker) to be validated against the data-api trade tape (§4) before crediting a fill.
4. Prices are quantised to `orderPriceMinTickSize` (0.001 for large markets, 0.01 for older/thin
   ones). Sub-tick "edge" is noise.

### 2.6 Worked example: full history of a resolved market

Zohran Mamdani, 2025 NYC mayoral — $143M volume, 197-day life, chained 15-day windows:

```python
m = GET gamma /markets/slug/will-zohran-mamdani-win-the-2025-nyc-mayoral-election
# startDate 2025-04-22T16:10:05Z   closedTime 2025-11-05 05:44:47+00
tok = json.loads(m['clobTokenIds'])[0]        # YES token
s = ts(m['startDate']); end = ts(m['closedTime'])
pts = []
while s < end:
    e = min(s + 1_296_000, end)
    pts += GET clob /prices-history?market={tok}&startTs={s}&endTs={e}&fidelity=1 ['history']
    s = e
```

Result:

```
span days              196.57
total 1-min points     282,780     (vs 283,055 theoretical → 99.90% coverage)
duplicate timestamps   0
first  2025-04-22T16:24:06  p=0.275
last   2025-11-05T05:44:06  p=0.9995
min    0.035  @ 2025-05-23T20:04:06
max    0.9995 @ 2025-11-05T03:11:05
outcomePrices ["1","0"]  → YES resolved true
```

14 HTTP calls, ~7 s. **This is a clean, complete, minute-resolution reconstruction of a resolved
market including the resolution.** The answer to the core question is yes.

### 2.7 Throughput and the batch endpoint

Sequential vs parallel, `fidelity=1`, 15-day windows, all HTTP 200, no throttling observed:

```
sequential 10 requests → 5.15 s   (each ~21,600 points)
parallel   10 requests → 1.44 s
```

There is also an undocumented-in-the-UI **batch endpoint** — verified working:

```bash
curl -s -X POST https://clob.polymarket.com/batch-prices-history \
  -H 'Content-Type: application/json' \
  -d '{"markets":["<tok1>","<tok2>","<tok3>"],"start_ts":1786400000,"end_ts":1786480000,"fidelity":60}'
```
```json
{"history":{"101885144930706737105576340105373476383506126608825983231914615563722778767317":
  [{"t":1786402809,"p":0.205},{"t":1786406420,"p":0.125},{"t":1786410009,"p":0.155}, ...]}}
```

* **Max 20 markets per request** ([docs](https://docs.polymarket.com/api-reference/markets/get-batch-prices-history.md)).
* Note the response is `{"history": {token_id: [...]}}` — a *map*, unlike the singular endpoint's list.
* **Same 15-day cap applies**: `days=15,fidelity=1 → 21,594 pts in 1.6 s`; `days=16 → 400 interval is too long`.
* 20 tokens × 21,594 minute-bars in ~1.6 s ⇒ a full-history pull of ~1,000 filtered resolved markets
  is a few minutes of wall clock. Budget ~1.5 MB of JSON per market-year at `fidelity=1`; store
  parquet at `fidelity=1` for event windows and `fidelity=60` for the long tail to stay inside the
  1–2 GB disk allowance.

---

## 3. Order books — the gap

**There is no historical order book, at any granularity, in the public API.**

```bash
# live market
curl -s "https://clob.polymarket.com/book?token_id=32338220190071351435772801779725302244575775216413325951443816017994629993401"
{"market":"0xa467b14d...","asset_id":"3233822019...","timestamp":"1786483975529",
 "hash":"5c0a5bcdcccf42ff587887ae9b6a4baa0c01ab8c",
 "bids":[{"price":"0.001","size":"10607926.22"},{"price":"0.002","size":"2321169.67"},{"price":"0.003","size":"185934.2"}],
 "asks":[{"price":"0.999","size":"1502655.66"},{"price":"0.998","size":"245464.95"},{"price":"0.997","size":"12549.43"}],
 "min_order_size":"5","tick_size":"0.001","neg_risk":false,"last_trade_price":"0.045"}

# resolved market
curl -s "https://clob.polymarket.com/book?token_id=25714007960293389110960044475283546872601238755063051359394740854408462452120"
{"error":"No orderbook exists for the requested token id"}
```

Other live-only quote endpoints, all unauthenticated and working:
`/midpoint?token_id=` → `{"mid":"0.0455"}` · `/price?token_id=&side=buy` → `{"price":"0.045"}` ·
`/spread?token_id=` → `{"spread":"0.001"}` · `/last-trade-price?token_id=` → `{"price":"0.045","side":"SELL"}`.
Batch variants `/books`, `/prices`, `/midpoints` exist (documented rate limit 500 req/10 s).

**The subgraph route is dead.** All three historically-cited Goldsky endpoints
(`polymarket-orderbook-resync`, `polymarket-activity-polygon`, `polymarket-pnl` under project
`cl6mb8i9h0003e201j6li0dii`) return:

```json
{"statusCode":404,"message":"Subgraph not found. Have you deleted this subgraph recently? ..."}
```

`[UNVERIFIED]` whether Polymarket publishes a current subgraph elsewhere — nothing in the live
`llms.txt` docs index mentions one. On-chain reconstruction from Polygon CTF Exchange logs is
possible in principle but is a multi-day project, not a today project.

**What to do instead:**

1. **Depth caps must be measured forward, not backward.** Stand up a snapshotter now
   (`wss://ws-subscriptions-clob.polymarket.com/ws/market`, or REST `/books` polling at 1–5 s on a
   watchlist) writing book snapshots to parquet. Every hour it runs from today is depth data the
   backtest cannot otherwise get. Cheap to build, and the P5 tournament will want it.
2. **For the historical backtest**, derive the depth cap from the trade tape (§4): the realised
   traded notional in the N minutes around your entry is a defensible upper bound on what you
   could have taken. This is the same "$1–3k mid-tier depth" figure the README cites, but measured
   per market instead of assumed.
3. `liquidityNum` on the Gamma record (`liquidity`, `liquidityClob`) is a *current* USD liquidity
   score, not point-in-time depth. Do not backtest against it.

---

## 4. Data API — the real trade tape

### 4.1 `GET /trades`

```bash
curl -s "https://data-api.polymarket.com/trades?market=0x3733a1b647e7364095736ab0966465d896a84cf3b6bc1695ca1f26c3239b3868&limit=1"
```
```json
[{"proxyWallet":"0xceb57e549f8aa0627f4a4c6004bc8951d78ea27b","side":"BUY",
  "asset":"25714007960293389110960044475283546872601238755063051359394740854408462452120",
  "conditionId":"0x3733a1b647e7364095736ab0966465d896a84cf3b6bc1695ca1f26c3239b3868",
  "size":32862.45,"price":0.001,"timestamp":1780533322,
  "title":"MicroStrategy sells any Bitcoin by May 31, 2026?",
  "slug":"microstrategy-sells-any-bitcoin-by-may-31-2026",
  "eventSlug":"microstrategy-sell-any-bitcoin-in-2025","outcome":"Yes","outcomeIndex":0,
  "name":"grenadine","pseudonym":"Trivial-Gopher",
  "transactionHash":"0x63b0437aa4f525918d650d5c2bed11ff5a1714bfa5b48dfca4e27cf204d92432"}]
```

`market` takes the **conditionId** (0x…), unlike prices-history which takes the token id. This
endpoint works fine for fully resolved markets.

**Verified behaviour:**

| Property | Finding |
|---|---|
| `limit` | 500 / 1000 / 2000 all honoured exactly. No cap found up to 2000. |
| `offset` | **Hard cap 10,000**: `{"error":"max historical trades offset of 10000 exceeded"}` |
| Time filters | **None exist.** `startTs`, `endTs`, `before`, `after`, `from`, `min_ts` are all silently ignored (each returned the same newest trade). |
| Ordering | Newest first. |
| `filterType=CASH&filterAmount=N` | **Works** — filters to trades ≥ $N notional. This is the only lever for reaching further back. |
| `takerOnly` | accepted; effect not isolated in testing `[UNVERIFIED]` |

**Reach, measured on the $143M Mamdani market** (closed 2025-11-05T05:44):

```
offset=0                    → 2025-11-05T05:45:05
offset=9999                 → 2025-11-04T10:57:09     ← only ~19 hours of tape
offset=0    CASH≥$1000      → 2025-11-05T05:44:19  ($9,972)
offset=2000 CASH≥$1000      → 2025-11-03T14:42:03  ($1,159)
offset=5000 CASH≥$1000      → 2025-10-26T05:22:09  ($4,093)   ← 10 days back
offset=2000 CASH≥$5000      → 2025-10-17T10:54:42  ($23,437)  ← 19 days back
offset=5000 CASH≥$5000      → []                              ← whole-life exhausted
```

**Reach on thin markets — this is the good news.** Full tape pulled to exhaustion:

```
vol=$58,900  Will Fiorentina win the 2025–26 Serie A league?   840 trades  (complete)
vol=$53,801  Will Cremonese win the 2025–26 Serie A league?    507 trades  (complete)
```

**Actionable:** the 10,000-offset cap is a non-issue for exactly the market segment the thesis
targets — thin/neglected mid-tier markets. For those we get the *entire* trade history: price,
size, side, wallet, timestamp. That is enough to (a) build honest fill simulation, (b) measure
real depth, (c) compute maker-fill probability at resting limit prices, and (d) identify recurring
counterparties. For $10M+ markets, run a stratified pull: unfiltered tape for the tail plus
`CASH≥$1000` and `CASH≥$5000` sweeps for the whale flow deeper in history.

### 4.2 `GET /holders`

```bash
curl -s "https://data-api.polymarket.com/holders?market=<conditionId>&limit=3"
```
```json
[{"token":"25714007960293389110960044475283546872601238755063051359394740854408462452120",
  "holders":[{"proxyWallet":"0x59aed45d6b8c0a4fc67af69a371007b3cceb22d5","pseudonym":"Overlooked-Dentist",
              "amount":20278769.746834,"outcomeIndex":0,"name":"0x59Aed45d...-1730864521381"},
             {"proxyWallet":"0x9097b9fd27dd69aa8170e1b16f1b8b839ad70ef0","pseudonym":"Burdensome-Fav",
              "amount":7912828.932664,"outcomeIndex":0,"name":"kahanetzadak"}, ...]},
 {"token":"3192689304828767159232889612891719105504357313659012189260030438494464480574","holders":[...]}]
```

Requires `market` (conditionId); `?market=<tokenId>` → `400 {"error":"required query param 'market' not provided"}`.
Grouped by token, sorted by size. **Current snapshot only, no history** — useful for live
"who is on the other side" signals, useless for point-in-time backtesting.

### 4.3 `GET /oi`

```bash
curl -s "https://data-api.polymarket.com/oi?market=<conditionId>"
# → [{"market":"0x3733a1b6...","value":19898.840558}]
```
Current open interest in USD. Snapshot only.

`/activity`, `/positions`, `/value` all exist but returned `400` with these params —
`[UNVERIFIED]`, they need a `user` address rather than a market.

---

## 5. Fees

### 5.1 The formula

```
fee = C × feeRate × p × (1 − p)
```

where `C` = shares traded and `p` = share price
([docs.polymarket.com/polymarket-learn/trading/fees](https://docs.polymarket.com/polymarket-learn/trading/fees),
[startpolymarket.com](https://startpolymarket.com/learn/polymarket-fees/)).
**Takers only. Makers pay zero and receive a rebate.**

This is structurally identical to Kalshi's `0.07·P·(1−P)` — the harness can share one fee module
with a per-venue, per-category rate.

### 5.2 Live per-category rates, read straight off the Gamma market record

Surveyed the 100 highest-24h-volume live markets (2026-08-11). Distinct `(feesEnabled, feeType, feeSchedule)` tuples:

| n | feeType | rate | takerOnly | rebateRate | max fee / 100 shares @ p=0.50 |
|---|---|---|---|---|---|
| 46 | `sports_fees_v2` | 0.05 | true | 0.15 | $1.25 |
| 25 | *(none — `feesEnabled: false`)* | — | — | — | $0.00 |
| 12 | `politics_fees` | 0.04 | true | 0.25 | $1.00 |
| 5 | `economics_fees` | 0.05 | true | 0.25 | $1.25 |
| 5 | `finance_prices_fees` | 0.04 | true | 0.25 | $1.00 |
| 3 | `crypto_fees_v2` | 0.07 | true | 0.20 | $1.75 |
| 2 | `culture_fees` | 0.05 | true | 0.25 | $1.25 |
| 2 | `weather_fees` | 0.05 | true | 0.25 | $1.25 |

Also seen in the historical sweep: `crypto_15_min`, `tech_fees`, `general_fees`.
Geopolitics is documented as fee-free (rate 0) and a quarter of top live markets still carry
`feesEnabled: false` entirely.

**This is the cleanest fee source available on either venue: the exact schedule that applied to a
given market is stored on the market record.** The harness should read `feeSchedule` per market
rather than hard-coding a rate:

```python
fs = market.get('feeSchedule') or {}
rate = fs.get('rate', 0.0) if market.get('feesEnabled') else 0.0
taker_fee = shares * rate * p * (1 - p)       # exponent is 1 on every schedule observed
maker_fee = 0.0
# optional: maker rebate credit = fs['rebateRate'] × pool of taker fees (do NOT model as guaranteed)
```

`exponent: 1` on every schedule seen; treat `exponent != 1` as `rate * (p*(1-p))**exponent`
`[UNVERIFIED]` — no live example to test against.

**Ignore `makerBaseFee` / `takerBaseFee`.** Gamma reports `1000` on fee-enabled markets and `null`
otherwise; CLOB `/markets` reports `1000` for the same market and `0` for pre-2023 ones; the
`GET /fee-rate?token_id=…` endpoint returns `{"base_fee":0}` for a live fee-enabled market. These
are legacy bps ceilings, not the charged rate. `feeSchedule` is the real thing.

### 5.3 Fees are new — this changes the backtest

Tracing `feesEnabled` across resolution cohorts (top-100 by volume, ≥$100k, per window):

```
endDate Jun 2025   100/100 feesEnabled=false
endDate Sep 2025   100/100 false
endDate Dec 2025    98/100 false,  2 finance_prices_fees
endDate Feb 2026   100/100 false
endDate May 2026    15/100 false, 69 sports_fees_v2, 15 politics_fees, 1 culture_fees
endDate Jul–Aug 26  36/100 false, 51 sports_fees_v2, 8 general_fees, 5 politics_fees
```

**Polymarket was effectively fee-free until ~2026 and rolled fees out broadly around Q2 2026.**
Two implications:

1. A backtest over 2023–2025 Polymarket data that applies zero fees is *historically accurate* but
   **not predictive of forward economics**. Report both: realised-fee P&L and
   current-schedule P&L. The tournament (P5) should rank on the latter.
2. The `feeSchedule` stored on an old market row is the *current* value, not point-in-time. Known
   drift: sports moved 0.03 → 0.05 in July 2026
   ([startpolymarket](https://startpolymarket.com/learn/polymarket-fees/)). Flag any
   fee attribution on pre-2026 markets as approximate.

### 5.4 Gas and withdrawal

Trading itself is gasless for the user: orders settle through Polymarket's relayer/proxy-wallet
architecture and the docs state the "Relayer API can submit transactions without requiring POL for
gas" ([auth docs](https://docs.polymarket.com/developers/CLOB/authentication)). Polymarket charges
nothing to move USDC; blockchain gas on deposit/withdraw is variable —
"$10 minimum on Ethereum; $3 on Polygon, Solana, Base, Arbitrum"
([startpolymarket](https://startpolymarket.com/learn/polymarket-fees/)) `[secondary source]`.
No fee is charged on winnings or redemption.

**For the P&L model:** per-trade gas ≈ $0; charge a fixed ~$3 per deposit/withdrawal cycle. This is
materially better than it looks on paper for a high-frequency resting-limit strategy — but note
that maker orders being free is exactly what makes the "resting limit order into the panic dip"
play from `docs/viability.md` economically viable on Polymarket.

---

## 6. Rate limits

**Documented** ([docs.polymarket.com/api-reference/rate-limits.md](https://docs.polymarket.com/api-reference/rate-limits.md)):

| Host / endpoint | Limit |
|---|---|
| Gamma general | 4,000 req/10 s |
| Gamma `/markets` | 300 req/10 s |
| Gamma `/events` | 500 req/10 s |
| CLOB general | 9,000 req/10 s |
| CLOB `/prices-history` | 1,000 req/10 s |
| CLOB `/book`, `/price`, `/midpoint` | 1,500 req/10 s each |
| CLOB `/books`, `/prices`, `/midpoints` | 500 req/10 s each |
| Data API general | 1,000 req/10 s |
| Data API `/trades` | 200 req/10 s |
| CLOB auth | 100 req/10 s |
| `POST/DELETE /order` | 5,000 req/10 s burst, 120,000 req/10 min sustained |

**Observed from this container** (behind the agent proxy, so a shared egress IP — treat as a floor):

* `clob /prices-history`: 10 concurrent requests, no throttling, 1.44 s wall. Comfortable.
* `data-api /trades`: 30 sequential requests in 19.9 s (~1.5 req/s) → 0 errors. But short bursts of
  ~5 req/s with varying params repeatedly returned `{"error":"Too Many Requests"}`.
  **Rate-limit data-api far below the documented 20 req/s.** Use ≤2 req/s with exponential backoff
  and treat `Too Many Requests` as retryable — it comes back as a 200-status JSON body, not a 429,
  so a naive `raise_for_status()` will not catch it. **Check for the `error` key on every response.**

Gamma serves `cache-control: public, max-age=300` and prices-history `max-age=90`, both behind
Cloudflare — a local HTTP cache will absorb most repeat traffic for free.

---

## 7. Recommended data-layer design

```
bot/data/polymarket/
  markets.py    # keyset-enumerate resolved markets → sqlite/parquet
  prices.py     # chained 15-day batch-prices-history pulls → parquet
  trades.py     # data-api tape, offset-paged with CASH sweeps → parquet
  books.py      # NEW: live book snapshotter (wss or /books polling) → parquet
```

**Universe selection** (one keyset scan, ~2 min):

```
GET /markets/keyset?closed=true
    &uma_resolution_status=resolved
    &volume_num_min=50000
    &end_date_min=2026-02-01           # contamination-safe per ORCHESTRATION.md #2
    &limit=100&after_cursor=…
  then drop: enableOrderBook != true
             feeType in {crypto_15_min, crypto_fees_v2, sports_fees_v2}
             seriesSlug matching ^(btc|eth)-up-or-down
             (closedTime − startDate) < 10 days      # FutureSearch horizon filter
             outcomePrices not in {["1","0"],["0","1"]}   # unresolved / disputed
```

**Price pull** — for each surviving market:

```
tokens = json.loads(m['clobTokenIds'])          # pull YES only; NO = 1 − YES up to spread
t0 = ts(m['startDate']); t1 = ts(m['closedTime'])     # ← closedTime, never endDate
windows = [(s, min(s+1_296_000, t1)) for s in range(t0, t1, 1_296_000)]
POST /batch-prices-history {"markets": [≤20 tokens], "start_ts":…, "end_ts":…, "fidelity": 1}
```

Store `fidelity=1` for the final 15 days before close (the event window where all the
overcorrection alpha lives) and `fidelity=60` for the rest. Dedupe on `t` (0 duplicates observed,
but the last point of window *n* and first of *n+1* can collide if you don't half-open the ranges).

**Point-in-time discipline checklist** (constraint #1 — strategy sees only info ≤ t):

- ✅ `prices-history` points are timestamped and immutable — safe.
- ⚠️ **Every Gamma field except `question`/`description`/`startDate`/`endDate` is a live snapshot.**
  `outcomePrices`, `lastTradePrice`, `bestBid`, `bestAsk`, `volumeNum`, `liquidityNum`,
  `oneDayPriceChange`, `feeSchedule`, `holders`, `oi` are all as-of-now. Loading a market record
  into a strategy at simulated time *t* is **lookahead**. The loader must hand strategies only
  `(question, description, startDate, tick_size, min_order_size)` plus the price series truncated
  at *t*; resolution fields go to the scorer only.
- ⚠️ `description` can be edited post-launch (there is a `/market-clarifications` endpoint and a
  `hasReviewedDates` flag). For LLM-forecaster strategies this is a real contamination channel —
  a clarification added after an ambiguity arose leaks the ambiguity. Flag, don't ignore.

---

## 8. Open questions / not verified

* `[UNVERIFIED]` Whether any current Polymarket subgraph exists. The three canonical Goldsky URLs are 404.
* `[UNVERIFIED]` `gamma /market-estimate-history?event_id=…` (event-level series, same interval/fidelity
  params) returned `{"history":[]}` for the event id tested. May need a different event class.
* `[UNVERIFIED]` `data-api /activity`, `/positions`, `/value` semantics — all 400 with a `market` param;
  they likely key on `user`.
* `[UNVERIFIED]` `takerOnly` param effect on `data-api /trades`.
* `[UNVERIFIED]` negRisk (multi-outcome, mutually-exclusive) markets: `neg_risk: true` markets share a
  `negRiskMarketID` and have conversion mechanics that change effective fees/capital. Not probed.
  Relevant if we trade the "victory margin bracket" style markets from the README, which are exactly
  negRisk events.
* `[UNVERIFIED]` `exponent != 1` fee schedules — none observed live.
* `[DOCS ONLY]` Gas figures ($3 Polygon / $10 Ethereum) are from a third-party 2026 guide, not
  Polymarket's own docs.
* Websocket handshake was not completed from this container (plain `curl` upgrade returned 400);
  the endpoint is documented at
  [docs.polymarket.com/api-reference/wss/market.md](https://docs.polymarket.com/api-reference/wss/market.md).
  Use a real WS client.

---

## Appendix — copy-paste probes

```bash
# 1. Find a resolved market
curl -s "https://gamma-api.polymarket.com/markets/keyset?limit=5&closed=true&uma_resolution_status=resolved&volume_num_min=100000&end_date_min=2026-02-01"

# 2. Its full record (closedTime + feeSchedule live here)
curl -s "https://gamma-api.polymarket.com/markets/slug/microstrategy-sells-any-bitcoin-by-may-31-2026"

# 3. Settlement truth
curl -s "https://clob.polymarket.com/markets/0x3733a1b647e7364095736ab0966465d896a84cf3b6bc1695ca1f26c3239b3868"

# 4. 15-day minute-bar slice ending at close  (endTs-startTs MUST be <= 1296000)
curl -s "https://clob.polymarket.com/prices-history?market=25714007960293389110960044475283546872601238755063051359394740854408462452120&startTs=1779237259&endTs=1780533259&fidelity=1"

# 5. 20 markets at once
curl -s -X POST https://clob.polymarket.com/batch-prices-history -H 'Content-Type: application/json' \
  -d '{"markets":["<tok>","<tok>"],"start_ts":1779237259,"end_ts":1780533259,"fidelity":1}'

# 6. Trade tape (conditionId, newest first, offset <= 10000)
curl -s "https://data-api.polymarket.com/trades?market=0x3733a1b647e7364095736ab0966465d896a84cf3b6bc1695ca1f26c3239b3868&limit=500&offset=0"

# 7. Whale flow deeper in history
curl -s "https://data-api.polymarket.com/trades?market=<conditionId>&limit=500&offset=0&filterType=CASH&filterAmount=5000"

# 8. Live book / OI / holders
curl -s "https://clob.polymarket.com/book?token_id=<tok>"
curl -s "https://data-api.polymarket.com/oi?market=<conditionId>"
curl -s "https://data-api.polymarket.com/holders?market=<conditionId>&limit=20"

# 9. The full parameter surface
curl -s "https://gamma-api.polymarket.com/openapi.json" | jq '.paths | keys'
```

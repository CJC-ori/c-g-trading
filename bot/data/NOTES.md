# Kalshi public-API data layer — empirical notes

All findings below were established by probing the live public API
(`https://api.elections.kalshi.com/trade-api/v2/`, unauthenticated) on
**2026-08-11**. They drive the design of `kalshi_client.py`, `store.py` and
`pull.py`, and several of them materially constrain backtest design.

---

## TL;DR — the things that change backtest design

> **SUPERSEDED IN PART (2026-08-12).** Everything in this section describes the
> **live** tier only. Kalshi also serves an unauthenticated `/historical/*`
> archive that reaches back to **2021**, which removes the ~90-day wall for
> settled markets *and* for the tick tape. The store now holds ~5 years of
> history. Read the final appendix, **"The `/historical/*` tier and the
> multi-year backfill"**, before designing anything around the horizons below.
> The one claim here that survives unchanged is #4 (no-trade candles carry
> quotes but no price) — it is if anything stronger on the archive tier.

**There are three *different* retention horizons, and they do not agree.**
This is the single most important result of the probe.

| data | how far back (as of 2026-08-11) | limit |
|---|---|---|
| settled **markets** (enumeration + by-ticker) | **2026-05-08** | ~95 days, hard |
| **trades** (public tape) | **2026-05-25** | ~78 days, hard |
| **candlesticks** | back to **market open** — 475 days observed | none found |

1. **Settled markets are purged ~90 days after close.** On 2026-08-11 the
   earliest close date that still returns settled markets is **2026-05-08**
   (binary-searched to the day: 2026-05-07 returns nothing, 2026-05-08 does).
   Nothing from the 2024 elections, 2025, or even April 2026 is reachable — not
   by window query, not by series query, not by direct ticker fetch. Any
   backtest built on this API is a **rolling ~3-month window** and must be
   re-pulled continuously or the data is gone for good.
   *The only exceptions found in a 159k-market pull are three zero-volume
   `ECB-24MAY08-*` zombies — noise, not usable history.*
2. **The trade tape is cut off even earlier, at a single global timestamp.**
   Every market's tape begins at **2026-05-25T00:00Z** regardless of when the
   market opened — `KXTRUMPATTEND` opened 2026-01-09 and `KXHORMUZNORM`
   2026-03-17, yet both tapes start within 90 seconds of that same instant.
   Asking for `min_ts`/`max_ts` before it returns zero rows. So **trade-level
   fill simulation is only possible for the last ~78 days**, a narrower window
   than the market universe itself.
3. **Candlestick history, by contrast, is deep** — it runs back to market
   *open*, not to any retention horizon, and it survives settlement.
   `KXSENATEMID-26-AELS` (settled 2026-08-05) serves **10,077 hourly candles
   back to 2025-03-28**; `SENATEMI-26-D` serves 9,371 back to **2024-12-03**,
   over 600 days. So the way to get long price series is to harvest
   *recently settled, long-dated* markets promptly, and the only way to
   backtest anything before 2026-05-25 is **candles**.
4. **Zero-volume candles have no price, and they are the vast majority.**
   When no trade occurs in a period the API omits the `price` OHLC block
   entirely, leaving only `price.previous_dollars` plus the bid/ask OHLC.
   Across the 435,101 candles pulled here, **only 13.5% carry a last-trade
   price while 100% carry a `yes_bid` quote.** A backtest that reads
   `price.close` sees gaps in seven candles out of eight; it must mark to the
   **bid/ask midpoint**. This also means these markets are quote-driven and
   thin — 86.5% of hours in a typical market have no trade at all, which is
   itself a hard constraint on any strategy that assumes it can transact
   hourly.

**Net effect on the backtest harness:** fills can be simulated against the real
tape only from 2026-05-25 onward. Before that, the honest option is
candle-based fills using the bid/ask OHLC (crossing the spread, no queue
model). The contamination rule in `ORCHESTRATION.md` — LLM strategies only on
markets resolving after Feb 2026 — is satisfied by *everything* the API still
serves, so contamination is not the binding constraint here; **sample size
is**. Roughly 95 days of settled markets is the entire available universe.

---

## API shape and quirks

### Money and size fields are decimal-dollar strings

The v2 API no longer returns integer cents. Prices arrive as `*_dollars`
strings and quantities as `*_fp` (fixed-point) strings:

```json
{"yes_price_dollars": "0.9700", "no_price_dollars": "0.0300", "count_fp": "8.40"}
```

Prices can be **sub-cent** — markets carry `"price_level_structure": "deci_cent"`
and `price_ranges` with `"step": "0.0010"`. Trade tapes really do contain
values like `no_price_dollars: "0.0020"`. Sizes are fractional too (`"8.40"`
contracts), so nothing here is safely an integer. The store keeps every
monetary column as a `REAL` in dollars and retains the untouched payload in
`raw_json`.

### `/markets` status filter

Valid values are exactly `unopened | open | closed | settled`; anything else
400s with `"invalid status filter"`. The mapping to the `status` field on the
returned rows is not one-to-one:

| filter | returns rows whose `status` is | meaning |
|---|---|---|
| `settled` | `finalized` | settled and paid out |
| `closed` | `closed` | closed but **never settled** — `result` is `""` |
| `open` | `active` | tradeable |

The `closed` bucket is almost entirely zero-volume zombie markets (e.g.
`KXDENSNOWM-25DEC-*`, `KXSCOTUS14-25`) that closed without ever resolving.
These are the *only* pre-May-2026 rows the API still serves, and they are
useless for backtesting. Do not mistake them for surviving history.

### Markets carry no category

`/markets` rows have no `category` field. Category lives on **events** and
**series**. `GET /series?category=<c>` is unpaginated and returns every series
in the category in one response, which makes the series catalog the cheapest
source of truth. `pull.py series` builds it, and `Store.backfill_categories()`
joins it onto markets by `series_ticker`.

There is no endpoint that lists categories. The 14 that resolve are:

```
Politics, Elections, Economics, World, Financials, Crypto, Commodities,
Sports, Climate and Weather, Science and Technology, Health, Entertainment,
Companies, Transportation
```

Unrecognised strings return an empty set rather than an error, so probing is
safe but **silent** — and that silence bites. `Commodities` (134 series,
14,747 markets, 12,865 of them settled) was missed on the first pass simply
because it was not in the guess list, and nothing signalled the omission; it
only surfaced when a stray `category='Commodities'` row appeared in the series
table via some other category's response. Tried and confirmed *not* valid:
`Weather`, `Climate`, `Culture`, `Music`, `Awards`, `Technology`, `Business`,
`Economy`. If a future pull looks thin in some domain, suspect a missing
category string before suspecting the API.

### Candlesticks

Path (confirmed by probing; the series ticker is required in the path):

```
GET /series/{series_ticker}/markets/{ticker}/candlesticks
    ?start_ts=&end_ts=&period_interval=
```

* `period_interval` is in **minutes** and only `1`, `60`, `1440` are accepted.
* **Hard cap of 5000 periods per request.** Exceeding it 400s with
  `"requested time range with candlesticks: 9600.000000, max candlesticks: 5000"`.
  `KalshiClient.get_candlesticks` chunks the range transparently and dedupes.
* Candles are keyed by `end_period_ts` (unix seconds).
* A candle looks like:

```json
{"end_period_ts": 1765515600,
 "open_interest_fp": "19096.00",
 "volume_fp": "0.00",
 "price":   {"previous_dollars": "0.2900"},
 "yes_bid": {"open_dollars":"0.2800","high_dollars":"0.2800",
             "low_dollars":"0.2600","close_dollars":"0.2700"},
 "yes_ask": {"open_dollars":"0.2900","high_dollars":"0.3000",
             "low_dollars":"0.2800","close_dollars":"0.3000"}}
```

Note the missing `price.open/high/low/close/mean` — that is the no-trade case
described above, not an error. The store writes those columns as `NULL`.

* **Settled markets still serve candlesticks**, for as long as the market
  itself is served. `KXSENATEMID-26-AELS` (settled 2026-08-05) returns 475
  daily candles going back to **2025-03-29**, i.e. all the way to market open.
* 1-minute candles are available historically too, not just for live markets —
  the final-2-hours probe on the same settled market returned a full 106
  one-minute candles.

### Trades

`GET /markets/trades?ticker=&limit=&cursor=` returns the public tape,
**newest first**, cursor-paginated. Settled markets keep serving their full
trade history. Fields: `trade_id`, `ticker`, `created_time`,
`yes_price_dollars`, `no_price_dollars`, `count_fp`, `taker_side`,
`taker_book_side`, `taker_outcome_side`, `is_block_trade`.

`taker_side` is the side the aggressor bought (`yes`/`no`), and
`taker_book_side` (`bid`/`ask`) tells you which side of the book was lifted —
between them you can sign the flow without inferring from price.

**The tape has a hard global floor at 2026-05-25T00:00Z** (see TL;DR). Verified
two ways: every one of the 22 markets pulled here starts its tape within 90
seconds of that instant no matter its open date, and an explicit
`min_ts`/`max_ts` request for 2026-05-20..2026-05-24 on a market that was
trading throughout returns zero rows, as does 2026-03-01..2026-03-05.

Trade volume is chunky: `KXSENATEMID-26-HSTE` did $20.5 M on 36,637 trades
(~560 contracts/trade), so depth is real but the tape is not tick-dense enough
to model queue position. Treat it as a fill-feasibility check, not a book.

### Rate limiting

The unauthenticated tier 429s readily. Measured behaviour:

* 8 req/s → frequent 429s.
* 5 req/s → occasional 429s (about one per 30 requests during a long
  `limit=1000` walk), all cleared by a single retry.

The client rate-limits to 5 req/s by default and retries 429/5xx with
exponential backoff plus jitter. 4xx other than 429 raise immediately, since
they are deterministic.

### Pagination

`{"<collection>": [...], "cursor": "..."}`. An empty cursor, an empty page, or
a repeated cursor ends the walk — the repeat guard matters because some
endpoints keep echoing the last cursor instead of blanking it.

---

## The high-frequency flood

This is severe, and much worse than the 15-minute crypto markets alone.

**One day of settled markets (close_time in 2026-08-04): 365,093 markets**,
retrieved in 372 paginated requests. Breakdown:

| series | markets | share | volume ($) |
|---|---:|---:|---:|
| `KXMVESPORTSMULTIGAMEEXTENDED` | 207,755 | 56.9% | 268,710,157 |
| `KXMVECROSSCATEGORY` | 85,471 | 23.4% | 86,168,750 |
| `KXSOLE` / `KXSOLD` | 14,550 | 4.0% | 775,402 |
| `KXETHD` / `KXETH` | 14,480 | 4.0% | 2,488,063 |
| `KXBTCD` / `KXBTC` | 9,184 | 2.5% | 48,543,508 |
| `KXNASDAQ100U` | 2,800 | 0.8% | 948,278 |
| `KXXRPD`/`KXXRP`/`KXHYPED`/`KXHYPE`/`KXBNBD`/`KXBNB` | 11,040 | 3.0% | 218,745 |
| everything else (336 series) | ~19,800 | 5.4% | — |

Only 344 distinct series produce those 365k markets.

**The dominant spam is not crypto — it is `KXMVE*`**, auto-generated
*multivariate parlay* combinations (e.g. "no BTC target price AND yes
Makhachev AND yes Las Vegas"). They are 80.3% of all settled markets by count,
they are machine-generated per-user combos, and they are untradeable as a
systematic strategy. Excluding the `KXMVE` prefix alone removes four fifths of
the volume of rows.

### Consequence: full window enumeration is not viable

365k markets/day × ~95 reachable days ≈ **35 million settled markets**, which
is ~35,000 paginated requests (~2 h at 5 req/s) and far more than 1.5 GB once
stored. `pull.py markets --settled-since` is implemented faithfully and
day-chunks its walk, but the pagination cost is paid even for rows that the
prefix filter then discards, because the API has no server-side series-prefix
filter.

**The bounded path that actually finishes is `--by-series`**: iterate the
series catalog and issue one `?series_ticker=` request per series. For the nine
non-sport, non-crypto categories that is ~5,300 requests (~20 min), it returns
exactly the markets worth having, and it attaches categories for free. That is
what the initial pull below used.

### Filters provided

* `--exclude-series-prefix` — comma-separated, defaults to
  `KXMVE,KXBTC,KXETH,KXSOL,KXXRP,KXDOGE,KXBNB,KXHYPE,KXLTC,KXADA,KXLINK,KXINXU,KXNASDAQ100U,NASDAQ100I,KXEURUSDH,KXGOLDH,KXGOLDD,KXSILVERH,KXPLATINUMH,KXPALLADIUMH,KXWTIH,KXNGASH`.
  Pass an empty string to disable.
* `--exclude-frequency` — defaults to `fifteen_min,hourly`. This is the more
  principled filter: the series catalog carries a `frequency` field, and its
  distribution across all 12,652 series is

  ```
  custom 5353 | one_off 4575 | annual 1346 | monthly 307 | weekly 256
  daily 197 | hourly 52 | fifteen_min 16 | quarterly 2
  ```

  so the 68 `hourly`/`fifteen_min` series are exactly the spam-frequency tier.
  (`KXMVE*` series are *not* in the category catalog at all, so the prefix
  filter remains necessary.)
* `--min-volume` — drop illiquid markets at write time.

---

## Settlement `result` semantics

`result` is a short string on settled markets, empty (`""`) while a market is
still active or if it closed without resolving:

Observed distribution over the 159,154 markets pulled:

| `result` | count | meaning |
|---|---:|---|
| `""` (empty) | 79,104 | still active, or closed-without-settling |
| `no` | 50,513 | settled $0.00 |
| `yes` | 29,054 | settled $1.00 |
| `scalar` | 483 | **partial payout** — see below |

* `"yes"` / `"no"` — the overwhelming majority. Kalshi models essentially
  everything, including multi-candidate races and numeric strike ladders, as a
  set of **independent binary markets**, one per outcome. A mutually-exclusive
  race is an *event* holding many binary markets of which exactly one settles
  `yes`. `KXSENATEMID` (Michigan Senate Democratic primary) is 18 binary
  markets; only `KXSENATEMID-26-AELS` has `result = "yes"`.
* `""` with `status = "closed"` — closed, never settled (void in practice).
  These carry zero volume.
* **`"scalar"` is real and must be handled.** 483 markets (0.3%) settle to a
  *fractional* payout carried in `settlement_value_dollars`, not to 0 or 1.
  The distribution is continuous over `0.00 .. 0.99` with a large spike at
  **0.50 (215 markets)** — that spike is ties/pushes. The affected series are
  almost entirely sports head-to-heads and totals: `KXPGA3BALL` (101),
  `KXPGAH2H` (77), `KXNPBGAME` (55), `KXMLBTOTAL` (43), `KXATPGTOTAL` (42),
  `KXMLBTB` (29). A backtest that treats `result != "yes"` as a total loss
  will misprice every one of these.
* Numeric questions are otherwise expressed as strike ladders (`strike_type`,
  `floor_strike`, `cap_strike`, or a `custom_strike` dict) with binary yes/no
  settlement per strike.
* Companion fields: `settlement_ts` (RFC3339), `settlement_value_dollars`,
  `expiration_value`, `can_close_early`. Note `settlement_ts` can be *before*
  the scheduled `expiration_time` when `can_close_early` is set — the Michigan
  primary closed 2026-08-05T14:02:38Z and settled 30 minutes later, while its
  nominal expiration was much later.

**Backtest implication:** resolve P&L as
`payout = 1.0 if result=="yes" else (settlement_value if result=="scalar" else 0.0)`,
always on the individual binary ticker; never assume complementarity across an
event; and use `settlement_ts` (not `expiration_time`) as the horizon.

---

## The Michigan Senate primary complex

The task named `KXSENATEDPRIMARYMI`; that series does not exist. The real
tickers, found by walking the Elections series catalog:

| series | what | markets | status |
|---|---|---|---|
| `KXSENATEMID` | MI Senate **Democratic** primary | 18 | settled 2026-08-05 |
| `KXSENATEMIR` | MI Senate **Republican** primary | 5 | settled 2026-08-05 |
| `KXMIPRIMARY` | MI congressional-district primaries | 36 | settled 2026-08-05/06 |
| `KXMISENATE` | MI Senate general, by person | 4 | open |
| `SENATEMI` | MI Senate general, by party | 2 | open |
| `KXMISENGOVCOMBO` | MI Senate–Governor combination | — | — |

Results: **Abdul El-Sayed** won the Democratic primary
(`KXSENATEMID-26-AELS`, `result = "yes"`, $9.94 M volume). **Mike Rogers** won
the Republican primary (`KXSENATEMIR-26-MROG`, $75 k volume).

The interesting artifact for strategy work: `KXSENATEMID-26-HSTE` (Haley
Stevens) traded **$20.5 M** — more than double the eventual winner — and
settled `no`. A single primary produced a >$20 M market that resolved to zero,
which is exactly the kind of episode a favorite-longshot or late-panic
strategy needs in its sample.

No separate victory-margin series exists for the Michigan Senate primary;
the closest siblings are `KXMISENGOVCOMBO` (Senate–Governor combo) and the
per-district `KXMIPRIMARY` markets, all of which are pinned into the sample by
`PINNED_SERIES` in `pull.py`.

---

## Initial pull (2026-08-11, ~40 min wall clock)

Commands, in order:

```bash
python -m bot.data.pull series
python -m bot.data.pull markets --by-series \
    --categories "Politics,Elections,Economics,World,Science and Technology,Health,Companies,Transportation,Climate and Weather"
python -m bot.data.pull markets --by-series --categories "Financials,Crypto" --min-volume 100
python -m bot.data.pull markets --by-series --categories "Sports,Entertainment" --min-volume 1000   # stopped early, see below
python -m bot.data.pull markets --status open
python -m bot.data.pull events --status open
python -m bot.data.pull events --status settled --max-pages 300
python -m bot.data.pull candles --sample 300 --strategy stratified --final-48h-minute
python -m bot.data.pull trades --ticker <22 markets>
python -m bot.data.pull markets --by-series --categories "Commodities"   # added after finding the missing category
```

### Result

| table | rows |
|---|---:|
| markets | **173,135** |
| series | 12,174 |
| events | 49,744 |
| candlesticks | **435,101** (321,773 hourly + 113,328 one-minute) |
| trades | **260,669** |
| **DB size** | **940 MB** (budget was 1.5 GB) |

Markets by category (settled = `finalized`):

| category | markets | settled | volume ($) |
|---|---:|---:|---:|
| Sports | 83,485 | 38,850 | 3,838,740,742 |
| Climate and Weather | 21,063 | 20,010 | 195,592,484 |
| Elections | 15,116 | 3,490 | 829,556,688 |
| Commodities | 14,747 | 12,865 | 82,232,909 |
| Financials | 13,269 | 6,944 | 76,969,055 |
| Economics | 10,806 | 7,258 | 260,699,165 |
| Entertainment | 6,628 | 22 | 33,062,142 |
| Politics | 3,857 | 1,771 | 229,177,453 |
| Science and Technology | 2,270 | 1,212 | 85,680,248 |
| (uncategorised) | 896 | 0 | 4,308,242 |
| Crypto | 573 | 407 | 1,747,825 |
| Companies | 402 | 10 | 1,662,525 |
| World | 14 | 1 | 80,513 |
| Health | 9 | 0 | 0 |

Settlement results across the whole store: 80,020 empty (active or
never-settled), **56,852 `no`**, **35,780 `yes`**, **483 `scalar`**.

Note how lopsided the settled counts are relative to what a politics-focused
bot wants: Elections has 15,116 markets but only **3,490 settled**, and
Politics only **1,771**. World has essentially nothing (1). The settled sample
available for outcome-supervised work in the non-sports domains is on the
order of **10–15k markets**, not hundreds of thousands.

Coverage:

* settled `close_time`: **2026-05-08 .. 2026-08-11** (plus the 3 ECB zombies)
* hourly candles: **2024-12-03 .. 2026-08-11**, 302 markets
* 1-minute candles: **2026-05-23 .. 2026-08-11**, 302 markets (final 48 h each)
* trades: **2026-05-25 .. 2026-08-08**, 22 markets

The candlestick sample is 311 markets = 69 pinned (the whole Michigan complex)
+ 244 drawn stratified by category with the weights in `CATEGORY_WEIGHTS`,
volume-filtered at ≥ 100 contracts, seeded at 20260811 for reproducibility.
Zero fetch failures.

Trade tapes cover the Michigan primary complex (`KXSENATEMID-26-AELS` 26,400;
`KXSENATEMID-26-HSTE` 36,637; `KXSENATEMIR-26-MROG` 48) plus 19 politics
markets chosen for volume and outcome variety — `KXTRUMPATTEND` (51,882),
`KXTRUMPUFC-26JUL-DJT` (25,356), `KXNEXTAG-29-TBLA` (23,515),
`KXTRUMPNBAFINALS-26JUN-DJT` (23,006), `KXHORMUZNORM-26MAR17-B260701`
(21,728), `KXUSAIRANAGREEMENT-27-26JUL` (13,933), `KXCLARITYVOTE-26JUL-AUG08`
(12,529) and others.

### Deviations from the requested pull, and why

* **`--settled-since 2026-05-01` was not used for the bulk pull.** At 365k
  settled markets/day it would have meant ~35 M rows and ~2 h of pagination
  for data that is 80% parlay spam. `--by-series` reaches the same non-spam
  universe in ~5,300 requests. The window mode is implemented and works; it is
  just the wrong tool at this exchange's market count. Note 2026-05-01 is in
  any case unreachable — the API floor is 2026-05-08.
* **Sports + Entertainment was stopped after 400 of 5,681 series** (34,737
  markets kept). It was tracking toward ~490k markets and would have blown the
  1.5 GB budget on the lowest-priority categories. The 83k sports markets in
  the DB are already an ample favorite-longshot sample.
* **Settled events were capped at 300 pages.** The settled-event feed is
  dominated by 15-minute crypto series (`KXBTC15M`, `KXETH15M`, … 1,740 events
  each); 19,633 spam events were purged after the fact and the same prefix
  filter now applies inside `pull.py events`.

### Scale markers worth remembering

* The open-market snapshot stored 78,239 markets and **skipped 1,581,471** as
  excluded series — a 20:1 spam-to-signal ratio in the live universe alone.
* 5 req/s produced 178 retryable 429s over ~4,600 requests (~3.9%), all
  cleared on the first retry.

---

## Files

* `kalshi_client.py` — rate-limited, retrying HTTP client; candlestick
  chunking; cursor pagination; 404-tolerant single-object getters.
* `store.py` — SQLite schema (`markets`, `series`, `events`, `candlesticks`,
  `trades`, `pull_log`) with idempotent upserts and dollar-parsing helpers.
* `pull.py` — argparse CLI: `series`, `markets`, `candles`, `trades`, `stats`.
* `test_smoke.py` — pytest smoke tests over the local DB, skipped if absent.

The DB lives at `data/kalshi.db`; `data/` is gitignored.

---

## Appendix: backtest-harness access paths and indexing (2026-08-11)

`bot/backtest/dataport.SqliteHistoryProvider` reads this store read-only
(`mode=ro`, WAL-safe against a live pull). Its hot queries and the indexes
that serve them:

| query | served by |
|---|---|
| candles by `(ticker, period_interval)` ordered by `ts` | the `candlesticks` PRIMARY KEY `(ticker, period_interval, ts)` — WITHOUT ROWID, so this is a pure index-range scan |
| trades by `ticker` ordered by `ts` | `idx_trades_ticker_ts` |
| market/settlement lookups by `ticker` | `markets` PK |
| universe scans (category/result/volume/series prefix) | full scan of `markets` (173k rows, ~0.2s) with `idx_markets_category` / `idx_markets_result` assisting |

**No additional indexes were needed.** Measured on the real 940MB DB: the
full research-universe HoldFavorite baseline (278 markets, 296k decision
points over 322k hourly candles) runs in ~55s end to end, of which data
loading is a small fraction — the bound is the Python replay loop, not
SQLite. (The engine gained a fast-path skip for markets with no pending
data at an event timestamp, which is what makes multi-market replays
scale; before that the loop was O(events x markets).)

Unit/vocabulary mapping decisions made at the provider boundary (integer-
cent conversion with conservative directional rounding, scalar settlements,
`''` = never-settled, no-trade candle OHLC synthesized from bid/ask mids)
are documented in `bot/backtest/dataport.py`'s section header and verified
by `python -m bot.backtest.validate_wiring` against this DB.

---

## Appendix: the `/historical/*` tier and the multi-year backfill (2026-08-12)

Everything above this appendix was written before `/historical/*` was found.
**The TL;DR's retention table is superseded**: it describes the *live* tier
only. The archive tier changes the answer for two of the three rows.

### The correction, in one table

| data | live tier (`/markets`, `/markets/trades`) | archive tier (`/historical/*`) | net reach |
|---|---|---|---|
| settled **markets** | 2026-05-08, hard | back to **2021-07** | **~5 years** |
| **trades** (tick tape) | 2026-05-25, hard | back to **2021-08** (first print seen) | **~5 years** |
| **candlesticks** | market open, for markets the live tier still serves | market open, for archived markets | market open, always |

The two tiers **partition by market, not by timestamp**. A given ticker is
served by exactly one of them; `/historical/*` 404s for anything the live tier
still holds and vice versa. That is why `store.markets.source` exists and why
`KalshiClient.trades_any` / `candlesticks_any` take a `prefer=` hint — routing
off the stored tier saves a wasted 404 round trip per market. `/historical/cutoff`
read **2026-06-12T00:00:00Z** on every key throughout this work.

### What the archive tier does *not* give you

* **No date filters.** `/historical/markets` accepts `min_close_ts` and friends
  and then silently ignores them. The only usable indexes are `series_ticker`,
  `event_ticker` and an explicit `tickers=` list (mutually exclusive — passing
  a pair 400s, and `mve_filter` counts as one of the pair). So enumeration must
  go through the **series catalog**, which is what `pull.py hist-markets` does.
* **A series ticker resolves its legacy pre-`KX` markets too.** Asking for
  `KXHIGHNY` returns the 2021–2022 `HIGHNY-*` rows alongside the modern ones.
  This is load-bearing: **deriving the series from a ticker prefix mis-files
  every market renamed in the 2023 `KX` migration**, which is most of the
  pre-2023 archive. `upsert_markets(..., series_ticker=)` therefore takes the
  series from the *query*, not the ticker, for archived rows.
  (`test_legacy_ticker_prefixes_map_to_modern_series` guards this.)
* **Different JSON key convention.** Archive candles use bare
  `close`/`volume`/`open_interest`; live candles use `close_dollars`/`volume_fp`.
  Miss it and the rows land with every price column `NULL`.
  (`test_historical_candles_parsed_from_bare_keys` guards this.)
* **The exchange-wide trade firehose is the only ticker discovery mechanism**
  for markets whose series has left the live catalog. `/historical/trades` with
  `min_ts`/`max_ts` and no `ticker` returns every print exchange-wide — which is
  also the cheapest way to build an event-night universe, since it surfaces
  exactly the tickers that actually traded. `pull.py discover` wraps this.

### The `--window final-Nh` trap (cost us a re-pull)

A market's **decisive** hours are not always its **final** hours. `PRES-2024-*`
resolved on election night, 2024-11-05/06, but stayed open until inauguration
on **2025-01-20**. So `hist-candles --window final-72h` sampled three dead days
in January at 1-minute grain and missed the 54c -> 97c run entirely — the pull
"succeeded", the rows were real, and the data was worthless. `hist-candles` now
takes `--min-ts` / `--max-ts` to pin an **absolute** window, which is the right
tool for any event study. Rule of thumb: use `final-Nh` only when
`close_time` is the event; otherwise name the window.

### The backfill, as run (2026-08-11 22:34 → 2026-08-12, ~5 h wall clock)

```bash
# (a) archived market metadata — series-driven, whole history
python -m bot.data.pull hist-markets --categories "Elections,Politics,Economics,World,Companies"
python -m bot.data.pull hist-markets --categories "Climate and Weather"
python -m bot.data.pull hist-markets --categories "Financials,Science and Technology,Crypto,Health,Commodities,Transportation"

# (b) tick tape: election universe, then the deep pre-cutoff sweep
python -m bot.data.pull hist-trades --categories "Elections,Politics" --min-volume 5000
python -m bot.data.pull hist-trades --closed-before 2024-11-01 --settled-only \
    --min-volume 1000 --skip-existing --max-pages 40

# (c) 1-minute candles: final 72 h of each election/politics market ...
python -m bot.data.pull hist-candles --categories "Elections,Politics" --interval 1 --window final-72h
#     ... plus the absolute election-night window for the markets that traded it
python -m bot.data.pull hist-candles --tickers "<581 tickers with >=100 prints 11-04..11-07>" \
    --interval 1 --min-ts 2024-11-05 --max-ts 2024-11-07

# (d) hourly candles, final 15 days, favorite-longshot universe
python -m bot.data.pull hist-candles --interval 60 --window final-15d \
    --categories "Politics,Economics,Elections,Commodities,Financials,..." --min-volume 5000

# (e) weather: full hourly history for every settled KXHIGH* market
python -m bot.data.pull hist-candles --series-like 'KXHIGH%' --settled-only \
    --min-volume 1 --skip-existing --interval 60 --window full

# event-night ticker discovery (exchange-wide firehose)
python -m bot.data.pull discover --min-ts 2024-11-04T12:00 --max-ts 2024-11-07T00:00 --store-trades
```

Every one of these is **idempotent** — `--skip-existing` skips tickers that
already carry rows in the target table, and all writes are upserts, so an
interrupted run is resumed by re-issuing the same command. `pull_log` records
each completed phase with row counts and per-tier splits.

### Rate limiting, re-measured on the archive tier

The archive endpoints throttle **harder** than the live ones. Measured on the
weather sweep (one `/historical/markets/{t}/candlesticks` call per market):

| requested rate | 429 share | net markets/sec |
|---|---:|---:|
| 9 req/s | **49%** | 4.4 |
| 5 req/s | 21% | 4.05 |

So pushing past ~5 req/s buys almost nothing: the server gives back roughly
4 useful requests/second either way, and the extra pressure is just retries.
**Plan archive sweeps at ~4 markets/sec** — the 49k-market weather backfill is
a ~3.3-hour job, not a 15-minute one, and there is no batch endpoint that
avoids the per-market round trip.

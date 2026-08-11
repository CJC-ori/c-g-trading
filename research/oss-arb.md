# Open-source prediction-market arbitrage bots: dissection and design reference

**Written 2026-08-11 for the autonomous build (see `ORCHESTRATION.md`).** Three
repos were cloned into the scratchpad (NOT this repo) and read end to end; every
numeric claim below is either (a) from the source code, (b) from a live API call
made from this container today, or (c) cited to a URL. Anything I could not
verify is explicitly flagged **[UNVERIFIED]**.

Clone locations (scratchpad, ephemeral):

```
/tmp/claude-0/-home-user-c-g-trading/93211cb9-88a6-508f-a563-f877c13eddf7/scratchpad/
  ├── polymarket-arbitrage/              ImMike/polymarket-arbitrage   @7e4acc1 (2025-12-09), MIT, ~10.4k LOC Python
  ├── prediction-market-arbitrage-bot/   realfishsam/…                 @0952524 (2026-01-16), MIT, ~816 LOC JS
  └── pmxt/                              pmxt-dev/pmxt                 @4a367d8 (2026-07-18), MIT, ~118k LOC TS
```

---

## 0. TL;DR — the verdict up front

1. **Neither "arbitrage bot" has ever placed an order.** ImMike's
   `place_order` is a stub whose docstring says `TODO: Implement with actual
   Polymarket CLOB API`; its Kalshi client is read-only (no POST methods at
   all); and its `CrossPlatformArbEngine.check_arbitrage()` — the function that
   computes the cross-venue edge — **is never called from anywhere in the
   repo** (`grep -rn check_arbitrage` returns only its own definition). The
   cross-venue path stops at "found N matched pairs" and renders them in a
   dashboard. The "99.6% win rate / $573 profit" screenshot in its README is
   from `data_mode: "simulation"`, whose `SimulatedOrderBook.step()` *injects*
   an arbitrage with probability 0.05 per tick. It is a scanner + demo, not a
   trading system.
2. **Intra-venue "bundle arb" (YES+NO ≠ $1) does not exist on either venue.**
   It is absorbed by the matching engines by construction. I scanned 236 live
   two-sided Kalshi markets today: `yes_bid + no_bid > $1.00` occurred **0
   times**, and `yes_ask + no_ask < $1.00` occurred **0 times**. On Polymarket
   the CLOB mints a complete set whenever a YES bid and a NO bid cross $1.00,
   so the same state is un-representable
   ([CLOB architecture](https://benjamincup.medium.com/how-polymarket-orders-actually-get-executed-a-deep-dive-into-clob-v2-for-developers-fdcd5d395ef5)).
   **Do not build this. It is not a strategy, it is a description of what the
   exchange already does.**
3. **Cross-venue arb is real but is priced out by fees at our latency.** Both
   venues now charge the *same functional* taker fee — `C × rate × p × (1−p)` —
   Kalshi at rate 0.07 and Polymarket at 0.04–0.07 by category. That means a
   50/50 pair must show **>2.76¢ of gross gap on a $1 payout** before it nets
   anything. Measured live today on the most liquid identical pair across the
   two venues (Fed September decision, $2.3M Kalshi vol / $6M Polymarket vol):
   the best gross gap was **0.00¢**, and every executable direction was
   **−0.6¢ to −5.0¢ after fees**. Table in §5.
4. **What *is* worth taking**: pmxt's orderbook normalizers (they are the only
   ones in the three repos that match the *current* Kalshi API), its
   `getExecutionPrice` depth-walk (32 lines, directly portable to our fill
   model), its `MatchRelation` taxonomy (`identity | complement | subset |
   superset | overlap | disjoint`) and its curated `SERIES_MAP` — the design
   admission that title-similarity matching does not work. Plus ImMike's
   opportunity-duration instrumentation and its risk-manager/kill-switch shape.
5. **What to reject outright**: every title-similarity matcher in all three
   repos. I ran both matchers on adversarial pairs (§4.2). ImMike's scores
   *"Bitcoin above $100,000"* vs *"Bitcoin above $110,000"* at **1.000** and
   *"Will Trump be indicted in 2026"* vs *"Will Trump resign in 2026"* at
   **0.861**, against a configured threshold of 0.6. These are not random
   errors: the highest-scoring false positives are exactly the near-identical-
   strike markets whose prices *legitimately* differ, so the matcher's
   confidence is *positively correlated* with phantom edge.

**Recommendation for P4**: do not build an arbitrage strategy. Build the
cross-venue **divergence signal** instead (§7) — same plumbing, but it treats
a Kalshi/Polymarket gap as *information about mispricing* to be traded on one
venue with a directional thesis and a real edge threshold, rather than as a
locked riskless pair that fees eat. Reuse the plumbing, discard the strategy.

---

## 1. `realfishsam/prediction-market-arbitrage-bot` (JS, 816 LOC)

The smaller and more honest of the two bots. Its own README says it "ignores
gas fees, trading fees, and slippage" and is "not optimized for real-world
profitability". Built on `pmxtjs`.

### Architecture (4 files, all worth reading in 10 minutes)

| File | Role |
| --- | --- |
| `src/index.js` | entry, config validation, SIGINT handling |
| `src/bot.js` (273 LOC) | poll loop, outcome parsing, execution, position tracking |
| `src/matcher.js` (60 LOC) | Jaccard + Levenshtein fuzzy outcome matching |
| `src/arbitrage.js` (55 LOC) | the entire "arb math" |

### How it matches markets

The user hardcodes **one** Polymarket event URL and **one** Kalshi market URL in
`config.js`; the bot extracts slugs by regex and calls
`pmxt.polymarket.getMarketsBySlug()` / `pmxt.kalshi.getMarketsBySlug()`. Within
that single event it then fuzzy-matches *outcome labels* (candidate names)
across venues:

```js
// src/matcher.js:26
combinedSimilarity = jaccard(tokens) * 0.6 + (1 - levenshtein/maxLen) * 0.4
// greedy: for each Poly outcome, take the best unused Kalshi outcome ≥ 0.7
```

This is the least-bad matcher of the three, purely because the human has
already constrained it to one event pair. Within a candidate list ("Kevin
Warsh" vs "Kevin Hassett" scores 0.446 — correctly rejected) it is adequate.

### How it computes edge

```js
// src/arbitrage.js:4-8 — the whole thing
strategy1Cost = polymarket.yesPrice + kalshi.noPrice;  profit = 100 - cost;
strategy2Cost = kalshi.yesPrice   + polymarket.noPrice; profit = 100 - cost;
```

The synthetic-arb identity itself is correct — buy YES on A and NO on B, pay
`cost`, receive 100¢ on either resolution. Everything around it is broken:

### Concrete defects

- **Zero fee model.** `profit = 100 - cost` with `minProfitCents: 0.001` in the
  shipped `config.js` (the README claims 1). Against the real fee curve
  (§5) a 50/50 pair needs ~2.76¢, so the bot will fire on ~2.75¢ of pure loss
  and call it a win.
- **Prices are not executable.** `parseOutcomes()` (`bot.js:51`) reads
  `outcome.price` — a single mid/last price from pmxt's unified model — not the
  ask. You cannot buy at the mid. Real cost is at least the mid plus half the
  spread on each leg; on Kalshi the tick *is* 1¢ and the median spread on
  liquid markets is exactly 1¢ (measured, §5), so this alone is ~1¢/leg of
  optimism.
- **The two legs are sized differently — the hedge isn't a hedge.**
  `bot.js:103-104`:
  ```js
  const polyContracts   = Math.floor(tradeAmountCents / (polyPrice   || 1));
  const kalshiContracts = Math.floor(tradeAmountCents / (kalshiPrice || 1));
  ```
  With the README's own example (Poly YES 42¢, Kalshi NO 57¢, $10 notional):
  **23 YES contracts against 17 NO contracts**. Six contracts are naked
  directional. The "locked spread" advertised in the README does not exist in
  the code.
- **No leg-failure unwind.** Both legs go out via `Promise.all` as market
  orders (`bot.js:109`). If one succeeds and one fails, `executeArbitrage`
  logs `[ERROR] Trade execution failed` and returns `false` — the filled leg
  is left on the books, unhedged, untracked (`this.currentPosition` is only set
  when *both* succeed). This is the single most dangerous line of the repo.
- **The exit logic destroys the trade it just made.** A locked synthetic arb
  should be *held to resolution* — that is where the guaranteed 100¢ comes
  from. `shouldExitPosition()` (`bot.js:169`) instead sells both legs as soon
  as the opportunity leaves the top-5 list or a better one appears — i.e.
  exactly when the gap has closed, paying the spread and taker fees a second
  time to convert a locked profit into a realized loss.
- **Doc/code mismatch**: README says YOLO mode "goes ALL IN with available
  capital"; `bot.js:97` hardcodes `1000` cents.
- **Stale dependency**: `package.json` pins `pmxtjs ^0.4.4`; upstream pmxt is
  at **v2.54.0**. `getMarketsBySlug` / `createOrder({type:'market'})` may not
  exist in the current SDK. Assume this repo does not run as-is.
- **Latency**: 30–60s polling. See §4.3.

**Reuse verdict**: read `arbitrage.js` for the two-direction synthetic-arb
identity (10 lines, correct), then throw the rest away.

---

## 2. `ImMike/polymarket-arbitrage` (Python, 10.4k LOC)

Much larger, dashboard-driven, with real structure (data feed → arb engine →
execution → risk → portfolio) and 750 LOC of tests. It is the better *skeleton*
and the worse *bot*.

### Architecture

```
core/data_feed.py          market state cache + REST orderbook polling
core/arb_engine.py         intra-venue bundle arb + market making  (598 LOC)
core/cross_platform_arb.py MarketMatcher + CrossPlatformArbEngine  (799 LOC)
core/execution.py          signal queue → risk check → order placement (430 LOC)
core/risk_manager.py       position/exposure/loss/drawdown + kill switch
core/portfolio.py          positions, realized/unrealized PnL
utils/backtest.py          synthetic random-walk simulator (NOT historical)
kalshi_client/, polymarket_client/, dashboard/
```

### The good parts (genuinely worth stealing as *design*)

- **Opportunity-duration instrumentation** (`arb_engine.py:57-274`). Every
  detected opportunity is tracked until the price moves away, and durations are
  bucketed into `<100ms / <500ms / <1s / >1s`. This is precisely the
  measurement that tells you whether a strategy is reachable at your latency,
  and it costs ~80 LOC. **We should port this concept into the backtest
  harness**: for any signal, log how long the trigger condition persisted in
  the historical book/trade tape. A strategy whose median opportunity lifetime
  is below our round-trip latency is dead on arrival, and this makes that
  visible before we write the strategy.
- **Risk manager shape** (`risk_manager.py`): `check_order()` gates on
  per-market notional, global exposure, daily loss, and drawdown-from-peak,
  with a latched kill switch. Clean, synchronous, testable, no I/O. Adopt this
  interface for our sizing layer more or less verbatim.
- **Fee-aware bundle math** (`arb_engine.py:298-320`) — it at least *tries*,
  subtracting taker fees per leg and gas before comparing to `min_edge`, and
  logs gross/fees/net separately. The intent is right even though the model is
  wrong (below).
- **Category bucketing before matching** (`cross_platform_arb.py:479-510`):
  categorize both venues' markets, then only compare within category. It
  reduces the comparison count by ~1–2 orders of magnitude and logs the
  reduction. Keep the *blocking* idea; replace the *scoring*.

### Concrete defects

- **The cross-venue engine is dead code.** `check_arbitrage()` has zero
  callers. The wired path (`run_with_dashboard.py:266-387`) only runs
  `find_matches()` and paints pairs on the dashboard. No orderbooks are ever
  fetched for a matched pair; no edge is ever computed cross-venue.
- **No execution exists.** `polymarket_client/api.py:758` —
  ```python
  async def place_order(...):
      """TODO: Implement with actual Polymarket CLOB API:
         POST https://clob.polymarket.com/order"""
      order_id = f"order_{uuid.uuid4().hex[:12]}"   # fabricated
  ```
  and `kalshi_client/api.py` has no POST path at all. `ExecutionEngine.__init__`
  only accepts a `PolymarketClient`, so cross-venue execution is not even
  structurally possible.
- **The Kalshi client is broken against the live API.** It parses
  `data["orderbook"]["yes"]` as `[[price_cents:int, qty:int], …]`
  (`kalshi_client/api.py:341-362`). The API today returns:
  ```json
  {"orderbook_fp": {"yes_dollars": [["0.0100","1539195.82"], …], "no_dollars": […]}}
  ```
  (verified live from this container against
  `https://api.elections.kalshi.com/trade-api/v2/markets/KXFEDDECISION-26SEP-H0/orderbook`).
  So `get_orderbook()` returns `None` for every market — the Kalshi side is
  silently dark. Market fields likewise moved to `yes_bid_dollars`,
  `yes_ask_dollars`, `volume_fp`. **pmxt handles the new shape correctly**
  (`core/src/exchanges/kalshi/normalizer.ts:254`), which is the strongest
  argument for using pmxt's normalizers as our reference.
- **Fee model is wrong in both magnitude and dimension.** Config uses
  `taker_fee_bps: 150` (1.5% of notional) for Polymarket and a hardcoded
  `kalshi_taker_fee: float = 0.01` (1%) in `CrossPlatformArbEngine.__init__`.
  Both venues actually charge `rate × p × (1−p)` — a *parabola*, not a
  percentage of notional. At p=0.5 the true Kalshi taker fee is 1.75¢ on a 50¢
  contract = **3.5% of notional** (3.5× the model); at p=0.05 it is 0.33¢ on a
  5¢ contract = 6.6% of notional (6.6×); at p=0.9 it is 0.63¢ on 90¢ = 0.7%
  (under-charged nowhere it matters). The model is wrong in the direction that
  invents profit at the tails, which is exactly where a longshot-hunting bot
  spends its time.
- **Dimensional bug: gas added to a per-share edge.** `arb_engine.py:301,315`
  computes `gas_cost = 0.02 * 2` (dollars, per *order*) and subtracts it from
  `net_edge_long`, which is measured in dollars *per share*. Same in
  `cross_platform_arb.py:657`. For a 500-contract order this over-charges by
  ~500×; combined with `min_edge = 0.01` it means the bundle detector requires
  ~6.5¢ of gross edge on a $1 bundle and therefore never fires — which is
  presumably why nobody noticed it is also detecting a thing that cannot exist.
- **Sizing can exceed available liquidity.** `arb_engine.py:325-329`:
  ```python
  suggested_size = min(default_order_size / max(ask_yes, ask_no), max_size)
  suggested_size = max(self.config.min_order_size, suggested_size)   # ← undoes the cap
  ```
  If book depth (`max_size`) is 0, the final `max()` restores `min_order_size`.
  The depth cap is advisory. Units are also mixed: `max_size` is in contracts,
  `min_order_size` is in dollars.
- **"Backtest" is a random-number generator.** `utils/backtest.py` has no
  historical data path: `SimulatedOrderBook.step()` does a Gaussian random walk
  and, with `mispricing_probability = 0.05`, deliberately widens/narrows the
  book to create an arb. `BacktestResult` then reports win rate and Sharpe on
  that. This is the trap our `ORCHESTRATION.md` constraint #1 exists to
  prevent; treat the repo's headline numbers as meaningless.
- **REST polling, not WebSocket.** `kalshi_client.stream_orderbooks()` loops
  batches of REST `GET /orderbook` calls. Kalshi publishes an `orderbook_delta`
  WS channel (pmxt subscribes to it at
  `core/src/exchanges/kalshi/websocket.ts:171`). The repo's own instrumentation
  buckets opportunities at `<100ms`; its data path cannot see them.

---

## 3. `pmxt-dev/pmxt` — the plumbing layer (MIT, v2.54.0)

Not an arb bot: a "CCXT for prediction markets" — unified adapters for
Polymarket, Polymarket US, Kalshi, Limitless, Smarkets, Probable, Myriad,
Metaculus, Hyperliquid and others, with TS and Python SDKs and a local sidecar
server. This is the piece worth actually depending on, and its README is
already in our `README.md` §"Market plumbing".

### Orderbook handling — the part to copy

**Kalshi** (`core/src/exchanges/kalshi/normalizer.ts:254-284`). Kalshi's book
is *bids-only on both sides*; the ask must be synthesized from the complement:

```ts
// for the YES outcome:
bids = data.yes_dollars.map(l => ({price: +l[0], size: +l[1]}))
asks = data.no_dollars .map(l => ({price: round4(1 - +l[0]), size: +l[1]}))
// for a "-NO" outcome id the two are swapped
bids.sort(desc); asks.sort(asc);
```

This is the correct and complete statement of Kalshi book semantics, and it is
the thing our data layer must get right. Note the consequence: because
`ask_yes ≡ 1 − bid_no` identically, **`ask_yes + ask_no ≡ 2 − (bid_yes + bid_no)`**,
so an intra-venue bundle arb on Kalshi is algebraically the same as a *crossed
book* — which the matching engine consumes instantly. (ImMike's
`kalshi_client/models.py:46` derives asks the same way and then feeds the result
to a bundle-arb detector, which is why that detector can never fire on Kalshi.)

**Polymarket** (`core/src/exchanges/polymarket/normalizer.ts:158`) is
conventional: real `bids` and `asks` per token id, plus `isNegRisk` and
`lastTradePrice` passthrough. Neg-risk (multi-outcome events sharing collateral)
is flagged but not modeled — if we ever trade neg-risk event legs we must handle
it ourselves.

### `getExecutionPrice` — port this into the backtest fill model

`core/src/utils/math.ts` (72 LOC total) walks the book and returns the VWAP for
a requested size:

```ts
levels = (side === 'buy' ? book.asks : book.bids).filter(l => l.size > 0).sort(best-first)
for (level of levels) { fill = min(remaining, level.size); cost += fill*level.price; ... }
return { price: cost/filled, filledAmount: filled, fullyFilled: remaining <= 1e-8 }
```

This is exactly the depth-aware fill primitive our harness needs (constraint #4:
"no assumed fills at mid"), and it already returns partial-fill state.
**One footgun to fix on port**: the thin wrapper `getExecutionPrice()` returns
**`0`** when the book cannot fill the size (`math.ts:9`). A caller that doesn't
check `fullyFilled` reads a free fill. Our version must return `None`/raise.

### Cross-venue matching — the instructive part

pmxt's `Router` is the only implementation here that has *given up on fuzzy
title matching*, and how it gave up is the lesson:

- A curated `SERIES_MAP` (`core/src/router/series-map.ts`, 154 LOC) hand-maps
  normalized series ids to venue-native tickers:
  `{id:'tennis-atp-match', venues:{kalshi:'KXATPSETWINNER', polymarket:'atp'}}`.
  Partial coverage is expected and skipped gracefully.
- Actual pair matching is delegated to a **hosted match catalog** over HTTP
  (`PmxtApiClient`), which returns `{relation, confidence, reasoning}` per pair.
- The relation vocabulary is the useful export:
  ```ts
  type MatchRelation = 'identity' | 'complement' | 'subset' | 'superset' | 'overlap' | 'disjoint';
  ```
  This is the right abstraction and the thing both arb bots lack. "Fed cuts
  25bps" vs "Fed cuts ≥25bps" is `subset`, not `identity`; trading it as
  identity is the phantom-arb bug from §4.2, expressed as a type error.

### pmxt's own defects (do not adopt uncritically)

- **No fee model anywhere.** `grep -rn fee core/src/exchanges/kalshi` returns
  one hit: an unrelated `/series/fee_changes` endpoint path. Fees are entirely
  our problem.
- `fetchArbitrageFallback()` (`Router.ts:786-853`) computes
  `spread = matchBid − sourceAsk` with **no fees** and, worse, sets
  `const sourceBid = sourceAsk` (line 808) — it uses the same single price for
  both sides of the source venue. Both `fetchArbitrage` and `fetchMatchedPrices`
  are marked deprecated in the source. Treat pmxt's arbitrage surface as
  non-functional; use it for data only.
- The Router's match quality depends on a hosted API key, so it is not a
  self-contained open-source matcher.

---

## 4. Cross-cutting failure modes

### 4.1 Fee blindness (all three)

Nobody models the actual fee curve. The two venues' current schedules:

| Venue | Taker | Maker | Source |
| --- | --- | --- | --- |
| Kalshi | `round_up(0.07 × C × P × (1−P))`, rounded up to the next cent on the **order total** | `round_up(0.0175 × C × P × (1−P))` (≈¼ of taker) | [kalshi.com/fee-schedule](https://kalshi.com/fee-schedule) (official PDF at `kalshi.com/docs/kalshi-fee-schedule.pdf` returned HTTP 429 to this container; formula corroborated by [marketmath.io](https://marketmath.io/platforms/kalshi) and [whirligigbear](https://whirligigbear.substack.com/p/makertaker-math-on-kalshi)) |
| Polymarket | `C × rate × p × (1−p)`, rate by category: crypto 0.07, sports 0.05, **finance/politics/mentions/tech 0.04**, economics/culture/weather/other 0.05, **geopolitics 0** | **0 — "Makers are never charged fees"**, plus daily rebates of 15–25% of category taker fees | [docs.polymarket.com/polymarket-learn/trading/fees](https://docs.polymarket.com/polymarket-learn/trading/fees) |

Two consequences the bots all miss:

1. **The fee is a parabola peaking at 50¢, not a percent of notional.** Any
   `fee_bps`-style model is wrong everywhere; it is wrong by 3–7× at the tails
   in the direction that manufactures fake edge.
2. **Maker is free on Polymarket and ~¼-price on Kalshi.** Every taker-only
   design in these repos gives away the single largest available economy. A
   resting-limit-order strategy (which is also what our Michigan panic-dip
   thesis needs — see `README.md`) pays 0–0.44¢ where these bots pay 1.75¢.

**[UNVERIFIED]** marketmath.io mentions a `$0.035/contract` cap on Kalshi taker
fees; the formula's own maximum is 1.75¢, so this likely refers to a
higher-multiplier series. Confirm against the official PDF before relying on it.
Also unconfirmed: whether Kalshi maker fees apply to all series or only some,
and Polymarket US vs Polymarket global rate differences (a search result
mentioned a 0.06 taker / 0.0125 maker coefficient and a $1.50-per-100 ceiling
for Polymarket US as of 2026-07-01 — **not verified against primary docs**, and
relevant to us because venue eligibility for US persons differs).

### 4.2 Title matching produces phantom arbs — measured

I ran both matchers on adversarial pairs. ImMike's `calculate_similarity` is
reimplemented faithfully from `cross_platform_arb.py:348-409`; the JS one is the
literal `combinedSimilarity` from `matcher.js`. Configured thresholds: **0.6**
(ImMike, `config.yaml: min_match_similarity`) and **0.7** (realfishsam).

| Pair (these are *different* markets) | ImMike score | realfishsam score |
| --- | --- | --- |
| "Bitcoin above $100,000 on Dec 31" vs "…$110,000 on Dec 31" | **1.000** | **0.816** |
| "Highest temperature in NYC above 90F on Aug 12" vs "…95F…" | **0.863** | **0.871** |
| "Will the Chiefs win by more than 3.5 points" vs "…6.5 points" | **0.815** | **0.856** |
| "Fed cuts rates by 25 bps in September" vs "…50 bps…" | **0.817** | **0.845** |
| "Will Trump be indicted in 2026?" vs "Will Trump resign in 2026?" | **0.861** | — |
| "Will Trump win Iowa" vs "Will Trump win Ohio" | **0.850** ¹ | 0.676 |
| "Kevin Warsh" vs "Kevin Hassett" (control — correctly rejected) | — | 0.446 |

¹ short-circuited by `is_same_person_event()`, which returns a hardcoded 0.85 for
any two markets mentioning the same politician and sharing a verb
(`cross_platform_arb.py:341`). The crypto branch (`:406`) adds +0.2 to any pair
mentioning the same coin, which is what pushes the Bitcoin strike pair to 1.000.

Every single one of these is a **strike/threshold/date variant** — precisely the
family where prices *should* differ, and where a false "identity" match produces
the largest apparent edge. A fuzzy matcher is not merely noisy here; its
confidence is anti-correlated with correctness on the cases that matter.

**Design conclusion**: market matching must be **structural, not textual**.
Match on (series → event → resolution criteria → strike → resolution timestamp
→ resolution source), require exact agreement on the numeric strike and the
resolution date, and classify with pmxt's relation vocabulary. Anything else
gets a `needs-human-review` flag, never a trade. A curated map of the ~20 series
we actually care about (pmxt's `SERIES_MAP` approach) beats a matcher over 5,000
markets, and takes an afternoon.

### 4.3 Latency and leg risk

| | ImMike | realfishsam | Reachable? |
| --- | --- | --- | --- |
| Data path | REST orderbook polling in batches | REST poll every 30–60s | — |
| Signal expiry config | `signal_expiry_seconds: 5.0` | none | — |
| Its own measured opportunity lifetimes | bucketed `<100ms / <500ms / <1s / >1s` | — | **no** |
| Leg failure handling | n/a (no execution) | none — filled leg left naked | — |

ImMike's instrumentation and ImMike's data path contradict each other: it counts
sub-100ms opportunities while polling REST. `README.md` in this repo already
concludes that "pure arbitrage is already owned by millisecond bots"; this is the
code-level confirmation. Add to that the structural leg risk of cross-venue
execution — two different exchanges, two different settlement systems, USDC on
Polygon vs USD at a CFTC-regulated DCM, no atomicity, no cross-margining — and
the practical fill probability on the second leg after the first prints is the
dominant unmodeled term. FutureSearch measured ~43% fill rates in simulation on
*single*-venue resting orders (`ORCHESTRATION.md` constraint #4); a two-leg
cross-venue taker sequence at 30s polling is worse, not better.

---

## 5. The numbers that decide it (measured today from this container)

### 5.1 Kalshi book structure and spreads

Scan of 236 live two-sided Kalshi markets across `KXHIGHNY`, `KXMLBGAME`,
`KXBTCD`, `KXFEDDECISION`, `KXPRESPARTY`, `KXNFLGAME`:

- `yes_bid + no_bid > $1.00`: **0 / 236**
- `yes_ask + no_ask < $1.00`: **0 / 236**
- YES spread: **median 1.0¢**, mean 2.4¢, min 1.0¢; **158/236 (67%) sit at
  exactly one tick.**

Intra-venue arb: dead. And the spread being *one tick* means a taker round trip
costs 1¢ of spread before a fee that is 1.75¢ at the money.

### 5.2 Fee cost per contract (100-contract order, so cent-rounding is negligible)

| p | Kalshi taker | Polymarket taker @0.04 (finance/politics) | @0.07 (crypto) |
| --- | --- | --- | --- |
| 0.50 | 1.76¢ | 1.00¢ | 1.75¢ |
| 0.30 | 1.47¢ | 0.84¢ | 1.47¢ |
| 0.10 | 0.64¢ | 0.36¢ | 0.63¢ |
| 0.05 | 0.34¢ | 0.19¢ | 0.33¢ |

**Break-even gross gap for a cross-venue synthetic pair** (buy YES on one venue,
buy NO on the other; Kalshi + Polymarket-finance legs):

| Leg prices | Minimum gross discount below $1.00 required |
| --- | --- |
| 0.50 / 0.50 | **2.76¢** |
| 0.30 / 0.70 | 2.31¢ |
| 0.10 / 0.90 | 1.00¢ |
| 0.05 / 0.95 | 0.53¢ |

And that is *before* the 1¢ Kalshi tick you cross to be a taker, before slippage
past the top level, and before the second-leg fill risk.

### 5.3 Live cross-venue check on the most liquid identical pair

Kalshi `KXFEDDECISION-26SEP-*` (volumes $0.3M–$2.3M) vs Polymarket event
`fed-decision-in-september-762` (per-market volume $3.5M–$6.8M), quotes pulled
simultaneously today. Polymarket best bid/ask from Gamma; Kalshi from
`yes_bid_dollars`/`yes_ask_dollars`. "Buy NO @" is the complement of the other
venue's best YES bid. Fees: Kalshi 0.07, Polymarket 0.04 (finance).

| Contract | Direction | Gross | Fees | **Net** |
| --- | --- | --- | --- | --- |
| No change (0bps) | buy YES Kalshi @0.58 + buy NO Poly @0.42 | +0.00¢ | 2.68¢ | **−2.68¢** |
| No change (0bps) | buy YES Poly @0.59 + buy NO Kalshi @0.43 | −2.00¢ | 2.68¢ | **−4.68¢** |
| Hike 25bps | buy YES Poly @0.41 + buy NO Kalshi @0.59 | +0.00¢ | 2.68¢ | **−2.68¢** |
| Hike 25bps | buy YES Kalshi @0.42 + buy NO Poly @0.60 | −2.00¢ | 2.68¢ | **−4.68¢** |
| Hike >25bps | buy YES Poly @0.006 + buy NO Kalshi @1.00 | −0.60¢ | 0.02¢ | **−0.62¢** |
| Cut 25bps | buy YES Poly @0.015 + buy NO Kalshi @0.99 | −0.50¢ | 1.06¢ | **−1.56¢** |

The two venues agree to **within one tick** on a market both are quoting to
$1M+. There is no gross gap to split, let alone one that clears 2.76¢.

**Caveat, stated honestly**: this is one snapshot of the *most efficient* pair
on both venues. Thin, neglected pairs will show wider gaps — but they also show
wider spreads (mean Kalshi spread 2.4¢ vs median 1.0¢), thinner depth, and the
gap is more likely to be a *matching error* (§4.2) or a genuine rules difference
than an arb. Cross-venue gaps on illiquid pairs are a **signal**, not a free
lunch. That is the reframe in §7.

---

## 6. Verdict

### Is intra-venue arb viable for us?
**No — it is not a thing.** On Kalshi the YES and NO stacks are one book
(`ask_yes ≡ 1 − bid_no`), so a bundle arb is a crossed book the engine consumes.
On Polymarket the CLOB mints a complete set when a YES bid and NO bid cross
$1.00. Measured: 0 occurrences in 236 live Kalshi markets. Do not spend an hour
on this. (Adjacent and *not* covered here: multi-outcome/neg-risk "sum of legs ≠
1" within a single event — that one is real, is what pmxt's `isNegRisk` flag
hints at, and is worth a separate 30-minute probe. Not investigated in this
pass.)

### Is cross-venue arb viable for us post-fee at side-project scale?
**No, not as a primary strategy.** Three independent reasons, any one of which
is sufficient:

1. **Fee floor.** ~2.76¢ of gross gap needed at the money; the liquid pairs
   agree to within 1¢ (measured).
2. **Latency.** The gaps that do open on liquid pairs are the sub-second
   variety ImMike's own instrumentation measures; we would be polling REST from
   a laptop against firms colocated with both venues.
3. **Capital and leg risk.** Cross-venue requires simultaneous funded balances
   on two venues in two currencies with no atomicity and no cross-margining; a
   locked pair also ties up ~97¢ per $1 of payout until resolution, so even a
   real 3¢ gap on a 6-month market is a ~6% annualized return with tail risk
   from resolution-criteria divergence (the two venues can settle the "same"
   market differently — see the `subset`/`overlap` relations).

This agrees with `README.md`'s existing "Against" section — now with code-level
and quote-level evidence rather than assertion.

### What *is* worth reusing
Plumbing, not strategy. Specifically:

| Take | From | Why |
| --- | --- | --- |
| Kalshi orderbook normalization (`orderbook_fp` → bids/derived asks) | pmxt `core/src/exchanges/kalshi/normalizer.ts:254` | The only current-API-correct implementation of the three |
| Depth-walking VWAP fill (`getExecutionPriceDetailed`) | pmxt `core/src/utils/math.ts` | 32 usable lines; exactly our honest-fills requirement. Fix the `return 0` on partial fill |
| `MatchRelation` vocabulary + curated `SERIES_MAP` | pmxt `core/src/router/{types,series-map}.ts` | The correct abstraction for cross-venue pairing, and the admission that fuzzy matching fails |
| Opportunity-lifetime instrumentation | ImMike `core/arb_engine.py:57-274` | Tells us pre-emptively whether a signal is reachable at our latency |
| Risk-manager interface + latched kill switch | ImMike `core/risk_manager.py` | Clean, I/O-free, testable gate for our sizing layer |
| Category blocking before pair scoring | ImMike `core/cross_platform_arb.py:479` | Cheap comparison-count reduction; keep blocking, replace scoring |
| Two-direction synthetic-arb identity | realfishsam `src/arbitrage.js:4-8` | 10 correct lines to reuse as the *pricing* of a cross-venue divergence, not as a strategy |

| Reject | Why |
| --- | --- |
| All three title/fuzzy matchers | §4.2 — confidence anti-correlated with correctness |
| Any `fee_bps` percent-of-notional fee model | §4.1 — off by 3–7× at the tails |
| ImMike `utils/backtest.py` | Synthetic RNG that injects its own alpha |
| ImMike Kalshi REST client | Broken against the current API |
| realfishsam execution/exit logic | Unequal legs, no unwind, exits at the worst moment |
| pmxt `fetchArbitrage*` | Deprecated upstream, fee-free, `sourceBid = sourceAsk` bug |

---

## 7. Concrete recommendations for the build (next few hours)

1. **Data layer (P2)** — mirror pmxt's Kalshi normalizer rather than inventing
   one. Store, per snapshot: `yes_dollars`/`no_dollars` ladders (not just top of
   book), and derive `ask_yes = 1 − bid_no`. Note the API is now
   `orderbook_fp` + `*_dollars` string fields + `volume_fp`; any tutorial code
   using cents-ints is stale.
2. **Backtest harness (P3)** — port `getExecutionPriceDetailed` as the fill
   primitive; make it return `(vwap, filled_qty, fully_filled)` and *never* a
   zero price. Then implement fees exactly:
   ```python
   def kalshi_taker_fee(contracts, price):        # dollars, per ORDER
       return math.ceil(0.07 * contracts * price * (1 - price) * 100) / 100
   def kalshi_maker_fee(contracts, price):
       return math.ceil(0.0175 * contracts * price * (1 - price) * 100) / 100
   def poly_taker_fee(contracts, price, rate):    # rate: 0.04 finance/politics, 0.05 sports/econ, 0.07 crypto, 0 geopolitics
       return rate * contracts * price * (1 - price)
   ```
   Charge maker vs taker explicitly per constraint #4. The rounding is on the
   order total, so per-contract cost falls slightly with order size — model it
   at the order level, not per share.
3. **Add an opportunity-lifetime metric to the harness** (ImMike's idea, our
   data): for every signal a strategy emits, measure how long the trigger
   condition persisted in the historical tape. Report it next to P&L. Any
   strategy whose median lifetime is under ~5 seconds is not ours to trade, and
   we want to know that before P5 rather than after.
4. **If we want cross-venue at all, build the divergence signal, not the arb.**
   Same plumbing, different claim: when a *structurally verified identical* pair
   (exact strike, exact resolution date, exact resolution source) shows Kalshi
   and Polymarket disagreeing by more than the combined spread + fee floor, that
   is evidence one venue is wrong — trade the cheaper side *directionally* on
   the venue with better depth and hold to resolution, sized by Kelly against a
   posterior that blends both venues' prices. Bridgewater's finding cited in
   `README.md` — that model+price ensembles beat the market while models alone
   do not — is exactly an argument for feeding a second venue's price into the
   forecaster as a feature. That is a real, cheap, contamination-free
   price-only strategy for P4, and it reuses everything in the "take" table.
5. **Build the matcher structurally and small.** Hand-curate the 10–20 series
   pairs we care about in a `series_map.py` (pmxt's file is a good template),
   assert equality on `(strike, resolution_date, resolution_source)`, and tag
   everything else `subset`/`overlap`/`unknown` → excluded from trading. Budget:
   one afternoon, zero ML, and it will beat every matcher in these three repos.

## 8. Open questions / unverified

- Kalshi official fee PDF (`kalshi.com/docs/kalshi-fee-schedule.pdf`) returned
  **HTTP 429** to this container; formula corroborated by two secondary sources
  but **verify the primary before live trading**, especially (a) the claimed
  $0.035/contract cap, (b) whether maker fees apply to all series, (c) any
  settlement fee.
- Polymarket US fee coefficients (0.06 taker / 0.0125 maker / $1.50-per-100
  ceiling, effective 2026-07-01) came from a search snippet, **not primary
  docs** — and venue eligibility for US persons is a separate question this
  research did not touch.
- Neg-risk / multi-outcome intra-event arb (sum of mutually exclusive legs ≠ 1)
  was **not** investigated. pmxt flags `isNegRisk` but does not model it. Worth
  a short probe — it is the one arb family these repos do not cover and the one
  the venues' matching engines do not automatically absorb.
- pmxt's hosted match catalog quality is unknown (requires an API key); the
  open-source part of its matcher is only the 154-line curated `SERIES_MAP`.

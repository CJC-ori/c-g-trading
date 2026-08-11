# Viability: AI-Agent Trading on Prediction Markets (Kalshi)

**Status:** Research scoping doc, 2026-08-11. Job: stress-test the thesis, not cheerlead.
**Method note:** Everything below is grounded in fetched sources or in raw Kalshi API data pulled on 2026-08-11 (endpoints cited inline). Anything unverifiable is explicitly flagged.

---

## 1. The thesis

Prediction markets (Kalshi first, Polymarket second) may be exploitable by an AI agent with real forecasting ability that gets invoked frequently. Two candidate strategies:

- **(a) Arbitrage** — within-platform (mutually exclusive brackets summing ≠ 100%) and cross-platform (Kalshi vs Polymarket on the same event).
- **(b) Hits-based bets on genuine mispricings**, especially around time-bound events where markets **overcorrect to news and then revert** — buy the panic, sell the recovery — plus longer-horizon bets where a calibrated forecast disagrees with the price.

Seed observation (Chris, instinctive, pre-verification): the 2026 Michigan Democratic Senate primary on Kalshi — El-Sayed ~98.5% pre-election felt too high; on election night the price crashed to ~77%, recovered, and resolved YES; the victory-margin markets looked even more mispriced.

---

## 2. The Michigan seed example — verified

**This checks out, and it is actually better than remembered.** Verified from the Kalshi public trade API (primary source) plus press coverage.

### The race
- Aug 4, 2026 Michigan Democratic Senate primary: **Abdul El-Sayed 48.5%, Haley Stevens 47.5%, Mallory McMorrow 4.0%** — a ~1-point margin ([NBC News results page](https://www.nbcnews.com/politics/2026-primary-elections/michigan-senate-results)). AP called it only at 9:56 a.m. the next morning, with El-Sayed leading by 1% at 99% counted ([Bridge Michigan](https://bridgemi.com/michigan-government/michigan-democratic-senate-primary-results-abdul-el-sayed-haley-stevens/)). ~$98.2M in ad spending/reservations made it the third most expensive Senate primary in US history, most of it behind Stevens (same Bridge Michigan source).
- Pre-election polling was **genuinely divided**: Emerson (Jul 26–27) had El-Sayed +15 ([Emerson](https://emersoncollegepolling.com/michigan-2026-poll-abdul-el-sayed-leads-haley-stevens-for-democratic-us-senate-nomination/)); Tavern Research (Jul 7–8) had a statistical tie ([Michigan Advance](https://michiganadvance.com/2026/07/08/new-poll-shows-el-sayed-stevens-statistically-tied-in-michigans-democratic-u-s-senate-primary/)); a Detroit News/WDIV poll (Jul 8–11) had Stevens ahead ([Detroit News](https://www.detroitnews.com/story/news/politics/2026/07/16/michigan-us-senate-democratic-primary-poll-haley-stevens-abdul-el-sayed-detroit-news-wdiv/90939862007/)).

### The Kalshi winner market (ticker `KXSENATEMID-26-AELS`, resolved YES, lifetime volume ≈ 9.94M contracts)
Hourly candlesticks pulled from `api.elections.kalshi.com/trade-api/v2/series/KXSENATEMID/markets/KXSENATEMID-26-AELS/candlesticks` (times UTC; election night in ET = UTC−4):

| Time (UTC) | Trade close | Low | Note |
|---|---|---|---|
| Aug 4, 12:00–23:00 | 97.9–98.6¢ | — | **Pre-election plateau ≈ 98–98.5¢** (matches Chris's memory; Kalshi's own blog had him at 96.6% on Jul 31 — [Kalshi News](https://news.kalshi.com/p/michigan-senate-primary-odds-el-sayed-has-a-96-chance-to-be-the-democratic-nominee)) |
| Aug 5, 01:00 | 99.9¢ | — | Early returns look fine; price touches 99.9 |
| Aug 5, 02:00 | 95.3¢ | 95.0¢ | Suburban (Stevens-friendly) counts land |
| Aug 5, 03:00 | 83.0¢ | **79.0¢** | Panic leg |
| Aug 5, 03:16–03:25 | 75–78¢ | **74.0¢** | **Trough: 74¢ at 03:17–03:20 UTC (11:17–11:20 pm ET)** — deeper than the remembered 77% |
| Aug 5, 04:00 | 94.2¢ | 74.0¢ | V-shaped recovery within the hour |
| Aug 5, 05:00–07:00 | 97.9→99.5¢ | — | Recovery complete; resolves YES ($1.00) |

Depth in the dip (1-minute candles, same endpoint): **39 one-minute bars traded with lows ≤ 85¢; ~304k contracts traded in minutes touching ≤ 80¢ (~$230k notional); ~675k contracts in minutes touching ≤ 85¢; ~1.45M contracts total traded in the 02:30–05:00 UTC window.**

**Buy-the-dip arithmetic (ex post):** buy YES at 77¢ (taker fee 0.07×0.77×0.23 ≈ 1.24¢), sell at 97¢ five hours later (fee ≈ 0.20¢) → **+23.7% in ~5 hours**; hold to settlement → **+27.8% in ~36 hours**. Real fills of $25k–$100k in the ≤85¢ zone look plausible given traded volume, though an aggressive new bidder would also have moved the price — treat $100k+ as optimistic.

### The victory-margin markets (ticker `KXPRIMARYMOV-KXSENATEMID26`) — the bigger mispricing
Verified via the same API. On election day, **before polls closed**:

- **"El-Sayed ≥15%" traded 53–64¢** all day (Kalshi's own blog promoted "favored to win by 15+ points" at 62% on election morning — [Kalshi News](https://news.kalshi.com/p/michigan-senate-primary-odds-el-sayed-favored-15-plus)), with 12–15% at ~17% and 9–12% at ~13%.
- **"El-Sayed 0–3%" — the bracket that actually happened — traded at 2–5¢ all day.** It went to 98–99¢ by mid-morning Aug 5.
- Press confirmed the miss: "Kalshi and Polymarket badly miss El-Sayed margin" ([Prediction News](https://predictionnews.com/story/prediction-markets-lengthen-el-sayed-lead-in-michigan-democratic-senate-primary) — headline verified via search; page itself returned 403 to our fetcher), and DeFi Rate documented Kalshi at 98% on ~$8M volume and Polymarket at 99% on ~$1.8M pre-election ([DeFi Rate](https://defirate.com/news/el-sayed-survives-michigan-primary-prediction-markets-still-think-he-wins-in-november/)).

**The honest ex-ante readings:**
1. Given polls ranging from "tie" to "+15", pricing 62% on a ≥15-point blowout was an over-weighting of the single friendliest poll. A boring poll-aggregation model would have assigned meaningful mass to single-digit margins. Buying NO on "≥15%" at ~36–47¢ paid ~**+113–178% overnight**, with real capacity (~213k contracts / ~$130k notional traded on that bracket on election day alone). Buying the 0–3% bracket at 4¢ paid **~24x**, but pre-election capacity in that bracket was tiny (a few hundred dollars of notional per hour).
2. **The winner-market expression, done right (Chris's correction):** Kalshi positions are tradeable continuously until resolution, so buying NO at ~1.5–2¢ pre-election did NOT require El-Sayed to lose — selling NO into the election-night panic (NO ≈ 26¢ at the 74¢ YES trough) closed the position at roughly **13–17x gross, ~11–14x net of taker fees**. Ex ante this is a *convexity bet on a scare, not a directional bet*: in the no-scare world (returns come in comfortably) NO bleeds from 2¢ toward 0 with no exit — a 100% loss of the stake. So it's a lottery-ticket structure: total loss most times it's tried, ~10–20x when any panic materializes before resolution; position sizing has to assume the former. Only *holding NO to resolution* — betting on the outcome rather than on the volatility — lost 100% here. Execution caveat: the ≤80¢ trough lasted ~3 minutes (~$230k notional traded ≤80¢), so capturing it realistically means resting limit orders placed in advance — which also avoids taker fees, and is precisely what a bot can do that a human watching returns cannot. Net reading: the margin markets are the *pre-event forecasting* expression of the instinct; cheap NO plus a resting exit ladder is the *volatility* expression of the same instinct.

### What could not be verified
- No independent tick-level record of *who* was on each side of the dip (Kalshi doesn't publish trader-level data).
- Whether the 74¢ trough was "irrational panic" vs. a rational read of the counts at 11:17 pm (Detroit/Wayne County was still out; a live county-baseline model would likely have said >85% at that moment, but we have no archived model output to prove it).
- The Polymarket-side price path on election night (their market existed — [Polymarket event page](https://polymarket.com/event/michigan-democratic-senate-primary-winner) — but we did not pull their history).

---

## 3. What the evidence says (for)

**Documented, persistent favorite–longshot bias on Kalshi itself.** Bürgi, Deng & Whelan, "Makers or Takers: The Economics of the Kalshi Prediction Market" (GWU working paper 2026-001, Feb 2026 — [PDF](https://www2.gwu.edu/~forcpgm/2026-001.pdf)), using transaction-level data on 313,972 contracts (2021–Apr 2025):
- Low-price contracts win far less often than break-even requires (a 5¢ contract winning 3% of the time = −40% pre-fee); **contracts above 70¢ show statistically significant positive post-fee returns**; makers on 50¢+ contracts earn **+2.6% after fees** per contract-episode (not annualized), SD 33%.
- Makers systematically out-earn takers.
- The authors' own explanation for why this persists: **small market sizes** (top-decile Kalshi markets average only **$526,245** final volume), lumpy risk, and ignorance — i.e., too small for professional capital, which is exactly the niche a two-person operation can occupy.

**Election-night overreaction has an academic pedigree.** Page (2012) documented the "Yogi Berra bias" on InTrade — prices for trailing sides in the final minutes stay too high relative to how often they win; Tetlock (2004) found TradeSports overreacts to news (both summarized in the GWU paper's lit review and the broader literature — [favourite-longshot bias overview](https://en.wikipedia.org/wiki/Favourite-longshot_bias)). The Michigan V-shape (98.5 → 74 → 100 in ~6 hours on 1.45M contracts) is a live specimen of exactly this class.

**Model-driven trading has beaten these markets before.** The French trader "Théo" commissioned private neighbor-polls and made ~$80M+ on Trump 2024 on Polymarket ([CBS News](https://www.cbsnews.com/news/french-whale-made-over-80-million-on-polymarket-betting-on-trump-election-win-60-minutes/)); academic post-mortems of Polymarket 2024 found sophisticated traders running early-vote models on election night, ahead of TV coverage ([Anatomy of Polymarket, arXiv](https://arxiv.org/html/2603.03136v1)).

**Arbitrage is real and measured.** "Unravelling the Probabilistic Forest" (AFT 2025, [arXiv:2508.03474](https://arxiv.org/abs/2508.03474)) measured **>$40M of arbitrage profit extracted from Polymarket alone, Apr 2024–Apr 2025** ($10.6M single-condition, $23.3M market-rebalancing, ~$95k combinatorial). Cross-platform Kalshi↔Polymarket spreads of 2–5% on major events are routinely documented by scanner services ([DropsTab research](https://news.dropstab.com/research/kalshi-vs-polymarket), [ahasignals](https://ahasignals.com/research/prediction-market-arbitrage-strategies/)).

**The tooling exists.** Kalshi's market data API is public and free (we pulled everything in §2 without an account); FutureSearch runs a public AI-forecaster pipeline over ~3,500 Kalshi markets at **~$0.60/question** in research+forecasting cost and paper-trades the disagreements ([forecaster case study](https://futuresearch.ai/blog/kalshi-forecaster-case-study/), [trader case study](https://futuresearch.ai/blog/kalshi-trader-case-study/)).

---

## 4. What cuts against it (honest)

**The arb lane is already occupied by fast automated capital.** The same AFT 2025 paper found arb profits went overwhelmingly to a handful of automated wallets — top three took $4.2M combined; the single top arbitrageur averaged **$496/trade over 4,049 trades**. That is a latency-and-infrastructure game. A part-time two-person team enters that race last.

**Kalshi's fee curve is engineered to kill thin mid-price edges.** Official schedule ([kalshi.com/docs/kalshi-fee-schedule.pdf](https://kalshi.com/docs/kalshi-fee-schedule.pdf), corroborated by [OddsShopper](https://www.oddsshopper.com/articles/prediction-markets/kalshi-fees) and [pm.wiki](https://pm.wiki/learn/kalshi-fees-explained)): taker fee = ⌈0.07 × C × P × (1−P)⌉, maker fee = 25% of that; no deposit/settlement fees. At 50¢ that is 1.75¢/side → **a round-trip taker pays ~3.5¢ on ~50¢ at risk ≈ 7% drag**. A "2–5% cross-platform spread" at mid prices is mostly or entirely eaten by fees + slippage + settlement-rule mismatch between platforms. (Near the extremes fees shrink — at 98¢ the taker fee is ~0.14¢ — which is why the favorite/dip end of the book is where post-fee edge survives; consistent with the GWU findings.)

**Liquidity is the binding constraint everywhere except marquee events.** Measured on 2026-08-11 via the orderbook endpoint:
- Mid-tier political market (`KXMIDTERMMOV-MISEND-P3`, "Dems win MI Senate by 3+"): ~2¢ spread, **top-of-book depth of a few hundred to ~1,100 contracts per level — roughly $1–3k near the touch**.
- Top-tier market (`KXMISENATE-26-AELS`, Michigan Senate general): 1¢ spread (54/55), ~**$22k** of bids within 5¢.
- FutureSearch's simulated $100k portfolio could deploy only ~$50k against real order books; **only 8 of 24 positions filled completely, average fill ~43%** ([trader case study](https://futuresearch.ai/blog/kalshi-trader-case-study/)). GWU: top-decile markets average $526k *lifetime* volume.
- Implication: the strategy's realistic capacity is **$10k–100k per event, on a handful of Michigan-grade events per year**. This can be a good side project; it cannot become a fund.

**Adverse selection on election night is real.** The counterparties in the dip window include people running live early-vote/precinct models (documented in 2024 — [Anatomy of Polymarket](https://arxiv.org/html/2603.03136v1)) and, in primaries, people with campaign internals. When you buy the 74¢ print, you must believe you know more than the person selling it at 11:17 pm with the Wayne County count on a second monitor. In Michigan the panickers were wrong — but the bot needs to distinguish "retail panic" from "informed selling" *live*, and there is no dataset proving anyone can do that reliably across many events.

**Hits-based betting with small n has brutal variance.** At ~4 qualifying events/year, one wrong dip-buy (price crashes because the news is *right*, position → 0) erases ~3–8 winners (see §5). The Samuelson/proper-risk-aversion argument in the GWU paper (§6) is exactly about this: the measured +2.6% maker edge comes with 33% SD per episode. And remember §2: the seed instinct itself ("98.5 too high"), expressed naively, lost money.

**Regulatory/platform risk is nonzero and moving.** Kalshi is a CFTC-designated contract market, federally regulated, legal for US persons in 40+ states — but **Minnesota's ban took effect Aug 1, 2026**, and courts have let state regulators restrict Kalshi's *sports* contracts in Nevada, Ohio, **Michigan**, Arizona, Maryland, Massachusetts (political/event contracts so far unaffected) ([Saturday Down South state guide](https://www.saturdaydownsouth.com/prediction-markets/kalshi-promo-code/legal-states/), [masterpredictionmarkets](https://masterpredictionmarkets.com/blog/is-kalshi-legal/)). Check the current rules for whichever state each of you trades from. In July 2026 the CFTC took the unprecedented step of staying a Kalshi emergency rule and ordering the exchange to honor trades it had tried to unwind ([Government Enforcement Report](https://www.governmentenforcementreport.com/2026/07/cftc-stays-kalshi-emergency-rule-directs-exchange-to-honor-trades-in-unprecedented-exercise-of-federal-authority/)) — good that the regulator backed traders, bad that it was needed. Withdrawals are ACH 1–3 business days ([californiapredictionmarkets guide](https://californiapredictionmarkets.com/guides/how-to-withdraw-from-kalshi)), so capital recycles slowly across events. **Not researched here: tax treatment of Kalshi gains — flag for a real accountant before going live.**

**No public proof yet that AI forecasters beat these markets post-fee.** FutureSearch — the best-positioned team publicly attempting exactly this thesis — is still in *paper-trading* mode, explicitly saying "We don't know yet whether the AI forecaster adds enough accuracy," with a stated aspiration of ~30% annualized ([trader case study](https://futuresearch.ai/blog/kalshi-trader-case-study/)). Absence of proof isn't proof of absence, but the null hypothesis has not been rejected in public by anyone.

---

## 5. Back-of-envelope economics

**Strategy (a), cross-platform arb — likely not worth building.**
Gross spreads 2–5% on major events; Kalshi round-trip taker drag ~1.5–3.5¢ at mid prices, plus Polymarket-side costs, plus settlement-rule mismatch risk, plus capital split across two platforms with 1–3-day ACH recycling. Net edge after all that is ~0–2% per opportunity on small size, in a race where incumbent bots average $496/trade at scale and take the good prints in milliseconds ([arXiv:2508.03474](https://arxiv.org/abs/2508.03474)). Expected outcome for a part-time entrant: low hundreds of dollars per month, high engineering cost. **Use arb detection as a monitoring/signal tool, not a P&L strategy.**

**Strategy (b), overcorrection + margin-market alpha — plausibly positive EV, capacity-limited.**
Michigan-calibrated unit economics:

| Play | Entry | Exit | Fees (round trip) | Return | Realistic size |
|---|---|---|---|---|---|
| Dip-buy YES at trough zone | 77¢ | 97¢ (5h) or $1 (settle) | ~1.4¢ | **+24–28%** | $25–100k |
| NO on "≥15% margin" (day-of) | ~38¢ | $1 | ~1.9¢ | **+150%** | $5–25k |
| YES on "0–3% margin" (day-of) | ~4¢ | $1 | ~0.3¢ | **~+2,300%** | $0.5–2k (book was empty) |

Portfolio math for the dip-buy strategy: assume 4 qualifying events/year, $25k each, +25% when right. If the dip is a false alarm 15% of the time (position → ~0, lose ~78¢ on the dollar):
EV/play ≈ 0.85×(+25%) + 0.15×(−78%) ≈ **+9.5%/play ≈ +$2.4k per $25k play, ~$9–10k/year** at that cadence — and one loss is −$19.5k, so a 2-loss year (~2% probability at these assumptions, but your assumed 85% hit rate is itself unproven) roughly wipes two years of wins. The whole strategy lives or dies on whether the bot's live estimate of p is genuinely ~10+ points better than the panicked market's — which is exactly the thing to measure before betting (see §6).
The **margin/bracket markets are the higher-EV, lower-drama edge**: mispricing there was visible for *days* at size (62% on ≥15pt vs a polling spread of tie-to-+15), fees at extreme prices are negligible, and being right doesn't require calling the winner — only calling *uncertainty* correctly, which is the one thing a calibrated forecaster is actually good at. Capacity is the cost: low-tens-of-thousands per event.

**Overall realistic ceiling:** with $50–100k of combined bankroll, disciplined event selection (elections, time-bound political/econ events; skip sports/crypto/insider-prone markets, as FutureSearch does), something like **$10–40k/year** if — big if — the forecasting edge is real. Meaningful side project; not a business.

---

## 6. Verdict & cheap MVP test

**Verdict: qualified yes on strategy (b), no on strategy (a) as a business.** The Michigan example survives verification completely — including a 24¢ V-shaped election-night dislocation on seven figures of volume and a margin market that priced a 62% chance of a 15-point blowout the morning of a 1-point race. Independent academic work confirms both the bias family (favorites underpriced, longshots overpriced, post-fee positive returns above 70¢ on Kalshi specifically) and the reason it persists (markets too small for professional capital). But the edge is capacity-constrained to side-project scale, hits-based variance is severe with small n, adverse selection on election night is real, and nobody has publicly demonstrated a post-fee-profitable AI forecaster on Kalshi yet. So: earn the right to bet with a paper-trailed record first.

**MVP (cheap, ~8–12 weeks, ~$50–100 total in API costs, $0 at risk):**
1. **Forecast logger.** Nightly job: pull all open Kalshi markets via the free public API, filter to politics/econ/time-bound events (drop sports, crypto, insider-prone), have the agent produce a probability + one-paragraph rationale for the ~20 largest disagreements with the market (FutureSearch's pipeline costs ~$0.60/question as a benchmark). Log forecast, market mid, order-book snapshot, timestamp. Never look at outcomes before they resolve.
2. **Simulated execution, real books.** Fill simulated positions only against the *actual* order book snapshot (walk-the-book, FutureSearch-style), with taker fees applied. This bakes the capacity constraint into the P&L from day one.
3. **Scorecard.** After ≥100 resolved markets: Brier score vs. market-price-as-forecast; simulated post-fee P&L; calibration curve. **Go/no-go gate: the bot must beat the market's Brier score AND show positive simulated post-fee P&L with a margin that survives halving.**
4. **Election-night module (the Michigan replay).** Before risking anything on live dips, build the county-baseline model (expected vote share by county/precinct as returns land) and *paper-trade it live* on the next few 2026 primaries and the Nov 3, 2026 midterms — margin markets for November are already listed and thin (we measured $1–3k of depth; early makers get the good prices). Compare the model's live p to the market's tick-by-tick p; count how often the market's excursions beyond the model band revert.
5. **Only then go live**, with $2–5k, maker orders where possible (25% of taker fees, and the GWU data says makers earn the edge), hard per-event caps, and a pre-registered rule for what performance kills the project.

One more honest note: the seed instinct was validated, but the profitable expression of it was non-obvious (margin brackets, not the winner market). That is an argument *for* the systematic-bot approach over vibes — and *for* paper-trading long enough to learn which instrument expresses each edge before real money goes in.

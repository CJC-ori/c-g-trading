# Prior Art: Open-Source Code for an AI-Forecasting Trading Bot

*Survey for Chris + Griffin's Kalshi (maybe Polymarket) bot. Researched 2026-08-11. Every claim below comes from a page actually fetched that day; anything not verified against a primary source is flagged. Repo metadata (license, last push, stars) comes from the GitHub API on 2026-08-11.*

---

## 1. What we could fork or use directly

Ranked by how much of our stack each one covers.

| # | Repo | License | What it gives us | Last push / status | Verdict |
|---|------|---------|------------------|--------------------|---------|
| 1 | [Metaculus/forecasting-tools](https://github.com/Metaculus/forecasting-tools) | MIT | Python framework for AI forecasting bots: pre-built `MainBot`/`TemplateBot`, `GeneralLlm` (litellm-based multi-provider wrapper with retries + cost tracking), `MonetaryCostManager` (spend limits), `SmartSearcher`/`ExaSearcher` research tools, Metaculus API client. `pip install forecasting-tools`. | 2026-08-09; 76★, 36 forks — actively developed | **Best forecasting-side base.** README says "still in an experimental phase," but MIT + active + exactly our problem shape. Fork or depend on it; strip the Metaculus-specific I/O. |
| 2 | [pmxt-dev/pmxt](https://github.com/pmxt-dev/pmxt) | MIT | "CCXT for prediction markets": one unified API for market discovery, orderbooks, and order execution across Polymarket, Kalshi, Limitless, Smarkets + ~15 venues. Python (`pip install pmxt`, 3.8+) and TypeScript SDKs, CLI, hosted mode or self-host with your own venue credentials, MCP server (`@pmxt/mcp`) so Claude agents can call it directly. | 2026-07-18; 2,074★, 256 forks — active, but **1,051 open issues** | **Best trading-side abstraction**, especially if we want Kalshi + Polymarket behind one interface. Caveats: TypeScript core, huge open-issue count, and hosted mode means custody/API-key trust in a third party — self-host mode avoids that. For Kalshi-only, the official SDK (below) is simpler. |
| 3 | [Panshul42/Forecasting_Bot_Q2](https://github.com/Panshul42/Forecasting_Bot_Q2) | **AGPL-3.0** | The Q2 2025 Metaculus AI Benchmark **winner**, open-sourced. Full pipeline: question parsing → query generation → parallel retrieval (Serper, AskNews, Perplexity, BrightData) → inside-view/outside-view research reports → 5-agent ensemble (Claude 3.7 Sonnet ×2, o4-mini ×2, o3 ×1) → aggregation. | 2025-06-15 (frozen post-tournament); 35★, 18 forks | **Best reference implementation to study, not to fork.** AGPL is viral (any derivative we deploy as a service must be open-sourced); code is Metaculus-question-shaped. Read it, reimplement the ideas under our own roof. |
| 4 | Kalshi official SDKs — [`kalshi_python_sync` / `kalshi_python_async`](https://docs.kalshi.com/sdks/overview) | Not checked (official Kalshi packages on PyPI) | Official Python sync + async clients and a TypeScript client generated from Kalshi's OpenAPI spec. Docs warn SDKs "may lag the API" and recommend generating a client from the OpenAPI/AsyncAPI specs for cutting-edge use. Old `kalshi-python` package is deprecated. | Current per docs.kalshi.com (fetched 2026-08-11) | **Use for Kalshi order plumbing** if we skip pmxt. Thin, official, boring — good. |
| 5 | [Polymarket/py-sdk](https://github.com/Polymarket/py-sdk) | MIT | Official unified Python SDK for Polymarket. Successor to `py-clob-client`, which is **archived** with the warning "no longer functional... should not be used" ([source](https://github.com/Polymarket/py-clob-client)). | 2026-08-10; 93★ — active, official | **The** Polymarket client if we go there. Do not use py-clob-client despite its Google ranking. |
| 6 | [forecastingresearch/forecastbench](https://github.com/forecastingresearch/forecastbench) + [forecastbench-datasets](https://github.com/forecastingresearch/forecastbench-datasets) | MIT (code), CC-BY-SA-4.0 (data) | Dynamic, contamination-free LLM forecasting benchmark (Karger, Bastani, ... Tetlock; ICLR 2025) with human comparison groups. Datasets/leaderboards update nightly. | Code 2026-08-06, datasets 2026-08-11 — active | **Eval harness, not a bot.** Use to benchmark our forecaster before risking money, and mine the nightly question/resolution datasets for calibration testing. |
| 7 | [Metaculus/metac-bot-template](https://github.com/Metaculus/metac-bot-template) | **None detected** (GitHub API shows no license; README doesn't state one) | Quickstart for the AI Benchmark tournament: fork, add API keys, GitHub Actions runs the bot every 20 min. Two variants: `main.py` (on forecasting-tools) and `main_with_no_framework.py` (minimal deps). Supports OpenRouter (recommended), OpenAI, Anthropic, Perplexity, AskNews. Poetry, Python 3.11+. | 2026-06-04; 258 forks | Great to skim for wiring patterns (esp. the no-framework file), and the free way to enter the tournament to test our forecaster. Missing license = technically all-rights-reserved; don't copy code verbatim. |
| 8 | [dannyallover/llm_forecasting](https://github.com/dannyallover/llm_forecasting) | **None** (no LICENSE file per GitHub API + README) | Official code for Halawi et al. 2024, *Approaching Human-Level Forecasting with Language Models* ([arXiv:2402.18563](https://arxiv.org/abs/2402.18563), NeurIPS 2024): query generation → relevance filtering → summarization → reasoning (base or fine-tuned) → aggregation. Demo notebook + HF dataset. | Pushed 2026-04-19 but only 8 commits; 62★ | The academic blueprint for the retrieval→reasoning→aggregation pattern. No license → study, don't copy. An MIT-ish reimplementation exists at [getdatachimp/llm-superforecaster](https://github.com/getdatachimp/llm-superforecaster) (found via search; not fetched/vetted). |
| 9 | [ImMike/polymarket-arbitrage](https://github.com/ImMike/polymarket-arbitrage) | README claims MIT; GitHub API detects **no license** — verify before reuse | Python bot watching 10,000+ markets for intra- and cross-platform (Polymarket↔Kalshi) mispricings; text-similarity market matching; dry-run and live modes. Author warns markets "are highly efficient" and opportunities "rare and fleeting." | 2025-12-09, only 4 commits (~2-day project); 237★ | Useful as a worked example of cross-venue market matching + execution; too thin to build on. |
| 10 | [realfishsam/prediction-market-arbitrage-bot](https://github.com/realfishsam/prediction-market-arbitrage-bot) | MIT | Educational synthetic-arbitrage bot (buy YES on one venue, NO on the other) built on pmxt. Explicitly **ignores gas, trading fees, and slippage** per its own README. | 2026-01-16 (one-day project); 171★ | Read as a pmxt usage demo only. The fee-blindness is exactly the mistake to avoid on Kalshi (see §2 fees). |

Other repos that surfaced but weren't vetted in depth: [limyifan1/metaculus-bot](https://github.com/limyifan1/metaculus-bot), [No-Stream/metaculus-bot](https://github.com/No-Stream/metaculus-bot), [TexasCoding/kalshi-python-sdk](https://github.com/TexasCoding/kalshi-python-sdk) (unofficial, has websocket docs), [pbeets/kalshi-trade-rs](https://github.com/pbeets/kalshi-trade-rs) (Rust), and several low-effort Kalshi/Polymarket "arbitrage bot" repos (TopTrenDev, cutupdev, CarlosIbCu) that look like portfolio pieces — treat with suspicion.

---

## 2. The Kalshi API surface

Sources: [docs.kalshi.com](https://docs.kalshi.com/) (intro, [rate limits](https://docs.kalshi.com/getting_started/rate_limits), [API keys](https://docs.kalshi.com/getting_started/api_keys.md), [websockets](https://docs.kalshi.com/websockets.md), [SDK overview](https://docs.kalshi.com/sdks/overview), site [llms.txt](https://docs.kalshi.com/llms.txt) index), all fetched 2026-08-11.

**Protocols.** Two API suites: "Predictions" (event-contract markets) and "Perps" (perpetual futures, margin). Each offers REST, WebSocket, and FIX. OpenAPI + AsyncAPI specs are downloadable, and Kalshi recommends generating clients from them for production.

**Auth.** RSA key pair generated in account settings (private key shown once). Every request is signed: concatenate `timestamp_ms + HTTP method + path` (path *without* query params), sign with RSA-PSS/SHA-256, base64-encode; send `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP`, `KALSHI-ACCESS-SIGNATURE` headers. A **demo environment** exists at `external-api.demo.kalshi.co` with identical auth — we can paper-trade before funding anything.

**Market data.** REST endpoints for markets/events/series plus a WebSocket at `wss://external-api-ws.kalshi.com/trade-api/ws/v2` (demo: `wss://external-api-ws.demo.kalshi.co/...`), using the same API-key signing. Per third-party guides ([QuantVPS](https://www.quantvps.com/blog/kalshi-order-book-api-endpoints-explained), [botforkalshi](https://www.botforkalshi.com/blog/kalshi-api-tutorial) — *not verified against the AsyncAPI spec*): ~10 channels including public `ticker`, `trade`, `market_lifecycle_v2` and authenticated `orderbook_delta` (initial `orderbook_snapshot` then sequenced deltas), `fill`, `market_positions`, `user_orders`; the WebSocket is read-only — **all order management is REST-only**.

**Orders.** Confirmed from the official docs index (llms.txt): `create-order`, `batch-create-orders`, quick-start "find markets, place orders, check status, cancel" guide. Third-party guides say order placement is `POST /portfolio/orders` with ticker/action/side/count/type/price params (consistent with the official endpoint names, but the exact param list wasn't verified against the official reference page).

**Rate limits** (official, exact): independent read and write token buckets, refilled per second; most requests cost 10 tokens.

| Tier | Read tokens/s | Write tokens/s |
|------|--------------|----------------|
| Basic | 200 | 100 |
| Advanced | 300 | 300 |
| Expert | 600 | 600 |
| Premier | 1,000 | 1,000 |
| Paragon | 2,000 | 2,000 |
| Prime | 4,000 | 4,000 |
| Prestige | 10,000 | 8,000 |

At 10 tokens/request, Basic ≈ **20 reads + 10 writes per second** — plenty for a forecasting bot, tight for market-making. Basic→Advanced is a self-serve API call; Expert+ are earned by trailing-30-day volume share (e.g. Expert at 0.075% of exchange volume). Advanced+ buckets can burst to 2× after two idle seconds. 429s carry no penalty.

**Fees.** The official fee schedule PDF (`kalshi.com/docs/kalshi-fee-schedule.pdf`) returned HTTP 429 on three fetch attempts, so the numbers below come from search-result summaries of it and third-party fee guides ([pm.wiki](https://pm.wiki/learn/kalshi-fees-explained), [marketmath.io](https://marketmath.io/blog/kalshi-fees-guide-2026), [kalshibacktest.com](https://kalshibacktest.com/resources/what-are-kalshi-fees)) — **treat as approximately right, verify against the PDF before writing the P&L model**:

- Taker fee = `0.07 × fee_multiplier × contracts × P × (1−P)`, **ceiling-rounded to $0.0001** (Kalshi's own fee_rounding doc — not round-to-cent), max ≈ $0.0175/contract at P = 50¢, shrinking toward the tails. The per-series `fee_multiplier` from the API is only ever **0, 0.5 or 1 — no multiplier above 1 exists**, and two crypto series sit at 0; an earlier draft of this line claimed "higher for premium categories like crypto", which the API data contradicts. *(corrected 2026-08-11 per research/kalshi-api.md §3)*
- Maker fees are much lower — reported as 25% of taker (0.0175 coefficient, still unverified against the PDF), and charged on only 130 of 12,658 series (`quadratic_with_maker_fees`; none in Elections/Politics/Weather, but CPI and Fed series do charge makers). Zero on everything else. *(corrected 2026-08-11 per research/kalshi-api.md §3.1)*
- Volume tiers discount taker fees (reported range ~12.0 bps down to ~2.6 bps for $3B+ volume).

The strategic consequence is solid even if the digits shift: **a taker strategy near 50¢ pays ~3.5% round-trip of a 50¢ position's value in fees, so the forecaster's edge threshold must be fee-aware, and resting maker orders are dramatically cheaper.**

---

## 3. What the best bots do

The pattern that wins Metaculus's AI Benchmark tournaments is remarkably consistent, and it's the same pipeline academia converged on: **retrieval → structured reasoning → ensemble aggregation → (light) calibration**. Halawi et al. 2024 ([arXiv:2402.18563](https://arxiv.org/abs/2402.18563), code in [llm_forecasting](https://github.com/dannyallover/llm_forecasting)) formalized it: LM-generated search queries → news retrieval → LM relevance-rating and filtering → summarization → multiple reasoning passes (optionally fine-tuned) → aggregate into a final probability. [Panshul42's Q2-winning bot](https://github.com/Panshul42/Forecasting_Bot_Q2) is that same skeleton scaled up: a 6–7-step agentic research phase that *separately* builds an outside-view report (base rates, reference classes) and an inside-view report (current specifics) from Serper/AskNews/Perplexity/BrightData retrieval, then five semi-independent forecasts from a mixed-model ensemble (Claude Sonnet ×2, o4-mini ×2, o3 ×1) aggregated to a final number — with prompts iterated by manually reviewing reasoning traces on ~35 held-out questions. The [Q2 results writeup on LessWrong](https://www.lesswrong.com/posts/Surnjh8A4WjgtQTkZ/q2-ai-benchmark-results-pros-maintain-clear-lead) draws the same lessons from the whole 96-bot field: model quality dominated prompt cleverness (o3 was the standout), aggregating multiple forecasts consistently improved scores (Metaculus's own survey of bot makers lists "aggregate multiple forecasts" as the top best practice), and the best in-house Metaculus bot was simply o3 + AskNews.

Two sobering findings from the same writeup temper the "AI forecaster prints money" thesis. First, **pro human forecasters still beat every bot decisively** in Q2 2025 (head-to-head −20.03, p ≈ 0.00001; all 10 pros individually outranked every bot). Second, the winners were tournament bots optimized for log-score on free questions — none of them trade, size positions, or pay fees. For our purposes: the forecasting pipeline is a solved *architecture* (fork the pattern, not necessarily the code), but market prices on liquid Kalshi contracts are themselves aggregated human forecasts, so the bar for positive expected value after the 0.07·P·(1−P) fee is higher than the bar for a good tournament score. The realistic edge is on thin, numerous, research-heavy markets where nobody has done the reading — exactly where a cheap parallel retrieval+reasoning pipeline scales and humans don't.

---

## 4. Gaps — what Chris & Griffin would actually have to build

Nothing off the shelf connects a forecaster to an exchange. Specifically:

1. **The forecast→trade bridge (the core product).** No open-source system takes an LLM probability estimate plus a Kalshi orderbook and decides *whether/how much/at what price* to trade. Everything found is either a Metaculus tournament bot (forecasts, never trades) or a mechanical arbitrage bot (trades, never forecasts). Fee-aware edge thresholds, Kelly-style position sizing, bankroll caps, and maker-vs-taker execution choice all have to be written from scratch.
2. **Market→question compilation.** Tournament bots receive well-posed questions with resolution criteria. A Kalshi bot must *generate* the forecasting question from a market's ticker/rules text, get the resolution nuances right (Halawi et al. and the Metaculus rules both treat resolution-criteria precision as critical), and decide which of thousands of open markets are worth researching at all. Nothing does this.
3. **Calibration + P&L backtesting infrastructure.** ForecastBench benchmarks accuracy against questions, not profit against historical prices. There is no open historical-Kalshi-orderbook dataset or backtester in anything surveyed (kalshibacktest.com exists as a commercial site; unverified). We'd need to log our own price snapshots from day one, plus a Brier/calibration tracker per market category.
4. **Cross-venue market equivalence.** The Polymarket↔Kalshi arb bots match markets by text similarity, which is exactly where "same-looking markets with different resolution rules" burns you. A reliable equivalence layer (rules-text diffing, not title matching) doesn't exist. Only matters if we do the two-venue version.
5. **Licensing forces a rewrite of the best reference.** The single best end-to-end forecasting bot (Panshul42) is AGPL-3.0, and the academic pipeline (llm_forecasting) and metac-bot-template have *no* license. The MIT-licensed pieces (forecasting-tools, pmxt, official SDKs, ForecastBench) are frameworks and plumbing — the winning *bot logic* must be re-implemented, using those repos as documentation.

**Suggested assembly:** forecasting-tools (MIT) for the LLM/research/cost scaffolding + Panshul's architecture re-implemented on top + official `kalshi_python_async` (or pmxt self-hosted if Polymarket is in scope) for execution + ForecastBench for pre-money evals + Kalshi demo environment for paper trading — and hand-build items 1–3 above, which is the actual project.

---

### Source log (all fetched 2026-08-11)

Primary: github.com READMEs for every repo in §1; GitHub API metadata for licenses/last-push/stars; docs.kalshi.com (intro, rate_limits, api_keys.md, websockets.md, sdks/overview, llms.txt); [LessWrong Q2 AI Benchmark results](https://www.lesswrong.com/posts/Surnjh8A4WjgtQTkZ/q2-ai-benchmark-results-pros-maintain-clear-lead); [arXiv:2402.18563](https://arxiv.org/abs/2402.18563).
Secondary (flagged inline where used): pm.wiki, marketmath.io, kalshibacktest.com, QuantVPS and botforkalshi blog posts, WebSearch snippets. **Not obtained despite retries:** the official Kalshi fee-schedule PDF (HTTP 429 ×3) — verify §2 fee digits against it before modeling.

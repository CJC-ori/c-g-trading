# Systematic (no-LLM) edges in prediction markets

**Researcher output, 2026-08-11.** Survey of documented inefficiencies exploitable by
pure price/structure rules — no live LLM at decision time, so these backtest with
**zero contamination** on any historical period (constraint #2 in ORCHESTRATION.md).
Every load-bearing claim cites its source; unverified items are flagged. Audience:
the engineering agents building the data layer (P2), backtest harness (P3), and
strategy prototypes (P4) in the next few hours.

**Fee baseline used throughout** (must be in every backtest):

- Kalshi taker fee = `round_up(0.07 · P · (1−P))` per contract (P in dollars) —
  confirmed as the formula in the GWU academic study covering 2021–Apr 2025
  ([GWU 2026-001 PDF](https://www2.gwu.edu/~forcpgm/2026-001.pdf), §"How Kalshi works")
  and by the official fee schedule
  ([kalshi.com/docs/kalshi-fee-schedule.pdf](https://kalshi.com/docs/kalshi-fee-schedule.pdf),
  July 2026 update — third-party summaries say the 7.7.26 revision keeps the 0.07
  coefficient and adds a per-market multiplier defaulting to 1:
  [laikalabs fee comparison](https://laikalabs.ai/prediction-markets/kalshi-vs-polymarket-fees-comparison)).
  **⚠ Verify the official PDF directly before hard-coding — our earlier repo fetch 429'd.**
- Rounding matters: Kalshi rounds the *total* fee up to the next cent; GWU imputed
  an effective ~1.77% fee on a 50¢ contract for a 100-lot (vs 1.75% nominal).
- Maker fee: 0% on most markets; ~25% of the taker fee (or a flat bp charge) on
  select high-volume events, introduced after April 2025
  ([whirligigbear maker/taker math](https://whirligigbear.substack.com/p/makertaker-math-on-kalshi),
  [pm.wiki fees](https://pm.wiki/learn/kalshi-fees-explained) — secondary sources,
  verify per-series via the API's fee fields).
- Polymarket: no trading fee on most markets but gas + spread; treat spread as the fee.

---

## 1. Favorite–longshot bias (FLB) on Kalshi — THE anchor result

**The Feb 2026 study the README cites:** Bürgi, Deng & Whelan, *"Makers or Takers:
The Economics of the Kalshi Prediction Market"*, GWU Research Program on Forecasting
working paper **2026-001**, Feb 2026 — [PDF](https://www2.gwu.edu/~forcpgm/2026-001.pdf)
(author mirror: [karlwhelan.com/Papers/Kalshi.pdf](https://www.karlwhelan.com/Papers/Kalshi.pdf);
popular summary: [CEPR VoxEU column](https://cepr.org/voxeu/columns/economics-kalshi-prediction-market)).
I pulled the full PDF and extracted the text; numbers below are from the paper itself.

**Data:** transaction-level records on 46,282 Kalshi contract types from inception
(2021) through **April 2025**; analysis sample = 313,972 Yes+No contract-day
observations (156,986 Yes contracts), restricted to **≤10 days before market close**;
Kalshi's data flags which side of each trade was Maker vs Taker.

**Findings (post-fee unless noted):**

- Clear FLB: contracts ≤10¢ lose **>60% of stake** on average; losses shrink as price
  rises; **small positive returns above 50¢; statistically significant positive
  post-fee returns above 70¢**.
- Magnitudes: a 5¢ contract winning ~3% of the time = **−40% pre-fee**; a 95¢
  contract winning ~98% = +3.1% pre-fee. Average pre-fee return across all
  contracts: **−20%** (asymmetry, since money-weighted average is 0 by construction).
- Maker/taker split: average return **Makers −9.64% vs Takers −31.46%** (difference
  significant at extreme levels). **Makers buying contracts ≥50¢ earn +2.6% per
  contract-episode after fees, SD 33%** (not annualized; a few-day holding period,
  so annualized compounding potential is large).
- Robustness: Mincer–Zarnowitz unbiasedness (profit = α + ψ·price) is **rejected in
  every subsample** — every year 2021–2025, every category (financials, crypto,
  climate, politics, entertainment, sports), every days-to-close bucket from 0 to
  10, and every transaction-size quintile (**largest-size quintile shows the largest
  bias** — this is not just penny-lottery noise). Bias is somewhat weaker in 2025
  data and for politics/entertainment.
- Why it persists (authors' own argument, and our niche): **markets are too small
  for professional capital** — top-decile markets average only **$526,245 lifetime
  volume**; order-book depth at any instant is far less; plus 33% per-episode SD
  deters (Samuelson risk-aversion argument).

**Corroboration:** independent practitioner analysis of **72M Kalshi trades** finds
makers earn ~+2.5% excess per trade, takers lose the mirror image, 5¢ contracts win
~4% of the time, and reports the same pattern in **124M Polymarket trades**
([hackingthemarkets, "Why Kalshi Takers Lose Money"](https://hackingthemarkets.com/why-kalshi-takers-lose-money-72m-trades-analyzed/)
— unverified independently, partially paywalled). On Polymarket, academic evidence
is more mixed: low-probability tokens show negative realized returns but the bias
is "not pronounced at the market level" in at least one analysis
([Polymarket winners/losers analysis](https://www.studocu.com/row/document/ankara-universitesi/matematik-i/who-wins-and-who-loses-in-prediction-markets-insights-from-polymarket-analysis/161619696) —
low-quality mirror, flag as weak; classic background:
[Snowberg–Wolfers NBER w15923](https://www.nber.org/system/files/working_papers/w15923/w15923.pdf),
[Ottaviani–Sørensen](https://igier.unibocconi.eu/sites/default/files/media/attach/131205.pdf)).

- **Evidence quality: HIGH** (peer-quality working paper, transaction-level, full
  history, fee-aware, replicated by an independent 72M-trade analysis).
- **Effect size after fees:** +1–3% per episode buying favorites ≥70¢ as taker;
  **+2.6%/episode as maker ≥50¢**; avoiding/shorting <20¢ longshots is worth
  −40–60% of avoided loss. SD ≈ 33%/episode → needs many independent events.
- **Data to backtest:** Kalshi settled markets + trades (`/markets/trades` includes
  `taker_side`, so maker/taker replication is possible) + candlesticks. All public,
  already probed in P0.
- **Testable rule (R1):** *For every market with ≤10 days to close, buy the
  high-priced side (YES or NO priced 70–97¢) and hold to resolution. Variant A
  (taker): cross the spread, charge full fee. Variant B (maker): rest a bid 1¢
  below last trade, fill only if the historical trade tape shows trades at or
  below the bid after placement. Exclusions: none initially, then ablate by
  category. Success = mean post-fee return > 0 with event-clustered SE, target
  ≈ +1–2.6%/episode.*

---

## 2. Maker-side passive capture / spread harvesting (microstructure edge A)

The GWU paper's core structural finding is that Kalshi's quote-driven design
endogenously transfers money from takers (extreme beliefs, impatience) to makers;
the maker–taker return gap (−9.6% vs −31.5%) *is* the spread
([GWU 2026-001](https://www2.gwu.edu/~forcpgm/2026-001.pdf), §5–6). One caution from
the same paper: maker returns **deteriorate at closing prices** (final-day makers
show losses similar to takers — "over-optimism from Makers... closer to closing"),
so passive quoting should stand down in the last hours unless doing R4 deliberately.

- **Evidence quality: HIGH** (same sources as §1).
- **Effect size after fees:** the +2.6% maker figure ≥50¢ is the cleanest number;
  most Kalshi markets still charge makers 0%.
- **Data to backtest:** trade tape with `taker_side`; simulate "our resting order
  was at price p from time t" and fill it against subsequent taker flow (this is
  exactly the honest-fill machinery constraint #4 requires anyway; FutureSearch got
  only ~43% fills — [trader case study](https://futuresearch.ai/blog/kalshi-trader-case-study/)).
- **Testable rule (R2):** *Quote a resting bid on the ≥50¢ side at (best bid) or
  (last trade − 1¢) with size ≤ 20% of trailing 1-hour taker volume; cancel/reprice
  each hour; no quoting in final 6 hours before close. Fill only when tape shows a
  taker sell at ≤ our price. Benchmark: GWU's +2.6%/episode.*

---

## 3. Election-night overcorrection / mean-reversion

Three independent lines of evidence:

1. **PredictIt 2020 ("red mirage"):** Biden YES 61¢ pre-election → 42¢ at 11:59pm
   election night on a *known-in-advance* vote-count-order illusion → 87¢ next day;
   post-election, decided states (GA at 88¢, MI at 90¢) traded far below 99¢ for
   ~3 weeks; internally inconsistent EC-margin brackets (Trump +280 at 8¢ vs
   Trump +10–29 at 3¢). Persistence blamed on PredictIt's $850/contract cap
   ([Social Science Encyclopedia writeup](https://www.socialscience.international/aiden-singh-predictit-inefficiencies)).
   Kalshi/Polymarket have no such cap — expect *smaller but nonzero* versions.
2. **2024 general election:** Polymarket–Kalshi prices diverged 3–8¢ for hours as
   state results arrived ([arXiv:2604.24147](https://arxiv.org/pdf/2604.24147));
   58% of Polymarket national presidential markets showed **negative daily serial
   correlation** — spikes reversed next day
   ([DL News on the cross-platform reliability study](https://www.dlnews.com/articles/markets/polymarket-kalshi-prediction-markets-not-so-reliable-says-study/)).
3. **Our own verified seed (in-repo, primary-source):** Michigan Dem Senate primary,
   Aug 4 2026 — YES 98–98.5¢ → 99.9¢ → **74¢ trough lasting ~3 minutes** at 11:17pm
   ET → 98¢ within 2h → resolved YES; ~$230k notional traded ≤80¢
   (minute-level Kalshi API verification in
   [`docs/viability.md`](/home/user/c-g-trading/docs/viability.md) §2).

Generic (non-election) mean-reversion also tests positive on Polymarket:
QuantPedia's study of 10-minute bars on three near-certain "No" contracts found the
best variant earned **+22.09% CAR, Sharpe 1.23** (short-lookback variants Sharpe up
to 2.97), **but performance degrades significantly under market-order execution** —
the alpha survives only with passive limit fills
([QuantPedia mean-reversion study](https://quantpedia.com/exploiting-mean-reversion-in-decentralized-prediction-markets-evidence-from-polymarket-binary-contracts/) —
n=3 contracts, serious selection-bias risk; treat as suggestive only).

- **Evidence quality: MEDIUM-HIGH** for elections (multiple episodes, one verified
  tick-level in-repo; but n of usable events per year is small); MEDIUM for generic
  mean-reversion (small samples, execution-sensitive).
- **Effect size after fees:** Michigan: buy-the-dip YES at 77¢ → +23.7% in 5h net
  of taker fees; the ex-ante convexity version (cheap NO + sell the panic) paid
  ~11–14x net when the scare came, −100% when it doesn't (sizing must assume
  frequent total loss). Generic reversion: low single digits per round-trip, maker
  execution required.
- **Data to backtest:** Kalshi 1-minute candlesticks for all election/primary
  series (public); event calendar of scheduled result nights; Polymarket
  prices-history for cross-checks. The **Nov 2026 midterms are the out-of-sample
  test** — dozens of races in one night.
- **Testable rule (R4):** *For scheduled-resolution binary markets whose YES traded
  ≥95¢ average over the 24h before the event window: at window start, place a GTC
  resting YES bid ladder at 85/80/75¢ (size split by measured book depth); if
  filled, exit via resting ask at (pre-event price − 2¢) or hold to resolution;
  stop-loss = none (binary, sized for total loss). Backtest on 1-min candles with
  fills only when candle low < bid. Companion rule (R4b, convexity): buy NO at
  ≤3¢ pre-event, rest an NO ask ladder at 15/20/25¢ during the window, count the
  no-scare bleed-to-zero cases honestly.*

---

## 4. Time-decay / "theta harvesting" near expiry — mostly a re-statement, one real wrinkle

Careful: prediction markets have **no volatility risk premium to decay** — a binary
at fair probability has zero expected drift, so "theta" is not a free income stream
([Quantcha, Greeks for prediction markets](https://quantcha.com/news/options-greeks-prediction-markets/)).
What IS documented:

- **Page & Clemen (2013), InTrade:** longshot overpricing appears mainly for
  contracts **>10 days from close** (partly explained by discounting of capital
  tied up); **no significant miscalibration close to expiry** (as summarized in
  [GWU 2026-001](https://www2.gwu.edu/~forcpgm/2026-001.pdf) §2.1). GWU then shows
  Kalshi *does* stay biased at 0–10 days — every days-to-close bucket rejects
  unbiasedness (Table 5, ψ significant at every horizon incl. day 0).
- **In-play sports "Yogi Berra bias"** (Page 2012, InTrade): losing teams' prices
  in the final 15 minutes are too high relative to actual comeback rates (same
  GWU §2.1 summary).
- **New 2026 sports evidence:** 23M Kalshi moneyline trades — calibration is good
  mid-life but **"departs sharply as expiry approaches"; in the final 10 minutes
  the calibration curve becomes step-like** (losing-side buyers acting like
  insurance seekers); and **cross-game parlays are systematically overpriced vs
  the product of their leg prices, worsening with leg count**
  ([arXiv:2607.14430](https://arxiv.org/html/2607.14430)).

So the honest framing: the near-expiry edge is *the FLB concentrated in the
endgame* — buy the near-certain side in the final minutes/hours when it trades
below its step-function fair value; fade late longshots.

- **Evidence quality: MEDIUM-HIGH** (academic, large n, but the specific final-10-min
  effect sizes aren't in the abstract we could access; GWU's day-0 row confirms the
  bias exists at closing on all-category data).
- **Effect size after fees:** GWU day-0: same order as §1 (favorites ≥70¢ positive
  post-fee). Fee formula helps here: at 95¢ the taker fee is only
  0.07·0.95·0.05 ≈ 0.33¢, so a 95→100 hold nets ~+4.9% gross / ~+4.5% net if the
  true win rate is ~99%+. The whole question is the true win rate — backtest it,
  don't assume it.
- **Data to backtest:** Kalshi 1-min candles + trades for the final 24h of every
  settled market; sports series included (they're the volume).
- **Testable rule (R5):** *In the final 6/12/24h before close, buy (as maker) the
  side priced 90–98¢; hold to resolution. Report win-rate vs price curve by
  category. Kill the rule for any category where realized win rate < breakeven
  (price + fee). Companion (R5b): in in-play sports, sell/avoid the trailing side
  in the final 15 min at any price >2× its model-free historical comeback rate for
  that score/time state — requires only historical Kalshi tapes to calibrate.*

---

## 5. Dumb-money flow patterns (sports/crypto retail)

Evidence that retail flow is the counterparty:

- Sports is Kalshi's **#1 category by trade count**, and taker flow loses ~31%/trade
  on average ([hackingthemarkets 72M-trade analysis](https://hackingthemarkets.com/why-kalshi-takers-lose-money-72m-trades-analyzed/);
  [GWU 2026-001](https://www2.gwu.edu/~forcpgm/2026-001.pdf)).
- Parlay overpricing growing with leg count ([arXiv:2607.14430](https://arxiv.org/html/2607.14430))
  is a pure retail-lottery-demand signature (we likely can't short parlays directly;
  the tradable implication is that single-leg longshot demand around big events is
  similarly distorted).
- GWU category regressions: bias present in **crypto, financials, climate** as well;
  crypto price-threshold markets ("BTC above X by date") are quasi-mechanical
  (lognormal-ish underlying) so mispricing vs a simple vol model is directly testable.
- The GWU robustness cut **excluding sports entirely still shows the bias** — so
  this is additive, not the whole story.

- **Evidence quality: MEDIUM** (strong that retail loses; weaker on *which specific
  flow patterns* are ex-ante identifiable beyond price level itself).
- **Effect size after fees:** subsumed in §1's numbers; sports/crypto are where
  volume (and therefore fillable size) lives.
- **Data to backtest:** same Kalshi tape; for crypto-threshold markets add free
  spot-price history (e.g. exchange candles) to fit a no-LLM lognormal/EWMA-vol
  fair value — still zero contamination (pure math on prices ≤ t).
- **Testable rule (R6):** *Crypto-threshold markets: compute fair P(hit) from a
  driftless lognormal with EWMA vol estimated from spot data ≤ t; trade Kalshi side
  whose price deviates from fair by > (fee + 3¢), maker-only. Sports: apply R1/R5
  restricted to sports series and report separately.*

---

## 6. Cross-venue divergence persistence (Kalshi vs Polymarket)

- First systematic cross-platform study (Ng et al. 2026, via
  [arXiv:2603.03152](https://arxiv.org/html/2603.03152v2) and
  [arXiv:2604.24147](https://arxiv.org/pdf/2604.24147)): Polymarket, Kalshi,
  PredictIt, Robinhood showed **systematically different probabilities for extended
  periods** in 2024; **Polymarket leads Kalshi in price discovery** (Polymarket
  prices predict future Kalshi prices, not vice versa); weekly Polymarket–Kalshi
  spreads **exceeded the ~2% arbitrage break-even**; election-night divergences of
  3–8¢ persisted for hours.
- **Critical caution — semantic non-fungibility:** much "divergence" is *different
  contracts*: e.g. Polymarket resolved 2024 on media network calls, Kalshi on
  inauguration ([arXiv:2601.01706](https://arxiv.org/pdf/2601.01706)). True arb
  requires a resolution-criteria audit per pair. The in-repo prior-art review also
  notes the popular open-source arb bot **ignores fees/slippage entirely**
  ([realfishsam bot](https://github.com/realfishsam/prediction-market-arbitrage-bot),
  flagged in [`docs/prior-art.md`](/home/user/c-g-trading/docs/prior-art.md)).
- Fast pure arb is already occupied by low-latency bots
  ([Polyflux on Polymarket arb](https://polyflux.io/blog/polymarket-arbitrage/));
  our realistic use is **divergence as a *signal*** (lead–lag), not two-legged arb.

- **Evidence quality: MEDIUM-HIGH** that gaps exist and persist; LOW-MEDIUM that a
  small player can capture them as riskless arb (latency + capital on two venues +
  resolution mismatch).
- **Effect size after fees:** documented gaps 2–8¢ on liquid political pairs at
  high-news moments; after Kalshi taker fee (~1.75¢ max) and Polymarket spread,
  realistic captured edge ~1–4¢ per contract on the slow leg, episodic.
- **Data to backtest:** Kalshi candles + Polymarket CLOB `prices-history` (probe
  pending per ORCHESTRATION.md); a rule-identical market pairing table built by
  matching event definitions (manual/heuristic — this pairing table is itself a
  reusable asset). The [Polymarket-v1 research database](https://arxiv.org/html/2606.04217v1)
  may shortcut historical Polymarket data.
- **Testable rule (R7):** *For audited rule-identical pairs: when |Kalshi mid −
  Polymarket mid| > 4¢ sustained ≥ 10 minutes, trade the Kalshi side toward the
  Polymarket price (maker), exit when gap < 1¢ or at resolution. Polymarket is the
  signal leg only (no capital there in v1). Score vs a no-signal baseline on the
  same markets.*

---

## 7. NO-side bias on multi-outcome events (Σ YES > 100)

Mechanism: FLB applied bracket-by-bracket — every longshot bucket in a mutually
exclusive event carries a few cents of lottery premium, so the YES prices sum to
>100. Buying NO on **all n brackets** costs Σ(100−YES_i) and pays exactly (n−1)·100
at resolution → riskless when Σ YES asks > 100 + total fees.

- On Polymarket this is formalized: "negRisk" events are exactly these groups, the
  protocol has a NO-set→collateral convert function, and bots actively harvest it
  ([Start Polymarket negRisk explainer](https://startpolymarket.com/learn/converting-negative-risk/),
  [Polyflux](https://polyflux.io/blog/polymarket-arbitrage/)) — so on Polymarket the
  residual is thin. **On Kalshi, no equivalent convert exists and no academic count
  of Σ>100 frequency is published — this is a genuine gap worth measuring ourselves**
  (unverified opportunity; the GWU paper rejects price unbiasedness for mutually
  exclusive market groups but does not report overround statistics).
- Historical precedent for gross versions: PredictIt Nov 2020 EC-margin brackets
  were internally inconsistent for weeks
  ([Social Science Encyclopedia](https://www.socialscience.international/aiden-singh-predictit-inefficiencies));
  our own verified Michigan margin ladder (≥15 at 62¢ while 0–3 at 2–5¢) is the
  soft (non-arb, forecasting) version ([`docs/viability.md`](/home/user/c-g-trading/docs/viability.md) §2).
- Fee drag on Kalshi is the binding constraint: n legs each pay
  0.07·P(1−P), worst near 50¢; a 6-bracket event with YES sum 104 may not clear
  fees as taker. Maker entry or partial sets (leave out the most overpriced-to-fee
  leg) may be needed. Also each NO leg near 90–95¢ has tiny fee (~0.3–0.6¢), so
  ladders of longshot brackets are the friendly case.

- **Evidence quality: MEDIUM** (mechanically certain when observed; frequency/depth
  on Kalshi unmeasured — that measurement is cheap and high-value).
- **Effect size after fees:** unknown on Kalshi; on Polymarket residual opportunities
  are sub-1% and bot-competed. Expect small but *riskless* when present; also
  valuable as a **screen** for which events contain an overpriced bracket to fade
  singly (higher capacity than the full-set arb).
- **Data to backtest:** Kalshi events endpoint (`mutually_exclusive` flag) +
  orderbook/candles per bracket; compute Σ best-ask YES minus 100 minus fees over
  time for all settled multi-outcome events.
- **Testable rule (R3):** *Scan all mutually exclusive events; when
  Σ ask_YES − 100 > Σ fees + 1¢ buffer, simulate buying the full NO set at ask
  (depth-capped, all-or-nothing per set). Separately: when a single bracket's YES
  ≤ 10¢ contributes ≥ half the overround, fade just that bracket per R1. Report
  frequency, average edge, and fillable size — this doubles as original research
  nobody has published for Kalshi.*

---

## 8. Market microstructure B: thin-market spreads & panic-wick capture

- Wide spreads on thin markets are documented structurally: GWU's model derives the
  bid–ask spread endogenously from maker/taker belief dispersion and shows the
  spread is where maker profit lives ([GWU 2026-001](https://www2.gwu.edu/~forcpgm/2026-001.pdf) §5);
  instantaneous book depth even on a headline CPI market was a few $100–$3k per level
  (paper's Figure 2; matches the $1–3k depth note in this repo's README).
- Panic wicks are real, deep, and *fast*: our Michigan verification shows the ≤80¢
  trough lasted **~3 minutes** with ~$230k notional trading through it — capturable
  only by **pre-placed resting limit orders**, which also pay maker (zero/low) fees
  ([`docs/viability.md`](/home/user/c-g-trading/docs/viability.md) §2). The QuantPedia
  result that reversion alpha survives only under passive limit execution is the
  same lesson ([QuantPedia](https://quantpedia.com/exploiting-mean-reversion-in-decentralized-prediction-markets-evidence-from-polymarket-binary-contracts/)).
- Caveat (adverse selection): a resting bid 20¢ below market fills precisely when
  something happened; some wicks are the market being *right* early. The GWU
  finding that makers lose at closing prices is this risk showing up in data.

- **Evidence quality: MEDIUM** (structure is well documented; the specific
  "wick-capture" P&L distribution is not published anywhere — we must measure it
  from Kalshi minute candles, which is exactly what R4's backtest does).
- **Effect size after fees:** per-episode +10–25% when a wick fills and reverts
  (Michigan arithmetic); frequency and adverse-selection rate unknown → the
  backtest's main job.
- **Data to backtest:** 1-min candles for all settled markets in event-window
  hours; trades tape to bound fill realism (fills only on traded-through prices,
  ≤ some % of traded volume at that price).
- **Testable rule:** R4 above, generalized beyond elections: *any market with
  ≥95¢ 24h-average and a scheduled information event gets a resting ladder.*

---

## Ranking: top 5 by (evidence × tractability)

| # | Edge | Rule | Evidence | Tractability (data in hand, no LLM, fills honest) | Notes |
|---|------|------|----------|--------------------------------------------------|-------|
| 1 | **FLB favorite-buying / longshot-fading, ≤10 days out** | R1 | HIGH (GWU 313k contracts; 72M-trade replication) | HIGH — settled markets + candles suffice for taker variant | The default benchmark strategy; everything else must beat it |
| 2 | **Maker-side passive capture on ≥50¢ contracts** | R2 | HIGH (+2.6%/episode, same sources) | MEDIUM-HIGH — needs tape-replay fill simulation (required anyway) | Stand down near close |
| 3 | **Near-expiry favorite endgame (FLB day-0 + sports final-minutes)** | R5 | MEDIUM-HIGH (GWU day-0 rows; arXiv 23M sports trades) | HIGH — same data, tight windows, small fees at extreme prices | Sports gives the n |
| 4 | **Election/event-night panic-wick resting ladders** | R4 | MEDIUM-HIGH (PredictIt 2020, 2024 studies, Michigan verified in-repo) | MEDIUM — episodic (small n/year), but Nov 2026 midterms are a natural out-of-sample | Highest per-episode payoff; convexity variant R4b included |
| 5 | **Multi-outcome Σ YES > 100 NO-set scan (Kalshi)** | R3 | MEDIUM (mechanism certain; Kalshi frequency unmeasured — original research) | HIGH — pure API scan, riskless when it triggers | Also a screen feeding R1 |

Below the line: cross-venue divergence (R7 — keep as *signal*, not arb; needs the
pairing audit), crypto-threshold vol-model fading (R6 — clean but competes with
sophisticated flow), generic 10-min mean reversion (execution-fragile, weak sample).

## Cross-cutting backtest requirements (hand to P2/P3)

1. Pull **all settled Kalshi markets** with category, `mutually_exclusive` flag,
   close/settle times, result (public API, cursor pagination — verified in P0).
2. Pull **trades** (`taker_side` per trade) for maker/taker replication, and
   **1-min candlesticks** for event-window strategies; hourly for the rest.
   Bounded: prioritize (a) last 10 days of each market's life, (b) full minute data
   only for markets whose series match election/scheduled-event patterns.
3. Fee engine: `ceil_to_cent(0.07·P·(1−P)·contracts)` taker; per-series maker fee
   field; both switchable by date (maker fees start Apr 2025).
4. Fill model: taker fills at ask with depth cap; maker fills only against
   subsequent tape prints at ≤ bid (≤ x% of printed volume). Report fill rates —
   expect ~43% (FutureSearch's number) not 100%.
5. Cluster standard errors by event (GWU does; brackets of one event are correlated).
6. Report every strategy's return distribution, not just the mean — the +2.6%/33%SD
   shape is the whole risk story.

**Main unverified items to close out:** official current fee-schedule PDF digits
(429'd); Kalshi maker-fee coverage by series; Σ YES > 100 frequency on Kalshi
(nobody has published it — we measure it); exact effect sizes in arXiv:2607.14430's
final-10-minute sports miscalibration (abstract-level access only).

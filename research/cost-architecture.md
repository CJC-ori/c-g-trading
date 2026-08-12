# Inference-cost economics and multi-model architecture

*Compiled 2026-08-11 by a Claude research agent. Every load-bearing number cites the URL it
was fetched from on that date, or shows the arithmetic that produced it. Claims I could not
verify from a primary source are flagged **[UNVERIFIED]**. Numbers I computed are flagged
**[derived]** with the inputs shown so you can re-run them.*

**TL;DR for the engineering agents.**

1. **The break-even budget is ~$0.60/researched-question, and that is not a coincidence.**
   Working from Kalshi's fee formula, the $1–3k book depth this repo measured, a 43% fill
   rate and a 15% trade-conversion rate, the "inference < 20% of expected profit" rule lands
   at **$0.63 per researched question** for a 5-point edge at 50¢ ([§4](#4-break-even-the-per-question-inference-budget)).
   FutureSearch's published $0.60 is right at the line. Anything more expensive than that
   is not a pipeline, it's a hobby.
2. **A tiered Haiku/Sonnet/Opus pipeline costs ~$0.17/question single-judge and ~$0.35
   3-judge** at today's real prices, i.e. 2–4× under the break-even ceiling
   ([§3](#3-the-tiered-architecture)). The headroom is what buys you the ensemble.
3. **Thinking tokens are output tokens.** On Opus 5 output is 5× input, so the `effort`
   dial is the single largest cost lever in the whole system — larger than model choice,
   larger than retrieval depth ([§3.4](#34-tier-3--the-judge)).
4. **Cost does not bind; bankroll binds.** At a $50–100k bankroll you can only open ~2–5
   new positions/day, which back-solves to ~35 researched questions/day, ~$7/day of
   inference ([§5](#5-funnel-sizing-cost-does-not-bind-bankroll-does)). Sizing the funnel to
   the bankroll, not to the market universe, is the whole cost-control strategy.
5. **Three caching tricks are worth >50% each**: event-level dossier sharing (52% on a
   12-bracket event), a cached methodology prefix (86% on the system prompt), and
   delta-check-gated forecast reuse (85% at a 15% refresh rate) ([§6](#6-cachingreuse-tricks)).
6. The harness already has the hook: `Strategy.last_inference_cost_cents`
   (`bot/backtest/types.py:248`) is read after every `on_decision_point` and charged to P&L
   as its own line item. **Populate it truthfully or the backtest is a lie**
   ([§7](#7-wiring-cost-into-the-backtest)).

---

## 1. Current API prices (fetched 2026-08-11)

### 1.1 Anthropic

All from <https://platform.claude.com/docs/en/about-claude/pricing> unless noted. USD per
million tokens (MTok).

| Model | Input | 5m cache write | 1h cache write | Cache read | Output |
|---|---|---|---|---|---|
| Claude Fable 5 | $10 | $12.50 | $20 | $1.00 | $50 |
| **Claude Opus 5** | **$5** | $6.25 | $10 | **$0.50** | **$25** |
| Claude Opus 4.8 / 4.7 / 4.6 / 4.5 | $5 | $6.25 | $10 | $0.50 | $25 |
| **Claude Sonnet 5** | **$2** | $2.50 | $4 | **$0.20** | **$10** |
| Claude Sonnet 4.6 / 4.5 | $3 | $3.75 | $6 | $0.30 | $15 |
| **Claude Haiku 4.5** | **$1** | $1.25 | $2 | **$0.10** | **$5** |

Context windows and cutoffs (<https://platform.claude.com/docs/en/about-claude/models/overview>):
Fable 5 / Opus 5 / Sonnet 5 are 1M context, 128k max output; Haiku 4.5 is 200k / 64k.
**Opus 5's reliable knowledge cutoff is May 2026, Fable 5 and Sonnet 5 are Jan 2026, Haiku
4.5 is Feb 2025.** This matters enormously for ORCHESTRATION constraint #2 — see
[§8](#8-contamination-the-cost-of-doing-it-honestly).

Key modifiers, same page:

- **Prompt caching multipliers:** 5-minute write = 1.25× input, 1-hour write = 2× input,
  cache read = **0.1× input**. Break-even is one read for the 5m TTL, two reads for 1h.
- **Batch API: flat 50% off input *and* output**, ≤24h turnaround. Stacks with caching.
- **Long context is free.** "A 900k-token request is billed at the same per-token rate as a
  9k-token request." There is no long-context premium on 4.6+ models. (This removes an
  optimization but also a trap: stuffing raw pages into context is priced linearly, so
  compression only pays via the model-tier gradient — see §3.3.)
- **Web search server tool: $10 per 1,000 searches** ($0.01/search) plus token cost of the
  results. **Web fetch: no surcharge**, token cost only. **Code execution: free when used
  alongside `web_search_20260209`/`web_fetch_20260209`**; otherwise 1,550 free container-hours
  per org per month, then $0.05/container-hour.
- **Tool-use system prompt overhead:** Opus 5 adds 286 tokens (`tool_choice: auto`) or 406
  (`any`/`tool`); Sonnet 5 adds 354/474. Trivial but real at scale.
- **Data residency:** `inference_geo: "us"` costs 1.1×. Leave it unset.
- **Fast mode** (Opus 5 / 4.8 only, Claude API only): $10/$50 per MTok, 2× the standard
  price, and **not available with the Batch API**. Only relevant for the election-night fast
  lane (§5.3).

Two facts from the bundled `claude-api` skill reference that I did **not** independently
verify against a fetched docs page — flag as **[UNVERIFIED]**, check
<https://platform.claude.com/docs/en/build-with-claude/prompt-caching> before relying on them:

- The **minimum cacheable prefix on Claude Opus 5 is 512 tokens** (down from 1024 on Opus
  4.8; 4096 on Opus 4.6/Haiku 4.5). If true, almost any methodology prompt we write is
  cacheable on Opus 5 but *silently will not cache* on Haiku 4.5 unless it exceeds 4k tokens.
  This is a real footgun for the Tier-1 screener.
- Max **4 `cache_control` breakpoints per request**, and each breakpoint looks back at most
  **20 content blocks** to find a prior entry.

### 1.2 Competitors (for the ensemble-diversity decision)

OpenAI, from <https://developers.openai.com/api/docs/pricing>, USD/MTok:

| Model | Input | Output |
|---|---|---|
| gpt-5.6-sol | $5.00 | $30.00 |
| gpt-5.6-terra | $2.00 | $12.00 |
| gpt-5.6-luna | $0.20 | $1.20 |
| gpt-5.5 | $5.00 | $30.00 |
| gpt-5.5-pro | $30.00 | $180.00 |
| gpt-5.4 / -mini / -nano | $2.50 / $0.75 / $0.20 | $15.00 / $4.50 / $1.25 |
| gpt-5.1 / gpt-5 | $1.25 | $10.00 |
| gpt-5-mini / gpt-5-nano | $0.25 / $0.05 | $2.00 / $0.40 |

Cached input ≈ 10% of standard input; **Batch API = exactly 50% off**. Same shape as
Anthropic's, so a cross-provider ensemble does not change the cost model's structure — only
the constants.

Google Gemini — **[UNVERIFIED]**: I could not fetch Google's own pricing page and the
numbers below come from third-party aggregators (<https://www.cloudzero.com/blog/gemini-pricing/>,
<https://benchlm.ai/google/api-pricing>) which disagree with each other on model naming.
Treat as order-of-magnitude only and re-check at
<https://ai.google.dev/gemini-api/docs/pricing> before wiring a Gemini leg into the ensemble.
Reported: Gemini 3.1 Pro ≈ $2/$12 (tiering to $4 input above 200k); Gemini 3.5 Flash ≈
$1.50/$9; Gemini 2.5 Flash ≈ $0.30/$2.50; Flash-Lite ≈ $0.10/$0.40. Context caching reads
≈10% of input **plus a per-hour storage fee** ($1–4.50/MTok/hr) — structurally different
from Anthropic's write-once model and worse for our access pattern (write once, read a few
dozen times within an hour).

### 1.3 Retrieval / search APIs

**[UNVERIFIED — all from third-party comparison sites, not vendor pages.]** Sources:
<https://keirolabs.cloud/blogs/comparisons/ai-search-api-pricing-compared>,
<https://apiserpent.com/blog/serper-pricing-credits-explained>,
<https://fastcrw.com/blog/exa-pricing-explained>.

| Provider | Reported price | Notes |
|---|---|---|
| Anthropic `web_search` | **$10 / 1k searches** (primary source — verified) | Server-side, zero integration cost, results already tokenized into context |
| Serper | ~$0.30–$1.00 / 1k | Cheapest; raw Google SERP, you fetch pages yourself |
| Brave Search API | ~$5 / 1k | |
| Exa (standard) | ~$7 / 1k (raised from $5 in Mar 2026); Deep Search $12–15/1k | Semantic/neural; `contents` $1/1k pages |
| Tavily | ~$0.005–$0.008 per credit | Credit-based, not per-call |
| Metaculus tournament note | "Each Exa search costs half a cent per search plus a tenth of a cent per text-content per site" (<https://github.com/Metaculus/metac-bot-template>) | Contradicts the $7/1k figure above — likely a stale README or a tournament rate |

**Decision:** search-provider choice moves per-question cost by ~$0.045 (5 searches at
$0.01 vs $0.001). That is ~25% of a $0.17 question. Start on Anthropic's `web_search` for
zero integration cost and *measure*; migrate the high-volume screening lane to Serper +
`web_fetch` (which has no surcharge) if search calls become the dominant line item.

---

## 2. What known bots actually spend, and where it goes

### 2.1 FutureSearch — the only public, itemized number

From <https://futuresearch.ai/blog/kalshi-forecaster-case-study/> (also dissected at greater
length in `research/futuresearch.md`, which staged their 153-question CSV):

| Item | Cost |
|---|---|
| Per-market screening (their `classify` op) | **~$0.01/market** |
| Research + forecast, all-in | **~$0.60/question** |
| Same notebook billed to an outside user | ~$1.00/question |

Their architecture, for reference: two sequential AI screening filters (insider-information
filter, methodology-fit filter) → **six parallel research agents** doing live web search
(current state, base rates, key factors, expert opinions, YES thesis, NO thesis) → **three
independent forecaster calls** (Gemini, Claude Opus, a second Claude Opus run) → **median**.
Filters: price 3–97¢, minimum volume, >10 days to resolution, no sports, no crypto, no
insider-advantage markets. Scale: **~3,500 open Kalshi events scanned → 153 forecasted** in
the Feb 26 2026 run.

**Where the money goes — the ratio is the whole design insight.** Screening is **60–100×
cheaper than forecasting**. One full weekly Kalshi sweep at their prices:
3,500 × $0.01 + 153 × $0.60 = **$35 screening + $92 forecasting ≈ $127/sweep** [derived].
Note the screen is 28% of total spend even at 1/60th the unit price, purely because it runs
on 23× more markets. **Both ends of the funnel need optimizing; neither dominates.**

Their per-question spend decomposes (my reconstruction, **[derived]**, not published): six
research agents each doing 2–4 searches and reading 3–8 pages is 15k–40k retrieved tokens
per agent, which at Sonnet-class prices is where most of the $0.60 sits; three frontier
forecaster calls on a compressed dossier is a few cents each of input plus reasoning
output. **Retrieval tokens dominate, not judgment tokens** — as long as you compress before
the judge. If you *don't* compress and hand 6×30k raw tokens to three Opus calls, the judge
side explodes to 6×30k×3×$5/MTok = $2.70/question of input alone.

### 2.2 Metaculus tournament bots

- **Cost is systematically obscured** in this population because OpenAI and Anthropic donate
  credits through a Metaculus LLM proxy, distributed per-model rather than per-account
  (<https://github.com/Metaculus/metac-bot-template>). Tournament bots are therefore
  optimized for *score*, not for cost — do not copy their architecture without re-pricing it.
- **`Metaculus/forecasting-tools` ships a `MonetaryCostManager`**
  (<https://github.com/Metaculus/forecasting-tools>): tracks AI+API spend, errors past a
  limit, async-safe and nestable, with the documented caveat that **costs are recorded after
  calls complete, so concurrent batches can overshoot the limit mid-flight**. The two knobs
  that set cost are `research_reports_per_question` and `predictions_per_research_report` —
  i.e. exactly the retrieval-fan-out × judge-fan-out product from §3. No per-question dollar
  figures are published.
- **`TemplateBot`** is documented as the cheap/fast/simple tier; the framework's own docs
  don't state defaults.

### 2.3 Panshul42 — the Q2 2025 winner

Winner of the Metaculus Q2 2025 AI Forecasting Benchmark ($7,550 of a $30k pool, 96 bots,
300+ questions), with a sum-of-spot-peer-score of 5,899 vs 5,131 for the strongest Metaculus
baseline bot `metac-o3+asknews`
(<https://forum.effectivealtruism.org/posts/F2stjK9wHSy3HPEC9/q2-ai-benchmark-results-pros-maintain-clear-lead>).

Architecture per the repo README (<https://github.com/Panshul42/Forecasting_Bot_Q2>):
**five forecasting agents — 2× Claude 3.7 Sonnet, 2× o4-mini, 1× o3 (double-weighted)** —
with LLM-generated search queries feeding a multi-source research layer (AskNews, Perplexity
`sonar-reasoning-pro`, Google/Bing/DuckDuckGo, static HTML scraping), combined by weighted
averaging. Agents are deliberately split between outside-view and inside-view framings.

⚠️ **Conflict to resolve before citing:** the EA Forum write-up describes the ensemble as
"o3, o4-mini, Sonnet 4 for his final predictions", while the repo README says Claude 3.7
Sonnet. Different runs or a stale README — **[UNVERIFIED]** either way.

**The repo publishes no cost, runtime, or budget numbers at all.** Reconstructing at today's
prices **[derived, illustrative]**: 5 agents × (~8k research-context input + ~2k reasoning
output). If we substitute today's equivalents — 2× Sonnet 5 ($2/$10), 2× gpt-5-mini
($0.25/$2), 1× a $5/$30 frontier model double-weighted — the judge layer alone is roughly
2×(8k·$2 + 2k·$10)/1e6 + 2×(8k·$0.25 + 2k·$2)/1e6 + (8k·$5 + 2k·$30)/1e6 ≈
$0.072 + $0.012 + $0.100 ≈ **$0.18/question of judging**, plus the research layer (AskNews +
Perplexity + multi-engine search: easily $0.10–0.30/question at list prices). **~$0.30–0.50
per question all-in.** Same order as FutureSearch, arrived at from a completely different
architecture — which is a useful convergence signal.

**Cheap-diversity finding:** the winning ensemble was *not* five frontier models. It was
mid-tier models run multiple times with different framings, plus one strong model
double-weighted. That is a ~3× cheaper ensemble than 5× frontier and it beat the field.
**Copy this shape.**

### 2.4 Where the cost goes — the consolidated picture

Across all three: **retrieval is 50–70% of per-question spend, judgment is 30–50%, screening
is <5% per unit but 20–30% of total spend once fan-out is counted.** The dominant
controllable terms, in order:

1. Number of retrieved tokens that reach a *frontier* model uncompressed.
2. `effort` / thinking-token volume on the judge (output tokens at 5× input price).
3. Judge fan-out (ensemble size).
4. Search-call count × search unit price.
5. Screening fan-out (cheap per unit, huge N).

---

## 3. The tiered architecture

Design target: **`bot/forecaster/` as a 4-tier funnel where each tier is ≥5× cheaper per
unit than the next and cuts volume by ≥3×.** Concrete token budgets below; all costs
computed from the §1.1 table.

> ⚠️ **Measure your tokens with `client.messages.count_tokens`, not `tiktoken` and not the
> ~4-chars-per-token rule.** Opus 4.7+ and Fable 5 use a newer tokenizer that produces
> **~30% more tokens for the same text** than Sonnet 4.6 and earlier
> (<https://platform.claude.com/docs/en/about-claude/pricing>). Every budget below is in
> tokens; if you size them from character counts you will be 30% under on the Opus legs.

### 3.1 Tier 0 — deterministic prefilter (free)

No LLM. Straight from the Kalshi API fields we already pull (`bot/data/kalshi_client.py`):

- price band 3¢–97¢ (FutureSearch's filter; also where fee-per-edge is tolerable, §4.3)
- volume / open-interest floor
- `close_ts − now > 10 days` for the slow lane
- category blocklist: sports, crypto, entertainment/reality-TV, anything insider-prone
- **dedupe to the event level** — one research job per event, not per bracket (§6.2)

Expect **~1,000 open markets → ~250–400 candidates.** Cost: $0.

### 3.2 Tier 1 — the cheap screener (Haiku 4.5)

Job: *"does this market deserve research at all?"* — **not** "what is the probability".
Output a triage decision, a crude prior, and an uncertainty flag.

- Batch ~20 markets per call behind a shared, cached system prompt (the rubric).
- Per market: **~400 input tokens** (title + rules summary + price + volume + close date)
  and **~30 output tokens** (`{research: bool, prior: float, uncertainty: 0-3, reject_code}`).
- Use **structured outputs** (`output_config.format`) so the JSON is guaranteed parseable —
  a parse-retry loop is a silent 2× cost multiplier.

**Cost: $0.00055/market sync, $0.000275 batched** [derived: 400×$1/1e6 + 30×$5/1e6].
**1,000 markets/day = $0.55/day, or $0.28 batched.** That is **~20× cheaper than
FutureSearch's $0.01/market screen** — model tier + batching + the shared cached rubric.

⚠️ **Haiku 4.5's cache minimum is reportedly 4,096 tokens [UNVERIFIED, §1.1].** If your
rubric is 2k tokens it will *silently* not cache — `cache_creation_input_tokens: 0` and no
error. Either pad the rubric past the minimum with genuinely useful few-shot examples, or
verify `usage.cache_read_input_tokens > 0` on the second call and drop the `cache_control`
if it never fires.

### 3.3 Tier 2 — retrieval and compression (Sonnet 5)

Job: turn a question into a **compressed evidence dossier**. This tier exists for one
reason: **to shrink 25k retrieved tokens into a 3–4k dossier before an $5/MTok model sees
them.** Compressing at Sonnet ($2/MTok in) instead of paying Opus ($5/MTok in) for the raw
pages saves 25k × ($5−$2)/1e6 = **$0.075 per question, per judge**. With a 3-judge ensemble
reading raw pages you'd burn $0.375/question on nothing but redundant reading.

Budget per question: 4–6 searches, ~25k input tokens of retrieved content, ~3k output
dossier.

| Search backend | Token cost | Search cost | **Total** |
|---|---|---|---|
| Anthropic `web_search` (5 calls @ $0.01) | $0.080 | $0.050 | **$0.130** |
| Serper + `web_fetch` (5 calls @ $0.001) | $0.080 | $0.005 | **$0.085** |

Use FutureSearch's six-agent decomposition as the *prompt structure* (current state, base
rates, key factors, expert opinion, YES thesis, NO thesis) but run it as **one Sonnet call
with six sections**, not six agents — six separate calls means six copies of the system
prompt and six round trips of overhead for the same retrieved corpus.

The dossier must be **written for reuse**: question-agnostic facts first, question-specific
synthesis last, so the fact block can be a cached prefix for sibling markets (§6.2).

### 3.4 Tier 3 — the judge (Opus 5)

Job: a calibrated probability, a rationale, and — critically — an **explicit uncertainty
band**, from the dossier plus the methodology prompt in `forecasting/methodology/`.

Budget: ~5k uncached input (dossier + market rules), ~3k **cached** methodology prefix,
and output that is dominated by thinking tokens.

| `effort` | Output tokens | Cold | Warm (3k cached prefix) |
|---|---|---|---|
| medium | 1,500 | $0.0775 | **$0.0640** |
| high (default) | 2,500 | $0.1025 | **$0.0890** |
| xhigh | 5,000 | $0.1650 | **$0.1515** |

[derived from §1.1: e.g. high/warm = 5,000×$5/1e6 + 3,000×$0.50/1e6 + 2,500×$25/1e6]

**Thinking tokens are billed as output tokens**, and Opus 5 output is 5× its input price.
That makes `effort` the biggest single lever in the pipeline: **medium → xhigh is a 2.4×
cost swing on the judge with no change to the prompt.** Two more Opus-5-specific traps
worth knowing before you tune it:

- **Thinking is ON by default on Opus 5** when the `thinking` field is omitted (unlike
  Opus 4.8/4.7, where omitting it meant no thinking). A route that silently ran
  thinking-free on 4.8 will start spending thinking tokens on 5, *and* `max_tokens` caps
  thinking + response together, so a tight `max_tokens` truncates mid-answer.
- **`thinking: {type: "disabled"}` is only legal at `effort: high` or below** on Opus 5 —
  pairing it with `xhigh`/`max` is a 400. And disabling thinking has two documented failure
  modes (tool calls emitted as plain text that silently never run; `<thinking>` tags leaking
  into visible output). **Prefer `effort: low`/`medium` with thinking on over disabling it.**

**Recommended policy:** `effort: medium` for the first-pass judge; escalate to `high`/`xhigh`
only for markets whose Tier-2 dossier flags high uncertainty *and* whose expected profit
clears an EV gate (§4). This is an EV-weighted compute allocation and it is the cheapest
quality win available.

### 3.5 Ensemble sizing

Diversity beats depth, per §2.3. Recommended default:

- **Judge A:** Opus 5, `effort: medium`, outside-view framing (base rates first) — $0.064
- **Judge B:** Opus 5, `effort: medium`, inside-view framing (causal path first) — $0.064
- **Judge C:** a *different provider* on the same dossier (gpt-5.4 or Gemini Pro) for genuine
  error decorrelation — ~$0.06–0.09
- Aggregate by **median**, not mean (FutureSearch's choice; robust to one blown call).

The two Opus legs share the same cached methodology prefix and the same cached dossier, so
the second one costs only its uncached delta.

### 3.6 The end-to-end unit cost

| Configuration | Per researched question |
|---|---|
| screen + retrieval (Serper) + **1 judge** @ high/warm | **$0.175** |
| screen + retrieval + **3-judge ensemble** @ high/warm | **$0.353** |
| same, **Batch API on retrieval + judges** | **$0.179** |
| same, 3-judge @ `effort: medium` | **$0.278** |
| FutureSearch's published number, for comparison | $0.60 |

[derived: screen $0.00055 + retrieval $0.085 + N×$0.089]

**We should land at 2–3× cheaper than FutureSearch's published pipeline for the same shape**,
because (a) Sonnet 5 at $2/MTok is cheaper than what they were running, (b) prompt caching
of the methodology prefix and dossier, (c) one Sonnet call with six sections instead of six
agents, and (d) batch for the slow lane.

---

## 4. Break-even: the per-question inference budget

### 4.1 The model

Per Kalshi's fee formula as implemented in `bot/backtest/fees.py`:
`fee = ceil_to_cent(0.07 · P · (1−P))` **per contract**, taker only; maker is modeled at 0
(both flagged `TODO(verify)` against
<https://kalshi.com/docs/kalshi-fee-schedule.pdf> — that verification is a prerequisite for
trusting any of the numbers below).

Definitions:

- `S` = notional deployed per trade (depth-capped)
- `p` = entry price (dollars)
- `e` = **realized** edge in probability points (not the forecast's claimed edge)
- `φ` = fill rate — **0.43**, FutureSearch's simulated Kalshi fill rate
  (<https://futuresearch.ai/blog/kalshi-trader-case-study/>, per `docs/viability.md` and
  ORCHESTRATION constraint #4)
- `t` = fraction of researched questions that produce a tradeable disagreement

Then, holding to settlement (no exit fee):

```
contracts        = S / p
net edge/contract = e − 0.07·p·(1−p)
E[net profit per researched question] = t · φ · (S/p) · (e − 0.07·p·(1−p))
Budget_max       = 0.20 × that
```

### 4.2 The answer

Base case: **S = $750** (a conservative half of a $1.5k book level — this repo measured
$1–3k depth on mid-tier Kalshi markets, corroborated by the CPI-market order-book figure of
"a few $100–$3k per level" in `research/systematic-edges.md:342`), **p = 0.50**, **φ = 0.43**,
**t = 0.15**.

| Realized edge | Gross/trade | Net/trade | **Budget: 20% of gross** | **Budget: 20% of net** |
|---|---|---|---|---|
| 2 pts | $30.00 | $3.75 | $0.387 | **$0.048** |
| 3 pts | $45.00 | $18.75 | $0.581 | **$0.242** |
| **5 pts** | **$75.00** | **$48.75** | **$0.968** | **$0.629** |
| 8 pts | $120.00 | $93.75 | $1.548 | **$1.209** |
| 12 pts | $180.00 | $153.75 | $2.322 | **$1.983** |

[derived; script reproduced in §4.5]

**The headline: at a 5-point realized edge, the honest (net-of-fee) budget is $0.63 per
researched question.** FutureSearch's $0.60 sits exactly on that line — which strongly
suggests they solved the same equation. Our $0.175–0.353 pipeline sits at 5.6%–11.2% of
expected net profit, comfortably inside the 20% rule with room for the ensemble.

**And the warning: below a ~3-point realized edge the whole thing stops working.** At 2
points, fees eat 87.5% of the edge and the budget collapses to 4.8¢/question — cheaper than
a single Haiku screen plus one search call. There is no pipeline that is profitable at a
2-point edge on 50¢ taker fills.

### 4.3 Fees as a fraction of edge — the real gate

At p = 0.50 the taker fee is 1.75¢/contract, the maximum of the `P(1−P)` parabola:

| Edge | Fee as % of edge | Net edge |
|---|---|---|
| 2 pts | **87.5%** | 0.25 pt |
| 3 pts | 58.3% | 1.25 pt |
| 5 pts | 35.0% | 3.25 pt |
| 8 pts | 21.9% | 6.25 pt |
| 12 pts | 14.6% | 10.25 pt |

Three operational consequences:

1. **Set the trade gate at |forecast − price| ≥ 4–5 points, not 2.** Anything tighter is
   donating to Kalshi. This gate directly sets `t` and therefore the budget.
2. **Trade away from 50¢ when you can.** At p = 0.10 or 0.90 the fee is 0.63¢ — a third of
   the mid-price fee. The margin/bracket markets that `docs/viability.md` identifies as the
   highest-EV edge live at these prices, and they are *also* the cheapest to trade. That is a
   strong reason to prioritize them.
3. **Maker fills roughly double the net edge** if the 0% maker rate holds — 3.25 pts → 5 pts
   at a 5-point edge. But maker fill rate is well below 43%. This is a `t·φ` vs `e` tradeoff
   the backtest can resolve empirically; don't guess it.

### 4.4 Sensitivity to `t` and `φ` — where the estimate is fragile

The budget is *linear* in both, so an over-optimistic `t` propagates one-for-one:

- `t = 0.30` (loose gate) → budget doubles to $1.26, but the marginal trades have low `e`
  and the *realized* portfolio edge falls. Loosening `t` to afford a bigger pipeline is
  self-defeating.
- `t = 0.08` (strict gate) → budget falls to $0.34 — still above our $0.175 single-judge
  pipeline, below the $0.353 3-judge one. **The ensemble is only affordable at a trade
  conversion rate above ~8%.**
- `φ = 0.25` (pessimistic, resting-order heavy) → budget falls to $0.37.

**Use `t` and `φ` measured by the backtest, not assumed.** Both are directly observable from
`BacktestResult` — `t` from decisions-that-produced-intents over total decisions, `φ` from
fills over intents.

### 4.5 Reproduce it

```python
def budget(S=750, p=0.50, e=0.05, fill=0.43, trade=0.15, frac=0.20, feerate=0.07):
    contracts = S / p
    fee_per_contract = feerate * p * (1 - p)
    gross = contracts * e
    net   = contracts * (e - fee_per_contract)
    return gross, net, fill*trade*gross*frac, fill*trade*net*frac
# budget(e=0.05) -> (75.0, 48.75, 0.968, 0.629)
```

---

## 5. Funnel sizing: cost does not bind, bankroll does

Per-day cost for the reference pipeline (screen $0.00055/market, retrieval $0.085/question,
judge $0.089/question), with expected profit at the §4.2 base case:

| Scenario | Screened | Researched | Judges | Cost/day | Trades/day | Deployed/day | E[net]/day | **Cost as % of net** |
|---|---|---|---|---|---|---|---|---|
| A daily full sweep, 3-judge | 1,000 | 120 | 3 | $42.79 | 7.7 | $5,805 | $377 | 11.3% |
| B daily full sweep, 1-judge | 1,000 | 120 | 1 | $21.43 | 7.7 | $5,805 | $377 | 5.7% |
| **C bankroll-matched, 3-judge** | 1,000 | 35 | 3 | **$12.87** | 2.3 | $1,693 | $110 | **11.7%** |
| D bankroll-matched, 1-judge | 1,000 | 35 | 1 | $6.64 | 2.3 | $1,693 | $110 | 6.0% |
| E bankroll-matched, 3-judge, **batched** | 1,000 | 35 | 3 | **$6.44** | 2.3 | $1,693 | $110 | 5.8% |
| F weekly sweep (per sweep) | 3,500 | 150 | 3 | $54.73 | — | — | — | 11.6% |

[derived]

**Every configuration passes the 20% test.** That is the surprising result: at 2026 prices,
inference cost is simply not the binding constraint on a well-designed pipeline. Note the
ratio is *scale-invariant* (both cost and profit are linear in questions researched) — so
"cost as % of net" is a property of the *architecture*, not of the funnel size. Choose the
funnel on bankroll, and the architecture on the 20% rule.

### 5.1 What actually binds

Scenario A researches 120 questions/day and wants to deploy **$5,805/day of new notional.**
On a $50–100k bankroll with 10–30 day average holds, that saturates in 9–17 days and then
stops. The real constraint chain:

```
bankroll $75k / avg position $750  = 100 concurrent positions max
100 positions / 20-day avg hold    = 5 new positions/day sustainable
5 / (t=0.15 × φ=0.43)              = ~78 researched questions/day   [derived]
```

At a stricter `t` or with per-category caps you land at 30–50/day. **Scenario C/D/E is the
right size for this project**, and it costs **$6–13/day, i.e. $190–390/month.**

This also means: **do not spend engineering effort shaving the screen cost.** Going from
$0.55/day to $0.28/day of screening saves $8/month. Spend that effort on `e` instead — one
extra point of realized edge is worth $15/trade.

### 5.2 Cost per deployed dollar — the metric to track

`inference_cost / notional_deployed`. Scenario C: $12.87 / $1,693 = **0.76%**. Compare to
the Kalshi taker fee at 50¢, which is 1.75¢ on a 50¢ contract = **3.5% of notional**.
**Inference is ~1/5th the cost of fees.** If your inference-per-deployed-dollar ever
approaches the fee drag, the architecture is wrong.

### 5.3 Two lanes, two cost profiles

The batch/sync split falls out of the market clock, not the budget:

| Lane | Markets | Latency budget | Config |
|---|---|---|---|
| **Slow** | >10 days to close (FutureSearch's filter) | hours | **Batch API, 50% off**, nightly sweep, `effort: medium`, full 3-judge ensemble |
| **Fast** | Election night, rulings, data releases — the `docs/viability.md` overcorrection play | seconds | Sync, no batch, small cached prompts, **1 judge**, possibly Opus 5 fast mode ($10/$50) |

The fast lane is where the Michigan-style dip-buy lives, and the trough lasted ~3 minutes.
At $0.09–0.30 per fast-lane call and a handful of calls per event, fast-lane *inference* is
rounding error — **latency, not cost, is the fast lane's constraint.** Budget it as a
fixed monthly line and do not tier it.

---

## 6. Caching/reuse tricks

Ordered by measured saving. All computed from §1.1 prices.

### 6.1 Cached methodology prefix — 86% off the system prompt

`forecasting/methodology/` is the crown-jewel prompt material and it is *stable*. Put it
first in the request, mark it with `cache_control` at a **1-hour TTL** during a sweep.

For a 5k-token methodology prompt across 50 questions in one hour:

- No cache: 50 × 5,000 × $5/MTok = **$1.2500**
- 1h cache: (5,000 × $10/MTok write) + 49 × (5,000 × $0.50/MTok read) = **$0.1725**
- **Saving: 86%** [derived]

**The three ways this silently breaks** (verify with `usage.cache_read_input_tokens > 0`, and
audit against <https://platform.claude.com/docs/en/build-with-claude/prompt-caching>):

1. Any volatile byte in the prefix — a timestamp, the market ticker, `datetime.now()`,
   a run UUID. Caching is a **prefix match**; one changed byte invalidates everything after
   it. Render order is `tools` → `system` → `messages`, so put stable content first and the
   per-market question **last**.
2. Non-deterministic serialization — `json.dumps` without `sort_keys=True`, iterating a
   `set`, or a tool list assembled in varying order.
3. Changing the model or the tool definitions mid-sweep. Caches are model-scoped and tools
   render at position 0. **Freeze the tool list for the duration of a sweep.**

### 6.2 Shared evidence dossiers across related markets — 52% on a 12-bracket event

This is the single most repo-specific trick, and it maps directly onto the highest-EV edge
`docs/viability.md` identified: **victory-margin and bracket markets.** A Kalshi event with 12
margin brackets is *one* forecasting question ("what is the distribution of the margin?")
sold as 12 binary contracts. Researching it 12 times is 12× waste.

Design: **research at the event level, judge at the market level.**

- One Tier-2 retrieval per *event* → one dossier.
- Mark the dossier with `cache_control` (1h TTL) as a shared prefix.
- Fan out one cheap judge call per bracket, each reading the dossier from cache.
- Better still: have a single judge emit the **full distribution** in one structured-output
  call and derive all 12 bracket probabilities from it — this also guarantees the brackets
  sum to 1, which is itself a tradeable consistency signal (see the "bracket sums" edge in
  `research/systematic-edges.md`).

| Approach | Cost |
|---|---|
| 12 independent questions (retrieval + judge each) | $2.088 |
| Shared dossier + cached prefix, 12 judges | **$0.995** |
| **Saving** | **52%** |

[derived: naive = 12 × ($0.085 + $0.089); shared = $0.085 retrieval + $0.089 cold judge +
$0.04 dossier cache-write + 11 × $0.071 warm judge]

The warm judge costs $0.071 vs $0.089 cold — the dossier and methodology both arrive at
cache-read rates. Push it further with a distribution-emitting single judge and the 12
brackets cost **$0.17 total**, a 92% saving.

### 6.3 Forecast reuse until news breaks — 85% at a 15% refresh rate

Full re-forecasting every market every day is the default and it is wasteful: most markets
have no news on most days.

Design a **delta-check gate**:

1. Store, with each dossier: the forecast, the market price at forecast time, a TTL by
   question class, and a **hash of the top-k retrieved URLs + headlines**.
2. Daily, run a **Haiku 4.5 delta-check** (~1.5k input, ~60 output = **$0.0018**): "here are
   today's top headlines for this question and yesterday's evidence summary — has anything
   materially changed? yes/no + one line."
3. Trigger a full refresh only on: (a) delta-check fires, (b) market price moved more than
   X cents since the last forecast, (c) TTL expiry, (d) a scheduled catalyst from the event
   calendar.

Steady-state cost per market per day, with a $0.352 full refresh (3-judge):

| Refresh probability | Cost/market/day |
|---|---|
| 1.00 (always) | $0.354 |
| 0.30 | $0.107 |
| **0.15** | **$0.055** |
| 0.07 | $0.026 |

[derived]

**A 15% refresh rate is an 85% saving.** Note the price-move trigger already exists in the
harness: `Strategy.price_move_trigger_cents` (`bot/backtest/types.py:244`) creates an extra
decision point when the price moves. **Wire the refresh gate to that, not to a wall clock** —
it makes the backtest and the live bot share the same trigger logic.

⚠️ **This trick is also a contamination risk in backtest.** A "has anything changed" check
that reads *today's* headlines during a replay of March 2026 is a lookahead leak. The
delta-check must go through the same point-in-time retrieval discipline as everything else
(see `research/point-in-time-retrieval.md`).

### 6.4 Batch API — flat 50% on the slow lane

Nightly sweep of markets >10 days out has no latency requirement. 50% off input *and*
output, stacks with caching. Scenario E above: $12.87/day → $6.44/day.

Caveats: ≤24h turnaround (not guaranteed <1h); results arrive **in arbitrary order — key by
`custom_id`, never by position**; the Batch API is **incompatible with fast mode**; and
results are retained 29 days.

### 6.5 EV-weighted effort allocation

Don't spend the same compute on every question. After Tier 2, you have a rough edge
estimate; gate the judge on expected profit:

```
E[profit] ≈ φ · (S/p) · (|p_hat − p| − 0.07·p·(1−p))
if E[profit] < $10:  drop (below the fee gate anyway)
if E[profit] < $40:  1 judge, effort=medium         → $0.064
if E[profit] < $100: 3 judges, effort=medium        → $0.192
else:                3 judges, effort=high/xhigh    → $0.267–0.455
```

This concentrates spend where the 20% rule has headroom and starves it where it doesn't.

### 6.6 Structured outputs instead of parse-and-retry

Use `output_config.format` with a JSON schema on every tier. A parse failure that triggers a
retry is a 2× cost on that call, and retries cluster on exactly the hard/expensive questions.
First use of a new schema pays a one-time compile latency, then it's cached 24h.

### 6.7 Things that are *not* worth doing

- **Chasing the long-context discount.** There isn't one — 1M-token requests bill at the same
  per-token rate. The only reason to compress is the model-tier gradient (§3.3).
- **Micro-optimizing the screener.** $8/month of headroom (§5.1).
- **Cross-provider cache sharing.** Caches are model-scoped and provider-scoped. A 3-provider
  ensemble pays 3 cold prefixes. Budget for it — it's ~$0.02/question and worth it for
  decorrelation.
- **Fast mode by default.** 2× price for latency you only need in the fast lane.

---

## 7. Wiring cost into the backtest

ORCHESTRATION constraint #3 says every strategy's backtest must charge per-question inference
cost. **The harness already supports this.** From `bot/backtest/types.py:248`:

> `last_inference_cost_cents: int (0)` — read by the engine after every `on_decision_point`
> call and charged to P&L as inference cost.

And `bot/backtest/engine.py:18-20` charges it as its own P&L line; `BacktestResult` exposes
`net_pnl_cents` (gross of inference) and `net_pnl_after_inference_cents`, and
`Portfolio.inference_cost_cents` is visible to the strategy at decision time.

**What the LLM strategies must do:**

1. **Log real token counts, not estimates.** Every Anthropic response carries
   `usage.input_tokens`, `usage.output_tokens`, `usage.cache_creation_input_tokens`,
   `usage.cache_read_input_tokens`, and `usage.server_tool_use.web_search_requests`. Price
   them with the §1.1 table. Attribute cached-read tokens at $0.50/MTok, **not** $5 — the
   caching saving is real P&L and must show up.
2. **Amortize shared costs honestly.** An event-level dossier shared across 12 brackets
   should charge each bracket 1/12 of the retrieval cost plus its own judge cost — otherwise
   bracket #1 looks unprofitable and #2–12 look free.
3. **Charge screening to the markets that pass, not to the universe.** A screen that rejects
   950 of 1,000 markets still cost $0.55; amortize it across the 50 survivors ($0.011 each)
   or the strategy will underreport by the whole screening line.
4. **Make cost a first-class ablation.** The `pnl_summary` metric already reports
   `inference_cost_cents` (`bot/backtest/metrics.py:193`). Add to the tournament report:
   `inference_cost / net_pnl` (target <20%), `inference_cost / notional_deployed` (target
   <1%, cf. 3.5% fee drag), and a **cost-stress run at 2× inference price** alongside the
   existing 1.5× fee-stress run (SPEC §6.7). A strategy whose edge dies at 2× inference cost
   is one model-price change from dead.
5. **Replay, don't re-call.** For deterministic backtests, cache every LLM response keyed by
   `(prompt_hash, model, params)` to a local store so a re-run of the same window costs $0
   and produces byte-identical decisions. This is required for the P5 tournament to be
   reproducible at all — you cannot ablate a strategy against a nondeterministic, paid
   oracle.

**Recommended `CostLedger` shape** (drop next to `bot/backtest/fees.py`):

```python
PRICES = {  # $/MTok: (input, cache_write_5m, cache_write_1h, cache_read, output)
    "claude-opus-5":   (5.0, 6.25, 10.0, 0.50, 25.0),
    "claude-sonnet-5": (2.0, 2.50,  4.0, 0.20, 10.0),
    "claude-haiku-4-5":(1.0, 1.25,  2.0, 0.10,  5.0),
}
WEB_SEARCH_USD = 0.010   # per search
BATCH_MULTIPLIER = 0.5
```

...with a `charge(usage, model, batch=False) -> cents` that rounds **up** to the cent (match
`fees._ceil_cents`, so cost is never understated) and an `amortize(job_id, n_consumers)`
for shared dossiers. Keep prices in one dict so the 2×-price stress run is a single knob.

---

## 8. Contamination: the cost of doing it honestly

ORCHESTRATION constraint #2 restricts LLM-forecast backtests to markets resolving after
**Feb 2026**. The model-cutoff table from §1.1 makes this sharper and it has a direct cost
consequence:

| Model | Reliable knowledge cutoff |
|---|---|
| Claude Opus 5 | **May 2026** |
| Claude Fable 5 | Jan 2026 |
| Claude Sonnet 5 | Jan 2026 |
| Claude Haiku 4.5 | Feb 2025 |

**Opus 5 knows things through May 2026.** A backtest on markets resolving March–May 2026 is
contaminated *for the Opus judge specifically*, even though it would be clean for Sonnet 5.
Two implications:

1. **The clean LLM-backtest window with an Opus 5 judge starts ~June 2026** — roughly 10
   weeks of resolved markets as of today. That is a small n; `research/benchmarks.md:32`
   notes the smallest provable Brier improvement at n=1,216 is ~0.004, so at n≈100 the
   statistical power is very weak. Plan the gate accordingly.
2. **A cheaper judge may be the *only* honest judge for the longer window.** Sonnet 5
   (Jan 2026 cutoff) buys back Feb–May 2026, and Haiku 4.5 (Feb 2025) buys back everything.
   This is a rare case where the cost-optimal choice and the contamination-optimal choice
   point the same way: **run the historical validation on Sonnet 5, and reserve Opus 5 for
   forward paper-trading where cutoff is irrelevant.**

Also: the delta-check gate (§6.3) and any `web_search` call must go through point-in-time
retrieval or they leak the future. Note that **Anthropic's server-side `web_search` cannot be
made point-in-time** — it searches the live web. For backtests you need a replayable
retrieval layer (see `research/point-in-time-retrieval.md`); the server tool is a live-lane
tool only. That has a cost consequence too: the backtest lane pays for a snapshot corpus,
the live lane pays $0.01/search.

---

## 9. Concrete recommendations for the build

1. **Ship the four tiers as separate, separately-priced modules** under `bot/forecaster/`:
   `prefilter.py` (free), `screen.py` (Haiku 4.5, batched, structured output),
   `research.py` (Sonnet 5, one call, six sections, dossier out),
   `judge.py` (Opus 5, cached methodology prefix, effort-gated, ensemble).
   Each returns `(result, TokenUsage)`. Never let a tier call the next one directly — the
   funnel controller owns the gates so the gates are testable and tunable.
2. **Default config:** screen everything ≥ Tier-0; research 35–50/day; 3-judge ensemble at
   `effort: medium`; Batch API for anything >10 days out; sync single-judge for the fast lane.
   **~$7–13/day.**
3. **Set the trade gate at ≥4 points** of forecast–price disagreement (§4.3), and prefer
   markets away from 50¢ where the fee is a third as large.
4. **Prioritize bracket/margin events** — highest EV per `docs/viability.md`, cheapest fees,
   *and* 52–92% cheaper to research via dossier sharing. All three arguments point the same way.
5. **Verify before trusting:** the Kalshi fee schedule (`fees.py` TODOs), the maker rate, the
   prompt-cache minimums per model, and the Gemini/search-API prices in §1.2–1.3.
6. **Instrument first.** Land `CostLedger` + real `usage` logging *before* the first LLM
   strategy, so the P4 prototypes are cost-honest from their first backtest rather than
   retrofitted in P5.
7. **Track three ratios in every tournament report:** inference/net-P&L (<20%),
   inference/notional-deployed (<1%), and cost-at-2×-price survival.

---

## 10. Open questions

- **What is the realized `t` (trade conversion) and `φ` (fill rate) on our data?** The whole
  budget is linear in their product. FutureSearch's 43% is a simulation on *their* filters;
  ours will differ. Measure in P4, don't assume.
- **Is the maker rate really 0?** Doubles net edge if so. Blocks a maker-first strategy if not.
- **Does a 3-judge ensemble actually beat 1 judge by enough to justify 2×?** Directly
  testable as a P5 ablation: same dossiers, 1 vs 3 judges, compare Brier and net-of-inference
  P&L. My prior from §2.3 is that *framing diversity* (outside/inside view) matters more than
  *model diversity* and that 2 well-framed Opus calls beat 3 undifferentiated ones — but this
  is exactly the kind of thing the tournament exists to settle.
- **Does `effort: medium` cost us measurable Brier vs `high`?** A 1.4× cost swing on the
  dominant line item. One ablation answers it.
- **Can the bracket-distribution single-judge (§6.2) match 12 independent judges?** If yes it
  is a 92% saving on the highest-EV market class.

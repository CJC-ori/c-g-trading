# Model-alias probe — which Claude generation each CLI alias maps to

Probed empirically 2026-08-11 from this container by asking each aliased model
for its model id and knowledge cutoff via headless `claude -p --model <alias>`
(no tools, single turn). Raw responses below, verbatim.

| CLI alias | Self-reported model id | Self-reported cutoff | Honest judging window |
|---|---|---|---|
| `haiku` | `claude-haiku-4-5-20251001` | February 2025 | anything post-Feb-2025 (screen/plan tiers only) |
| `sonnet` | `Claude Sonnet 5` | January 2026 | **markets resolving Feb 2026+ (used for the Feb–May 2026 rung-2 window)** |
| `opus` | `claude-opus-5` | May 2026 | markets resolving June 2026+ only |

Raw responses:

- haiku → "claude-haiku-4-5-20251001 / February 2025"
- sonnet → "Claude Sonnet 5, knowledge cutoff January 2026."
- opus → "claude-opus-5, knowledge cutoff May 2026"

Consequences applied in `bot/forecaster/`:

1. The rung-2 judge is pinned to `sonnet` (Sonnet 5): its Jan-2026 cutoff
   predates every question in the Feb–May-2026-resolving eval window, so its
   parametric memory cannot contain outcomes. Opus 5 (May 2026 cutoff) would
   be contaminated for that window and is reserved for June-2026+ / forward
   runs (research/cost-architecture.md §8 — confirmed by this probe).
2. Haiku 4.5 (Feb 2025) is safe everywhere but too weak to judge; it runs the
   Tier-1 screen and retrieval planning only.
3. Costs are charged to `bot.backtest.costs.CostLedger` under the price keys
   `claude-haiku-4-5` / `claude-sonnet-5` / `claude-opus-5` from exact usage
   blocks reported by the CLI (`input_tokens`, cache write/read splits,
   `output_tokens` — thinking tokens are inside `output_tokens`).

Execution mechanism note: no ANTHROPIC_API_KEY is available to scripts in
this container; `bot/forecaster/llm.py` therefore ships three backends —
`AnthropicAPIClient` (real SDK, written but unused here), `ReplayCacheClient`
(deterministic replay from `data/forecaster-cache/`), and `SubagentClient`
(headless `claude -p` calls with tools disabled, write-through to the replay
cache). All results in this directory were produced via `SubagentClient` and
are replayable from `rung2-llm-cache.jsonl`.

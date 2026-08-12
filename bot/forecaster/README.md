# bot/forecaster — the P-4 cost-engineered LLM forecaster

SYNTHESIS §1 P-4, built 2026-08-11. A tiered funnel with point-in-time
retrieval, structured ground-truth injection, and truthful cost accounting.

## Modules

| Module | Tier | Model | What it does |
|---|---|---|---|
| `prefilter.py` | 0 (free) | — | price band 3–97¢, volume ≥ 5,000 (Kalshi only), >10 days to close (actually enforced), category keyword screens, event-level dedupe |
| `screen.py` | 1 | Haiku 4.5 | FutureSearch's two screening prompts (insider/adverse-selection + methodology fit), merged, batched ~15 questions/call, fail-closed parsing |
| `retrieval.py` | 2 | — | PIT retrieval: GDELT DOC (BOTH date gates: client-side `seendate < D` + publisher `datePublished`/`dateModified` check), Wikipedia revisions as-of-date, retrieval ledger (immutable row before content reaches a model), blocking audit, banned live search engines |
| `groundtruth.py` | 2 | — | the differentiator: VoteHub polls (`created_at < D`), FEC Schedule E by `filing_date` (DEMO_KEY, 40/hr), ALFRED vintages (needs key; refuses leaky latest-vintage fallbacks), BLS non-revised-only |
| `dossier.py` | 2 | Sonnet 5 | per-EVENT compressed 6-section dossier (current state / base rates / key factors / expert+market opinion / YES thesis / NO thesis) from the ledgered evidence pack |
| `judge.py` | 3 | Sonnet/Opus | Chris's methodology: 2 independent passes with distinct stances (outside-view-first vs causal-chain-first), no pass sees the other's number, geometric-mean-of-odds aggregation, FutureSearch contamination guard + status-quo weighting + named uncertainties + 3–97 rails; **parse failure ⇒ no trade** |
| `pipeline.py` | — | — | the funnel controller; owns the gates, the retrieval ledger, and `CostLedger` charging |
| `llm.py` | — | — | `LLMClient` with three backends: `AnthropicAPIClient` (SDK, unused here — no API key in this container), `ReplayCacheClient` (deterministic replay), `SubagentClient` (headless `claude -p`, tools disabled, write-through cache) |
| `run_rung2.py` | — | — | the ForecastBench rung-2 eval runner (select/run/ablation/placebo/score) |

## Judge-cutoff rules (probed, see reports/forecaster/model-probe.md)

`sonnet` → Claude Sonnet 5, Jan-2026 cutoff → honest judge for markets
resolving Feb–May 2026. `opus` → claude-opus-5, May-2026 cutoff → June 2026+
only. Rung 2 runs entirely on Sonnet with retrieval bounded to each
question's `freeze` timestamp.

## Reproducibility

Every LLM response is cached in `data/forecaster-cache/` keyed by
`(prompt, system, model, params)`; `run_rung2.py --phase score` exports the
entries used by the final results to
`reports/forecaster/rung2-llm-cache.jsonl`, so the whole eval replays
deterministically (and network-free) via `ReplayCacheClient`.

Every retrieval writes a ledger row before its content may reach a model;
`retrieval.audit_ledger` is the blocking checker (SOURCE_AFTER_D /
PUBLISHED_AFTER_D / MODIFIED_AFTER_D / UNDATED_SOURCE / DATE_INCONSISTENT /
BANNED_SOURCE). Results: `reports/forecaster/rung2-results.json`.

## Run

```
python -m bot.forecaster.run_rung2 --n 120 --workers 6 --phase all
python -m pytest bot/forecaster/test_forecaster.py
```

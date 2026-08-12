# Rung 2 — LLM forecaster vs market baseline on ForecastBench resolved market questions

Run 2026-08-11/12. Judge: **Sonnet 5** (`sonnet` CLI alias, Jan-2026 knowledge
cutoff, empirically probed — `model-probe.md`), the honest judge for markets
resolving **2026-02-01..2026-05-31** with retrieval bounded to each row's
`freeze_datetime`. All model calls executed through `SubagentClient` (headless
`claude -p`, tools disabled) with write-through to the replay cache; every
number below replays deterministically from `rung2-llm-cache.jsonl` at $0.
Machine-readable results: `rung2-results.json`. Runner:
`bot/forecaster/run_rung2.py` (seed 7, resumable).

## Headline (n = 120, pooled)

| forecaster | Brier | ΔBrier vs market (paired, 95% CI) | verdict |
|---|---|---|---|
| **Full pipeline** (retrieval → dossier → 2-pass Sonnet judge) | **0.1596** | −0.0038 [−0.0151, +0.0076] | parity with market; not better |
| **No-retrieval ablation** (question text only, same judge) | 0.1613 | −0.0055 [−0.0178, +0.0069] | parity with market; not better |
| **Market baseline** (price at freeze) | 0.1558 | — | — |
| **Always-0.5** | 0.2500 | — | beaten decisively by all |

Base rate of YES: 0.333. Brier-index (ours, full): 60.0. Divergent judge
passes: 0/120; parse failures: 3 (all recovered by the second pass — no
question was dropped; 120/120 scored, coverage 1.0).

**Reading:** the forecaster is a competent forecaster in absolute terms
(≈36% better than chance) and lands within noise of the market price
(RMS-disagreement equivalent of the Δ: 6.2¢), but the point estimate is on the
wrong side: it does **not** beat the market. This exactly reproduces the
ForecastBench field result (no bare LLM beats the market baseline; best
public LLM ≈0.132 vs market 0.077 on their board — our window is harder,
n smaller).

## Three-number block

Number (c) — simulated post-fee P&L — is deliberately absent at this rung
(no trading here); it is produced by rung 3 through the engine.

| | pooled ΔBrier (n=120) | traded subset \|q−p\|≥4pts | post-fee P&L |
|---|---|---|---|
| Full | −0.0038 (se 0.0058, t=−0.66, NS) | n=45: −0.0162 [−0.0455, +0.0131], NS | → rung 3 |
| Ablation | −0.0055 (se 0.0063, t=−0.87, NS) | n=42: −0.0131 [−0.0478, +0.0216], NS | — |

The traded-subset point estimates are *more negative* than pooled: where we
disagree with the market by ≥4 points, the market is right more often than we
are (kill-criterion (b) shape, though n=45 is far below the n≥200 needed to
call it). Statistical gate verdict: **UNDERPOWERED** — min detectable
ΔBrier at n=120 is 0.0113; the superforecaster-vs-market margin (~0.004) is
undetectable at any n we can afford here. The scoring harness demands n≥500
for a significance claim (SYNTHESIS §2.4).

## No-retrieval-ablation delta — the load-bearing negative result

Paired full-vs-ablation on the same 120 questions:

> **ΔBrier(full − ablation) = +0.0017** (se 0.0045, t=0.37,
> 95% CI [−0.0072, +0.0106]) — **parity**.

The entire retrieval stack (GDELT + article fetch + Wikipedia revisions +
VoteHub, 1,389 ledgered retrievals, 3.1× the ablation cost) buys **no
detectable Brier improvement** over the judge's parametric prior. At this n
we cannot distinguish "retrieval adds a hair" from "retrieval adds nothing".
**Kill-criterion (e) effectively fires**: on this evidence the pipeline's
forecasting skill is prior recall, not research. Any rung-3/forward result
must be interpreted with that prior.

Two mitigating notes, honestly weighed: (1) the eval window sits entirely
within ~4 months of Sonnet 5's cutoff, where its prior is freshest — retrieval
should matter more further out; (2) the placebo test (below) shows the
pipeline *does* absorb new information when it exists. Neither note rescues
the headline: retrieval is currently not paying for itself.

## Per-source breakdown (full pipeline, ΔBrier vs market, + = we win)

| source | n | ΔBrier | 95% CI | tier |
|---|---|---|---|---|
| infer | 11 | **+0.0178** | [+0.0004, +0.0353] | superforecaster-class (tiny n — do not bank it) |
| manifold | 25 | +0.0130 | [−0.0111, +0.0371] | parity |
| metaculus | 41 | −0.0148 | [−0.0402, +0.0105] | fail |
| polymarket | 43 | −0.0085 | [−0.0221, +0.0051] | fail |

Consistent with the P-4 thesis direction: we look best against the *thinnest*
crowds (INFER, Manifold) and worst against the most liquid aggregations
(Polymarket, Metaculus community). n=11 at p<0.05 is one lucky bucket until
replicated, but it is where the niche-market thesis predicts the edge would
live.

## Calibration (full pipeline)

| bucket | n | mean forecast | realized |
|---|---|---|---|
| [0.00,0.05) | 6 | 0.042 | 0.000 |
| [0.05,0.15) | 30 | 0.092 | 0.100 |
| [0.15,0.35) | 25 | 0.229 | 0.200 |
| [0.35,0.65) | 33 | 0.450 | 0.424 |
| [0.65,0.85) | 15 | 0.742 | **0.533** |
| [0.85,0.95) | 8 | 0.902 | 0.875 |
| [0.95,1.01) | 3 | 0.957 | 1.000 |

Well calibrated everywhere except the 0.65–0.85 bucket (overconfident YES,
15 questions) — the classic optimism-on-change failure; the judge prompt's
status-quo weighting did not fully suppress it.

## Cost (CostLedger, exact usage blocks, standard API prices)

| mode | total | per question | notes |
|---|---|---|---|
| Full | $11.35 | **$0.095/q** | Haiku plan + Sonnet dossier + 2× Sonnet judge (effort medium) |
| Ablation | $3.62 | $0.030/q | 2× Sonnet judge only |

Well inside the $0.63/q break-even budget at a 5-point realized edge
(cost-architecture §4.2). Tier-1 screen (Haiku, batched): ~$0.002/market
screened. The prior probe + this run's marginal subagent spend stayed well
under the session budget; ~240 fresh judge calls were made for the ablation,
everything else was replay-cache hits.

## Selection funnel (deterministic, seed 7)

614 eligible resolved market questions (resolving 2026-02-01..05-31)
→ tier-0 prefilter 384 (rejects: price-band 129, sports 82, crypto 18, taped 1)
→ Haiku screen 199 (rejects: insider 69, sports 41, other 21, personal 15,
fan-minutiae 14, fin-price 10, taped 8, crypto 7)
→ stratified sample **120**: polymarket 43, metaculus 41, manifold 25, infer 11.

## Leak audit

- **Retrieval ledger**: 1,389 rows (873 GDELT headline-lists, 295 article
  fetches, 213 Wikipedia revision reads, 8 VoteHub). Verdicts: 1,232 PASS
  (873 headline-only, 351 full-content, 8 date-gated client-side),
  86 QUARANTINED (48 no-pubdate, 28 modified-after-asof, 10 pub-after-asof),
  71 fetch failures. Quarantined content never reached a model.
- **Blocking audit** (`audit_ledger`): **0 actual leakage violations** —
  no BANNED_SOURCE, no SOURCE_AFTER_D, no PUBLISHED_AFTER_D, no
  MODIFIED_AFTER_D, no UNDATED_SOURCE among PASS rows. **4 DATE_INCONSISTENT
  flags** (publisher `datePublished` 1–14h *later* than GDELT `seendate` —
  timezone/ingest skew): in all 4 cases every date involved is **days before
  D**, so no future content was admitted; strict `hard_fail` mode would still
  void the run on the inconsistency tripwire, recorded here verbatim for
  honesty. Ablation mode performs no retrieval (empty ledger by
  construction). Placebo ledger: 84 rows, 0 violations.
- **Placebo D vs D+14** (10-question sample, full pipeline re-run with
  retrieval shifted 14 days forward): Brier 0.1690 at D → **0.1457 at D+14**.
  The improvement with genuinely newer information is present and
  directionally correct — a flat pair would have meant the D run was already
  seeing the future. Not flat ⇒ **no gross leak signature**. (n=10 is a
  smoke test, not proof.)

## Kill-criteria verdicts (SYNTHESIS P-4)

| criterion | verdict |
|---|---|
| (a) fails to beat always-0.5 **and** market | **Not fired** — beats 0.5 by 0.090 Brier; statistically indistinguishable from market (Δ −0.0038 ± 0.0058). Rung 3 proceeds, with humility. |
| (b) traded subset ΔBrier ≤ 0 with n ≥ 200 | Point estimate negative at n=45 — **warning, underpowered**, cannot fire formally. |
| (e) ablation matches full pipeline | **Effectively fired** — Δ +0.0017, CI spans 0. Edge, if any, is prior recall. |
| (f) placebo flat | **Not fired** — D+14 markedly better (0.169→0.146). |

(c)/(d) are rung-3 criteria — see `RUNG3.md`.

**Bottom line:** the forecaster is market-parity, not market-beating, on 120
resolved questions; its research layer currently adds nothing measurable over
the judge's prior; and the only positive pocket (thin crowds, n=11) is exactly
where the thesis says it should be but far too small to bank. Proceed to
rung 3 as a costed engine-integration exercise, not as an expected money-maker.

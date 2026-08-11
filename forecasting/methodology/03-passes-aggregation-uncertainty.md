# Passes, aggregation, and honest uncertainty — how to run and report a forecast

*Companion to `01-forecasting-craft.md` and `02-fuzzy-questions.md`. This file covers the
mechanics that turn one model's opinion into a defensible number: independent passes,
aggregation math, disagreement handling, and the output format.*

---

## 1. Never ship a single pass

The most consistent engineering finding across every measured system: **single-pass forecasts
are poorly calibrated; small ensembles of independent passes are much better.**

- The [Metaculus AI Benchmark](https://forum.effectivealtruism.org/posts/F2stjK9wHSy3HPEC9/q2-ai-benchmark-results-pros-maintain-clear-lead)
  top bots: one research report, **5 forecasts from the same prompt, aggregated** — that simple
  recipe ranks top-10 against hundreds of bots.
- The [Metaculus forecasting-tools framework](https://github.com/Metaculus/forecasting-tools)
  defaults to 5 predictions per research report, with `research_reports_per_question` for
  independent research passes on top.
- The [AIA Forecaster](https://arxiv.org/pdf/2511.07678): M agents each do **independent
  adaptive research** and produce independent probabilities; a supervisor reconciles; a
  statistical calibration step (Platt scaling) finishes.
- [FutureSearch](https://futuresearch.ai/) (currently #1 of 196 on Metaculus FutureEval):
  parallel investigations into current state / base rates / key factors / expert opinions plus
  competing theses, then **three independent models** each produce a probability
  ([Kalshi case study](https://futuresearch.ai/blog/kalshi-forecaster-case-study/)).
- [Granade's bot](https://faintsignals.substack.com/p/building-an-ai-prediction-bot): minimum 3
  passes, **more when they diverge** — divergence-triggered extra passes are cheap insurance.
- [Samotsvety](https://forum.effectivealtruism.org/topics/samotsvety-forecasting), the best human
  team on record: independent individual forecasts first, discussion after, aggregate with the
  extremes trimmed.

**House default: 4–6 independent forecast passes.** Independence is the active ingredient — vary
the entry point so the passes aren't clones: at least one pass anchored outside-view-first, one
built inside-view/causal-chain-first, one explicitly adversarial ("strongest case for NO / for
YES"). If the passes span research too (different search trails, not just different reasoning
over one dossier), better still — that is the AIA Forecaster's architecture in miniature. If max−min
spread exceeds ~25pp (or 3× in odds), run 2 more passes and route the disagreement per §3.

Also remember the deflating meta-finding:
[model quality beats scaffolding](https://forum.effectivealtruism.org/posts/F2stjK9wHSy3HPEC9/q2-ai-benchmark-results-pros-maintain-clear-lead)
— scaffolding helps on the margin. Keep the harness simple; spend the complexity budget on
research quality and question operationalization (`02`), not on baroque aggregation.

---

## 2. Aggregation math

Follow [Sevilla](https://forum.effectivealtruism.org/posts/sMjcjnnpoAQCcedL2/when-pooling-forecasts-use-the-geometric-mean-of-odds)
and [Sempere](https://forum.effectivealtruism.org/posts/acREnv2Z5h4Fr5NWz/my-current-best-guess-on-how-to-aggregate-forecasts):

- **Default: geometric mean of odds.** Convert each p to odds o = p/(1−p), take the geometric
  mean, convert back. Empirically beats mean-of-probabilities and median, and is the only rule
  that makes the aggregate behave like a Bayesian. (Mean-of-probabilities over-weights outliers
  near the middle; it is the spreadsheet-default error.)
- **Samotsvety trim:** with ≥5 passes, drop the single most extreme forecast on each end first.
  Protects against one deranged pass without the bluntness of the median.
- **Median** only as the outlier-resistant fallback when you can't inspect the passes.
- **Arithmetic mean of probabilities** in exactly one situation: averaging over **mutually
  exclusive world-models** (law of total probability — "if the EU regulation lands, 70%; if not,
  15%; P(regulation)=30% → 0.3·70 + 0.7·15"). Scenario mixing is not opinion pooling; don't use
  odds-space math there.
- **Extremization: skip it at MVP.** [Principled extremizing](https://forum.effectivealtruism.org/posts/biL94PKfeHmgHY6qe/principled-extremizing-of-aggregated-forecasts)
  needs a track record showing systematic underconfidence; we have none yet. Log forecasts now
  (§5), revisit when ≥30 have resolved.

---

## 3. Disagreement is signal — reconcile, don't just average

When passes diverge sharply, the [AIA Forecaster](https://arxiv.org/pdf/2511.07678) pattern: a
**supervisor step reads the divergent rationales, identifies the crux, and gets one clarifying
research pass** before final aggregation. Practically:

1. Diff the rationales — do the passes disagree on a *fact* (checkable: check it), a *reference
   class* (keep both, report the spread), or a *judgment weight* (aggregate and name it as the
   crux)?
2. A factual disagreement that survives a check means the evidence is genuinely thin — widen the
   reported interval; do not let the supervisor silently pick a side.
3. Report the crux in the output. "Passes split 15% vs 45% depending on whether the 2025 Dutch
   covenant counts as precedent" is the most decision-useful sentence in the report.

Do NOT let passes see each other's numbers before they commit — anchoring destroys the
independence that makes aggregation work. (Same reason Samotsvety forecasts before discussing.)

---

## 4. Reporting: ranges + drivers, never false precision

The number alone is nearly worthless to a decision-maker — the
[rationale-shaped hole](https://forum.effectivealtruism.org/posts/qMP7LcCBFBEtuA3kL/the-rationale-shaped-hole-at-the-heart-of-forecasting)
critique. Every forecast ships as:

1. **Headline: central estimate + an honest interval**, e.g. "28% (plausible range 15–45%)."
   The interval is the spread of defensible views after aggregation — passes' spread widened by
   known unmodeled uncertainty — not a bogus formal CI. Two significant figures maximum;
   "28%," never "27.4%."
2. **The resolution criteria as forecast** (from `02` §1), including the stated proxy bias.
3. **Base rate and reference class** — the anchor and the count behind it.
4. **Top 3 drivers, signed** — "signed covenant elsewhere (+), no DE precedent (−), CEO turnover
   risk (−)." These are what Chris can argue with.
5. **The crux** — the single consideration that would most move the number, and what evidence
   would settle it. For counterfactual pairs: both branches + the delta with its own range
   (`02` §3), with the delta's crux called out separately.
6. **Key facts with URLs actually fetched** (facts / reasons / models, per the rationale-hole
   post) — and honest negatives listed as findings. House rule: nothing from pretraining memory;
   if research didn't find it, it isn't in the rationale.
7. **Confidence-in-the-forecast note, one line** — resilient (well-counted reference class,
   converging passes) or fragile (no precedent, passes diverged, resolution proxy weak).
   A 30% can be sturdy or wobbly; Chris needs to know which.

**Language discipline:** round numbers, monotone words ("roughly," "order of"), and never
"likely" without a number attached — vague verbal probabilities are how forecasts get
retroactively rules-lawyered.

---

## 5. Log everything for future calibration

Every forecast gets persisted (question text, resolution criteria, deadline, per-pass numbers,
aggregate, interval, date). This is non-negotiable plumbing: it is the only path to ever knowing
our calibration, the prerequisite for principled extremization (§2), and it doubles as the
M&E hook — grant-writeup predictions already exist in this repo; forecasts should land in the
same reviewable stream so resolved questions turn into Brier scores automatically.

---

## Sources

**AI forecasters:** [ACX: The AI Superforecasters Are Here](https://www.astralcodexten.com/p/the-ai-superforecasters-are-here) ·
[FutureSearch](https://futuresearch.ai/) ([evals](https://evals.futuresearch.ai/), [BTF-2 benchmark paper](https://arxiv.org/html/2604.26106v1), [Kalshi case study](https://futuresearch.ai/blog/kalshi-forecaster-case-study/)) ·
[Preseen](https://preseen.com/) ·
[AIA Forecaster technical report](https://arxiv.org/pdf/2511.07678) ·
[Halawi et al., Approaching Human-Level Forecasting with LMs](https://arxiv.org/abs/2402.18563) ·
[Metaculus Q2 AI Benchmark results](https://forum.effectivealtruism.org/posts/F2stjK9wHSy3HPEC9/q2-ai-benchmark-results-pros-maintain-clear-lead) ·
[forecasting-tools framework](https://github.com/Metaculus/forecasting-tools) ·
[metac-bot-template](https://github.com/Metaculus/metac-bot-template) ·
[Granade, Building an AI prediction bot](https://faintsignals.substack.com/p/building-an-ai-prediction-bot) ·
[Build a Metaculus bot in 30 minutes](https://forum.effectivealtruism.org/posts/Ytkvcvq4qQkwPsEPH)

**Craft:** [Tetlock's Ten Commandments](https://goodjudgment.com/philip-tetlocks-10-commandments-of-superforecasting/) ·
[Metaculus question-writing guidelines](https://www.metaculus.com/question-writing/) ·
[Samotsvety](https://samotsvety.org/blog/) ([EA Forum topic](https://forum.effectivealtruism.org/topics/samotsvety-forecasting)) ·
[LessWrong forecasting worksheet](https://www.lesswrong.com/posts/WAjPGK8pSTCM7Lca5/introduction-to-forecasting-worksheet) ·
[The Rationale-Shaped Hole](https://forum.effectivealtruism.org/posts/qMP7LcCBFBEtuA3kL/the-rationale-shaped-hole-at-the-heart-of-forecasting)

**Aggregation:** [Sevilla, geometric mean of odds](https://forum.effectivealtruism.org/posts/sMjcjnnpoAQCcedL2/when-pooling-forecasts-use-the-geometric-mean-of-odds) ·
[Sempere, how to aggregate forecasts](https://forum.effectivealtruism.org/posts/acREnv2Z5h4Fr5NWz/my-current-best-guess-on-how-to-aggregate-forecasts) ·
[Principled extremizing](https://forum.effectivealtruism.org/posts/biL94PKfeHmgHY6qe/principled-extremizing-of-aggregated-forecasts)

**Counterfactuals:** [Founders Pledge Climate Fund additionality](https://forum.effectivealtruism.org/posts/QbLKFRhbQN8JvtWkM/the-founders-pledge-climate-fund-at-2-years) ·
[Counterfactual impact of agents acting in concert](https://forum.effectivealtruism.org/posts/EP8x3vHRQJP57TjFL/the-counterfactual-impact-of-agents-acting-in-concert)

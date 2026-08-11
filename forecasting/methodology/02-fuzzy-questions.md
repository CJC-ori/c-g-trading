# Forecasting fuzzy questions — operationalization and counterfactual pairs

*Companion to `01-forecasting-craft.md`. This file exists because Chris's questions will almost
never arrive Metaculus-clean. They arrive like: "would a German grocery retailer commit to
protein-ratio changes absent this NGO?" — hazy, judgment-y, counterfactual, and decision-loaded.
The tool's first job is to turn that into something forecastable WITHOUT changing what Chris
actually wants to know.*

---

## 1. Operationalize first, forecast second

A forecast on an unoperationalized question is unfalsifiable and therefore worthless — you can
neither score it later nor argue with it now. The
[Metaculus question-writing standard](https://www.metaculus.com/question-writing/): resolution
criteria must be **clear, verifiable, and unambiguous**, defined on *concrete actions or
information*, never on vague public statements. Scott Alexander's ACX piece on
[AI superforecasters](https://www.astralcodexten.com/p/the-ai-superforecasters-are-here) makes the
same point from the consumer side: the value unlock is *"un-rules-lawyerable resolution
criteria"*, and the polite bot behavior is: **if the question isn't well-formed, rewrite it as a
well-formed one and confirm the rewrite before proceeding.**

The rewrite recipe:

1. **Entity, event, threshold, deadline, evidence.** "A German grocery retailer in the top-10 by
   revenue (entity) publicly announces (event) a quantified target for shifting its
   protein sales ratio toward plant-based (threshold: a % target with a date, not aspirational
   language) by 2028-12-31 (deadline), verifiable in a public document or credible press report
   (evidence source)."
2. **State what does NOT count.** Vague ESG language, a pilot in 5 stores, a commitment later
   retracted within 6 months. The "does not count" list is where most ambiguity dies.
3. **Name the edge cases and pick a rule** (Metaculus: specify what happens if assumptions are
   violated — merged retailer, changed metric, paywalled evidence). One line each is enough.
4. **Accept imperfection explicitly.** For internal decision-forecasts an imperfect-but-stated
   criterion beats a perfect-but-unstated one. Write: *"Resolution proxy: public quantified
   target. This under-counts private commitments; we accept that bias and note its direction."*
   Naming the proxy's bias direction is mandatory — it tells the reader which way the true
   probability sits relative to your number.

**The headline question and the resolution criteria must actually match.** If the operationalized
version has drifted from what Chris asked ("commit to changes" became "publish a % target"),
surface the drift in one sentence. He is deciding on the real question, not the proxy.

---

## 2. Decomposing a judgment-y question into forecastable sub-questions

NVF-specific: intervention questions often need BOTH climate-relevant and animal-welfare-relevant sub-questions — food-system interventions can shift consumption between species (beef→chicken), so when the intervention touches food systems, decompose for both dimensions and flag substitution effects explicitly.

When the question resists direct reference-classing, decompose along one of three axes:

**a) Causal-chain decomposition** (best for policy/corporate outcomes) — 3–5 conditional links,
per `01-forecasting-craft.md` §3. Each link should be individually researchable: "is this on the
retailer's agenda" has evidence (annual reports, trade press, NGO campaign trackers); the
holistic question has only vibes.

**b) Actor-by-actor decomposition** (best for "will anyone in class X do Y") — P(at least one) =
1 − Π(1 − pᵢ) over the named actors. Forces you to actually list the top-10 German retailers and
notice that two (typically the ones with existing covenant signatures) carry most of the
probability mass. Watch correlation: retailers copy each other, so the independent-actor formula
is an *upper* bound; note that and shade down.

**c) Precedent decomposition** (best when the event type has history elsewhere) — split into
"has this happened in an adjacent geography/sector?" (a checkable fact, often the dominant
evidence) and "what's the DE-specific transfer rate?" (the residual judgment call). This
concentrates the irreducible judgment into the smallest possible sub-question — which is the
whole point of decomposition: **isolate the hazy part, make it small, and let evidence carry
everything else.**

Publish the decomposition in the output. Per the
[rationale-shaped-hole argument](https://forum.effectivealtruism.org/posts/qMP7LcCBFBEtuA3kL/the-rationale-shaped-hole-at-the-heart-of-forecasting),
a bare probability is nearly useless to a decision-maker; the decomposition with per-link
numbers *is* the deliverable, because Chris can disagree with one link and re-derive.

---

## 3. Counterfactual pairs — the NVF signature move

Most of Chris's forecasting questions are really **additionality** questions: P(outcome | status
quo) vs P(outcome | NVF funds this NGO's campaign). Handle them as an explicit pair, never as a
single mushy "will the NGO succeed" question.

**Rules for pairs:**

1. **Same resolution criteria for both branches, verbatim.** The only thing that differs is the
   conditioning. If the criteria drift between branches the delta is meaningless.
2. **Forecast the status-quo branch FIRST**, with its own research pass and reference class
   ("how often do top-10 EU retailers adopt quantified protein targets per 3-year window with
   ordinary NGO-ecosystem pressure?"). Doing the intervention branch first anchors you high —
   advocacy narratives are engineered to make the intervention feel pivotal.
3. **The deliverable is the delta and its range**, e.g. "status quo 20% [10–35]; with funded
   campaign 32% [18–50]; Δ ≈ +12pp [0–25]." Note that the delta's low end includes ~0 — that
   honesty is exactly what a funder needs, and it feeds the BOTEC directly
   (Δpp × impact-if-happens × dollars is the cost-effectiveness bridge; see
   `docs/botec-methodology.md` on counterfactuals as the largest single lever).
4. **Decompose the counterfactual with the [Founders Pledge additionality
   triad](https://forum.effectivealtruism.org/posts/QbLKFRhbQN8JvtWkM/the-founders-pledge-climate-fund-at-2-years):**
   - *Funding additionality* — would someone else fund this campaign anyway?
   - *Activity additionality* — would another org run roughly this campaign anyway? (In a
     crowded advocacy space, marginal effort substitutes for work that would happen regardless.)
   - *Outcome additionality* — would the outcome arrive by another route anyway (EU regulation,
     consumer trends, retailer economics)? Often the dominant term: sometimes the most likely
     counterfactual is not "someone else does it" but "it simply doesn't happen."
5. **Beware shared-credit double counting.** Policy wins are produced by coalitions
   ([the concert problem](https://forum.effectivealtruism.org/posts/EP8x3vHRQJP57TjFL/the-counterfactual-impact-of-agents-acting-in-concert)):
   if you'd credit each of five NGOs with +12pp for the same win, your model has invented 60pp
   of counterfactual impact. Ask "what does THIS org add to the existing coalition," not "could
   this org tell a story about the win."
6. **Causal, not correlational, conditioning.** The with-intervention branch means "conditional
   on the campaign being funded and run," not "in worlds where funding happens" (those worlds
   differ in other ways — e.g. they're worlds where the org was already impressive).
   FutureSearch added [explicitly-causal conditional
   forecasting](https://www.astralcodexten.com/p/the-ai-superforecasters-are-here) for exactly
   this confusion. State the intervention concretely (budget, duration, activities) so both
   branches are well-defined.

---

## 4. When even the pair is too hazy

Fallback ladder, in order:

1. **Shrink the deadline or threshold** until history contains ≥5 reference events, forecast
   that, then state the direction of extrapolation back to the original question.
2. **Forecast a nearer observable proxy** on the causal path ("retailer publicly discusses
   protein split in its next annual report") — a leading indicator Chris can watch, plus a
   stated conversion judgment from proxy to outcome.
3. **Refuse the number, deliver the structure.** If irreducible uncertainty dominates (novel
   actor class, no precedent, resolution unverifiable), say "40–60%, evidence cannot narrow
   further; the crux is X" — per `01` §8 triage, an honest wide interval with a named crux
   is a *finding*, and false precision here would be actively harmful.

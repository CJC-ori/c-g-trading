# How to forecast well — the core craft

*Distilled 2026-08-02 from Tetlock's superforecasting research, Metaculus/Samotsvety practice,
the 2024–2026 AI-forecasting-bot literature (Halawi et al., AIA Forecaster, FutureSearch,
Metaculus AI Benchmark), and EA Forum / LessWrong practitioner guides. Sources hyperlinked
inline and collected in `03-passes-aggregation-uncertainty.md` §Sources.*

**Who this is for:** a Claude Code session about to produce a probability forecast for Chris —
usually on a hazy grantmaking question ("will this policy pass", "would this retailer commit
absent the NGO"). Read this before forecasting anything. Companion files: `02-fuzzy-questions.md`
(operationalizing judgment-y questions), `03-passes-aggregation-uncertainty.md` (passes,
aggregation, and honest reporting).

---

## 1. The one-sentence summary of the entire literature

**Start from the outside view (a base rate over a reference class), adjust with the inside view
(case-specific evidence), decompose what you can, run multiple independent passes, aggregate,
and report a range with named drivers — never a lone confident number.**

Every serious practitioner — [Tetlock's superforecasters](https://goodjudgment.com/philip-tetlocks-10-commandments-of-superforecasting/),
[Samotsvety](https://samotsvety.org/blog/), the top
[Metaculus AI Benchmark bots](https://forum.effectivealtruism.org/posts/F2stjK9wHSy3HPEC9/q2-ai-benchmark-results-pros-maintain-clear-lead),
[FutureSearch](https://futuresearch.ai/), the
[AIA Forecaster](https://arxiv.org/pdf/2511.07678) — converges on this same loop. The rest of
this file is the loop in detail.

---

## 2. Outside view FIRST. Always.

The single most reliable accuracy gain in the whole literature. Before touching any specifics of
the case:

1. **Name the reference class out loud.** "Corporate protein-ratio commitments by large European
   grocery retailers, 2018–2026." Get creative — Tetlock's superforecasters *"conduct creative
   searches for comparison classes even for seemingly unique events."* Nothing is unique; it is
   at worst a member of several awkward classes at once.
2. **Count.** How many members of the class? How many resolved YES? That fraction is your anchor.
   If you cannot count, estimate the count and say so — a rough base rate beats no base rate.
3. **Only then open the case file.** Adjust from the anchor with named, case-specific evidence.
   Each adjustment gets a direction, a rough magnitude, and a reason ("retailer already signed
   the Dutch covenant: +10–15pp").

The failure mode this prevents is the story-driven forecast: the inside view is vivid, the
narrative hangs together, and you emit 80% for something whose reference class resolves YES 15%
of the time. The [AIA Forecaster report](https://arxiv.org/pdf/2511.07678) lists "insufficient
base-rate incorporation" as a primary cause of volatile, badly calibrated estimates; so does
every post-mortem of a bad bot.

When several reference classes disagree (they will), keep 2–3 and treat the spread as
information, not annoyance — this is the forecasting version of the BOTEC rule "value it 2–3
independent ways" (`docs/botec-methodology.md` §2).

---

## 3. Fermi-decompose, then multiply carefully

Break the question into parts you can actually estimate
([commandment #2](https://fs.blog/ten-commandments-for-superforecasters/)). For a policy/corporate
outcome the canonical decomposition is a short causal chain of conditionals:

> P(commitment by 2028) = P(topic stays on retailer's agenda) × P(board approves a target |
> on agenda) × P(the approved target meets our resolution bar | approved)

Rules for the chain:

- **3–5 links, no more.** Long multiplicative chains manufacture fake precision and drive every
  answer toward 0. If you find yourself with seven conditionals, your question is over-specified
  or your links are not really independent.
- **Check conditional independence.** The classic error is multiplying probabilities that share a
  common driver (a pro-protein-transition CEO makes *every* link more likely — the links are
  correlated and the naive product is too low).
- **Sanity-check the product against the holistic outside view.** If the chain says 3% and the
  reference class says 20%, the chain is probably missing a pathway (things can resolve YES by
  routes you did not model). Reconcile before shipping. Sum over pathways, not just the one you
  find most vivid.

---

## 4. The evidence pass: research like a bot that wins

What the measurably-good systems actually do
([Halawi et al.](https://arxiv.org/abs/2402.18563), the [AIA Forecaster's
adaptive-search agents](https://arxiv.org/pdf/2511.07678),
[FutureSearch's research process](https://futuresearch.ai/blog/kalshi-forecaster-case-study/), the
[top benchmark bots](https://forum.effectivealtruism.org/posts/F2stjK9wHSy3HPEC9/q2-ai-benchmark-results-pros-maintain-clear-lead)):

- **Search adaptively, not once.** Let each query condition on what the last one returned. Chase
  the specific crux ("has any German retailer set a protein-split target?") not the general topic.
- **News recency is a trap.** "Over-reliance on recent news created recency bias" is a named
  failure in the [AIA report](https://arxiv.org/pdf/2511.07678). One dramatic article is an
  argument for a 5–15pp adjustment, not a rewrite of the base rate.
- **Argue both directions explicitly.** The single best-performing prompt element in [Granade's
  bot build](https://faintsignals.substack.com/p/building-an-ai-prediction-bot) was *"list the
  reasons why the resolution might be NO."* Generate the strongest case for YES and the strongest
  case for NO before numbers.
- **Source-ground everything.** House rule, same as everywhere in this repo: if research didn't
  find it, say "not found" — a plausible invented fact is the worst outcome. Honest negatives
  ("no German retailer has ever published a protein-ratio target — searched three query shapes")
  are load-bearing findings that usually *lower* the forecast.

---

## 5. Premortem before you commit

Once you have a number, assume it resolved the other way and write two sentences on the most
likely reason. This is the cheapest debiasing step there is
([LessWrong forecasting worksheet](https://www.lesswrong.com/posts/WAjPGK8pSTCM7Lca5/introduction-to-forecasting-worksheet)):
it reframes "how might I be wrong?" in a way that dodges attachment to the bottom line. If the
premortem surfaces a pathway you never priced (e.g. "an EU-level regulation makes the commitment
moot"), go back and price it.

---

## 6. Calibration norms

- **Well-calibrated ≠ conservative, and ≠ timid.** Use the whole probability scale. Tetlock's
  superforecasters are *granular* — they distinguish 12% from 18% and it shows up in Brier
  scores. Do not round every hard question to 50%, and do not hedge toward the middle out of
  politeness ("hedging behaviors without explicit calibration targets degraded performance" —
  [AIA](https://arxiv.org/pdf/2511.07678)).
- **But respect the tails.** Sub-5% and above-95% claims need either a strong structural argument
  (a hard deadline that cannot physically be met) or a large counted reference class. LLMs are
  known to resist the extremes *and* to overshoot them when narrative momentum builds; both are
  errors.
- **Scope-sensitivity check, every time.** Before shipping, re-ask the question with the scope
  dialed: would my number move if it were 2030 instead of 2028? One retailer instead of any
  German retailer? If the answer wouldn't move, you are pattern-matching to the vibe of the
  question, not forecasting the question. This is the classic
  [scope-insensitivity](https://www.lesswrong.com/w/forecasting-and-prediction) failure and LLMs
  are notably prone to it.
- **Never let the wording anchor you.** "Would X happen?" and "Would X fail to happen?" must sum
  to 1. When a question is high-stakes, actually forecast the complement as a check.

---

## 7. Belief updating

Forecasts are living objects. When new evidence lands, update in small increments — *"belief
updating is to good forecasting as brushing and flossing are to good dental hygiene"*
([Tetlock](https://goodjudgment.com/philip-tetlocks-10-commandments-of-superforecasting/)).
Overreaction to news and underreaction to structural change are the two symmetric sins. In this
repo: a re-run of the forecast tool on the same question should *diff against the previous run*
and justify the delta with the specific new evidence, not silently re-derive from scratch.

---

## 8. Triage — when NOT to forecast

Tetlock's commandment #1. Some questions are clock-like (forecast is nearly deterministic —
just look it up), some are cloud-like beyond anyone's ability (say "roughly 50%, low value of
effort"), and the payoff zone is in between. For Chris's use case: if the question's answer
would not change the grant decision at either end of your plausible range, say so and stop —
that is a finding worth more than the forecast.

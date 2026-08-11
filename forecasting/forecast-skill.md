---
description: Produce a calibrated probability forecast on a grantmaking question — reference-class-first, 2-3+ independent estimation passes aggregated by geometric mean of odds, explicit resolution criteria, status-quo vs with-grant pair for intervention questions. Persists to research_notes (kind='forecast') and renders on the org/person page Case Notes.
---

# /forecast — a defensible probability on a hazy question

**Read all three methodology files before forecasting anything** — they are the
reasoning; this file is only the harness:

- `tools/forecast/methodology/01-forecasting-craft.md` — outside view first, Fermi
  decomposition, premortem, calibration norms, when NOT to forecast.
- `tools/forecast/methodology/02-fuzzy-questions.md` — operationalizing hazy questions,
  decomposition axes, counterfactual pairs (the NVF signature move).
- `tools/forecast/methodology/03-passes-aggregation-uncertainty.md` — independent passes,
  geometric mean of odds, disagreement handling, the report format.

**Args** (`$ARGUMENTS`): a question in plain language, optionally with entity context
("…for the GFI grant", an org/person/grant name or id). Questions arrive hazy —
"would the retailer commit absent this NGO?" — that's expected; operationalizing
them is Phase 1's job. Resolve names → ids yourself (`search_orgs` / `search_grants`
/ `search_people`); never guess an id.

**Model gate: fable/opus-class judgment required.** A confidently wrong probability
inherits the authority of the whole apparatus. If running as haiku/sonnet, stop and
say so.

---

## Task

Open a `tool_runs` trace first (`log_tool_run`, tool_name `forecast`, org-linked when
anchored) and close it in Completion whatever happens (`close_tool_run` — never leave
a stuck row).

**1. Operationalize** (02 §1). Rewrite the question into entity / event / threshold /
deadline / evidence-source + a "does not count" list + the proxy's bias direction.
If the operationalized version drifts from what Chris asked, surface the drift in one
sentence — he decides on the real question, not the proxy. If the question is
clock-like (just look it up) or valueless at both ends of the plausible range, say so
and stop (01 §8) — that finding IS the deliverable.

**2. Pull context — the right amount.** Decompose-with-context: gather what bears on
*this* question, not everything that exists. For entity-anchored questions:
`get_org_dossier` (or `get_person`) for the internal record — research findings,
meetings, claims, prior evaluations; check `research_notes` for prior forecasts on
the same question (`uv run python -c "..." tools/notes/store_note.list_notes` or
`run_query`). For ecosystem questions, the DB may have nothing — say so and lean on
research. Build a **shared dossier**: the base-rate hunt ("name the reference class
and count it"), current state, the strongest YES case, the strongest NO case, honest
negatives logged ("searched three query shapes, no German retailer has ever…").
Free-first fetch ladder; every load-bearing fact needs a URL actually fetched or a DB
row id — nothing from pretraining memory.

**3. Independent estimation passes — the structure that is not optional.** Dispatch
**2–3 parallel subagents in a single message** (more if they diverge). Each pass gets:
the operationalized question, the shared dossier, the three methodology files, and a
distinct entry stance —

- *outside-view-first*: anchor on the reference-class count, adjust with signed,
  sized case evidence;
- *causal-chain-first*: 3–5 conditional links, checked for correlated links,
  sanity-checked against the holistic outside view;
- *adversarial*: build the strongest case for the unpopular side first, then price it.

Passes must NOT see each other's numbers (03 §3 — anchoring destroys the
independence that makes aggregation work). Each returns: probability + range,
rationale (with its own premortem sentence), top signed drivers, sources used.

**4. Aggregate + reconcile** (03 §2–3). Geometric mean of odds; Samotsvety-trim the
extremes only at ≥5 passes. If max−min spread exceeds ~25pp, diff the rationales —
fact disagreements get checked, reference-class disagreements widen the interval,
judgment disagreements become the named crux. Run 2 more passes when divergence
persists. Never silently pick a winner. Scenario mixing (mutually exclusive worlds)
uses arithmetic weighting, not odds-space math. No extremization — no track record yet.

**5. Counterfactual pairs** (02 §3) — for any intervention question ("would X happen
if we fund this?"), the deliverable is the PAIR: status-quo branch forecast FIRST
with its own reference class, then the with-grant branch against identical verbatim
resolution criteria, then **Δ with its own range** — whose low end honestly may
include ~0. Decompose the counterfactual with the Founders Pledge triad (funding /
activity / outcome additionality) and watch shared-credit double counting: ask what
THIS org adds to the existing coalition. The Δ is what feeds `/botec` as the
additionality parameter.

**6. Scope-sensitivity check before shipping** (01 §6): dial the deadline and
threshold; if the number doesn't move, you pattern-matched the vibe — go back.

## Persist

Store through the strict wrapper (NOT bare store_note — it enforces the forecast
contract: substantive criteria, in-bounds probabilities, ≥2 logged passes, pair
completeness):

```bash
echo '{
  "question": "<the operationalized headline question>",
  "answer_md": "<full reasoning: base rate + count, decomposition with per-link numbers, signed drivers, crux, what-would-change-this, premortem, honest negatives — cite DB row ids and fetched URLs inline>",
  "source_urls": ["<every URL actually fetched and load-bearing>"],
  "organization_id": "<uuid or omit>", "person_id": "...", "grant_id": "...",
  "source_tool": "forecast", "created_by": "<model>",
  "details": {
    "resolution_criteria": "<entity/event/threshold/deadline/evidence + does-not-count + proxy bias direction>",
    "probability_or_range": "28% (range 15-45%)",
    "base_rate": "<reference class + count>",
    "key_drivers": [{"driver": "...", "direction": "up|down"}, ...],
    "status_quo": "20% (range 10-35%)",        // pairs only — else omit both
    "with_intervention": "32% (range 18-50%)", // pairs only
    "delta": "+12pp (range 0-25pp)",           // pairs only
    "per_pass": [{"stance": "outside-view-first", "probability": "25%",
                   "rationale_summary": "<one line>"}, ...],
    "crux": "<the single consideration that would most move the number>",
    "review_by_date": "YYYY-MM-DD"
  }
}' | uv run python tools/forecast/store_forecast.py
```

Probability format is canonical: `"CENTRAL% (range LO-HI%)"`. `review_by_date` =
when the question resolves or should be revisited (it drives the review-due index).
Re-forecasting the same question later: pass `supersedes_note_id` and justify the
delta against the old note's evidence (01 §7) — never silently re-derive.
Resolution, when it comes, is a superseding note with `details.resolution`.

Then score it, non-blocking, and surface any failed check loudly:

```bash
uv run python tools/evals/run_evals.py --run-id <note_id> --run-type forecast || true
```

## Integration: /grant-writeup predictions

`/grant-writeup` MAY invoke this skill for its 3–5 predictions when a prediction is
load-bearing enough to deserve the full apparatus (typically the headline
outcome-additionality claim). The handshake: grant-writeup passes the grant_id +
its draft prediction text; /forecast returns the operationalized criteria +
aggregated probability, stores the forecast note anchored to the SAME grant_id and
organization_id, and grant-writeup then writes its prediction row using the
forecast's central estimate (rounded to 5s, per its own format) and cites the note
id in the prediction text. One source of truth: the note carries the reasoning; the
prediction row stays writeup-native. Most writeup predictions do NOT need this —
reserve it for the 1–2 that would change the grant decision if wrong.

## Guardrails

- **Never a bare number.** Range + drivers + crux + resolution criteria, always
  (the rationale-shaped hole). Two significant figures max; "28%", never "27.4%".
- **Source-grounded or absent.** If research didn't find it, it isn't in the
  rationale. Honest negatives are findings.
- **Independence is sacred**: no pass sees another's number before committing.
- **Pairs share verbatim criteria** across branches or the delta is meaningless.
- An honest "40–60%, evidence cannot narrow further, the crux is X" beats false
  precision — refusing the narrow number is a valid output (02 §4).
- Do not modify `/research` or `/evaluate` behavior from this skill.

## Exit form (report back)

1. **Headline**: central + range (both branches + Δ for pairs), and the
   fragile/resilient one-liner.
2. **Resolution criteria as forecast** + review-by date + proxy bias direction.
3. **Base rate** (reference class + count), **top signed drivers**, **the crux**,
   and what-would-change-this.
4. **Per-pass table** (stance / probability / one-line rationale) + the aggregation
   rule applied.
5. The `research_notes` id, where it renders (org/person page → Case Notes), and
   the eval scorecard line (any failure stated loudly).
6. `tool_runs` trace closed (status noted).

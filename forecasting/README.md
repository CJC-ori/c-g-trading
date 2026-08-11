# The forecasting engine (lifted from Chris's grantmaking system)

Everything in this directory was extracted from the internal tooling Chris built to
make calibrated probability forecasts on grantmaking questions ("will this policy
pass by 2028?", "would this org get funded without us?"). It's the candidate
**backbone** for the trading bot: the part that turns a hazy question into a
defensible probability. None of it knows anything about markets or trading — that's
the part we'd build new.

## What's here and what it's worth to us

| File | What it is | Portable? |
|---|---|---|
| `methodology/01-forecasting-craft.md` | The core craft doc: outside view first, reference classes, Fermi decomposition, premortems, calibration norms, when NOT to forecast | **Yes — this is the crown jewel.** Written to be handed to LLM agents as their operating manual |
| `methodology/02-fuzzy-questions.md` | How to operationalize vague questions into resolvable ones (entity/event/threshold/deadline + "does not count" list). Less critical for trading (market rules ARE the resolution criteria) but the proxy-bias thinking still applies | Mostly |
| `methodology/03-passes-aggregation-uncertainty.md` | The multi-agent structure: 2–3+ independent estimation passes with distinct stances (outside-view-first / causal-chain-first / adversarial), aggregated by **geometric mean of odds**, with rules for handling disagreement. Also carries the full reference-link list | **Yes — this is the architecture.** |
| `forecast-skill.md` | The orchestrator prompt that runs the whole pipeline as a Claude Code slash command: operationalize → gather context → dispatch independent passes in parallel → aggregate → sanity-check → persist | Yes, as a design template — strip the NVF-specific DB steps |
| `store_forecast.py` | Strict validation + persistence wrapper. The **validation logic is pure Python and portable**: probability parsing, range ordering, in-(0,100) enforcement, ≥2-independent-passes requirement, counterfactual-pair completeness | Validation yes; persistence targets NVF's Postgres (via `nvf-deps/store_note.py`) and won't run here |
| `test_store_forecast.py` | Unit tests for the validation logic | Yes |
| `evals/forecast_quality.py` + test | Automated quality scoring of a stored forecast (checks for base rate present, drivers signed, criteria substantive, etc.) | The checks are portable; the DB plumbing isn't |
| `nvf-deps/store_note.py` | NVF's notes-table persistence layer — included only so `store_forecast.py`'s import chain is visible | No — reference only |

## The design in one paragraph

A question comes in hazy. Phase 1 rewrites it into strict resolution criteria.
Phase 2 builds a shared evidence dossier — base-rate hunt first ("name the
reference class and count it"), then current-state evidence, strongest YES case,
strongest NO case, with honest negatives logged; every load-bearing fact needs a
fetched URL, nothing from model memory. Phase 3 dispatches 2–3 **independent**
LLM passes that never see each other's numbers (anchoring destroys the
independence that makes aggregation work), each with a different entry stance.
Phase 4 aggregates by geometric mean of odds and treats a >25-point spread as a
signal to investigate, not average away. Output is never a bare number: always
central + range + signed drivers + the crux + what-would-change-this.

For trading, the obvious adaptation: the market price becomes the thing the
forecast is compared *against*, the resolution criteria come free from the market
rules, and a new decision layer (edge threshold, Kelly sizing, fee model, timing)
sits on top.

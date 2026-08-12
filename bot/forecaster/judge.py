"""Tier 3 — the judge: independent passes with distinct stances, aggregated
by geometric mean of odds.

Design DNA (forecasting/methodology/):
- 01: outside view FIRST — name the reference class and count it; 3–5-link
  causal chains, checked for correlated links; premortem before committing;
  scope-sensitivity check; respect the tails but use the whole scale.
- 03: never ship a single pass; independent passes must not see each other's
  numbers; aggregate by geometric mean of odds; >25pp spread = named crux.

FutureSearch guards baked into the prompt (research/futuresearch.md §3.1):
the contamination guard, status-quo weighting, assume-not-yet-resolved,
2–4 named uncertainties, and the 3–97 calibration rails.

Output is structured JSON parsed strictly: **parse failure ⇒ no trade**
(research/oss-forecasters.md §7 — a silent 0.5 is a position).
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Sequence

from bot.forecaster.llm import LLMClient, LLMRequest, LLMResponse

P_FLOOR = 0.03
P_CEIL = 0.97
DIVERGENCE_PP = 0.25

JUDGE_SYSTEM = """\
You are an expert forecaster being evaluated for a prestigious forecasting
position. Your forecast will be graded on Brier score across hundreds of
questions. You never use tools.

HARD RULES:
- Treat TODAY as the AS-OF date given in the message. You know nothing after
  that date.
- Do NOT do any additional web research. Only use the information already
  provided (the dossier) plus timeless background knowledge.
- Do not trust any research that includes information from after the AS-OF
  date. If the AS-OF date is Feb 2026, do NOT trust research that tells you
  what a website says in Dec 2026 — treat that item as hallucinated and
  disregard everything that particular piece of research says.
- If you are being asked this question, assume it has NOT yet resolved. If
  resolution seems ambiguous, forecast as if the resolution threshold has
  not been met.
- When sources contradict (e.g. 5 say one thing, 1 says another), trust the
  majority but add considerable uncertainty.
- Put extra weight on the status quo outcome, since the world changes slowly
  most of the time.
- Even if something seems impossible, never forecast less than 3%. Even if
  something seems certain, never forecast more than 97%.
- Before committing, write a one-sentence premortem: assume you are wrong —
  what most likely made you wrong?
- End with EXACTLY this JSON object on its own line, no markdown fence:
{"p_yes": <integer 3-97>, "key_uncertainties": ["<2-4 items>"], "premortem": "<one sentence>"}
"""

STANCE_OUTSIDE = """\
STANCE (follow this reasoning order strictly):
1. OUTSIDE VIEW FIRST. Name 1-3 reference classes for this question out loud
   and estimate the base rate of YES in each (count members if you can; a
   rough count beats none). Anchor on that rate.
2. Only then adjust with case-specific evidence from the dossier — each
   adjustment gets a direction, a rough size in percentage points, and a
   named reason.
3. Sanity-check: does your adjusted number stay rooted to the base rate? If
   you moved more than ~25pp from the anchor, justify why the case evidence
   is strong enough (multiple independent reliable sources or a direct
   causal mechanism), or move back.
"""

STANCE_CAUSAL = """\
STANCE (follow this reasoning order strictly):
1. CAUSAL CHAIN FIRST. Lay out the 3-5 conditional links that must hold for
   YES (or for NO, whichever is the change from status quo), with a
   probability for each link. Check the links for shared drivers — if two
   links are correlated, do not multiply them naively.
2. Compute the chain product, then sanity-check it against a holistic
   outside view: if the chain says 3% and the reference class says 20%, the
   chain is probably missing a pathway — sum over pathways, not just the
   vivid one.
3. Consistency line: write "{{your p}} out of 100 times, {{the resolution
   criteria}} happens" and check it feels right at that frequency.
"""

JUDGE_PROMPT = """\
AS-OF DATE: {asof}
(You are forecasting as of this date. The question resolves by {close}.)

QUESTION: {question}
RESOLUTION CRITERIA: {criteria}
{market_line}
{stance}

DOSSIER (compiled under point-in-time discipline; only pre-AS-OF sources):

{dossier}

Reason step by step per your stance, then output the JSON line.
"""


@dataclass(frozen=True)
class JudgePass:
    stance: str
    p_yes: float | None          # in [0,1]; None = parse failure (no trade)
    key_uncertainties: tuple[str, ...] = ()
    premortem: str = ""
    raw_text: str = ""
    parse_failed: bool = False


@dataclass
class JudgeVerdict:
    p_yes: float | None          # aggregated; None = no valid pass (no trade)
    passes: list[JudgePass] = field(default_factory=list)
    divergent: bool = False
    n_parse_failures: int = 0

    @property
    def tradeable(self) -> bool:
        return self.p_yes is not None


_JSON_LINE = re.compile(r"\{[^{}]*\"p_yes\"[^{}]*\}", re.S)


def parse_judge_response(text: str, stance: str) -> JudgePass:
    """Last JSON object with a p_yes wins; anything malformed ⇒ no trade."""
    matches = _JSON_LINE.findall(text)
    for raw in reversed(matches):
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        p = obj.get("p_yes")
        if isinstance(p, bool) or not isinstance(p, (int, float)):
            continue
        raw_p = p
        p = float(p)
        if not 0 <= p <= 100:
            continue
        # The prompt contract is integer percent (3-97). Integers are percent;
        # a float <= 1.0 is read as a probability (a model that answered
        # "0.25" meant 25%, not 0.25%); floats > 1.0 are percent.
        if isinstance(raw_p, int) or p > 1.0:
            p = p / 100.0
        p = min(max(p, P_FLOOR), P_CEIL)
        unc = obj.get("key_uncertainties") or []
        if not isinstance(unc, list):
            unc = [str(unc)]
        return JudgePass(
            stance=stance, p_yes=p,
            key_uncertainties=tuple(str(u) for u in unc[:4]),
            premortem=str(obj.get("premortem", "")), raw_text=text,
        )
    return JudgePass(stance=stance, p_yes=None, raw_text=text, parse_failed=True)


def geometric_mean_of_odds(ps: Sequence[float]) -> float:
    """methodology/03 §2: convert to odds, geometric-mean, convert back."""
    if not ps:
        raise ValueError("no probabilities to aggregate")
    log_odds = [math.log(p / (1.0 - p)) for p in ps]
    mean = sum(log_odds) / len(log_odds)
    o = math.exp(mean)
    return o / (1.0 + o)


def judge_question(
    client: LLMClient,
    dossier: str,
    question: str,
    criteria: str,
    asof: str,
    close: str,
    model: str = "sonnet",
    include_market_price: float | None = None,
    effort: str | None = None,
) -> tuple[JudgeVerdict, list[LLMResponse]]:
    """Two independent stance passes (neither sees the other's number),
    aggregated by geometric mean of odds. Parse failure on a pass drops that
    pass; zero valid passes ⇒ verdict.p_yes is None ⇒ NO TRADE."""
    market_line = (
        f"CURRENT MARKET PRICE: {include_market_price:.0%} — treat as one "
        "informed opinion, not ground truth."
        if include_market_price is not None else ""
    )
    responses: list[LLMResponse] = []
    passes: list[JudgePass] = []
    for stance_name, stance_text in (
        ("outside_view_first", STANCE_OUTSIDE),
        ("causal_chain_first", STANCE_CAUSAL),
    ):
        req = LLMRequest(
            model=model,
            system=JUDGE_SYSTEM,
            prompt=JUDGE_PROMPT.format(
                asof=asof, close=close or "the stated deadline",
                question=question.strip(),
                criteria=(criteria or "(as titled)").strip()[:1200],
                market_line=market_line, stance=stance_text, dossier=dossier,
            ),
            params={"stage": "judge", "stance": stance_name,
                    **({"effort": effort} if effort else {})},
        )
        resp = client.complete(req)
        responses.append(resp)
        passes.append(parse_judge_response(resp.text, stance_name))

    valid = [p.p_yes for p in passes if p.p_yes is not None]
    verdict = JudgeVerdict(
        p_yes=geometric_mean_of_odds(valid) if valid else None,
        passes=passes,
        divergent=(max(valid) - min(valid) > DIVERGENCE_PP) if len(valid) >= 2 else False,
        n_parse_failures=sum(1 for p in passes if p.parse_failed),
    )
    return verdict, responses

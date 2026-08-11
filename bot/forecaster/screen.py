"""Tier 1 — the Haiku screener, adapted from FutureSearch's two screening
prompts (research/futuresearch.md §3.2: the highest-value copy-paste — they
encode adverse-selection avoidance).

Both screens run in ONE batched Haiku call per ~15 questions (shared rubric,
JSON-array output). Parse failure on any element ⇒ that market is REJECTED
(fail closed: "When in doubt, REJECT").
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Sequence

from bot.forecaster.llm import LLMClient, LLMRequest, LLMResponse

SCREEN_SYSTEM = """\
You are a market-screening classifier for a forecasting desk. You never use
tools and never do web research; you judge only from the question text given.
You answer ONLY with the JSON array requested — no prose, no markdown fence.
"""

# Adapted from FutureSearch's INSIDER_SCREEN_TASK + METHODOLOGY_SCREEN_TASK
# (research/futuresearch.md §3.2), merged into one rubric so a batch of
# questions costs one call.
SCREEN_RUBRIC = """\
For each numbered question below, decide whether a research-based forecasting
desk should forecast it. Apply BOTH screens:

SCREEN A — insider/adverse-selection. REJECT if the outcome is:
- a pre-taped or pre-recorded show (reality TV, game shows, pre-recorded
  interviews) or an award whose winner a committee has already decided;
- a celebrity's or private individual's personal decision (what someone will
  wear, say, attend, post, or announce);
- already determined but not yet publicly announced, or known to a small
  group (nominations, private negotiations, unannounced product decisions);
- subject to moral hazard: a low-cost stunt a bettor could perform, a
  self-fulfilling bet, or something a few people can resolve in their own
  favor.
PASS examples: elections and legislation, economic indicators and
central-bank decisions, geopolitics, large-scale outcomes no individual can
influence, scientific/technological milestones, public health, natural
disasters, climate.

SCREEN B — methodology fit. REJECT:
- sports of any kind (pro, college, international, esports, sports awards,
  drafts, trades, coaching changes, fantasy);
- cryptocurrency (price targets, market caps, on-chain metrics, NFTs, meme
  coins) and short-horizon financial price targets (a specific stock/index/
  commodity level by a date);
- video-game, streaming, or influencer minutiae where dedicated fan
  communities have better data.
PASS: elections and nomination races, geopolitics, government policy and
legal outcomes, economic indicators, public-health/science/tech milestones,
company events (IPOs, M&A, leadership changes).

When in doubt, REJECT. It is better to skip a good market than to trade into
one with adverse selection, and better to skip one where specialized traders
have an edge.

Answer with a JSON array, one object per question, in the same order:
[{"i": <number>, "pass": true|false, "reject_code": "insider"|"sports"|
"crypto"|"fin_price"|"taped"|"personal"|"fan_minutiae"|"moral_hazard"|
"other"|null}]

Questions:
"""


@dataclass(frozen=True)
class ScreenVerdict:
    index: int
    passed: bool
    reject_code: str | None
    parse_failed: bool = False


def build_screen_prompt(questions: Sequence[str]) -> str:
    lines = [f"{i + 1}. {q.strip()}" for i, q in enumerate(questions)]
    return SCREEN_RUBRIC + "\n".join(lines)


def parse_screen_response(text: str, n: int) -> list[ScreenVerdict]:
    """Strict-ish parse; any element missing/malformed ⇒ REJECT (fail closed)."""
    m = re.search(r"\[.*\]", text, re.S)
    verdicts: dict[int, ScreenVerdict] = {}
    if m:
        try:
            arr = json.loads(m.group(0))
            for obj in arr if isinstance(arr, list) else []:
                if not isinstance(obj, dict):
                    continue
                i = obj.get("i")
                if not isinstance(i, int) or not (1 <= i <= n):
                    continue
                verdicts[i - 1] = ScreenVerdict(
                    index=i - 1,
                    passed=bool(obj.get("pass") is True),
                    reject_code=obj.get("reject_code"),
                )
        except ValueError:
            pass
    out = []
    for i in range(n):
        v = verdicts.get(i)
        if v is None:
            v = ScreenVerdict(index=i, passed=False, reject_code="parse_failure",
                              parse_failed=True)
        out.append(v)
    return out


def screen_questions(
    client: LLMClient,
    questions: Sequence[str],
    batch_size: int = 15,
    model: str = "haiku",
    workers: int = 4,
) -> tuple[list[ScreenVerdict], list[LLMResponse]]:
    """Run the merged screen over all questions, batched (batches run in
    parallel). Returns verdicts (aligned with input order) + the raw
    responses (for cost charging)."""
    from concurrent.futures import ThreadPoolExecutor

    starts = list(range(0, len(questions), batch_size))

    def one(start: int) -> tuple[int, LLMResponse]:
        chunk = questions[start:start + batch_size]
        req = LLMRequest(
            model=model,
            system=SCREEN_SYSTEM,
            prompt=build_screen_prompt(chunk),
            params={"stage": "screen", "effort": "low"},
        )
        return start, client.complete(req)

    verdicts: list[ScreenVerdict] = []
    responses: list[LLMResponse] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for start, resp in ex.map(one, starts):
            responses.append(resp)
            chunk_len = len(questions[start:start + batch_size])
            for v in parse_screen_response(resp.text, chunk_len):
                verdicts.append(ScreenVerdict(
                    index=start + v.index, passed=v.passed,
                    reject_code=v.reject_code, parse_failed=v.parse_failed,
                ))
    verdicts.sort(key=lambda v: v.index)
    return verdicts, responses

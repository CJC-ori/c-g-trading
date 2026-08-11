"""The funnel controller — owns the tier gates, the cost ledger, and the
retrieval ledger. No tier calls the next one directly
(research/cost-architecture.md §9 item 1).

Modes:
- ``full``      prefilter -> screen -> retrieval -> dossier -> 2-pass judge
- ``ablation``  same judge, market title only, NO retrieval/dossier — the
  standing no-retrieval column (point-in-time-retrieval.md §5.4 item 4):
  if full barely beats this, the "forecasting" is prior recall.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from bot.backtest.costs import CostLedger
from bot.forecaster.dossier import (
    EvidencePack,
    build_dossier,
    gather_evidence,
    plan_retrieval,
)
from bot.forecaster.judge import JudgeVerdict, judge_question
from bot.forecaster.llm import LLMClient, LLMResponse
from bot.forecaster.retrieval import RetrievalLedger, parse_dt

ABLATION_DOSSIER = (
    "(No research dossier is available for this question. You have only the "
    "question text and your own pre-AS-OF background knowledge. Anchor on "
    "base rates and the status quo.)"
)


@dataclass
class ForecastResult:
    question_key: str
    mode: str                      # full | ablation
    p_yes: float | None            # None = no trade (parse failure etc.)
    verdict: JudgeVerdict | None
    dossier: str = ""
    pack: EvidencePack | None = None
    cost_cents: int = 0
    cache_keys: list[str] = field(default_factory=list)
    error: str | None = None

    def to_row(self) -> dict[str, Any]:
        return {
            "question_key": self.question_key,
            "mode": self.mode,
            "p_yes": self.p_yes,
            "divergent": self.verdict.divergent if self.verdict else None,
            "n_parse_failures": (
                self.verdict.n_parse_failures if self.verdict else None
            ),
            "per_pass": (
                [
                    {"stance": p.stance, "p_yes": p.p_yes,
                     "parse_failed": p.parse_failed}
                    for p in self.verdict.passes
                ] if self.verdict else []
            ),
            "n_sources": self.pack.n_sources() if self.pack else 0,
            "n_quarantined": self.pack.n_quarantined if self.pack else 0,
            "n_gate1_dropped": self.pack.n_gate1_dropped if self.pack else 0,
            "cost_cents": self.cost_cents,
            "error": self.error,
        }


class ForecastPipeline:
    def __init__(
        self,
        client: LLMClient,
        retrieval_ledger: RetrievalLedger,
        cost_ledger: CostLedger | None = None,
        run_id: str = "run",
        judge_model: str = "sonnet",
        dossier_model: str = "sonnet",
        include_market_price: bool = True,
        judge_effort: str | None = None,
    ):
        self.client = client
        self.ledger = retrieval_ledger
        self.costs = cost_ledger or CostLedger()
        self.run_id = run_id
        self.judge_model = judge_model
        self.dossier_model = dossier_model
        self.include_market_price = include_market_price
        self.judge_effort = judge_effort
        self.all_cache_keys: list[str] = []

    # -- internals -----------------------------------------------------------

    def _charge(self, resp: LLMResponse) -> int:
        # Replayed responses are free at run time, but we charge the cached
        # usage anyway so a replay reproduces the original cost figure —
        # cost is part of the result, not of the wall clock.
        self.all_cache_keys.append(resp.cache_key)
        return self.costs.charge(resp.usage, resp.price_model)

    # -- public --------------------------------------------------------------

    def forecast(
        self,
        question_key: str,
        question: str,
        background: str,
        criteria: str,
        asof: str,
        close: str,
        market_p: float | None = None,
        mode: str = "full",
        use_fec: bool = False,
    ) -> ForecastResult:
        cost_before = self.costs.total_cents
        keys_before = len(self.all_cache_keys)
        try:
            if mode == "ablation":
                dossier_text, pack = ABLATION_DOSSIER, None
            else:
                asof_dt = parse_dt(asof)
                if asof_dt is None:
                    raise ValueError(f"bad asof timestamp {asof!r}")
                plan, plan_resp = plan_retrieval(self.client, question, background)
                self._charge(plan_resp)
                pack = gather_evidence(
                    question_key=question_key, question=question,
                    background=background, asof=asof_dt, ledger=self.ledger,
                    run_id=self.run_id, plan=plan, use_fec=use_fec,
                )
                dossier_text, dossier_resp = build_dossier(
                    self.client, pack, question, criteria, background,
                    model=self.dossier_model,
                )
                self._charge(dossier_resp)

            verdict, judge_resps = judge_question(
                self.client, dossier_text, question, criteria,
                asof=asof, close=close, model=self.judge_model,
                include_market_price=(market_p if self.include_market_price else None),
                effort=self.judge_effort,
            )
            for r in judge_resps:
                self._charge(r)
            return ForecastResult(
                question_key=question_key, mode=mode, p_yes=verdict.p_yes,
                verdict=verdict, dossier=dossier_text, pack=pack,
                cost_cents=self.costs.total_cents - cost_before,
                cache_keys=self.all_cache_keys[keys_before:],
            )
        except Exception as e:  # a failed question is NO TRADE, loudly logged
            return ForecastResult(
                question_key=question_key, mode=mode, p_yes=None, verdict=None,
                cost_cents=self.costs.total_cents - cost_before,
                cache_keys=self.all_cache_keys[keys_before:],
                error=f"{type(e).__name__}: {e}",
            )


def save_results(results: list[ForecastResult], path: Path | str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        for r in results:
            fh.write(json.dumps(r.to_row(), ensure_ascii=False) + "\n")

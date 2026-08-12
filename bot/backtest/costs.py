"""CostLedger — truthful LLM inference-cost accounting for backtests.

Per research/cost-architecture.md §7: every LLM strategy must charge REAL
token usage to P&L via ``Strategy.last_inference_cost_cents`` (read by the
engine after every ``on_decision_point``). This module turns Anthropic
``usage`` blocks into cents, never understating (all rounding is up, to
match ``fees``' ceiling convention), and supports:

- prompt-caching prices (cache read 0.1x input; 5m write 1.25x; 1h write 2x);
- Batch API (flat 0.5x on input AND output);
- server-side web search ($10 / 1k searches);
- amortization of shared work (event-level dossiers) across consumers;
- a price-stress knob (``price_multiplier=2.0``) for the inference x2
  stress run (SPEC §7; a strategy whose edge dies at 2x model prices is
  one price change from dead).

Prices fetched 2026-08-11 from platform.claude.com/docs/en/about-claude/
pricing (see research/cost-architecture.md §1.1). Re-verify before trusting
absolute dollars in a new quarter.

Typical wiring inside a strategy::

    ledger = CostLedger()
    ...
    def on_decision_point(self, view, portfolio):
        resp = client.messages.create(...)
        self.ledger.charge(resp.usage, model="claude-sonnet-5")
        ...
        self.last_inference_cost_cents = self.ledger.drain_cents()
        return intents

For shared dossiers: ``charge(usage, model, job_id="dossier-EV1")`` then
``per_head = ledger.amortize("dossier-EV1", n_consumers=12)`` and add
``per_head`` to each consuming market's decision cost instead of the full
job cost (research/cost-architecture.md §7.2 — otherwise bracket #1 looks
unprofitable and #2-12 look free).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

#: $/MTok: (input, cache_write_5m, cache_write_1h, cache_read, output).
#: Cache multipliers per the pricing page: 5m write = 1.25x input,
#: 1h write = 2x input, cache read = 0.1x input.
PRICES: dict[str, tuple[float, float, float, float, float]] = {
    "claude-opus-5": (5.0, 6.25, 10.0, 0.50, 25.0),
    "claude-sonnet-5": (2.0, 2.50, 4.0, 0.20, 10.0),
    "claude-haiku-4-5": (1.0, 1.25, 2.0, 0.10, 5.0),
}

#: Model-id aliases -> PRICES key (dated API ids, dotted variants).
MODEL_ALIASES: dict[str, str] = {
    "claude-haiku-4.5": "claude-haiku-4-5",
}

WEB_SEARCH_USD = 0.010  # per search (verified primary source)
BATCH_MULTIPLIER = 0.5  # flat 50% off input AND output; stacks with caching

_MICROCENTS_PER_CENT = 1_000_000


def resolve_model(model: str) -> str:
    """Map a model id (possibly dated, e.g. claude-opus-5-20260115) to a
    PRICES key. Raises KeyError for unknown models — silence would mean
    silently charging $0, i.e. a lying backtest."""
    if model in PRICES:
        return model
    if model in MODEL_ALIASES:
        return MODEL_ALIASES[model]
    for key in PRICES:
        if model.startswith(key + "-") or model.replace(".", "-").startswith(key):
            return key
    raise KeyError(
        f"unknown model {model!r}: add it to bot.backtest.costs.PRICES "
        "(never default-to-zero an inference cost)"
    )


def _usage_get(usage: Any, key: str, default: int = 0) -> int:
    """Read a usage field from a dict or an SDK object attribute."""
    if usage is None:
        return default
    if isinstance(usage, Mapping):
        v = usage.get(key, default)
    else:
        v = getattr(usage, key, default)
    return int(v or 0)


def _web_searches(usage: Any) -> int:
    stu = (
        usage.get("server_tool_use")
        if isinstance(usage, Mapping)
        else getattr(usage, "server_tool_use", None)
    )
    return _usage_get(stu, "web_search_requests", 0)


@dataclass(frozen=True, slots=True)
class ChargeBreakdown:
    """One charge's exact cost, microcents (1e-6 cents) per component."""
    model: str
    input_mc: int
    cache_write_5m_mc: int
    cache_write_1h_mc: int
    cache_read_mc: int
    output_mc: int
    web_search_mc: int

    @property
    def total_mc(self) -> int:
        return (
            self.input_mc + self.cache_write_5m_mc + self.cache_write_1h_mc
            + self.cache_read_mc + self.output_mc + self.web_search_mc
        )


class CostLedger:
    """Accumulates exact inference costs; reports whole cents, rounded UP.

    Internal precision is microcents so amortization and small Haiku calls
    don't vanish to zero, and the ceiling is applied once per report
    boundary (drain/total), matching fees' never-understate convention.
    """

    def __init__(self, price_multiplier: float = 1.0):
        #: 1.0 normally; 2.0 for the inference-stress run (SPEC §7).
        self.price_multiplier = price_multiplier
        self._total_mc = 0
        self._drained_cents = 0
        self._jobs_mc: dict[str, int] = {}
        self._amortized_jobs: set[str] = set()
        self.charges: list[ChargeBreakdown] = []

    # -- charging ------------------------------------------------------------

    def charge(
        self,
        usage: Any,
        model: str,
        batch: bool = False,
        job_id: str | None = None,
    ) -> int:
        """Charge one API response's usage. Returns the charge in cents
        (ceil — for display; the ledger itself keeps exact precision).

        usage may be an Anthropic SDK usage object or a dict with any of:
        input_tokens, output_tokens, cache_creation_input_tokens (5m
        writes), cache_creation (dict with ephemeral_5m_input_tokens /
        ephemeral_1h_input_tokens), cache_read_input_tokens,
        server_tool_use.web_search_requests.
        """
        key = resolve_model(model)
        in_p, w5_p, w1_p, rd_p, out_p = PRICES[key]
        mult = self.price_multiplier * (BATCH_MULTIPLIER if batch else 1.0)

        cc = (
            usage.get("cache_creation")
            if isinstance(usage, Mapping)
            else getattr(usage, "cache_creation", None)
        )
        w5_tokens = _usage_get(cc, "ephemeral_5m_input_tokens", -1)
        w1_tokens = _usage_get(cc, "ephemeral_1h_input_tokens", 0)
        if w5_tokens < 0:  # no breakdown: cache_creation_input_tokens is 5m
            w5_tokens = _usage_get(usage, "cache_creation_input_tokens", 0)

        def mc(tokens: int, usd_per_mtok: float) -> int:
            # tokens * $/MTok -> microcents: * 100 cents * 1e6 / 1e6 tokens.
            return round(tokens * usd_per_mtok * mult * 100)

        breakdown = ChargeBreakdown(
            model=key,
            input_mc=mc(_usage_get(usage, "input_tokens"), in_p),
            cache_write_5m_mc=mc(w5_tokens, w5_p),
            cache_write_1h_mc=mc(w1_tokens, w1_p),
            cache_read_mc=mc(_usage_get(usage, "cache_read_input_tokens"), rd_p),
            output_mc=mc(_usage_get(usage, "output_tokens"), out_p),
            web_search_mc=round(
                _web_searches(usage) * WEB_SEARCH_USD * self.price_multiplier
                * 100 * _MICROCENTS_PER_CENT
            ),
        )
        self.charges.append(breakdown)
        total = breakdown.total_mc
        if job_id is not None:
            self._jobs_mc[job_id] = self._jobs_mc.get(job_id, 0) + total
        else:
            self._total_mc += total
        return _ceil_cents_mc(total)

    def charge_web_searches(self, n: int, job_id: str | None = None) -> int:
        """Charge n standalone web searches (e.g. a non-Anthropic search
        API priced per call). Returns cents (ceil)."""
        total = round(n * WEB_SEARCH_USD * self.price_multiplier * 100 * 1_000_000)
        if job_id is not None:
            self._jobs_mc[job_id] = self._jobs_mc.get(job_id, 0) + total
        else:
            self._total_mc += total
        return _ceil_cents_mc(total)

    # -- amortization --------------------------------------------------------

    def amortize(self, job_id: str, n_consumers: int) -> int:
        """Fold a shared job (event dossier, screening sweep) into the
        ledger and return the per-consumer share in cents (ceil).

        The job's FULL exact cost lands in the ledger total exactly once
        (repeat calls only recompute the share); callers attribute the
        returned share to each of the n consumers for per-market honesty.
        Charging the survivors, not the universe: pass the number of
        markets that actually consume the output.
        """
        if n_consumers <= 0:
            raise ValueError("n_consumers must be positive")
        job_mc = self._jobs_mc.get(job_id, 0)
        if job_id not in self._amortized_jobs:
            self._total_mc += job_mc
            self._amortized_jobs.add(job_id)
        return _ceil_cents_mc(-(-job_mc // n_consumers))

    # -- reporting -----------------------------------------------------------

    @property
    def total_cents(self) -> int:
        """Ledger total in cents, rounded up. Unamortized jobs excluded
        (they haven't been attributed yet — call amortize())."""
        return _ceil_cents_mc(self._total_mc)

    def drain_cents(self) -> int:
        """Cents accrued since the last drain (for
        Strategy.last_inference_cost_cents). Sums of drains == total_cents
        (the running ceiling is monotone, so nothing is double-charged)."""
        now = self.total_cents
        delta = now - self._drained_cents
        self._drained_cents = now
        return delta


def _ceil_cents_mc(microcents: int) -> int:
    return -(-microcents // _MICROCENTS_PER_CENT)

"""Scoring math shared by every rung of the eval ladder.

Pure functions, no I/O, no dependencies outside the stdlib. Everything the
ladder reports about forecast quality is computed here so that the three
modules cannot disagree with each other.

Conventions
-----------
* A *forecast* is a probability in [0, 1] that the question resolves YES.
* An *outcome* is 0.0 or 1.0. (ForecastBench resolves market questions to
  exactly 0.0/1.0; Kalshi's rare ``scalar`` settlements are excluded upstream.)
* Brier is the plain unweighted ``mean((p - y)**2)`` — identical to
  ForecastBench's ``src/leaderboard/main.py`` (``research/benchmarks.md`` §1.4),
  so our numbers are directly comparable to their published leaderboard.
* "Delta" is always oriented **market minus us**: positive = we beat the market
  (``research/benchmarks.md`` §4.1).

The statistical gates live here too (``gate_report``) because SYNTHESIS §2.4
requires the same n>=500 / paired-CI rule everywhere, and a gate that is
re-implemented per caller is a gate that drifts.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Sequence

__all__ = [
    "MIN_N_FORECAST_GATE",
    "PAIRED_SD_REFERENCE",
    "brier",
    "brier_index",
    "log_loss",
    "paired_delta",
    "min_detectable_delta",
    "rms_disagreement",
    "three_number_block",
    "gate_report",
    "calibration_table",
    "concentration",
    "tier_for_delta",
]

#: SYNTHESIS §2.4: SPEC's n>=100 is unsound; the forecast-strategy gate is 500.
MIN_N_FORECAST_GATE = 500

#: Paired-difference SD measured on real 2026 ForecastBench data
#: (``research/benchmarks.md`` §1.7/§4.3). Used only for power calculations.
PAIRED_SD_REFERENCE = 0.0698


# ---------------------------------------------------------------------------
# Point scores
# ---------------------------------------------------------------------------

def brier(forecasts: Sequence[float], outcomes: Sequence[float]) -> float:
    """Plain unweighted Brier score. Lower is better; 0.25 = always-0.5."""
    if len(forecasts) != len(outcomes):
        raise ValueError(f"length mismatch: {len(forecasts)} vs {len(outcomes)}")
    if not forecasts:
        raise ValueError("empty forecast set")
    return sum((p - y) ** 2 for p, y in zip(forecasts, outcomes)) / len(forecasts)


def brier_index(b: float) -> float:
    """ForecastBench's 0-100 display scale: ``(1 - sqrt(B)) * 100``.

    100 = perfect, 50 = always-0.5, 0 = maximally wrong. Higher is better.
    (``research/benchmarks.md`` §1.4.)
    """
    if b < 0:
        raise ValueError("Brier cannot be negative")
    return (1.0 - math.sqrt(b)) * 100.0


def log_loss(
    forecasts: Sequence[float], outcomes: Sequence[float], eps: float = 1e-6
) -> float:
    """Mean negative log-likelihood, clipped at ``eps`` so 0/1 forecasts score."""
    if len(forecasts) != len(outcomes):
        raise ValueError("length mismatch")
    if not forecasts:
        raise ValueError("empty forecast set")
    total = 0.0
    for p, y in zip(forecasts, outcomes):
        q = min(max(p, eps), 1.0 - eps)
        total += -(y * math.log(q) + (1.0 - y) * math.log(1.0 - q))
    return total / len(forecasts)


# ---------------------------------------------------------------------------
# Paired comparison against a baseline (the market)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PairedDelta:
    """Paired difference ``Brier(baseline) - Brier(ours)``; positive = we win."""

    n: int
    delta: float
    sd: float
    se: float
    t: float | None
    ci_low: float
    ci_high: float
    baseline_brier: float
    our_brier: float

    @property
    def significant(self) -> bool:
        """95% CI excludes zero."""
        return self.ci_low > 0.0 or self.ci_high < 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["significant"] = self.significant
        d["rms_disagreement_equivalent"] = rms_disagreement(abs(self.delta))
        d["tier"] = tier_for_delta(self.delta, self.n, self.ci_low)
        return d


def paired_delta(
    ours: Sequence[float],
    baseline: Sequence[float],
    outcomes: Sequence[float],
    z: float = 1.96,
) -> PairedDelta:
    """Paired ``(baseline-y)^2 - (ours-y)^2`` with a normal-approximation CI.

    The paired form is mandatory (SYNTHESIS §2.3): the two forecasters see the
    same questions, so the question-difficulty variance cancels and the CI is
    several times tighter than differencing two independent means.
    """
    if not (len(ours) == len(baseline) == len(outcomes)):
        raise ValueError("length mismatch between ours / baseline / outcomes")
    n = len(ours)
    if n == 0:
        raise ValueError("empty forecast set")
    diffs = [
        (b - y) ** 2 - (o - y) ** 2 for o, b, y in zip(ours, baseline, outcomes)
    ]
    mean = statistics.fmean(diffs)
    sd = statistics.stdev(diffs) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n > 1 else 0.0
    return PairedDelta(
        n=n,
        delta=mean,
        sd=sd,
        se=se,
        t=(mean / se) if se > 0 else None,
        ci_low=mean - z * se,
        ci_high=mean + z * se,
        baseline_brier=brier(baseline, outcomes),
        our_brier=brier(ours, outcomes),
    )


def min_detectable_delta(n: int, sd: float = PAIRED_SD_REFERENCE, z: float = 1.96) -> float:
    """Smallest paired ΔBrier detectable at p<0.05 two-sided (``research/benchmarks.md`` §4.3)."""
    if n <= 0:
        raise ValueError("n must be positive")
    return z * sd / math.sqrt(n)


def rms_disagreement(delta: float) -> float:
    """ΔBrier -> the RMS forecast-vs-price gap that produces it, in probability.

    From the §4.1 identity ``E[ΔBrier] = (p - q)**2`` when we are the calibrated
    one. Multiply by 100 for cents.
    """
    return math.sqrt(max(delta, 0.0))


def tier_for_delta(delta: float, n: int, ci_low: float) -> str:
    """Map a pooled ΔBrier onto ``research/benchmarks.md`` §4.4's tiers."""
    if delta > 0.05:
        return "implausible (suspect a leak — check point-in-time discipline)"
    if ci_low > 0.0 and delta >= 0.02:
        return "top-2026-bot-class (be suspicious)"
    if ci_low > 0.0 and delta >= 0.004:
        return "superforecaster-class"
    if ci_low > 0.0:
        return "significant but below superforecaster-class"
    if delta >= 0.0:
        return "parity (CI spans 0, point estimate >= 0)"
    return "fail (not better than the price)"


# ---------------------------------------------------------------------------
# The three-number block (SYNTHESIS §2.3 item 2 / benchmarks §4.2)
# ---------------------------------------------------------------------------

@dataclass
class ThreeNumberBlock:
    """The block every forecast strategy must report, all three together.

    (a) pooled ΔBrier vs market with paired CI — proves we are not *worse*;
    (b) ΔBrier on the traded subset ``|p_hat - mid| >= gate`` — the skill claim;
    (c) simulated post-fee P&L on that subset — the only number that pays rent.

    (c) is supplied by the caller (the backtest harness owns fills and fees);
    when it is ``None`` the block renders as INCOMPLETE rather than silently
    reporting two of three numbers.
    """

    pooled: PairedDelta
    gate: float
    subset: PairedDelta | None
    subset_n: int
    simulated_pnl_usd: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.subset is not None and self.simulated_pnl_usd is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pooled_delta_brier": self.pooled.to_dict(),
            "traded_subset": {
                "gate": self.gate,
                "n": self.subset_n,
                "delta_brier": self.subset.to_dict() if self.subset else None,
            },
            "simulated_post_fee_pnl_usd": self.simulated_pnl_usd,
            "complete": self.complete,
            "notes": self.notes,
        }


def three_number_block(
    ours: Sequence[float],
    baseline: Sequence[float],
    outcomes: Sequence[float],
    gate: float = 0.06,
    simulated_pnl_usd: float | None = None,
) -> ThreeNumberBlock:
    """Build the mandatory three-number block.

    ``gate`` defaults to 6 points — the practical tradeable threshold from
    ``research/benchmarks.md`` §4.2 (2 points is the fee break-even at mid; ~6-10
    once fills and adverse selection are priced in).
    """
    pooled = paired_delta(ours, baseline, outcomes)
    idx = [i for i in range(len(ours)) if abs(ours[i] - baseline[i]) >= gate]
    notes: list[str] = []
    subset = None
    if len(idx) >= 2:
        subset = paired_delta(
            [ours[i] for i in idx],
            [baseline[i] for i in idx],
            [outcomes[i] for i in idx],
        )
    else:
        notes.append(f"traded subset has n={len(idx)} at gate={gate}; too small to score")
    if simulated_pnl_usd is None:
        notes.append(
            "number (c) simulated post-fee P&L not supplied — block is INCOMPLETE; "
            "wire the backtest harness before quoting (a) or (b)"
        )
    return ThreeNumberBlock(
        pooled=pooled,
        gate=gate,
        subset=subset,
        subset_n=len(idx),
        simulated_pnl_usd=simulated_pnl_usd,
        notes=notes,
    )


def gate_report(pd: PairedDelta, min_n: int = MIN_N_FORECAST_GATE) -> dict[str, Any]:
    """Evaluate SYNTHESIS §2.4's statistical gates against a paired result."""
    mde = min_detectable_delta(pd.n, sd=pd.sd or PAIRED_SD_REFERENCE)
    passes_n = pd.n >= min_n
    return {
        "n": pd.n,
        "min_n_required": min_n,
        "passes_n_gate": passes_n,
        "min_detectable_delta_at_this_n": mde,
        "min_detectable_rms_disagreement": rms_disagreement(mde),
        "delta": pd.delta,
        "ci": [pd.ci_low, pd.ci_high],
        "ci_excludes_zero": pd.significant,
        "tier": tier_for_delta(pd.delta, pd.n, pd.ci_low),
        "verdict": (
            "PASS"
            if passes_n and pd.ci_low > 0
            else "UNDERPOWERED" if not passes_n else "NOT SIGNIFICANT"
        ),
    }


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def calibration_table(
    forecasts: Sequence[float],
    outcomes: Sequence[float],
    edges: Sequence[float] = (0.0, 0.05, 0.15, 0.35, 0.65, 0.85, 0.95, 1.01),
) -> list[dict[str, Any]]:
    """Reliability table using ``research/benchmarks.md`` §1.6's buckets."""
    if len(forecasts) != len(outcomes):
        raise ValueError("length mismatch")
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = [(p, y) for p, y in zip(forecasts, outcomes) if lo <= p < hi]
        if not sel:
            continue
        out.append(
            {
                "bucket": f"[{lo:.2f}, {hi:.2f})",
                "n": len(sel),
                "mean_forecast": statistics.fmean(p for p, _ in sel),
                "realized_freq": statistics.fmean(y for _, y in sel),
            }
        )
    return out


def concentration(pnls: Iterable[float], top: int = 5) -> dict[str, Any]:
    """Top-``top``-position share of P&L (SYNTHESIS §2.3 item 4).

    FutureSearch's own book fails this test badly; it is kept as a standing
    cautionary metric rather than a pass/fail gate on external data.
    """
    vals = sorted(pnls, reverse=True)
    if not vals:
        return {"n": 0}
    total = sum(vals)
    head = sum(vals[:top])
    return {
        "n": len(vals),
        "total_pnl": total,
        f"top_{top}_pnl": head,
        # The share is only interpretable on a profitable book: on a losing book
        # "top-5 share of P&L" divides by a negative and means nothing.
        f"top_{top}_share_of_pnl": (head / total) if total > 0 else None,
        "share_defined": total > 0,
        "best": vals[0],
        "worst": vals[-1],
        "n_positive": sum(1 for v in vals if v > 0),
    }

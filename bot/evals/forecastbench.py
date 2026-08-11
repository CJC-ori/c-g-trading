"""Rung 2 — ForecastBench: the market-consensus baseline every forecaster must beat.

ForecastBench (Karger et al., ICLR 2025; <https://github.com/forecastingresearch/forecastbench>)
publishes a nightly-refreshed dataset of question sets and resolution sets. Each
*market* question carries ``freeze_datetime_value`` — literally the market price
10 days before the forecast due date — so the dataset ships its own
market-consensus baseline, offline, with no API keys
(``research/benchmarks.md`` §1.2).

This module does three things:

1. **Self-test** (runs on every invocation, and under pytest). Reproduce
   ``research/benchmarks.md`` §1.6's numbers on the contamination-safe window
   (``forecast_due_date >= 2026-02-01``, market questions, ``resolved == true``):

       n = 1,216   market-price Brier = 0.1172   sd = 0.1785

   plus the per-source table (INFER 0.065, Manifold 0.096, Polymarket 0.125,
   Metaculus 0.155). If those numbers do not come out, the join or — far more
   likely — the ``resolved == true`` filter is wrong, and every downstream Brier
   in the repo is being scored against *prices* rather than outcomes. That is the
   single easiest way to silently corrupt this eval (§1.3).

2. **A clean API for the forecaster**: :func:`questions` returns the rows (with
   ``question``, ``background``, ``resolution_criteria``, ``url`` and the
   ``freeze`` timestamp that bounds retrieval), and :func:`score` takes
   ``{question_key: p_yes}`` and returns the full SYNTHESIS §2.3 metric block
   against the market baseline.

3. **The baseline table** the rung-2 gate is read against.

Data
----
``data/forecastbench-datasets`` (139 MB, gitignored via ``/data/``):

    GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 \\
      https://github.com/forecastingresearch/forecastbench-datasets \\
      data/forecastbench-datasets

``--ensure-data`` runs that clone (and ``--update`` pulls). Licence: CC BY-SA 4.0
— attribution + share-alike, fine for internal evals. See ``bot/evals/README.md``.

Point-in-time discipline
------------------------
A forecaster scored here may use information up to each row's ``freeze``
timestamp (= due date − 10 days) and nothing after. ``questions()`` returns that
timestamp on every row precisely so the retrieval layer can bound itself; the
eval cannot enforce it for you.

Run: ``python -m bot.evals.forecastbench [--ensure-data] [--forecasts p.json]``
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from bot.evals import scoring
from bot.evals.report import md_table, write_report

NAME = "forecastbench"

REPO = Path(__file__).resolve().parents[2]
DATA_DIR = Path(
    os.environ.get("FORECASTBENCH_DIR", REPO / "data" / "forecastbench-datasets")
)
CLONE_URL = "https://github.com/forecastingresearch/forecastbench-datasets"

#: The four market sources. Everything else (acled/fred/yfinance/wikipedia/
#: dbnomics) is a *dataset* question whose ``freeze_datetime_value`` is a
#: reference statistic, not a probability — scoring it as one is a bug.
MARKET_SOURCES = frozenset({"manifold", "metaculus", "polymarket", "infer"})

#: Contamination-safe window (``research/benchmarks.md`` §1.9). 2026-02-01's own
#: freeze is 2026-01-22; use ``--min-due 2026-03-01`` for a hard margin.
DEFAULT_MIN_DUE = "2026-02-01"

#: The self-test targets, verbatim from ``research/benchmarks.md`` §1.6.
EXPECTED = {
    "n": 1216,
    "brier": 0.1172,
    "sd": 0.1785,
    "by_source": {
        "infer": {"n": 102, "brier": 0.0651},
        "manifold": {"n": 357, "brier": 0.0963},
        "polymarket": {"n": 534, "brier": 0.1253},
        "metaculus": {"n": 223, "brier": 0.1554},
    },
    "by_due": {
        "2026-02-01": 0.0613, "2026-02-15": 0.0575, "2026-03-01": 0.1156,
        "2026-03-29": 0.1506, "2026-04-26": 0.1522, "2026-06-07": 0.0995,
        "2026-07-19": 0.1148,
    },
}
#: The dataset refreshes nightly (resolutions accrete), so exact equality is the
#: wrong assertion. These are the tolerances the self-test enforces.
TOL_BRIER = 0.005
TOL_N_FRAC = 0.05


class DataMissing(RuntimeError):
    """Raised when the dataset clone is absent."""


# ---------------------------------------------------------------------------
# Data management
# ---------------------------------------------------------------------------

def ensure_data(path: Path = DATA_DIR, update: bool = False) -> Path:
    """Clone (depth 1) the datasets repo if absent; optionally ``git pull``."""
    if path.exists() and (path / "datasets").exists():
        if update:
            subprocess.run(["git", "-C", str(path), "pull", "--ff-only"], check=True)
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"}
    subprocess.run(
        ["git", "clone", "--depth", "1", CLONE_URL, str(path)], check=True, env=env
    )
    return path


def data_available(path: Path = DATA_DIR) -> bool:
    return (path / "datasets" / "question_sets").is_dir()


# ---------------------------------------------------------------------------
# The question set
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Row:
    """One resolved market question, joined to its resolution."""

    key: str            # "<due>|<source>|<id>" — unique, and what score() keys on
    due: str
    source: str
    qid: str
    question: str
    background: str
    resolution_criteria: str
    url: str
    freeze: str         # retrieval must be bounded to this instant
    close: str
    resolution_date: str
    market_p: float     # the market price at freeze — our baseline
    outcome: float      # 0.0 or 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "due": self.due, "source": self.source, "id": self.qid,
            "question": self.question, "background": self.background,
            "resolution_criteria": self.resolution_criteria, "url": self.url,
            "freeze": self.freeze, "close": self.close,
            "resolution_date": self.resolution_date,
            "market_p": self.market_p, "outcome": self.outcome,
        }


def questions(
    min_due: str = DEFAULT_MIN_DUE,
    max_due: str | None = None,
    sources: Iterable[str] | None = None,
    resolved_only: bool = True,
    path: Path = DATA_DIR,
) -> list[Row]:
    """Join question sets to resolution sets and return market questions.

    Args:
        min_due: earliest ``forecast_due_date`` (inclusive). Default
            ``2026-02-01`` — the contamination-safe window.
        max_due: latest ``forecast_due_date`` (inclusive), or None.
        sources: restrict to these market sources (default: all four).
        resolved_only: keep only ``resolved == true`` rows. **Setting this False
            scores against live market prices, not outcomes** — only do it to
            enumerate an open set for a forward test.

    Returns rows carrying everything a forecaster needs plus the market baseline.
    """
    if not data_available(path):
        raise DataMissing(
            f"ForecastBench data not found at {path}. Run "
            f"`python -m bot.evals.forecastbench --ensure-data`."
        )
    keep = frozenset(s.lower() for s in sources) if sources else MARKET_SOURCES
    out: list[Row] = []
    for qf in sorted(glob.glob(str(path / "datasets" / "question_sets" / "*-llm.json"))):
        due = os.path.basename(qf).replace("-llm.json", "")
        if due < min_due or (max_due and due > max_due):
            continue
        rf = path / "datasets" / "resolution_sets" / f"{due}_resolution_set.json"
        if not rf.exists():
            continue
        with open(qf) as fh:
            qdata = json.load(fh)
        # Legacy 2024 sets carry combination questions whose `id` is a list; the
        # 2026 sets have none (research/benchmarks.md §1.2). Skip defensively.
        Q = {
            (str(q["id"]), q["source"]): q
            for q in qdata["questions"]
            if not isinstance(q["id"], list)
        }
        with open(rf) as fh:
            rdata = json.load(fh)
        for r in rdata["resolutions"]:
            src = r.get("source")
            if src not in keep:
                continue
            if resolved_only and not r.get("resolved"):
                continue
            q = Q.get((str(r["id"]), src))
            if q is None:
                continue
            try:
                p = float(q["freeze_datetime_value"])
            except (TypeError, ValueError, KeyError):
                continue
            try:
                y = float(r["resolved_to"])
            except (TypeError, ValueError):
                continue
            out.append(
                Row(
                    key=f"{due}|{src}|{r['id']}",
                    due=due,
                    source=src,
                    qid=str(r["id"]),
                    question=q.get("question", ""),
                    background=q.get("background", ""),
                    resolution_criteria=q.get("resolution_criteria", ""),
                    url=q.get("url", ""),
                    freeze=q.get("freeze_datetime", ""),
                    close=q.get("market_info_close_datetime", ""),
                    resolution_date=r.get("resolution_date", ""),
                    market_p=p,
                    outcome=y,
                )
            )
    return out


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------

def market_baseline(rows: Sequence[Row]) -> dict[str, Any]:
    """Brier of the market price at freeze, pooled and by source/due-date."""
    if not rows:
        return {"n": 0}
    ys = [r.outcome for r in rows]
    ps = [r.market_p for r in rows]
    sq = [(p - y) ** 2 for p, y in zip(ps, ys)]
    n = len(rows)
    sd = statistics.stdev(sq) if n > 1 else 0.0

    def group(keyfn) -> dict[str, dict[str, Any]]:
        buckets: dict[str, list[Row]] = {}
        for r in rows:
            buckets.setdefault(keyfn(r), []).append(r)
        return {
            k: {
                "n": len(v),
                "base_rate": statistics.fmean(x.outcome for x in v),
                "brier": scoring.brier([x.market_p for x in v], [x.outcome for x in v]),
                "log_loss": scoring.log_loss([x.market_p for x in v], [x.outcome for x in v]),
            }
            for k, v in sorted(buckets.items())
        }

    return {
        "n": n,
        "brier": statistics.fmean(sq),
        "sd": sd,
        "se": sd / (n**0.5) if n > 1 else 0.0,
        "ci95": [
            statistics.fmean(sq) - 1.96 * sd / (n**0.5),
            statistics.fmean(sq) + 1.96 * sd / (n**0.5),
        ] if n > 1 else None,
        "brier_index": scoring.brier_index(statistics.fmean(sq)),
        "base_rate": statistics.fmean(ys),
        "by_source": group(lambda r: r.source),
        "by_due": group(lambda r: r.due),
        "calibration": scoring.calibration_table(ps, ys),
        "always_half_brier": scoring.brier([0.5] * n, ys),
    }


def self_test(rows: Sequence[Row]) -> dict[str, Any]:
    """Check the loader against ``research/benchmarks.md`` §1.6's published numbers."""
    base = market_baseline(rows)
    checks: list[dict[str, Any]] = []

    def check(label: str, expected: float, actual: float, tol: float) -> None:
        checks.append(
            {
                "check": label,
                "expected": expected,
                "actual": actual,
                "tolerance": tol,
                "pass": abs(actual - expected) <= tol,
            }
        )

    check("pooled market Brier", EXPECTED["brier"], base["brier"], TOL_BRIER)
    check("pooled sd", EXPECTED["sd"], base["sd"], 0.01)
    check(
        "n resolved market questions",
        EXPECTED["n"],
        base["n"],
        EXPECTED["n"] * TOL_N_FRAC,
    )
    for src, exp in EXPECTED["by_source"].items():
        got = base["by_source"].get(src)
        if got is None:
            checks.append({"check": f"{src} present", "expected": exp["n"], "actual": 0, "pass": False})
            continue
        check(f"{src} Brier", exp["brier"], got["brier"], TOL_BRIER)
    return {"passed": all(c["pass"] for c in checks), "checks": checks, "baseline": base}


# ---------------------------------------------------------------------------
# Scoring a forecaster
# ---------------------------------------------------------------------------

def score(
    forecasts: Mapping[str, float],
    rows: Sequence[Row] | None = None,
    gate: float = 0.06,
    simulated_pnl_usd: float | None = None,
    **question_kwargs: Any,
) -> dict[str, Any]:
    """Score ``{key: p_yes}`` against the market baseline on the same questions.

    ``key`` is :attr:`Row.key` (``"<due>|<source>|<id>"``); a bare question id is
    also accepted when it is unambiguous.

    Probabilities may be on the 0-1 or the 0-100 scale: anything in ``(1, 100]``
    is divided by 100, anything outside ``[0, 100]`` raises. Note the
    consequence — ``1.5`` is read as 1.5%, not as an invalid probability. Emit
    one scale consistently and this never bites.

    Returns the SYNTHESIS §2.3 block: pooled ΔBrier with paired CI, the traded
    subset at ``gate``, the (caller-supplied) post-fee P&L, calibration, the
    per-source breakdown, and the §2.4 gate verdict.
    """
    rows = list(rows) if rows is not None else questions(**question_kwargs)
    by_key = {r.key: r for r in rows}
    by_id: dict[str, list[Row]] = {}
    for r in rows:
        by_id.setdefault(r.qid, []).append(r)

    matched: list[tuple[Row, float]] = []
    unmatched: list[str] = []
    for k, v in forecasts.items():
        p = float(v["p_yes"] if isinstance(v, dict) else v)
        if not 0.0 <= p <= 100.0:
            raise ValueError(f"forecast for {k} out of range: {p}")
        if p > 1.0:
            p /= 100.0
        r = by_key.get(k)
        if r is None:
            cands = by_id.get(str(k), [])
            r = cands[0] if len(cands) == 1 else None
        if r is None:
            unmatched.append(k)
            continue
        matched.append((r, p))

    if len(matched) < 2:
        return {
            "n_scored": len(matched),
            "n_unmatched": len(unmatched),
            "unmatched_keys": unmatched[:50],
            "error": "fewer than 2 forecasts matched the question set",
        }

    ours = [p for _, p in matched]
    mkt = [r.market_p for r, _ in matched]
    y = [r.outcome for r, _ in matched]
    block = scoring.three_number_block(ours, mkt, y, gate=gate, simulated_pnl_usd=simulated_pnl_usd)
    pooled = block.pooled

    per_source: dict[str, Any] = {}
    for src in sorted({r.source for r, _ in matched}):
        sel = [(r, p) for r, p in matched if r.source == src]
        if len(sel) < 2:
            continue
        pd_ = scoring.paired_delta(
            [p for _, p in sel], [r.market_p for r, _ in sel], [r.outcome for r, _ in sel]
        )
        per_source[src] = pd_.to_dict()

    return {
        "n_scored": len(matched),
        "coverage_of_question_set": len(matched) / len(rows) if rows else 0.0,
        "n_unmatched": len(unmatched),
        "unmatched_keys": unmatched[:50],
        "brier_ours": scoring.brier(ours, y),
        "brier_market": scoring.brier(mkt, y),
        "brier_index_ours": scoring.brier_index(scoring.brier(ours, y)),
        "three_number_block": block.to_dict(),
        "gate": scoring.gate_report(pooled),
        "by_source": per_source,
        "calibration_ours": scoring.calibration_table(ours, y),
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def build_report(st: dict[str, Any], scored: dict[str, Any] | None) -> tuple[str, list[tuple[str, str]]]:
    base = st["baseline"]

    sec_self = md_table(
        ["check", "expected", "actual", "tol", "pass"],
        [
            [c["check"], c["expected"], c["actual"], c.get("tolerance", "—"), c["pass"]]
            for c in st["checks"]
        ],
    )
    sec_self += (
        "\n\nTargets are `research/benchmarks.md` §1.6, computed on the same window "
        "(`forecast_due_date >= 2026-02-01`, market sources only, `resolved == true`). "
        "The dataset refreshes nightly and resolutions accrete, so `n` is checked to ±5% and "
        f"Brier to ±{TOL_BRIER}. A failure here means the join or the `resolved == true` filter "
        "is wrong — and a broken `resolved` filter scores forecasts against *market prices* "
        "instead of outcomes, which looks plausible and is completely worthless (§1.3)."
    )

    sec_base = md_table(
        ["scope", "n", "base rate", "Brier", "Brier Index", "log loss"],
        [["pooled (2026-02-01+)", base["n"], base["base_rate"], base["brier"],
          base["brier_index"], "—"]]
        + [
            [src, d["n"], d["base_rate"], d["brier"], scoring.brier_index(d["brier"]), d["log_loss"]]
            for src, d in sorted(base["by_source"].items(), key=lambda kv: kv[1]["brier"])
        ],
    )
    sec_base += (
        f"\n\nPooled market-price Brier **{base['brier']:.4f}** "
        f"(sd {base['sd']:.4f}, 95% CI [{base['ci95'][0]:.4f}, {base['ci95'][1]:.4f}], "
        f"n={base['n']}) is **the bar**. Always-0.5 scores {base['always_half_brier']:.4f} on the "
        "same set, which fixes the scale.\n\n"
        "This is higher (worse) than ForecastBench's published leaderboard baseline of 0.077 for "
        "two reasons (§1.6): their baseline uses the market value on the *due date*, 10 days "
        "better-informed than the `freeze_datetime_value` we have offline; and our subset is only "
        "questions that have already resolved in the 2026 window, which skews short-horizon. "
        "**0.1172 is our internal bar** because it is what our own code produces on our own "
        "subset — the apples-to-apples comparison for anything we score the same way.\n\n"
        "Read the per-source spread as a warning, not a menu: INFER (0.065) and Metaculus (0.155) "
        "differ by more than any forecaster edge we could plausibly find, so a forecaster's "
        "apparent skill can be manufactured entirely by which sources it happens to cover. "
        "Always report `by_source` alongside the pooled number."
    )

    sec_due = md_table(
        ["forecast_due_date", "n", "base rate", "market Brier"],
        [[k, d["n"], d["base_rate"], d["brier"]] for k, d in base["by_due"].items()],
    )

    sec_cal = md_table(
        ["price bucket", "n", "mean price", "realized freq"],
        [[c["bucket"], c["n"], c["mean_forecast"], c["realized_freq"]] for c in base["calibration"]],
    )
    sec_cal += (
        "\n\nTextbook longshot bias in the 5-35¢ band. **`research/benchmarks.md` §1.7 already "
        "showed this does not survive out of sample** — a logistic recalibration fitted pre-2026 "
        "gained +0.0027 in-sample and *lost* 0.0005 on this window. Do not build a price-only "
        "strategy on this table without a time-forward split."
    )

    sec_power = md_table(
        ["n", "min detectable ΔBrier", "equivalent RMS disagreement"],
        [
            [n, scoring.min_detectable_delta(n), f"{scoring.rms_disagreement(scoring.min_detectable_delta(n)) * 100:.1f}¢"]
            for n in (100, 200, 500, base["n"], 2000)
        ],
    )
    sec_power += (
        f"\n\nAt paired-difference sd {scoring.PAIRED_SD_REFERENCE} (measured, §4.3). The gate is "
        f"**n ≥ {scoring.MIN_N_FORECAST_GATE}** (SYNTHESIS §2.4): at n=100 the smallest detectable "
        "edge is 0.0137, three times the entire superforecaster-over-market edge of 0.004, so a "
        "'win' at n=100 is noise. Tiers: +0.004 superforecaster-class, +0.02 top-2026-bot-class "
        "(be suspicious), >+0.05 you have a leak."
    )

    sections = [
        ("Self-test — did we reproduce §1.6?", sec_self),
        ("The baseline table (the bar for rung 2)", sec_base),
        ("Market Brier by forecast-due-date cohort", sec_due),
        ("Market-price calibration at T−10d", sec_cal),
        ("Statistical power at this n", sec_power),
    ]

    if scored and "error" not in scored:
        tn = scored["three_number_block"]
        sec_scored = md_table(
            ["metric", "value"],
            [
                ["n scored", scored["n_scored"]],
                ["coverage of question set", f"{scored['coverage_of_question_set']:.1%}"],
                ["Brier(ours)", scored["brier_ours"]],
                ["Brier(market)", scored["brier_market"]],
                ["(a) pooled ΔBrier", f"{tn['pooled_delta_brier']['delta']:+.4f}"],
                [
                    "    95% CI",
                    f"[{tn['pooled_delta_brier']['ci_low']:+.4f}, {tn['pooled_delta_brier']['ci_high']:+.4f}]",
                ],
                ["(b) traded-subset n", tn["traded_subset"]["n"]],
                [
                    "(b) traded-subset ΔBrier",
                    f"{tn['traded_subset']['delta_brier']['delta']:+.4f}"
                    if tn["traded_subset"]["delta_brier"]
                    else "—",
                ],
                ["(c) simulated post-fee P&L", tn["simulated_post_fee_pnl_usd"]],
                ["block complete", tn["complete"]],
                ["gate verdict", scored["gate"]["verdict"]],
                ["tier", scored["gate"]["tier"]],
            ],
        )
        if tn["notes"]:
            sec_scored += "\n\n" + "\n".join(f"- ⚠ {n}" for n in tn["notes"])
        sections.append(("Scored forecaster", sec_scored))
        verdict = f"SELF-TEST {'PASS' if st['passed'] else 'FAIL'} — forecaster {scored['gate']['verdict']}"
    else:
        sections.append(
            (
                "Scored forecaster",
                "No forecasts supplied. Pass `--forecasts p.json` (`{\"<due>|<source>|<id>\": p_yes}`) "
                "or call `bot.evals.forecastbench.score(...)` from the forecaster's own harness.",
            )
        )
        verdict = f"SELF-TEST {'PASS' if st['passed'] else 'FAIL'} — baseline established, no forecaster scored"

    return verdict, sections


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ensure-data", action="store_true", help="clone the dataset if missing")
    ap.add_argument("--update", action="store_true", help="git pull the dataset first")
    ap.add_argument("--min-due", default=DEFAULT_MIN_DUE)
    ap.add_argument("--max-due", default=None)
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--forecasts", type=Path, default=None, help='JSON {"<key>": p_yes}')
    ap.add_argument("--gate", type=float, default=0.06)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.ensure_data or args.update:
        ensure_data(args.data_dir, update=args.update)

    rows = questions(min_due=args.min_due, max_due=args.max_due, path=args.data_dir)
    st = self_test(rows)

    scored = None
    if args.forecasts:
        forecasts = json.loads(args.forecasts.read_text())
        scored = score(forecasts, rows=rows, gate=args.gate)

    verdict, sections = build_report(st, scored)
    payload = {
        "window": {"min_due": args.min_due, "max_due": args.max_due},
        "self_test": {"passed": st["passed"], "checks": st["checks"]},
        "baseline": st["baseline"],
        "scored": scored,
        "licence": "ForecastBench datasets are CC BY-SA 4.0 — internal eval use only, "
                   "attribute and share-alike if republished; never on a live-money path.",
    }
    jpath, mpath = write_report(
        NAME, "Rung 2 — ForecastBench market-consensus baseline", payload, sections, verdict
    )
    if not args.quiet:
        print(f"{verdict}\n")
        for heading, body in sections:
            print(f"## {heading}\n{body}\n")
        print(f"wrote {jpath}\nwrote {mpath}")
    return 0 if st["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

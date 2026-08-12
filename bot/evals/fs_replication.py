"""Rung 2a — 3-way Brier on FutureSearch's published 153-question Kalshi run.

On 2026-02-26 16:09 UTC FutureSearch snapshotted 153 Kalshi markets and published,
per market, the point-in-time YES price, the volume, the resolution date and three
model forecasts plus their median
(``research/futuresearch-data/fs_kalshi_forecasts_2026-02-26_slim.csv``). That is
a free, point-in-time head-to-head against both a published AI forecaster *and*
the market, on real Kalshi questions — the eval ``research/futuresearch.md`` §7
calls "the first thing to build after the data layer".

This module resolves every ticker it can and scores three columns on the same
questions:

    Brier(FutureSearch median)  vs  Brier(Kalshi price @ 2026-02-26 16:09)  vs
    Brier(our forecaster)

The third column is a placeholder until the LLM forecaster exists: pass
``--forecasts path.json`` mapping ``ticker -> p_yes`` (0-1 or 0-100) and it is
scored alongside, with the full three-number block from SYNTHESIS §2.3.

Judge-cutoff hygiene
--------------------
Our judge model's training cutoff is May 2026 (SYNTHESIS §3 correction #1), and
these markets resolve from Feb 2026 into 2030. Results are therefore **always**
split at 2026-05-01: anything resolving before that date is inside the model's
training window and its forecasts are contaminated by construction. Only the
post-cutoff half is admissible evidence, and it is reported separately, never
pooled.

⚠ Coverage reality (measured 2026-08-11, see the report's `coverage` section)
-----------------------------------------------------------------------------
``research/futuresearch.md`` §7 asserts these markets "all resolved months ago,
and the outcomes are free". **That is wrong**, in two independent ways, and the
eval is much smaller than the doc promises. See the report.

Run: ``python -m bot.evals.fs_replication [--online] [--forecasts p.json]``
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from bot.evals import scoring
from bot.evals.report import md_table, write_report

NAME = "fs_replication"

REPO = Path(__file__).resolve().parents[2]
CSV_PATH = REPO / "research" / "futuresearch-data" / "fs_kalshi_forecasts_2026-02-26_slim.csv"
DB_PATH = REPO / "data" / "kalshi.db"
OVERRIDES_PATH = Path(__file__).resolve().parent / "fs_resolution_overrides.json"

#: Judge-model training cutoff (SYNTHESIS §3). Markets resolving before this are
#: contaminated for any LLM forecaster we run.
JUDGE_CUTOFF = date(2026, 5, 1)

FORECAST_TIMESTAMP = "2026-02-26T16:09:10Z"

_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        "january february march april may june july august september october "
        "november december".split()
    )
}


# ---------------------------------------------------------------------------
# The question set
# ---------------------------------------------------------------------------

@dataclass
class Question:
    ticker: str
    question: str
    market_price: float          # 0-1, YES price at FORECAST_TIMESTAMP
    volume: float
    resolution_date: date | None
    resolution_date_raw: str
    days_remaining: int | None
    fs_median: float             # 0-1
    fs_models: dict[str, float] = field(default_factory=dict)
    # filled by the resolver
    outcome: float | None = None
    outcome_source: str | None = None
    unresolvable_reason: str | None = None

    @property
    def resolved(self) -> bool:
        return self.outcome is not None

    @property
    def post_cutoff(self) -> bool | None:
        if self.resolution_date is None:
            return None
        return self.resolution_date >= JUDGE_CUTOFF


def parse_loose_date(raw: str) -> date | None:
    """Parse the CSV's mixed date column.

    Most rows are plain ISO. The rest are free text written by the spreadsheet
    author — ``"February 28, 2026"``, ``"2026-04-01T04:00:00Z"``,
    ``"2028-11-07 (Market End Date) / August 2028 (Estimated Convention Date)"``,
    and two rows carrying *two* real dates (``"March 3, 2026 (Primary Election)
    or May 26, 2026 (Runoff)"``). For multi-date rows we take the **latest**
    date mentioned, since that is the earliest instant the market is certain to
    be settled — the conservative choice for a cutoff split.

    Returns None only when no day-precision date is present at all; the caller
    buckets those into ``unknown_resolution_date`` rather than guessing.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    found: list[date] = []
    # ISO dates, possibly with a time suffix or trailing prose
    # ("2026-04-01T04:00:00Z", "2028-11-07 (Market End Date) / August 2028").
    for y, m, d in re.findall(r"(\d{4})-(\d{2})-(\d{2})", raw):
        try:
            found.append(date(int(y), int(m), int(d)))
        except ValueError:
            pass
    for m, d, y in re.findall(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", raw):
        mm = _MONTHS.get(m.lower())
        if mm:
            try:
                found.append(date(int(y), mm, int(d)))
            except ValueError:
                pass
    return max(found) if found else None


def _pct(v: str) -> float | None:
    """CSV probabilities are integer percents; return 0-1."""
    try:
        return float(v) / 100.0
    except (TypeError, ValueError):
        return None


def load_questions(path: Path = CSV_PATH) -> list[Question]:
    out: list[Question] = []
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            median = _pct(row["median_probability"])
            price = _pct(row["market_price"])
            if median is None or price is None:
                continue
            try:
                days = int(row["days_remaining"])
            except (TypeError, ValueError):
                days = None
            out.append(
                Question(
                    ticker=row["ticker"].strip(),
                    question=row["complete_question"],
                    market_price=price,
                    volume=float(row["volume"] or 0),
                    resolution_date=parse_loose_date(row["resolution_date"]),
                    resolution_date_raw=row["resolution_date"],
                    days_remaining=days,
                    fs_median=median,
                    fs_models={
                        k: p
                        for k in ("gemini_probability", "opus_probability", "opus_2_probability")
                        if (p := _pct(row.get(k, ""))) is not None
                    },
                )
            )
    return out


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def resolve_from_db(questions: list[Question], db_path: Path = DB_PATH) -> None:
    """Fill outcomes from the local Kalshi store (``markets.result``)."""
    if not db_path.exists():
        for q in questions:
            q.unresolvable_reason = q.unresolvable_reason or "no local DB"
        return
    tickers = [q.ticker for q in questions]
    with sqlite3.connect(db_path) as con:
        rows = {
            r[0]: r
            for r in con.execute(
                "SELECT ticker, status, result, settlement_value FROM markets WHERE ticker IN (%s)"
                % ",".join("?" * len(tickers)),
                tickers,
            )
        }
    for q in questions:
        r = rows.get(q.ticker)
        if r is None:
            q.unresolvable_reason = "not in local DB"
            continue
        _, status, result, settlement = r
        if result in ("yes", "no"):
            q.outcome = 1.0 if result == "yes" else 0.0
            q.outcome_source = "local_db"
            q.unresolvable_reason = None
        elif result == "scalar" and settlement is not None:
            # ~0.3% of Kalshi settlements pay a fraction; excluded from Brier
            # rather than silently coerced to 0/1.
            q.unresolvable_reason = "scalar settlement (excluded from Brier)"
        else:
            q.unresolvable_reason = f"still {status or 'unknown'} in local DB"


def resolve_from_api(questions: list[Question], base_url: str | None = None) -> dict[str, int]:
    """Fill remaining outcomes from Kalshi's public settled-market endpoint.

    Kalshi purges settled markets from ``/markets`` roughly 90 days after close
    (``bot/data/kalshi_client.py`` module docstring), so this can only recover
    markets that settled recently. Failures are recorded, never fatal.
    """
    import httpx

    base = (base_url or "https://api.elections.kalshi.com/trade-api/v2").rstrip("/")
    stats = {"queried": 0, "resolved": 0, "still_open": 0, "purged": 0, "error": 0}
    todo = [q for q in questions if not q.resolved]
    if not todo:
        return stats
    with httpx.Client(timeout=20.0, headers={"Accept": "application/json"}) as client:
        for q in todo:
            stats["queried"] += 1
            try:
                r = client.get(f"{base}/markets/{q.ticker}")
            except Exception:
                stats["error"] += 1
                continue
            if r.status_code == 404:
                stats["purged"] += 1
                q.unresolvable_reason = (
                    "purged from the Kalshi public API (settled >~90d ago, no local snapshot)"
                )
                continue
            if r.status_code != 200:
                stats["error"] += 1
                continue
            m = r.json().get("market", {})
            result = (m.get("result") or "").lower()
            if result in ("yes", "no"):
                q.outcome = 1.0 if result == "yes" else 0.0
                q.outcome_source = "kalshi_api"
                q.unresolvable_reason = None
                stats["resolved"] += 1
            else:
                stats["still_open"] += 1
                q.unresolvable_reason = f"still {m.get('status', 'open')} on Kalshi"
    return stats


def resolve_from_overrides(questions: list[Question], path: Path = OVERRIDES_PATH) -> int:
    """Apply hand-curated outcomes for markets no automated source can reach.

    Format: ``{"TICKER": {"outcome": 0 or 1, "source": "url", "note": "..."}}``.
    Every entry must carry a citable ``source``; this file is the only place in
    the ladder where a human number enters the scoring path.
    """
    if not path.exists():
        return 0
    data = json.loads(path.read_text())
    n = 0
    by_ticker = {q.ticker: q for q in questions}
    for ticker, rec in data.items():
        if ticker.startswith("_"):
            continue  # documentation keys
        q = by_ticker.get(ticker)
        if q is None or q.resolved:
            continue
        if not rec.get("source"):
            raise ValueError(f"override for {ticker} has no source citation")
        q.outcome = float(rec["outcome"])
        q.outcome_source = "override:" + rec["source"]
        q.unresolvable_reason = None
        n += 1
    return n


def load_our_forecasts(path: Path | None) -> dict[str, float]:
    """``ticker -> p_yes`` from JSON. Accepts 0-1 or 0-100; normalises to 0-1."""
    if path is None:
        return {}
    data = json.loads(Path(path).read_text())
    out = {}
    for k, v in data.items():
        p = float(v["p_yes"] if isinstance(v, dict) else v)
        if not 0.0 <= p <= 100.0:
            raise ValueError(f"forecast for {k} out of range: {p}")
        if p > 1.0:
            p /= 100.0
        out[k] = p
    return out


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score_group(qs: list[Question], ours: dict[str, float]) -> dict[str, Any]:
    if not qs:
        return {"n": 0}
    y = [q.outcome for q in qs]
    fs = [q.fs_median for q in qs]
    mkt = [q.market_price for q in qs]
    block: dict[str, Any] = {
        "n": len(qs),
        "base_rate": sum(y) / len(y),
        "brier_fs_median": scoring.brier(fs, y),
        "brier_market": scoring.brier(mkt, y),
        "brier_ours": None,
        "logloss_fs_median": scoring.log_loss(fs, y),
        "logloss_market": scoring.log_loss(mkt, y),
        "brier_index_fs_median": scoring.brier_index(scoring.brier(fs, y)),
        "brier_index_market": scoring.brier_index(scoring.brier(mkt, y)),
    }
    fs_vs_mkt = scoring.paired_delta(fs, mkt, y)
    block["fs_vs_market"] = fs_vs_mkt.to_dict()
    block["fs_vs_market_gate"] = scoring.gate_report(fs_vs_mkt)

    have = [q for q in qs if q.ticker in ours]
    if len(have) >= 2:
        oy = [q.outcome for q in have]
        op = [ours[q.ticker] for q in have]
        om = [q.market_price for q in have]
        block["brier_ours"] = scoring.brier(op, oy)
        block["ours_coverage"] = len(have) / len(qs)
        ovm = scoring.paired_delta(op, om, oy)
        block["ours_vs_market"] = ovm.to_dict()
        block["ours_vs_market_gate"] = scoring.gate_report(ovm)
        block["three_number_block"] = scoring.three_number_block(op, om, oy).to_dict()
        block["ours_vs_fs"] = scoring.paired_delta(
            op, [q.fs_median for q in have], oy
        ).to_dict()
        block["calibration_ours"] = scoring.calibration_table(op, oy)
    block["calibration_fs_median"] = scoring.calibration_table(fs, y)
    block["calibration_market"] = scoring.calibration_table(mkt, y)
    return block


def evaluate(questions: list[Question], ours: dict[str, float]) -> dict[str, Any]:
    resolved = [q for q in questions if q.resolved]
    groups = {
        "all_resolved": resolved,
        "pre_cutoff_2026_05_01": [q for q in resolved if q.post_cutoff is False],
        "post_cutoff_2026_05_01": [q for q in resolved if q.post_cutoff is True],
        "unknown_resolution_date": [q for q in resolved if q.post_cutoff is None],
    }
    by_reason: dict[str, int] = {}
    for q in questions:
        if not q.resolved:
            by_reason[q.unresolvable_reason or "unknown"] = (
                by_reason.get(q.unresolvable_reason or "unknown", 0) + 1
            )
    today = date.today()
    horizons = [q.resolution_date for q in questions if q.resolution_date]
    return {
        "forecast_timestamp": FORECAST_TIMESTAMP,
        "judge_cutoff": JUDGE_CUTOFF.isoformat(),
        "horizon": {
            "due_on_or_before_today": sum(1 for d in horizons if d <= today),
            "due_after_today": sum(1 for d in horizons if d > today),
            "no_parseable_date": len(questions) - len(horizons),
            "latest_resolution_date": max(horizons).isoformat() if horizons else None,
            "as_of": today.isoformat(),
        },
        "coverage": {
            "questions_in_csv": len(questions),
            "resolved": len(resolved),
            "resolution_rate": len(resolved) / len(questions) if questions else 0.0,
            "by_source": _count(q.outcome_source for q in resolved),
            "unresolved_by_reason": by_reason,
            "our_forecasts_supplied": len(ours),
        },
        "scores": {k: _score_group(v, ours) for k, v in groups.items()},
        "questions": [
            {
                "ticker": q.ticker,
                "question": q.question,
                "market_price": q.market_price,
                "fs_median": q.fs_median,
                "our_forecast": ours.get(q.ticker),
                "outcome": q.outcome,
                "outcome_source": q.outcome_source,
                "resolution_date": q.resolution_date.isoformat() if q.resolution_date else None,
                "post_cutoff": q.post_cutoff,
                "unresolvable_reason": q.unresolvable_reason,
            }
            for q in questions
        ],
    }


def _count(vals: Iterable[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in vals:
        out[str(v)] = out.get(str(v), 0) + 1
    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def build_report(result: dict[str, Any]) -> tuple[str, list[tuple[str, str]]]:
    cov = result["coverage"]
    scores = result["scores"]

    rows = []
    for label in (
        "all_resolved",
        "pre_cutoff_2026_05_01",
        "post_cutoff_2026_05_01",
        "unknown_resolution_date",
    ):
        s = scores[label]
        if not s.get("n"):
            rows.append([label, 0, "—", "—", "—", "—", "—"])
            continue
        rows.append(
            [
                label,
                s["n"],
                f"{s['base_rate']:.2f}",
                f"{s['brier_fs_median']:.4f}",
                f"{s['brier_market']:.4f}",
                f"{s['brier_ours']:.4f}" if s.get("brier_ours") is not None else "not wired",
                f"{s['fs_vs_market']['delta']:+.4f} "
                f"[{s['fs_vs_market']['ci_low']:+.4f}, {s['fs_vs_market']['ci_high']:+.4f}]",
            ]
        )
    sec_brier = md_table(
        [
            "split",
            "n",
            "base rate",
            "Brier(FS median)",
            "Brier(Kalshi price)",
            "Brier(ours)",
            "ΔBrier FS−market [95% CI]",
        ],
        rows,
    )
    sec_brier += (
        "\n\nΔ is oriented *market minus forecaster*: positive means the forecaster beat the "
        "price. Every split is reported with its paired CI, never as a point estimate "
        "(SYNTHESIS §2.4). Column 3 is scored only where `--forecasts` supplied a probability "
        "for that ticker."
    )

    all_s = scores["all_resolved"]
    if all_s.get("n"):
        g = all_s["fs_vs_market_gate"]
        sec_gate = md_table(
            ["gate", "required", "actual", "verdict"],
            [
                ["n (SYNTHESIS §2.4)", scoring.MIN_N_FORECAST_GATE, all_s["n"], g["verdict"]],
                [
                    "min detectable ΔBrier at this n",
                    "≤ 0.004 (superforecaster edge)",
                    f"{g['min_detectable_delta_at_this_n']:.4f}",
                    "FAIL" if g["min_detectable_delta_at_this_n"] > 0.004 else "PASS",
                ],
                [
                    "paired CI excludes 0",
                    "yes",
                    g["ci_excludes_zero"],
                    "PASS" if g["ci_excludes_zero"] else "FAIL",
                ],
                ["tier (§4.4)", "—", g["tier"], "—"],
            ],
        )
    else:
        sec_gate = "No resolved questions — nothing to gate."

    sec_cov = md_table(
        ["metric", "value"],
        [
            ["questions in the published CSV", cov["questions_in_csv"]],
            ["resolved (scorable)", cov["resolved"]],
            ["resolution rate", f"{cov['resolution_rate']:.0%}"],
            ["our forecasts supplied", cov["our_forecasts_supplied"]],
        ],
    ) + "\n\n" + md_table(
        ["outcome source", "n"], sorted(cov["by_source"].items(), key=lambda kv: -kv[1])
    ) + "\n\n**Why the rest are unscorable:**\n\n" + md_table(
        ["reason", "n"], sorted(cov["unresolved_by_reason"].items(), key=lambda kv: -kv[1])
    )
    h = result["horizon"]
    purged = sum(v for k, v in cov["unresolved_by_reason"].items() if k.startswith("purged"))
    still_open = sum(v for k, v in cov["unresolved_by_reason"].items() if "still" in k)
    sec_cov += (
        "\n\n⚠ **`research/futuresearch.md` §7 is wrong about this dataset.** It says the 153 "
        "markets \"all resolved months ago, and the outcomes are free\". Neither half holds "
        f"(measured {h['as_of']}):\n\n"
        f"1. **Half of them have not resolved.** Only {h['due_on_or_before_today']} of "
        f"{cov['questions_in_csv']} carry a resolution date on or before today; "
        f"{h['due_after_today']} are still ahead of us and the set runs out to "
        f"{h['latest_resolution_date']}. FutureSearch's filter was 3–97¢ + volume, *not* a "
        f"horizon filter — their >10-day rule sets a floor, not a ceiling. {still_open} tickers "
        "are confirmed still active on Kalshi right now.\n"
        f"2. **{purged} of the ones that did resolve are gone.** Kalshi purges settled markets "
        "from the public API ~90 days after close, and our local store post-dates the purge "
        "window for the Feb–Apr resolutions. Those tickers 404 on `/markets/{ticker}`, return an "
        "empty list from `/markets?event_ticker=…&status=settled`, and have no candlestick "
        "history locally either. Their outcomes are not recoverable from Kalshi at all.\n\n"
        "Consequences: this rung is a **calibration smoke test, not evidence**. To make it "
        "evidence, either (a) backfill outcomes into `bot/evals/fs_resolution_overrides.json` "
        "from citable public sources, or (b) re-run it periodically as the 76 still-active "
        "markets settle. Option (b) is free and grows n automatically *provided* the data layer "
        "snapshots settled markets on a schedule tighter than Kalshi's ~90-day purge — worth "
        "confirming with whoever owns `bot/data/`, because without that snapshot the same "
        "outcomes evaporate again."
    )

    sec_hygiene = (
        f"Judge-model cutoff is **{result['judge_cutoff']}** (SYNTHESIS §3 correction #1: "
        "ORCHESTRATION.md's \"Jan 2026\" is wrong for Opus 5). Splits:\n\n"
        f"- **pre-cutoff** (resolves < {result['judge_cutoff']}): "
        f"n={scores['pre_cutoff_2026_05_01'].get('n', 0)}. Contaminated by construction for any "
        "LLM forecaster we run — the outcome is inside the model's training window. Useful only "
        "as a leak *detector*: if our forecaster scores dramatically better here than post-cutoff, "
        "that gap is memorisation, not skill.\n"
        f"- **post-cutoff** (resolves ≥ {result['judge_cutoff']}): "
        f"n={scores['post_cutoff_2026_05_01'].get('n', 0)}. The only admissible evidence.\n"
        f"- **unknown resolution date**: n={scores['unknown_resolution_date'].get('n', 0)} "
        "(free-text date column the parser could not read).\n\n"
        "FutureSearch's own forecasts carry no such caveat — their snapshot is genuinely "
        "point-in-time at 2026-02-26 16:09 UTC, and the Kalshi price column is the price at that "
        "same instant, so the FS-vs-market comparison is clean at every n."
    )

    n = all_s.get("n", 0)
    if n == 0:
        verdict = "NO DATA — no questions resolvable"
    elif n < scoring.MIN_N_FORECAST_GATE:
        verdict = (
            f"UNDERPOWERED — n={n} resolved of {cov['questions_in_csv']} "
            f"(gate is n≥{scoring.MIN_N_FORECAST_GATE}); numbers are directional only"
        )
    else:
        verdict = f"SCORED — n={n}"

    return verdict, [
        ("3-way Brier", sec_brier),
        ("Statistical gates (SYNTHESIS §2.4)", sec_gate),
        ("Judge-cutoff hygiene", sec_hygiene),
        ("Coverage — and a correction to research/futuresearch.md §7", sec_cov),
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", type=Path, default=CSV_PATH)
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument(
        "--online", action="store_true", help="also query the Kalshi public API for outcomes"
    )
    ap.add_argument(
        "--forecasts",
        type=Path,
        default=None,
        help="JSON {ticker: p_yes} from our forecaster (the third Brier column)",
    )
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    questions = load_questions(args.csv)
    resolve_from_db(questions, args.db)
    api_stats = None
    if args.online:
        api_stats = resolve_from_api(questions)
    n_over = resolve_from_overrides(questions)

    ours = load_our_forecasts(args.forecasts)
    result = evaluate(questions, ours)
    result["coverage"]["kalshi_api_stats"] = api_stats
    result["coverage"]["overrides_applied"] = n_over
    verdict, sections = build_report(result)

    payload = {k: v for k, v in result.items() if k != "questions"}
    payload["question_detail_count"] = len(result["questions"])
    payload["questions"] = result["questions"]
    jpath, mpath = write_report(
        NAME, "Rung 2a — FutureSearch 153-question replication (3-way Brier)", payload, sections, verdict
    )
    if not args.quiet:
        print(f"{verdict}\n")
        for heading, body in sections:
            print(f"## {heading}\n{body}\n")
        print(f"wrote {jpath}\nwrote {mpath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

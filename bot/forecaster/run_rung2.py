"""Rung 2 — run the forecaster on the ForecastBench resolved market-question
subset and score it against the market baseline (bot.evals.forecastbench).

Judge-cutoff rules (research/cost-architecture.md §8, verified by live probe
2026-08-11): the CLI 'sonnet' alias maps to Claude Sonnet 5, knowledge cutoff
Jan 2026 ⇒ Sonnet is the honest judge for questions RESOLVING Feb–May 2026,
with retrieval bounded to each row's freeze timestamp.

Phases (everything cached; the script is resumable and replayable):

    select   tier-0 prefilter + Haiku screen + stratified sample
    run      full pipeline (retrieval -> dossier -> 2-pass judge)
    ablation same judge, question text only (no retrieval) — standing column
    placebo  full pipeline re-run at freeze+14d on a small sample (leak diag)
    score    three-number block vs market + always-0.5; audit; reports

Usage:
    python -m bot.forecaster.run_rung2 --n 120 --workers 6 --phase all
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from pathlib import Path

from bot.backtest.costs import CostLedger
from bot.evals import forecastbench as fb
from bot.evals import scoring
from bot.forecaster import prefilter as pf
from bot.forecaster import screen as sc
from bot.forecaster.llm import ReplayCache, SubagentClient
from bot.forecaster.pipeline import ForecastPipeline, ForecastResult
from bot.forecaster.retrieval import (
    RetrievalLedger,
    audit_ledger,
    parse_dt,
)

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "reports" / "forecaster"
WORK = REPO / "data" / "forecaster-cache" / "rung2"

RESOLVE_MIN = "2026-02-01"
RESOLVE_MAX = "2026-05-31"
SEED = 7


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def eligible_rows() -> list[fb.Row]:
    rows = [
        r for r in fb.questions()
        if RESOLVE_MIN <= r.resolution_date <= RESOLVE_MAX
    ]
    rows.sort(key=lambda r: r.key)
    return rows


def select_questions(client: SubagentClient, n_target: int,
                     costs: CostLedger) -> list[fb.Row]:
    """Tier 0 + Tier 1, then a deterministic stratified sample."""
    sel_path = WORK / f"selection-n{n_target}.json"
    rows = eligible_rows()
    by_key = {r.key: r for r in rows}
    if sel_path.exists():
        keys = json.loads(sel_path.read_text())["keys"]
        return [by_key[k] for k in keys if k in by_key]

    res = pf.prefilter_forecastbench(rows)
    log(f"tier-0: {len(rows)} rows -> {len(res.kept)} kept "
        f"(rejects: {res.reject_counts})")

    kept = res.kept
    verdicts, resps = sc.screen_questions(
        client, [r.question for r in kept], batch_size=15)
    for resp in resps:
        costs.charge(resp.usage, resp.price_model)
    passed = [kept[v.index] for v in verdicts if v.passed]
    reject_hist: dict[str, int] = {}
    for v in verdicts:
        if not v.passed:
            reject_hist[v.reject_code or "?"] = reject_hist.get(v.reject_code or "?", 0) + 1
    log(f"tier-1 screen: {len(kept)} -> {len(passed)} passed "
        f"(rejects: {reject_hist})")

    # stratified sample by source, proportional, deterministic
    rng = random.Random(SEED)
    by_source: dict[str, list[fb.Row]] = {}
    for r in passed:
        by_source.setdefault(r.source, []).append(r)
    total = len(passed)
    sample: list[fb.Row] = []
    for src, group in sorted(by_source.items()):
        k = max(1, round(n_target * len(group) / total))
        group = sorted(group, key=lambda r: r.key)
        rng.shuffle(group)
        sample.extend(group[:k])
    sample = sorted(sample, key=lambda r: r.key)[:n_target]
    WORK.mkdir(parents=True, exist_ok=True)
    sel_path.write_text(json.dumps({
        "keys": [r.key for r in sample],
        "n_eligible": len(rows), "n_tier0": len(kept), "n_screened": len(passed),
        "tier0_rejects": res.reject_counts, "screen_rejects": reject_hist,
    }, indent=1))
    log(f"selected {len(sample)} questions "
        f"({ {s: sum(1 for r in sample if r.source == s) for s in by_source} })")
    return sample


# ---------------------------------------------------------------------------
# Forecast phases
# ---------------------------------------------------------------------------

def run_forecasts(
    client: SubagentClient, rows: list[fb.Row], mode: str, workers: int,
    ledger_path: Path, results_path: Path, shift_days: int = 0,
) -> list[ForecastResult]:
    done: dict[str, dict] = {}
    if results_path.exists():
        for line in results_path.read_text().splitlines():
            if line.strip():
                d = json.loads(line)
                if d.get("p_yes") is not None or d.get("error"):
                    done[d["question_key"]] = d
    todo = [r for r in rows if r.key not in done]
    log(f"{mode}: {len(done)} already done, {len(todo)} to run")
    ledger = RetrievalLedger(ledger_path)
    results: list[ForecastResult] = []
    write_lock = threading.Lock()
    results_path.parent.mkdir(parents=True, exist_ok=True)

    def one(r: fb.Row) -> ForecastResult:
        asof = r.freeze
        if shift_days:
            dt = parse_dt(r.freeze)
            asof = (dt + timedelta(days=shift_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pipe = ForecastPipeline(
            client, ledger, CostLedger(),
            run_id=f"rung2-{mode}", judge_model="sonnet",
            judge_effort="medium",
        )
        res = pipe.forecast(
            question_key=r.key, question=r.question, background=r.background,
            criteria=r.resolution_criteria, asof=asof, close=r.close,
            market_p=r.market_p,
            mode=("ablation" if mode == "ablation" else "full"),
        )
        with write_lock:
            with open(results_path, "a") as fh:
                row = res.to_row()
                row["cache_keys"] = res.cache_keys
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return res

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(one, r): r.key for r in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                res = fut.result()
                results.append(res)
                log(f"  {mode} {i}/{len(todo)} {futs[fut][:40]} "
                    f"p={res.p_yes if res.p_yes is None else round(res.p_yes, 3)} "
                    f"cost={res.cost_cents}c"
                    + (f" ERR={res.error[:80]}" if res.error else ""))
            except Exception as e:  # noqa: BLE001
                log(f"  {mode} {i}/{len(todo)} {futs[fut][:40]} FAILED {e}")
    return results


def load_results(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                d = json.loads(line)
                out[d["question_key"]] = d   # last write wins
    return out


# ---------------------------------------------------------------------------
# Scoring + report
# ---------------------------------------------------------------------------

def score_and_report(rows: list[fb.Row], gate: float) -> None:
    full = load_results(WORK / "results-full.jsonl")
    abl = load_results(WORK / "results-ablation.jsonl")
    by_key = {r.key: r for r in rows}

    def forecasts(res: dict[str, dict]) -> dict[str, float]:
        return {k: d["p_yes"] for k, d in res.items()
                if d.get("p_yes") is not None and k in by_key}

    f_full, f_abl = forecasts(full), forecasts(abl)
    common = sorted(set(f_full) & set(f_abl))
    log(f"scoring: full={len(f_full)} ablation={len(f_abl)} common={len(common)}")
    crows = [by_key[k] for k in common]

    scored_full = fb.score({k: f_full[k] for k in common}, rows=crows, gate=gate)
    scored_abl = fb.score({k: f_abl[k] for k in common}, rows=crows, gate=gate)

    ys = [by_key[k].outcome for k in common]
    ms = [by_key[k].market_p for k in common]
    base = {
        "n": len(common),
        "brier_market": scoring.brier(ms, ys) if common else None,
        "brier_always_half": scoring.brier([0.5] * len(ys), ys) if ys else None,
        "base_rate": statistics.fmean(ys) if ys else None,
    }

    # cost summary
    def cost_stats(res: dict[str, dict]) -> dict:
        costs = [d.get("cost_cents", 0) for d in res.values()]
        return {"n": len(costs), "total_cents": sum(costs),
                "mean_cents": round(statistics.fmean(costs), 2) if costs else 0}

    # leak audit on the full-mode ledger
    audit: dict[str, object]
    try:
        recs = RetrievalLedger.load(WORK / "ledger-full.jsonl")
        violations = audit_ledger(recs, hard_fail=False)
        audit = {
            "n_ledger_rows": len(recs),
            "n_violations": len(violations),
            "violation_kinds": sorted({v for v, _ in violations}),
            "hard_fail_would_void_run": bool(violations),
        }
    except FileNotFoundError:
        audit = {"error": "no ledger"}

    # placebo (D vs D+14) diagnostic: Brier should IMPROVE markedly at D+14;
    # flat scores mean the D run was already seeing the future (§5.5)
    plc = load_results(WORK / "results-placebo.jsonl")
    f_plc = forecasts(plc)
    placebo: dict[str, object] | None = None
    pkeys = sorted(set(f_plc) & set(f_full))
    if len(pkeys) >= 2:
        pys = [by_key[k].outcome for k in pkeys]
        placebo = {
            "n": len(pkeys),
            "brier_at_D": scoring.brier([f_full[k] for k in pkeys], pys),
            "brier_at_D_plus_14": scoring.brier([f_plc[k] for k in pkeys], pys),
            "note": "expect D+14 to be markedly better; flat = leaking",
        }

    payload = {
        "selection": json.loads((WORK / "selection-n120.json").read_text())
        if (WORK / "selection-n120.json").exists() else None,
        "gate": gate,
        "baseline": base,
        "full": scored_full,
        "ablation": scored_abl,
        "cost_full": cost_stats(full),
        "cost_ablation": cost_stats(abl),
        "placebo": placebo,
        "leak_audit": audit,
        "judge_model": "sonnet (Claude Sonnet 5, Jan-2026 cutoff — probed)",
        "window": {"resolve_min": RESOLVE_MIN, "resolve_max": RESOLVE_MAX},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "rung2-results.json").write_text(json.dumps(payload, indent=1))
    log(f"wrote {OUT / 'rung2-results.json'}")

    # compact replay-cache export for reproducibility
    keys: list[str] = []
    for res in (full, abl):
        for d in res.values():
            keys.extend(d.get("cache_keys", []))
    cache = ReplayCache()
    n = cache.export_jsonl(sorted(set(keys)), OUT / "rung2-llm-cache.jsonl")
    log(f"exported {n} cache entries to reports/forecaster/rung2-llm-cache.jsonl")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--gate", type=float, default=0.04)
    ap.add_argument("--placebo-n", type=int, default=10)
    ap.add_argument("--phase", default="all",
                    choices=["select", "run", "ablation", "placebo", "score", "all"])
    args = ap.parse_args(argv)

    client = SubagentClient()
    costs = CostLedger()
    WORK.mkdir(parents=True, exist_ok=True)

    rows = select_questions(client, args.n, costs)
    if args.phase == "select":
        return 0
    if args.phase in ("run", "all"):
        run_forecasts(client, rows, "full", args.workers,
                      WORK / "ledger-full.jsonl", WORK / "results-full.jsonl")
    if args.phase in ("ablation", "all"):
        run_forecasts(client, rows, "ablation", args.workers,
                      WORK / "ledger-ablation.jsonl",
                      WORK / "results-ablation.jsonl")
    if args.phase in ("placebo", "all"):
        rng = random.Random(SEED)
        sample = sorted(rows, key=lambda r: r.key)
        rng.shuffle(sample)
        run_forecasts(client, sample[:args.placebo_n], "placebo", args.workers,
                      WORK / "ledger-placebo.jsonl",
                      WORK / "results-placebo.jsonl", shift_days=14)
    if args.phase in ("score", "all"):
        score_and_report(rows, args.gate)
    log(f"subagent calls={client.calls} cache_hits={client.cache_hits}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

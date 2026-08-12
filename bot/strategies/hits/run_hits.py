"""Hits-based selective forecasting — end-to-end runner.

Phases (all cached/resumable under data/forecaster-cache/hits/):
  candidates  enumerate the full price-only candidate set (both windows)
  screen      Haiku mispricing screen on the event-deduped candidates
  select      seeded sample of screen-passers (budget: ~88 forecasts)
  forecast    full pipeline (PIT retrieval -> dossier -> 2-pass judge) at D
  engine      4 engine runs: hits strategy, market-as-forecast null,
              always-fade-all null (FLB), always-fade-forecasted-subset
  report      trade-by-trade table + machine-readable results

Usage: python -m bot.strategies.hits.run_hits --phase all --workers 6
"""
from __future__ import annotations

import argparse
import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from bot.backtest.costs import CostLedger
from bot.backtest.dataport import SqliteHistoryProvider
from bot.backtest.engine import EngineConfig, run_backtest
from bot.backtest.fees import FeeSchedule
from bot.backtest.metrics import compute_metrics
from bot.forecaster import hits as H
from bot.forecaster.llm import SubagentClient
from bot.forecaster.retrieval import RetrievalLedger
from bot.strategies.hits.strategy import (
    AlwaysFadeStrategy,
    HitsForecast,
    HitsStrategy,
    MarketAsForecastStrategy,
)

REPO = Path(__file__).resolve().parents[3]
WORK = H.HITS_DIR
OUT = REPO / "reports" / "hits"

SEED = 13
N_FORECASTS = {"A": 48, "B": 40}    # ~88 total, inside the 60–100 budget


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def client() -> SubagentClient:
    return SubagentClient(cache=H.hits_replay_cache())


# ---------------------------------------------------------------------------
# phases
# ---------------------------------------------------------------------------

def phase_candidates() -> dict:
    path = WORK / "candidates.json"
    if path.exists():
        return json.loads(path.read_text())
    payload: dict = {}
    for w in ("A", "B"):
        cands, funnel = H.enumerate_candidates(w)
        deduped = H.dedupe_by_event(cands)
        payload[w] = {
            "funnel": funnel,
            "n_candidates": len(cands),
            "n_events": len(deduped),
            "candidates": [c.to_row() for c in cands],
            "deduped_tickers": [c.ticker for c in deduped],
        }
        log(f"window {w}: funnel={funnel} -> {len(cands)} candidates, "
            f"{len(deduped)} after event-dedupe")
    WORK.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1))
    return payload


def _load_deduped(cands_payload: dict) -> list[H.Candidate]:
    out = []
    for w in ("A", "B"):
        keep = set(cands_payload[w]["deduped_tickers"])
        out.extend(H.Candidate(**row) for row in cands_payload[w]["candidates"]
                   if row["ticker"] in keep)
    return out


def phase_screen(cands_payload: dict, workers: int) -> dict:
    path = WORK / "screen.json"
    if path.exists():
        return json.loads(path.read_text())
    deduped = _load_deduped(cands_payload)
    cl = client()
    verdicts, resps = H.screen_candidates(cl, deduped, workers=workers)
    costs = CostLedger()
    for r in resps:
        costs.charge(r.usage, r.price_model)
    rows = []
    for c, v in zip(deduped, verdicts):
        rows.append({"ticker": c.ticker, "window": c.window,
                     "passed": v.passed, "code": v.code, "path": v.path,
                     "parse_failed": v.parse_failed})
    hist: dict[str, int] = {}
    for r in rows:
        k = "pass" if r["passed"] else f"reject:{r['code']}"
        hist[k] = hist.get(k, 0) + 1
    payload = {"rows": rows, "histogram": hist,
               "screen_cost_cents": costs.total_cents,
               "n_batches": len(resps)}
    log(f"screen: {hist} cost={costs.total_cents}c")
    path.write_text(json.dumps(payload, indent=1))
    return payload


def phase_select(cands_payload: dict, screen_payload: dict) -> dict:
    path = WORK / "selection.json"
    if path.exists():
        return json.loads(path.read_text())
    passed_by_w: dict[str, list[str]] = {"A": [], "B": []}
    for r in screen_payload["rows"]:
        if r["passed"]:
            passed_by_w[r["window"]].append(r["ticker"])
    rng = random.Random(SEED)
    selected: dict[str, list[str]] = {}
    for w in ("A", "B"):
        pool = sorted(passed_by_w[w])
        rng.shuffle(pool)
        selected[w] = sorted(pool[:N_FORECASTS[w]])
        log(f"window {w}: {len(passed_by_w[w])} screen-passers "
            f"-> selected {len(selected[w])} (seed {SEED})")
    path.write_text(json.dumps(
        {"selected": selected, "n_passed": {w: len(v) for w, v in
                                            passed_by_w.items()}}, indent=1))
    return json.loads(path.read_text())


def phase_forecast(cands_payload: dict, selection: dict, workers: int) -> dict:
    fpath = WORK / "forecasts.json"
    done: dict[str, dict] = json.loads(fpath.read_text()) if fpath.exists() else {}
    by_ticker = {row["ticker"]: H.Candidate(**row)
                 for w in ("A", "B") for row in cands_payload[w]["candidates"]}
    todo = [by_ticker[t] for w in ("A", "B") for t in selection["selected"][w]
            if t not in done]
    log(f"forecast: {len(done)} cached, {len(todo)} to run")
    if not todo:
        return done
    cl = client()
    ledger = RetrievalLedger(WORK / "ledger.jsonl")
    lock = threading.Lock()

    def one(c: H.Candidate) -> tuple[str, dict]:
        return c.ticker, H.forecast_candidate(cl, ledger, c)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(one, c): c.ticker for c in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            tkr, row = fut.result()
            with lock:
                done[tkr] = row
                fpath.write_text(json.dumps(done, indent=1))
            log(f"  {i}/{len(todo)} {tkr} p={row['p_yes']} "
                f"cost={row['cost_cents']}c"
                + (f" ERR={str(row['error'])[:60]}" if row["error"] else ""))
    return done


def _result_summary(res, label: str) -> dict:
    n_intents = len(res.intents)
    traded = [tk for tk, m in res.per_market.items() if m["contracts_traded"]]
    return {
        "label": label,
        "n_markets": len(res.per_market),
        "n_intents": n_intents,
        "n_traded": len(traded),
        "fills": len(res.fills),
        "net_pnl_cents": res.net_pnl_cents,
        "net_pnl_after_inference_cents": res.net_pnl_after_inference_cents,
        "fees_cents": res.fees_cents,
        "inference_cost_cents": res.inference_cost_cents,
    }


def phase_engine(cands_payload: dict, selection: dict, fmap: dict,
                 screen_payload: dict) -> dict:
    provider = SqliteHistoryProvider(str(REPO / "data" / "kalshi.db"))
    config = EngineConfig(fee_schedule=FeeSchedule.load_default())
    by_ticker = {row["ticker"]: row
                 for w in ("A", "B") for row in cands_payload[w]["candidates"]}

    # amortize the screen cost over the forecasted markets (truthful
    # accounting: the screen was the price of finding them)
    ok = {t: d for t, d in fmap.items() if d.get("p_yes") is not None}
    n_err = len(fmap) - len(ok)
    screen_amort = (screen_payload["screen_cost_cents"] + len(ok) - 1) // max(1, len(ok))
    forecasts = {
        t: HitsForecast(ticker=t, p_yes=d["p_yes"], asof_ts=d["decision_ts"],
                        cost_cents=d["cost_cents"] + screen_amort)
        for t, d in ok.items()
    }
    log(f"engine: {len(forecasts)} forecasts usable ({n_err} no-trade), "
        f"screen amortized {screen_amort}c/market")

    runs: dict[str, dict] = {}

    # 1. the hits strategy
    tickers = sorted(forecasts)
    res_hits = run_backtest(provider, HitsStrategy(forecasts), config,
                            {"tickers": tickers})
    runs["hits"] = {"summary": _result_summary(res_hits, "hits"),
                    "metrics": compute_metrics(res_hits)}
    _dump_trades(res_hits, fmap, by_ticker, WORK / "trades-hits.json")

    # 1b. stress variants (SPEC §7: fee x1.5 and inference x2 must survive)
    from dataclasses import replace
    cfg_fee = replace(config, fee_stress=1.5)
    res_fee = run_backtest(provider, HitsStrategy(forecasts), cfg_fee,
                           {"tickers": tickers})
    runs["hits_fee_x1.5"] = {"summary": _result_summary(res_fee, "hits_fee_x1.5")}
    runs["hits_inference_x2"] = {
        "summary": {
            **runs["hits"]["summary"], "label": "hits_inference_x2",
            "inference_cost_cents": 2 * res_hits.inference_cost_cents,
            "net_pnl_after_inference_cents":
                res_hits.net_pnl_cents - 2 * res_hits.inference_cost_cents,
        }
    }

    # 2. null: market-price-as-forecast (must never fire)
    anchors = {t: forecasts[t].asof_ts for t in tickers}
    res_null = run_backtest(provider, MarketAsForecastStrategy(anchors),
                            config, {"tickers": tickers})
    runs["null_market_as_forecast"] = {
        "summary": _result_summary(res_null, "null_market_as_forecast")}

    # 3. null: always-fade EVERY deduped candidate (the FLB null)
    all_cands = {c.ticker: c.decision_ts for c in _load_deduped(cands_payload)}
    res_fade = run_backtest(provider, AlwaysFadeStrategy(all_cands), config,
                            {"tickers": sorted(all_cands)})
    runs["null_always_fade_all"] = {
        "summary": _result_summary(res_fade, "null_always_fade_all"),
        "metrics": compute_metrics(res_fade)}
    _dump_trades(res_fade, {}, by_ticker, WORK / "trades-fade-all.json")

    # 4. null: always-fade restricted to the forecasted subset
    sub = {t: anchors[t] for t in tickers}
    res_fsub = run_backtest(provider, AlwaysFadeStrategy(sub), config,
                            {"tickers": tickers})
    runs["null_always_fade_subset"] = {
        "summary": _result_summary(res_fsub, "null_always_fade_subset")}
    _dump_trades(res_fsub, {}, by_ticker, WORK / "trades-fade-subset.json")

    (WORK / "engine.json").write_text(json.dumps(runs, indent=1, default=str))
    for k, v in runs.items():
        s = v["summary"]
        log(f"  {k}: intents={s['n_intents']} traded={s['n_traded']} "
            f"net={s['net_pnl_cents']}c net_after_inf="
            f"{s['net_pnl_after_inference_cents']}c")
    return runs


def _dump_trades(res, fmap: dict, by_ticker: dict, path: Path) -> None:
    """Per-market trade rows for the report (only markets with an intent)."""
    intents_by_tkr: dict[str, list[dict]] = {}
    for it in res.intents:
        intents_by_tkr.setdefault(it.get("ticker", "?"), []).append(it)
    fills_by_tkr: dict[str, list] = {}
    for f in res.fills:
        fills_by_tkr.setdefault(f.ticker, []).append(f)
    rows = []
    for tkr, its in sorted(intents_by_tkr.items()):
        pm = res.per_market.get(tkr, {})
        fills = fills_by_tkr.get(tkr, [])
        qty = sum(f.count for f in fills)
        cost = sum(f.count * f.price_cents for f in fills)
        cand = by_ticker.get(tkr, {})
        settle = res.settlements.get(tkr)
        d = fmap.get(tkr, {})
        rows.append({
            "ticker": tkr,
            "title": cand.get("title", ""),
            "window": cand.get("window", ""),
            "category": cand.get("category", ""),
            "price_at_d": cand.get("price_at_d"),
            "result": settle.result if settle else cand.get("result"),
            "p_yes": d.get("p_yes"),
            "side": its[0].get("side"),
            "limit_price_cents": its[0].get("limit_price_cents"),
            "filled_contracts": qty,
            "avg_fill_cents": (cost / qty) if qty else None,
            "net_pnl_cents": pm.get("net_pnl_cents", 0),
            "fees_cents": pm.get("fees_cents", 0),
            "rationale": its[0].get("rationale", ""),
        })
    path.write_text(json.dumps(rows, indent=1))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="all",
                    choices=["candidates", "screen", "select", "forecast",
                             "engine", "all"])
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args(argv)

    WORK.mkdir(parents=True, exist_ok=True)
    cands = phase_candidates()
    if args.phase == "candidates":
        return 0
    screen = phase_screen(cands, args.workers)
    if args.phase == "screen":
        return 0
    selection = phase_select(cands, screen)
    if args.phase == "select":
        return 0
    fmap = phase_forecast(cands, selection, args.workers)
    if args.phase == "forecast":
        return 0
    phase_engine(cands, selection, fmap, screen)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

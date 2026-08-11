"""Rung 3 — the forecaster as a Strategy through the backtest engine, on a
small Kalshi train-split universe.

Judge-cutoff rule: this universe is restricted to markets CLOSING
2026-03-05..2026-05-31, judged by Sonnet 5 (Jan-2026 cutoff, probed) with
retrieval bounded to ``asof = close - 21d`` per market. June-2026+ markets
would admit fable/opus judges but are left to a follow-up run.

Phases (all cached/resumable):
  A  select markets: tier-0 (volume ≥ 5000, ≥30d lifetime, category
     keywords) + event dedupe + Haiku screen; backfill hourly candles from
     the public Kalshi API into a private store (the shared kalshi.db has
     candle coverage for almost none of this window and is not ours to
     write); keep markets priced 3–97¢ at the anchor.
  B  pre-compute pipeline forecasts at ``asof`` (full PIT discipline).
  C  engine run: maker-first ForecasterStrategy, ≥4-point gate, exact
     per-series fees, inference charged into P&L; metrics.full_report to
     reports/forecaster/rung3/.

Usage: python -m bot.forecaster.run_rung3 --n 18 --workers 6
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from bot.backtest.costs import CostLedger
from bot.backtest.engine import EngineConfig
from bot.backtest.fees import FeeSchedule
from bot.backtest.metrics import full_report
from bot.forecaster import prefilter as pf
from bot.forecaster import screen as sc
from bot.forecaster.kalshi_history import (
    CANDLES_DB,
    Rung3HistoryProvider,
    backfill_candles,
)
from bot.forecaster.llm import SubagentClient
from bot.forecaster.pipeline import ForecastPipeline
from bot.forecaster.retrieval import RetrievalLedger
from bot.forecaster.strategy import ForecasterStrategy, PrecomputedForecast

REPO = Path(__file__).resolve().parents[2]
DB = REPO / "data" / "kalshi.db"
WORK = REPO / "data" / "forecaster-cache" / "rung3"
OUT = REPO / "reports" / "forecaster" / "rung3"

CLOSE_MIN = "2026-03-05"
CLOSE_MAX = "2026-05-31"
ANCHOR_DAYS = 21
MIN_LIFETIME_DAYS = 30
SEED = 11


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _iso_to_ts(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def candidates() -> list[dict]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT ticker, event_ticker, series_ticker, title, yes_sub_title,
               category, open_time, close_time, volume, result, rules_primary
        FROM markets
        WHERE result IN ('yes','no')
          AND close_time >= ? AND close_time <= ?
          AND volume >= 5000
        ORDER BY ticker
        """,
        (CLOSE_MIN, CLOSE_MAX),
    ).fetchall()
    con.close()
    out = []
    for r in rows:
        close_ts = _iso_to_ts(r["close_time"])
        open_ts = _iso_to_ts(r["open_time"])
        if close_ts - open_ts < MIN_LIFETIME_DAYS * 86400:
            continue
        title = r["title"] or ""
        if r["yes_sub_title"]:
            title = f"{title} — {r['yes_sub_title']}"
        if pf.category_block(title):
            continue
        out.append({
            "ticker": r["ticker"], "event_ticker": r["event_ticker"],
            "series_ticker": r["series_ticker"] or r["ticker"].split("-")[0],
            "title": title, "category": r["category"],
            "rules": (r["rules_primary"] or "")[:1200],
            "open_ts": open_ts, "close_time": r["close_time"],
            "close_ts": close_ts, "asof_ts": close_ts - ANCHOR_DAYS * 86400,
            "volume": r["volume"], "result": r["result"],
        })
    return out


def price_at(ticker: str, ts: int) -> float | None:
    """Bid/ask-mid (or trade close) in [0,1] from the private candle store."""
    con = sqlite3.connect(f"file:{CANDLES_DB}?mode=ro", uri=True)
    try:
        r = con.execute(
            """SELECT price_close, yes_bid_close, yes_ask_close
               FROM candlesticks WHERE ticker=? AND ts<=? ORDER BY ts DESC
               LIMIT 1""", (ticker, ts)).fetchone()
    finally:
        con.close()
    if r is None:
        return None
    pc, yb, ya = r
    if yb is not None and ya is not None:
        return (float(yb) + float(ya)) / 2.0
    return float(pc) if pc is not None else None


def select_markets(client: SubagentClient, n_target: int) -> list[dict]:
    sel_path = WORK / f"selection-n{n_target}.json"
    if sel_path.exists():
        return json.loads(sel_path.read_text())["markets"]
    cands = candidates()
    log(f"tier-0: {len(cands)} candidates")
    by_event: dict[str, dict] = {}
    for c in cands:
        ev = c["event_ticker"] or c["ticker"]
        if ev not in by_event or c["volume"] > by_event[ev]["volume"]:
            by_event[ev] = c
    uniq = sorted(by_event.values(), key=lambda c: c["ticker"])
    log(f"event dedupe: {len(uniq)} events")
    verdicts, _ = sc.screen_questions(client, [c["title"] for c in uniq])
    passed = [uniq[v.index] for v in verdicts if v.passed]
    reject_hist: dict[str, int] = {}
    for v in verdicts:
        if not v.passed:
            reject_hist[v.reject_code or "?"] = reject_hist.get(v.reject_code or "?", 0) + 1
    log(f"screen: {len(passed)} passed (rejects: {reject_hist})")

    rng = random.Random(SEED)
    rng.shuffle(passed)
    sample: list[dict] = []
    for c in passed:                      # backfill until n_target in-band
        if len(sample) >= n_target:
            break
        try:
            n = backfill_candles(
                c["series_ticker"], c["ticker"], c["open_ts"], c["close_ts"])
        except RuntimeError as e:
            log(f"  backfill failed {c['ticker']}: {e}")
            continue
        p = price_at(c["ticker"], c["asof_ts"])
        if p is None or not (pf.PRICE_LO <= p <= pf.PRICE_HI):
            log(f"  {c['ticker']}: price at anchor "
                f"{'missing' if p is None else round(p, 2)} — skipped")
            continue
        c["price_at_asof"] = p
        c["n_candles"] = n
        sample.append(c)
        log(f"  {c['ticker']}: {n} candles, p(asof)={p:.2f} — selected")
    sample = sorted(sample, key=lambda c: c["ticker"])
    WORK.mkdir(parents=True, exist_ok=True)
    sel_path.write_text(json.dumps(
        {"markets": sample, "n_candidates": len(cands),
         "n_events": len(uniq), "n_screen_passed": len(passed),
         "screen_rejects": reject_hist}, indent=1))
    return sample


def precompute_forecasts(
    client: SubagentClient, markets: list[dict], workers: int,
) -> dict[str, dict]:
    fpath = WORK / "forecasts.json"
    done: dict[str, dict] = (
        json.loads(fpath.read_text()) if fpath.exists() else {}
    )
    todo = [m for m in markets if m["ticker"] not in done]
    log(f"forecast phase: {len(done)} cached, {len(todo)} to compute")
    ledger = RetrievalLedger(WORK / "ledger.jsonl")
    import threading
    save_lock = threading.Lock()

    def one(m: dict) -> tuple[str, dict]:
        asof = datetime.fromtimestamp(m["asof_ts"], tz=timezone.utc)
        pipe = ForecastPipeline(
            client, ledger, CostLedger(), run_id="rung3",
            judge_model="sonnet", judge_effort="medium",
        )
        res = pipe.forecast(
            question_key=m["ticker"],
            question=m["title"],
            background=m["rules"],
            criteria=m["rules"],
            asof=asof.strftime("%Y-%m-%dT%H:%M:%SZ"),
            close=m["close_time"],
            market_p=m["price_at_asof"],
            mode="full",
        )
        return m["ticker"], {
            "p_yes": res.p_yes, "asof_ts": m["asof_ts"],
            "cost_cents": res.cost_cents, "error": res.error,
            "price_at_asof": m["price_at_asof"], "result": m["result"],
            "cache_keys": res.cache_keys,
        }

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(one, m): m["ticker"] for m in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            tkr, row = fut.result()
            with save_lock:
                done[tkr] = row
                fpath.parent.mkdir(parents=True, exist_ok=True)
                fpath.write_text(json.dumps(done, indent=1))
            log(f"  {i}/{len(todo)} {tkr} p={row['p_yes']} "
                f"cost={row['cost_cents']}c"
                + (f" ERR={row['error'][:60]}" if row["error"] else ""))
    return done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=18)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--gate", type=int, default=4)
    ap.add_argument("--select-only", action="store_true")
    args = ap.parse_args(argv)

    client = SubagentClient()
    markets = select_markets(client, args.n)
    log(f"selected {len(markets)} markets: "
        + ", ".join(m["ticker"] for m in markets))
    if args.select_only:
        return 0
    fmap = precompute_forecasts(client, markets, args.workers)

    forecasts = {
        t: PrecomputedForecast(
            ticker=t, p_yes=d["p_yes"], asof_ts=d["asof_ts"],
            cost_cents=d["cost_cents"],
        )
        for t, d in fmap.items() if d.get("p_yes") is not None
    }
    n_no_trade = sum(1 for d in fmap.values() if d.get("p_yes") is None)
    log(f"engine phase: {len(forecasts)} forecasts ({n_no_trade} no-trade)")

    provider = Rung3HistoryProvider(str(DB))
    config = EngineConfig(fee_schedule=FeeSchedule.load_default())
    OUT.mkdir(parents=True, exist_ok=True)
    report = full_report(
        provider,
        lambda: ForecasterStrategy(forecasts, gate_cents=args.gate),
        config=config,
        out_dir=str(OUT),
        market_filters={"tickers": sorted(forecasts)},
        label="rung3-forecaster",
    )
    log(f"net pnl after inference: "
        f"{report['base']['pnl']['net_pnl_cents_after_inference']}c")
    (OUT / "forecasts-used.json").write_text(json.dumps(fmap, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

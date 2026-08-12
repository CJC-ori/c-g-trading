"""SwingFade train backtest: per-market engine replays with exact fees.

Discipline (SPEC par.7/par.8, same shape as bot/strategies/panic/run_backtest.py):
- The market shortlist comes from the census (reports/swings/episodes.jsonl.gz):
  every market with >= 1 TRAIN-window episode (ts0 < t_split, the 60th
  percentile of episode times) in the three frozen cells at X=10/T=24
  (strategy.py PARAMS_RATIONALE). The strategy re-derives every trigger
  point-in-time from its own MarketView; the census supplies ONLY the
  ticker list.
- arm_before = t_split: a train run can never initiate a position at or
  after the held-out boundary, even though a market's life may extend past
  it (positions entered pre-split settle whenever they settle — same
  convention as the panic train run).
- One engine run per market, fresh $10k bankroll (per-episode EV is the
  deliverable; no cross-market compounding is claimed). Exact per-series
  fee schedule, maker queue ON, tick quantization via the store's
  price_ranges.
- Fee stress x1.5 is a second pass over markets that filled in the base
  pass (fee stress cannot create fills, only shrink net P&L).

Usage:
  python -m bot.strategies.swings.run_backtest [--limit N] [--seed 20260812]
Writes reports/swings/train_results.json.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import random
import statistics
from collections import defaultdict
from typing import Any

from bot.backtest.dataport import SqliteHistoryProvider
from bot.backtest.engine import BacktestResult, EngineConfig, run_backtest
from bot.backtest.fees import FeeSchedule
from bot.backtest.risk import RiskConfig
from bot.strategies.swings.strategy import CELLS, FROZEN, SwingFade

EPISODES_PATH = "/home/user/c-g-trading/reports/swings/episodes.jsonl.gz"
OUT_PATH = "/home/user/c-g-trading/reports/swings/train_results.json"
DB_PATH = "/home/user/c-g-trading/data/kalshi.db"
BANKROLL_CENTS = 1_000_000
TRAIN_FRAC = 0.60


def tail_cell(e: dict) -> str | None:
    if e["direction"] == "down" and e["ref_c"] >= FROZEN["panic_dip_min_ref_c"]:
        return "panic_dip"
    if e["direction"] == "up" and e["ref_c"] <= FROZEN["spike_fade_max_ref_c"]:
        return "spike_fade"
    return None


def cell_of(e: dict) -> str | None:
    t = tail_cell(e)
    if t is None:
        return None
    for name, (cats, direction) in CELLS.items():
        if e["category"] in cats and e["direction"] == direction:
            if (t == "panic_dip") == name.endswith("panic_dip"):
                return name
    return None


def load_universe() -> tuple[int, dict[str, set[str]], dict[str, int]]:
    """Returns (t_split, cell -> train tickers, ticker -> n train episodes)."""
    all_ts: list[int] = []
    rows: list[dict] = []
    with gzip.open(EPISODES_PATH, "rt") as fh:
        for line in fh:
            e = json.loads(line)
            all_ts.append(e["ts0"])
            if e["x"] == FROZEN["x_cents"] and e["t_h"] == FROZEN["t_hours"]:
                c = cell_of(e)
                if c is not None:
                    rows.append({"ticker": e["ticker"], "ts0": e["ts0"], "cell": c})
    all_ts.sort()
    t_split = all_ts[int(len(all_ts) * TRAIN_FRAC)]
    cells: dict[str, set[str]] = defaultdict(set)
    n_eps: dict[str, int] = defaultdict(int)
    for r in rows:
        if r["ts0"] < t_split:
            cells[r["cell"]].add(r["ticker"])
            n_eps[r["ticker"]] += 1
    return t_split, cells, n_eps


def run_market(provider: SqliteHistoryProvider, ticker: str, t_split: int,
               fee_schedule: FeeSchedule, fee_stress: float = 1.0
               ) -> tuple[BacktestResult, dict[str, Any]]:
    strategy = SwingFade(bankroll_cents=BANKROLL_CENTS, arm_before=t_split)
    config = EngineConfig(
        risk=RiskConfig(bankroll_cents=BANKROLL_CENTS),
        candle_period_s=3600,
        fee_stress=fee_stress,
        fee_schedule=fee_schedule,
        keep_events_in_memory=False,
    )
    res = run_backtest(provider, strategy, config,
                       market_filters={"tickers": [ticker]})
    return res, summarize(ticker, res)


def summarize(ticker: str, res: BacktestResult) -> dict[str, Any]:
    buys = [f for f in res.fills if f.action == "buy"]
    sells = [f for f in res.fills if f.action == "sell"]
    deployed = sum(f.price_cents * f.count for f in buys)
    settle = res.settlements.get(ticker)
    entry_orders = {oid: o for oid, o in res.orders.items()
                    if o["action"] == "buy" and o["size"] > 0}
    filled_entry_orders = {f.order_id for f in buys if f.order_id in entry_orders}
    arm_ts = sorted({o["ts"] for o in entry_orders.values()})
    entry_side = buys[0].side if buys else None
    adverse = (
        settle is not None and entry_side is not None
        and settle.result == ("no" if entry_side == "yes" else "yes")
    )
    lifetimes = sorted(o["opportunity_lifetime_s"] for o in entry_orders.values()
                       if o.get("opportunity_lifetime_s") is not None)
    return {
        "ticker": ticker,
        "result": settle.result if settle else None,
        "n_arm_events": len(arm_ts),
        "n_entry_orders": len(entry_orders),
        "n_entry_orders_filled": len(filled_entry_orders),
        "n_entry_fills": len(buys),
        "entered": bool(buys),
        "entry_side": entry_side,
        "contracts_in": sum(f.count for f in buys),
        "premium_deployed_cents": deployed,
        "exited": bool(sells),
        "contracts_out": sum(f.count for f in sells),
        "fees_cents": res.fees_cents,
        "net_pnl_cents": res.net_pnl_cents,
        "return_on_deployed": round(res.net_pnl_cents / deployed, 4) if deployed else None,
        "adverse": bool(buys) and adverse,
        "maker_entry_fills": sum(1 for f in buys if not f.is_taker),
        "median_opportunity_lifetime_s": (
            lifetimes[len(lifetimes) // 2] if lifetimes else None),
        "first_fill_ts": min((f.ts for f in buys), default=None),
    }


def _dist(vals: list[float]) -> dict[str, float] | None:
    if not vals:
        return None
    vs = sorted(vals)
    n = len(vs)
    return {"n": n, "min": round(vs[0], 4), "p25": round(vs[n // 4], 4),
            "median": round(vs[n // 2], 4), "p75": round(vs[(3 * n) // 4], 4),
            "max": round(vs[-1], 4), "mean": round(sum(vs) / n, 4),
            "total": round(sum(vs), 2)}


def aggregate(rows: list[dict], cell_tickers: dict[str, set[str]]) -> dict[str, Any]:
    def agg(rs: list[dict]) -> dict[str, Any]:
        entered = [r for r in rs if r["entered"]]
        n_adverse = sum(1 for r in entered if r["adverse"])
        total = sum(r["net_pnl_cents"] for r in rs)
        entry_orders = sum(r["n_entry_orders"] for r in rs)
        entry_orders_filled = sum(r.get("n_entry_orders_filled", 0) for r in rs)
        entry_fills = sum(r["n_entry_fills"] for r in rs)
        return {
            "markets_run": len(rs),
            "markets_entered": len(entered),
            "arm_events": sum(r["n_arm_events"] for r in rs),
            "entry_orders": entry_orders,
            "entry_orders_filled": entry_orders_filled,
            "entry_fills": entry_fills,
            "maker_order_fill_rate": round(entry_orders_filled / entry_orders, 4) if entry_orders else None,
            "hit_rate_entered": round(
                sum(1 for r in entered if r["net_pnl_cents"] > 0) / len(entered), 4
            ) if entered else None,
            "adverse_selection_rate": round(n_adverse / len(entered), 4) if entered else None,
            "total_net_pnl_dollars": round(total / 100, 2),
            "ev_per_entered_market_dollars": round(total / len(entered) / 100, 2) if entered else None,
            "total_fees_dollars": round(sum(r["fees_cents"] for r in rs) / 100, 2),
            "net_pnl_per_market_dollars": _dist([r["net_pnl_cents"] / 100 for r in entered]),
            "return_on_deployed": _dist([r["return_on_deployed"] for r in entered
                                         if r["return_on_deployed"] is not None]),
            "median_opportunity_lifetime_s": _dist(
                [r["median_opportunity_lifetime_s"] for r in entered
                 if r["median_opportunity_lifetime_s"] is not None]),
        }

    out = {"all": agg(rows)}
    for cell, tickers in cell_tickers.items():
        out[cell] = agg([r for r in rows if r["ticker"] in tickers])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="cap markets per cell (seeded random sample)")
    ap.add_argument("--seed", type=int, default=20260812)
    ap.add_argument("--fee-stress", type=float, default=1.5)
    args = ap.parse_args()

    t_split, cells, n_eps = load_universe()
    print(f"t_split = {dt.datetime.fromtimestamp(t_split, dt.timezone.utc):%Y-%m-%d}")
    rng = random.Random(args.seed)
    chosen: dict[str, set[str]] = {}
    for cell, tickers in cells.items():
        tl = sorted(tickers)
        if args.limit is not None and len(tl) > args.limit:
            tl = rng.sample(tl, args.limit)
        chosen[cell] = set(tl)
        print(f"{cell}: {len(tickers)} train markets, running {len(tl)}")

    all_tickers = sorted(set().union(*chosen.values()))
    fee_schedule = FeeSchedule.load_default()
    provider = SqliteHistoryProvider(DB_PATH)

    rows: list[dict] = []
    for i, tkr in enumerate(all_tickers):
        _res, s = run_market(provider, tkr, t_split, fee_schedule)
        rows.append(s)
        if (i + 1) % 100 == 0:
            print(f"...{i + 1}/{len(all_tickers)} markets, "
                  f"{sum(1 for r in rows if r['entered'])} entered, "
                  f"net ${sum(r['net_pnl_cents'] for r in rows) / 100:.2f}")

    # fee-stress pass on markets that filled
    stress_rows: list[dict] = []
    for tkr in [r["ticker"] for r in rows if r["entered"]]:
        _res, s = run_market(provider, tkr, t_split, fee_schedule,
                             fee_stress=args.fee_stress)
        stress_rows.append(s)

    out = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "t_split": t_split,
        "t_split_iso": dt.datetime.fromtimestamp(t_split, dt.timezone.utc).isoformat(),
        "frozen_params": {k: (list(v) if isinstance(v, tuple) else v)
                          for k, v in FROZEN.items()},
        "cells": {c: sorted(t) for c, t in chosen.items()},
        "limit": args.limit,
        "aggregates": aggregate(rows, chosen),
        "aggregates_fee_stress": {
            "stress": args.fee_stress,
            **aggregate(stress_rows, chosen),
        },
        "per_market": rows,
    }
    with open(OUT_PATH, "w") as fh:
        json.dump(out, fh, indent=1)
    a = out["aggregates"]["all"]
    print(f"\nALL: run={a['markets_run']} entered={a['markets_entered']} "
          f"hit={a['hit_rate_entered']} adverse={a['adverse_selection_rate']} "
          f"net=${a['total_net_pnl_dollars']} "
          f"EV/entered=${a['ev_per_entered_market_dollars']}")
    for cell in chosen:
        c = out["aggregates"][cell]
        print(f"{cell}: entered={c['markets_entered']} "
              f"adverse={c['adverse_selection_rate']} "
              f"net=${c['total_net_pnl_dollars']} "
              f"EV/entered=${c['ev_per_entered_market_dollars']}")
    print("wrote", OUT_PATH)


if __name__ == "__main__":
    main()

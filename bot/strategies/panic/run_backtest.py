"""P-3 backtest runner: per-episode engine replays with exact fees.

Discipline (SPEC §7/§8):
- Episodes come from reports/panic/episodes.json (episodes.py discovery).
- Time split by episode window_start: train = first 60%, held-out = last
  40%. The held-out set is NEVER run here except the single sanctioned
  Michigan case-study replay (--michigan), which the brief demands and
  which is already fully documented in docs/viability.md §2 — it counts
  toward nothing.
- Every episode runs through bot/backtest's engine (exact per-series fee
  schedule, maker-queue model, tick quantization), one market per run,
  fresh $10k bankroll per episode (per-episode EV is the deliverable; no
  cross-episode compounding is claimed).
- Fee stress x1.5 is run as a second pass on the train set.

Usage:
  python -m bot.strategies.panic.run_backtest            # train split
  python -m bot.strategies.panic.run_backtest --michigan # case study only
Writes reports/panic/train_results.json (and michigan_replay.json).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
from typing import Any

from bot.backtest.engine import BacktestResult, EngineConfig, run_backtest
from bot.backtest.fees import FeeSchedule
from bot.backtest.risk import RiskConfig
from bot.strategies.panic.provider import EpisodeProvider
from bot.strategies.panic.strategy import (
    EpisodeWindow,
    R4PanicDipLadder,
    R4bConvexityNo,
)

EPISODES_PATH = "/home/user/c-g-trading/reports/panic/episodes.json"
OUT_DIR = "/home/user/c-g-trading/reports/panic"
MICHIGAN_TICKER = "KXSENATEMID-26-AELS"
TRAIN_FRAC = 0.60
BANKROLL_CENTS = 1_000_000


def load_episodes(path: str = EPISODES_PATH) -> list[dict]:
    with open(path) as fh:
        data = json.load(fh)
    eps = data["episodes"]
    eps.sort(key=lambda e: (e["window_start"], e["ticker"]))
    return eps


def split_episodes(eps: list[dict]) -> tuple[list[dict], list[dict]]:
    k = int(len(eps) * TRAIN_FRAC)
    return eps[:k], eps[k:]


def run_episode(
    ep: dict, strategy_name: str, fee_stress: float = 1.0
) -> tuple[BacktestResult, dict[str, Any]]:
    provider = EpisodeProvider(
        ticker=ep["ticker"],
        window_start=ep["window_start"],
        window_end=ep["window_end"],
        trades_cover_window=bool(ep["trades_cover_window"]),
    )
    period = provider.chosen_period_s
    windows = {
        ep["ticker"]: EpisodeWindow(
            ep["ticker"], ep["window_start"], ep["window_end"]
        )
    }
    cls = R4PanicDipLadder if strategy_name == "R4" else R4bConvexityNo
    strategy = cls(windows, bankroll_cents=BANKROLL_CENTS, candle_period_s=period)
    config = EngineConfig(
        risk=RiskConfig(bankroll_cents=BANKROLL_CENTS),
        candle_period_s=period,
        fee_stress=fee_stress,
        fee_schedule=FeeSchedule.load_default(),
        keep_events_in_memory=False,
    )
    result = run_backtest(
        provider, strategy, config, market_filters={"tickers": [ep["ticker"]]}
    )
    return result, summarize_run(ep, strategy_name, period, result)


def summarize_run(
    ep: dict, strategy_name: str, period: int, res: BacktestResult
) -> dict[str, Any]:
    buys = [f for f in res.fills if f.action == "buy"]
    sells = [f for f in res.fills if f.action == "sell"]
    deployed = sum(f.price_cents * f.count for f in buys)
    settle = res.settlements.get(ep["ticker"])
    resting_orders = [
        o for o in res.orders.values() if not o["is_taker_at_entry"] and o["size"] > 0
    ]
    filled_order_ids = {f.order_id for f in res.fills}
    lifetimes = sorted(
        o["opportunity_lifetime_s"]
        for o in res.orders.values()
        if o.get("opportunity_lifetime_s") is not None
    )
    return {
        "ticker": ep["ticker"],
        "et_date": ep["et_date"],
        "outcome_class": ep["outcome_class"],
        "result": settle.result if settle else None,
        "strategy": strategy_name,
        "period_s": period,
        "data_resolution": ep["data_resolution"],
        "n_intents": len(res.intents),
        "n_orders": len(res.orders),
        "n_resting_orders": len(resting_orders),
        "n_resting_filled": sum(
            1 for o_id in filled_order_ids
            if o_id in res.orders and not res.orders[o_id]["is_taker_at_entry"]
        ),
        "entry_fills": [
            {"ts": f.ts, "price_cents": f.price_cents, "count": f.count,
             "is_taker": f.is_taker, "fee_cents": f.fee_cents}
            for f in buys
        ],
        "exit_fills": [
            {"ts": f.ts, "price_cents": f.price_cents, "count": f.count,
             "is_taker": f.is_taker, "fee_cents": f.fee_cents}
            for f in sells
        ],
        "entered": bool(buys),
        "exited": bool(sells),
        "contracts_in": sum(f.count for f in buys),
        "premium_deployed_cents": deployed,
        "fees_cents": res.fees_cents,
        "net_pnl_cents": res.net_pnl_cents,
        "return_on_deployed": (
            round(res.net_pnl_cents / deployed, 4) if deployed else None
        ),
        "median_opportunity_lifetime_s": (
            lifetimes[len(lifetimes) // 2] if lifetimes else None
        ),
    }


def _dist(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    vs = sorted(values)
    return {
        "n": len(vs),
        "min": vs[0],
        "p25": vs[len(vs) // 4],
        "median": vs[len(vs) // 2],
        "p75": vs[(3 * len(vs)) // 4],
        "max": vs[-1],
        "mean": round(statistics.mean(vs), 4),
        "total": round(sum(vs), 2),
    }


def aggregate(rows: list[dict], strategy_name: str) -> dict[str, Any]:
    rows = [r for r in rows if r["strategy"] == strategy_name]
    entered = [r for r in rows if r["entered"]]
    per_class: dict[str, Any] = {}
    for cls in ("no-dip", "dip-and-revert", "dip-and-die", "died-without-dip"):
        crows = [r for r in rows if r["outcome_class"] == cls]
        centered = [r for r in crows if r["entered"]]
        per_class[cls] = {
            "episodes": len(crows),
            "entered": len(centered),
            "hit": sum(1 for r in centered if r["net_pnl_cents"] > 0),
            "net_pnl_dollars": _dist(
                [r["net_pnl_cents"] / 100 for r in centered]
            ),
            "return_on_deployed": _dist(
                [r["return_on_deployed"] for r in centered
                 if r["return_on_deployed"] is not None]
            ),
        }
    adverse_den = len(entered)
    adverse_num = sum(1 for r in entered if r["result"] == "no")
    total_net = sum(r["net_pnl_cents"] for r in rows)
    return {
        "strategy": strategy_name,
        "episodes_run": len(rows),
        "episodes_entered": adverse_den,
        "episodes_exited": sum(1 for r in entered if r["exited"]),
        "fills_total": sum(len(r["entry_fills"]) + len(r["exit_fills"]) for r in rows),
        "resting_orders": sum(r["n_resting_orders"] for r in rows),
        "resting_filled": sum(r["n_resting_filled"] for r in rows),
        "adverse_selection_rate_fills": (
            round(adverse_num / adverse_den, 4) if adverse_den else None
        ),
        "total_net_pnl_dollars": round(total_net / 100, 2),
        "ev_per_entered_episode_dollars": (
            round(total_net / adverse_den / 100, 2) if adverse_den else None
        ),
        "ev_per_episode_dollars": (
            round(total_net / len(rows) / 100, 2) if rows else None
        ),
        "per_class": per_class,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--michigan", action="store_true",
                    help="sanctioned single-episode case study (held-out)")
    ap.add_argument("--fee-stress", type=float, default=1.5,
                    help="second-pass stress multiplier on train")
    args = ap.parse_args()

    eps = load_episodes()
    train, test = split_episodes(eps)

    if args.michigan:
        mi = [e for e in eps if e["ticker"] == MICHIGAN_TICKER]
        if not mi:
            raise SystemExit("Michigan episode not found in episodes.json")
        out: dict[str, Any] = {"note": (
            "SANCTIONED HELD-OUT CASE STUDY (docs/viability.md §2, brief item 4). "
            "Counts toward no aggregate; parameters frozen before this run."
        )}
        for name in ("R4", "R4b"):
            _res, summary = run_episode(mi[0], name)
            out[name] = summary
        path = f"{OUT_DIR}/michigan_replay.json"
        with open(path, "w") as fh:
            json.dump(out, fh, indent=1)
        for name in ("R4", "R4b"):
            s = out[name]
            print(f"{name}: entered={s['entered']} contracts={s['contracts_in']} "
                  f"deployed=${s['premium_deployed_cents']/100:.2f} "
                  f"net=${s['net_pnl_cents']/100:.2f} ret={s['return_on_deployed']}")
            for f in s["entry_fills"][:5]:
                print("   entry", f)
            for f in s["exit_fills"][:5]:
                print("   exit ", f)
        print("wrote", path)
        return

    rows: list[dict] = []
    rows_stress: list[dict] = []
    for i, ep in enumerate(train):
        for name in ("R4", "R4b"):
            _res, summary = run_episode(ep, name)
            rows.append(summary)
            _res2, summary2 = run_episode(ep, name, fee_stress=args.fee_stress)
            rows_stress.append(summary2)
        if (i + 1) % 20 == 0:
            print(f"...{i + 1}/{len(train)} episodes")

    # Kill-criterion (b) input: ladder-fillable episodes over the FULL
    # history, straight from the taxonomy (min traded price <= top rung).
    # No engine run touches the held-out split for this count.
    fillable_full = [e for e in eps if e["min_price_cents"] <= 85.0]

    out = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "split": {
            "n_total": len(eps),
            "n_train": len(train),
            "n_heldout": len(test),
            "train_last_date": train[-1]["et_date"] if train else None,
            "heldout_first_date": test[0]["et_date"] if test else None,
        },
        "full_history_fillable_episodes": [
            {"ticker": e["ticker"], "et_date": e["et_date"],
             "outcome_class": e["outcome_class"],
             "min_price_cents": e["min_price_cents"],
             "split": "train" if e in train else "heldout"}
            for e in fillable_full
        ],
        "aggregates": {
            "R4": aggregate(rows, "R4"),
            "R4b": aggregate(rows, "R4b"),
        },
        "aggregates_fee_stress": {
            "stress": args.fee_stress,
            "R4": aggregate(rows_stress, "R4"),
            "R4b": aggregate(rows_stress, "R4b"),
        },
        "per_episode": rows,
    }
    path = f"{OUT_DIR}/train_results.json"
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1)
    for name in ("R4", "R4b"):
        a = out["aggregates"][name]
        print(f"{name}: run={a['episodes_run']} entered={a['episodes_entered']} "
              f"adverse={a['adverse_selection_rate_fills']} "
              f"net=${a['total_net_pnl_dollars']} "
              f"EV/entered=${a['ev_per_entered_episode_dollars']}")
    print(f"fillable episodes full history: {len(fillable_full)}")
    print("wrote", path)


if __name__ == "__main__":
    main()

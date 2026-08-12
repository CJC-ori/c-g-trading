"""Tournament ROUND 4 — the single held-out evaluation.

Per reports/TOURNAMENT_PROTOCOL.md Round 4 and the pre-commitments in
reports/tournament/round4/PRECOMMIT.md: the champion (ia_econ_only) and the
kept runner-up (ib_swing_filter) get ONE base run each on the held-out 40%
(settle_ts in (split_ts=1722556901, t1=1786483329], pinned universe files),
plus the SPEC §6 companion runs (capacity 3x/10x, fee stress x1.5).
Numbers are reported as-is; no iteration afterward.

Restart-resilient design: each (variant, run-type, shard) is an idempotent
unit writing shards/<shard>_<run>.json; existing outputs are skipped.
Shard merge rules are documented in PRECOMMIT.md and implemented in
merge_variant(); bankroll-coupling diagnostics (max concurrent deployed,
total-deploy / no-borrow clamp counts) are saved per shard so the merge's
exactness is checkable.

Usage:
  python -m bot.strategies.tournament.run_round4 --variant ia_econ_only \
      --tickers-file reports/tournament/round4/universe_heldout_econ.json \
      --run base --out reports/tournament/round4
  python -m bot.strategies.tournament.run_round4 --variant ib_swing_filter \
      --tickers-file reports/tournament/round4/universe_heldout.json \
      --run base --shard 0 --n-shards 4 --out reports/tournament/round4
  python -m bot.strategies.tournament.run_round4 --variant ia_econ_only \
      --merge --out reports/tournament/round4
"""
from __future__ import annotations

import argparse
import csv
import glob as globmod
import json
import os
import time
from dataclasses import replace
from typing import Any

from bot.backtest import metrics
from bot.backtest.dataport import SqliteHistoryProvider
from bot.backtest.engine import EngineConfig, run_backtest
from bot.backtest.fees import FeeSchedule
from bot.backtest.fills import MakerQueueConfig
from bot.strategies import flb, flb_analysis as fa
from bot.strategies.tournament.wrappers import R5SwingFiltered

HOUR = 3600
WINDOW_S = 6 * HOUR
RUNS = ("base", "cap3", "cap10", "stress")

VARIANTS = {
    "ia_econ_only": {
        "factory": lambda: flb.R5Endgame(
            window_s=WINDOW_S, categories=("Economics",)
        ),
        "description": "CHAMPION ia_econ_only: R5-6h maker-join 90-98c, "
                       "Economics only (frozen Round-2/3 config)",
    },
    "ib_swing_filter": {
        "factory": lambda: R5SwingFiltered(window_s=WINDOW_S),
        "description": "RUNNER-UP ib_swing_filter: R5-6h, entries blocked "
                       "within 24h of a >=20c/24h adverse swing (X20_T24)",
    },
}


def engine_config() -> EngineConfig:
    return EngineConfig(
        fee_schedule=FeeSchedule.load_default(),
        maker_queue=MakerQueueConfig(),
        keep_events_in_memory=False,
    )


def config_for_run(run: str, fee_stress: float) -> EngineConfig:
    cfg = engine_config()
    if run == "cap3":
        return replace(cfg, risk=replace(cfg.risk, depth_multiplier=3.0))
    if run == "cap10":
        return replace(cfg, risk=replace(cfg.risk, depth_multiplier=10.0))
    if run == "stress":
        return replace(cfg, fee_stress=fee_stress)
    return cfg


def clamp_diagnostics(result) -> dict[str, Any]:
    """Bankroll-coupling diagnostics for the shard-merge validity check."""
    counts: dict[str, int] = {}
    for i in result.intents:
        for r in i.get("clamp_reasons") or i.get("reasons") or ():
            counts[r] = counts.get(r, 0) + 1
    max_deployed = max((v for _, v in result.deployed_curve), default=0)
    return {
        "clamp_reason_counts": counts,
        "max_concurrent_deployed_cents": max_deployed,
        "final_cash_cents": result.final_cash_cents,
    }


def fill_rate_raw(result) -> dict[str, Any]:
    """Raw numerators/denominators so fill rates merge exactly across
    shards (fa.fill_stats only returns the ratios)."""
    filled_by_order: dict[int, int] = {}
    for f in result.fills:
        filled_by_order[f.order_id] = filled_by_order.get(f.order_id, 0) + f.count
    out = {}
    for kind, taker in (("maker", False), ("taker", True)):
        ids = [oid for oid, o in result.orders.items()
               if o["is_taker_at_entry"] is taker]
        out[kind] = {
            "n_orders": len(ids),
            "n_orders_filled": sum(1 for oid in ids
                                   if filled_by_order.get(oid, 0) > 0),
            "contracts_ordered": sum(result.orders[oid]["size"] for oid in ids),
            "contracts_filled": sum(filled_by_order.get(oid, 0) for oid in ids),
        }
    return out


def run_unit(variant: str, run: str, tickers: list[str], shard: int,
             n_shards: int, out_dir: str, fee_stress: float,
             db: str) -> None:
    vdir = os.path.join(out_dir, variant, "shards")
    os.makedirs(vdir, exist_ok=True)
    tag = f"s{shard}of{n_shards}" if n_shards > 1 else "all"
    path = os.path.join(vdir, f"{tag}_{run}.json")
    if os.path.exists(path):
        print(f"[skip] {path} exists (idempotent resume)", flush=True)
        return
    part = tickers[shard::n_shards] if n_shards > 1 else tickers
    print(f"[{time.strftime('%H:%M:%S')}] {variant} {run} {tag}: "
          f"{len(part)} markets ...", flush=True)
    t0 = time.time()
    provider = SqliteHistoryProvider(db)
    cfg = config_for_run(run, fee_stress)
    result = run_backtest(provider, VARIANTS[variant]["factory"](), cfg,
                          {"tickers": sorted(part)})
    dur = time.time() - t0
    payload: dict[str, Any] = {
        "variant": variant, "run": run, "shard": tag,
        "n_markets": len(part), "duration_s": round(dur, 1),
        "net_pnl_cents_after_inference": result.net_pnl_after_inference_cents,
        "fees_cents": result.fees_cents,
        "contracts_filled": sum(f.count for f in result.fills),
        "diagnostics": clamp_diagnostics(result),
    }
    if run == "base":
        payload["metrics"] = metrics.compute_metrics(result)
        payload["episodes"] = fa.episodes(result)
        payload["fill_stats"] = fa.fill_stats(result)
        payload["fill_rate_raw"] = fill_rate_raw(result)
        payload["per_market"] = {
            tk: {"net_pnl_cents": m["net_pnl_cents"],
                 "contracts_traded": m["contracts_traded"],
                 "category": m.get("category"),
                 "first_entry_ts": m.get("first_entry_ts")}
            for tk, m in result.per_market.items()
            if m["contracts_traded"] > 0
        }
        payload["fills"] = [
            {"ticker": f.ticker, "ts": f.ts, "side": f.side,
             "action": f.action, "price_cents": f.price_cents,
             "count": f.count, "is_taker": f.is_taker,
             "order_id": f.order_id}
            for f in result.fills
        ]
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh, indent=1, default=str)
    os.replace(tmp, path)
    print(f"[{time.strftime('%H:%M:%S')}] done {tag} {run}: "
          f"net={payload['net_pnl_cents_after_inference']}c "
          f"({dur:.0f}s)", flush=True)


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def merge_variant(variant: str, out_dir: str, fee_stress: float) -> dict:
    vdir = os.path.join(out_dir, variant)
    shard_files = sorted(globmod.glob(os.path.join(vdir, "shards", "*.json")))
    by_run: dict[str, list[dict]] = {}
    for p in shard_files:
        with open(p) as fh:
            d = json.load(fh)
        by_run.setdefault(d["run"], []).append(d)
    missing = [r for r in RUNS if r not in by_run]
    if "base" in missing:
        raise SystemExit(f"{variant}: missing base run")
    # Companion runs (capacity/stress) may have been cancelled when the
    # base run already determined the gate verdict (documented in FINAL.md).

    base_shards = by_run["base"]
    episodes = [e for d in base_shards for e in d["episodes"]]
    episodes.sort(key=lambda e: (e["entry_ts"], e["ticker"]))
    per_market: dict[str, dict] = {}
    for d in base_shards:
        per_market.update(d["per_market"])

    # fill rates from merged raw counts
    raw = {k: {f: 0 for f in ("n_orders", "n_orders_filled",
                              "contracts_ordered", "contracts_filled")}
           for k in ("maker", "taker")}
    for d in base_shards:
        for k in raw:
            for f in raw[k]:
                raw[k][f] += d["fill_rate_raw"][k][f]

    def _rate(n, den):
        return n / den if den else None

    fill_stats = {
        "n_maker_orders": raw["maker"]["n_orders"],
        "n_taker_orders": raw["taker"]["n_orders"],
        "maker_order_fill_rate": _rate(raw["maker"]["n_orders_filled"],
                                       raw["maker"]["n_orders"]),
        "maker_contract_fill_rate": _rate(raw["maker"]["contracts_filled"],
                                          raw["maker"]["contracts_ordered"]),
        "taker_order_fill_rate": _rate(raw["taker"]["n_orders_filled"],
                                       raw["taker"]["n_orders"]),
        "taker_contract_fill_rate": _rate(raw["taker"]["contracts_filled"],
                                          raw["taker"]["contracts_ordered"]),
        "maker_contracts_filled": raw["maker"]["contracts_filled"],
        "taker_contracts_filled": raw["taker"]["contracts_filled"],
    }

    # concentration from merged per-market pnl
    pnls = {tk: m["net_pnl_cents"] for tk, m in per_market.items()}
    ranked = sorted(pnls.items(), key=lambda kv: kv[1], reverse=True)
    total = sum(pnls.values())
    concentration = {
        "n_markets_traded": len(pnls),
        "top5_share_of_net": (sum(v for _, v in ranked[:5]) / total
                              if total > 0 else None),
        "worst_market_loss_cents": min(pnls.values()) if pnls else 0,
        "top5": [{"ticker": tk, "net_pnl_cents": v} for tk, v in ranked[:5]],
    }

    def _sum(run, field):
        return sum(d[field] for d in by_run[run])

    report = {
        "label": variant,
        "description": VARIANTS[variant]["description"],
        "fee_mode": "exact-per-series",
        "n_markets_replayed": _sum("base", "n_markets"),
        "n_shards": len(base_shards),
        "base": {
            "net_pnl_cents_after_inference":
                _sum("base", "net_pnl_cents_after_inference"),
            "fees_cents": _sum("base", "fees_cents"),
            "contracts_filled": _sum("base", "contracts_filled"),
            "concentration": concentration,
        },
        "capacity_curve": [
            {"depth_multiplier": m,
             "net_pnl_cents_after_inference":
                 _sum(r, "net_pnl_cents_after_inference")
                 if r in by_run else None,
             "contracts_filled": _sum(r, "contracts_filled")
                 if r in by_run else None}
            for m, r in ((1.0, "base"), (3.0, "cap3"), (10.0, "cap10"))
        ],
        "fee_stress": {
            "multiplier": fee_stress,
            "net_pnl_cents_after_inference":
                _sum("stress", "net_pnl_cents_after_inference")
                if "stress" in by_run else None,
            "still_positive":
                _sum("stress", "net_pnl_cents_after_inference") > 0
                if "stress" in by_run else None,
        },
        "runs_missing": missing,
        "inference_stress": {  # price-only: zero inference cost
            "multiplier": 2.0, "inference_cost_cents": 0,
            "net_pnl_cents_after_inference":
                _sum("base", "net_pnl_cents_after_inference"),
            "still_positive":
                _sum("base", "net_pnl_cents_after_inference") > 0,
        },
        "fill_stats": fill_stats,
        "merge_diagnostics": {
            d["shard"] + ":" + d["run"]: d["diagnostics"]
            for run in RUNS for d in by_run.get(run, [])
        },
        "episode_stats": fa.episode_return_stats(episodes),
        "per_category": fa.per_category_stats(episodes),
    }

    # F2 pre-committed contamination breakout
    flb_uni = json.load(open("reports/flb/universe.json"))
    flb_train = set(flb_uni["train_tickers"])
    pull_lo = flb_uni["t0"]
    prov = SqliteHistoryProvider("data/kalshi.db")
    settle = {}
    tick_list = sorted({e["ticker"] for e in episodes})
    for m in prov.iter_markets(tickers=tick_list):
        s = prov.settlement(m.ticker)
        settle[m.ticker] = s.settled_ts if s else m.close_ts
    for e in episodes:
        e["settle_ts"] = settle.get(e["ticker"])
        e["flb_train_ticker"] = e["ticker"] in flb_train
        e["subwindow"] = ("CONTAMINATED" if e["settle_ts"] is not None
                          and e["settle_ts"] >= pull_lo else "CLEAN")
    clean = [e for e in episodes if e["subwindow"] == "CLEAN"]
    contam = [e for e in episodes if e["subwindow"] == "CONTAMINATED"]
    flbeps = [e for e in episodes if e["flb_train_ticker"]]
    report["f2_breakout"] = {
        "flb_pull_window_start_ts": pull_lo,
        "clean": fa.episode_return_stats(clean),
        "contaminated": fa.episode_return_stats(contam),
        "flb_train_ticker_episodes": fa.episode_return_stats(flbeps),
        "n_clean": len(clean), "n_contaminated": len(contam),
        "n_flb_train_ticker_episodes": len(flbeps),
    }

    with open(os.path.join(vdir, "report.json"), "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    if episodes:
        with open(os.path.join(vdir, "episodes.csv"), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(episodes[0].keys()))
            w.writeheader()
            w.writerows(episodes)
    print(f"merged {variant}: net="
          f"{report['base']['net_pnl_cents_after_inference']}c "
          f"episodes={len(episodes)}", flush=True)
    return report


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default="data/kalshi.db")
    ap.add_argument("--out", default="reports/tournament/round4")
    ap.add_argument("--variant", choices=sorted(VARIANTS))
    ap.add_argument("--tickers-file")
    ap.add_argument("--run", choices=RUNS)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--n-shards", type=int, default=1)
    ap.add_argument("--fee-stress", type=float, default=1.5)
    ap.add_argument("--merge", action="store_true")
    args = ap.parse_args(argv)

    if args.merge:
        merge_variant(args.variant, args.out, args.fee_stress)
        return
    with open(args.tickers_file) as fh:
        tickers = json.load(fh)["tickers"]
    run_unit(args.variant, args.run, tickers, args.shard, args.n_shards,
             args.out, args.fee_stress, args.db)


if __name__ == "__main__":
    main()

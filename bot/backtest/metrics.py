"""Run metrics + reporting (SPEC.md §6-7).

`compute_metrics(result)` produces a JSON-safe dict for one run.
`full_report(...)` additionally re-runs the backtest for the capacity curve
(1x/3x/10x depth caps) and the fee-stress (x1.5) check, computes the
time/category splits, and writes report.json + report.md.

Brier is scored ONLY at instants where the strategy stated a p_hat, against
the market mid at the same instant, both against the eventual resolution -
the market mid is the baseline to beat.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace
from typing import Any, Callable

from bot.backtest.engine import BacktestResult, EngineConfig, run_backtest
from bot.backtest.types import Strategy

SECONDS_PER_YEAR = 365 * 86400


# ---------------------------------------------------------------------------
# Individual metrics
# ---------------------------------------------------------------------------

def brier_vs_baseline(result: BacktestResult) -> dict[str, Any]:
    """Strategy p_hat vs market mid, both scored at the same instants."""
    pairs: list[tuple[float, float, int]] = []  # (p_hat, mid_prob, outcome)
    for d in result.decisions:
        s = result.settlements.get(d.ticker)
        if s is None or s.result == "void" or d.market_mid_cents is None:
            continue
        outcome = 1 if s.result == "yes" else 0
        pairs.append((d.p_hat, d.market_mid_cents / 100.0, outcome))
    if not pairs:
        return {"n": 0, "strategy": None, "market_baseline": None, "beats_baseline": None}
    strat = sum((p - o) ** 2 for p, _, o in pairs) / len(pairs)
    base = sum((m - o) ** 2 for _, m, o in pairs) / len(pairs)
    return {
        "n": len(pairs),
        "strategy": strat,
        "market_baseline": base,
        "beats_baseline": strat <= base,
    }


def calibration(result: BacktestResult, n_bins: int = 10) -> dict[str, Any]:
    """Reliability curve over p_hat + expected calibration error."""
    binned: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for d in result.decisions:
        s = result.settlements.get(d.ticker)
        if s is None or s.result == "void":
            continue
        outcome = 1 if s.result == "yes" else 0
        idx = min(n_bins - 1, int(d.p_hat * n_bins))
        binned[idx].append((d.p_hat, outcome))
    curve = []
    total = sum(len(b) for b in binned)
    ece = 0.0
    for i, b in enumerate(binned):
        if not b:
            continue
        conf = sum(p for p, _ in b) / len(b)
        freq = sum(o for _, o in b) / len(b)
        curve.append(
            {
                "bin": f"[{i / n_bins:.1f},{(i + 1) / n_bins:.1f})",
                "n": len(b),
                "mean_p_hat": conf,
                "observed_freq": freq,
            }
        )
        ece += (len(b) / total) * abs(freq - conf)
    return {"n": total, "ece": ece if total else None, "curve": curve}


def max_drawdown(equity_curve: list[tuple[int, int]]) -> dict[str, Any]:
    peak = None
    worst_cents = 0
    worst_frac = 0.0
    for _, eq in equity_curve:
        if peak is None or eq > peak:
            peak = eq
        dd = peak - eq
        if dd > worst_cents:
            worst_cents = dd
            worst_frac = dd / peak if peak > 0 else 0.0
    return {"max_drawdown_cents": worst_cents, "max_drawdown_frac_of_peak": worst_frac}


def concentration(result: BacktestResult) -> dict[str, Any]:
    pnls = {
        tk: m["net_pnl_cents"]
        for tk, m in result.per_market.items()
        if m["contracts_traded"] > 0
    }
    if not pnls:
        return {
            "n_markets_traded": 0, "top5_share_of_net": None,
            "worst_market_loss_cents": 0, "top5": [],
        }
    ranked = sorted(pnls.items(), key=lambda kv: kv[1], reverse=True)
    total = sum(pnls.values())
    top5 = ranked[:5]
    return {
        "n_markets_traded": len(pnls),
        "top5_share_of_net": (
            sum(v for _, v in top5) / total if total > 0 else None
        ),
        "worst_market_loss_cents": min(pnls.values()),
        "top5": [{"ticker": tk, "net_pnl_cents": v} for tk, v in top5],
    }


def trade_stats(result: BacktestResult) -> dict[str, Any]:
    requested = sum(i["requested"] for i in result.intents)
    ordered = sum(i["clamped"] for i in result.intents)
    filled = sum(f.count for f in result.fills)
    taker = sum(f.count for f in result.fills if f.is_taker)
    maker = filled - taker
    orders_any_fill = len({f.order_id for f in result.fills})
    # Average edge at entry: strategy-stated win prob minus premium paid.
    edge_num = 0.0
    edge_den = 0
    for f in result.fills:
        p_hat = result.orders.get(f.order_id, {}).get("p_hat")
        if p_hat is None:
            continue
        yes_buy = (f.side == "yes") == (f.action == "buy")
        yes_price = f.price_cents if f.side == "yes" else 100 - f.price_cents
        p_win = p_hat if yes_buy else 1 - p_hat
        cost = yes_price if yes_buy else 100 - yes_price
        edge_num += (p_win * 100 - cost) * f.count
        edge_den += f.count
    holds = [
        m["exit_ts"] - m["first_entry_ts"]
        for m in result.per_market.values()
        if m["first_entry_ts"] is not None and m["exit_ts"] is not None
    ]
    return {
        "n_decision_calls": result.n_decision_calls,
        "n_intents": len(result.intents),
        "n_orders": len(result.orders),
        "contracts_requested": requested,
        "contracts_ordered": ordered,
        "contracts_filled": filled,
        "fill_rate_vs_ordered": filled / ordered if ordered else None,
        "fill_rate_vs_requested": filled / requested if requested else None,
        "order_any_fill_rate": len(result.orders) and orders_any_fill / len(result.orders),
        "taker_contracts": taker,
        "maker_contracts": maker,
        "maker_share": maker / filled if filled else None,
        "avg_edge_at_entry_cents": edge_num / edge_den if edge_den else None,
        "avg_holding_period_s": sum(holds) / len(holds) if holds else None,
    }


def _time_weighted_mean(curve: list[tuple[int, int]]) -> float:
    if len(curve) < 2:
        return float(curve[0][1]) if curve else 0.0
    num = 0.0
    den = 0.0
    for (t0, v0), (t1, _) in zip(curve, curve[1:]):
        dt = max(0, t1 - t0)
        num += v0 * dt
        den += dt
    return num / den if den else float(curve[-1][1])


def pnl_summary(result: BacktestResult) -> dict[str, Any]:
    span = (
        result.equity_curve[-1][0] - result.equity_curve[0][0]
        if len(result.equity_curve) >= 2
        else 0
    )
    avg_deployed = _time_weighted_mean(result.deployed_curve)
    net = result.net_pnl_cents
    net_after_inf = result.net_pnl_after_inference_cents
    annualized = (
        net_after_inf / avg_deployed * (SECONDS_PER_YEAR / span)
        if avg_deployed > 0 and span > 0
        else None
    )
    return {
        "bankroll_cents": result.bankroll_cents,
        "net_pnl_cents_gross_of_inference": net,
        "net_pnl_cents_after_inference": net_after_inf,
        "net_pnl_pct_of_bankroll": net_after_inf / result.bankroll_cents,
        "fees_cents": result.fees_cents,
        "inference_cost_cents": result.inference_cost_cents,
        "avg_deployed_cents": avg_deployed,
        "run_span_s": span,
        "annualized_return_on_avg_deployed": annualized,
    }


def splits(result: BacktestResult, train_frac: float = 0.6) -> dict[str, Any]:
    """Robustness splits: by time (first 60% vs last 40% of the run, markets
    assigned by first entry time) and by category."""
    if not result.equity_curve:
        return {"time": None, "category": {}}
    t0 = result.equity_curve[0][0]
    t1 = result.equity_curve[-1][0]
    split_ts = t0 + int(train_frac * (t1 - t0))
    time_split = {
        "split_ts": split_ts,
        "train": {"net_pnl_cents": 0, "n_markets": 0},
        "test": {"net_pnl_cents": 0, "n_markets": 0},
    }
    by_cat: dict[str, dict[str, int]] = {}
    for m in result.per_market.values():
        if m["contracts_traded"] == 0 or m["first_entry_ts"] is None:
            continue
        bucket = "train" if m["first_entry_ts"] < split_ts else "test"
        time_split[bucket]["net_pnl_cents"] += m["net_pnl_cents"]
        time_split[bucket]["n_markets"] += 1
        cat = m["category"] or "(uncategorized)"
        c = by_cat.setdefault(cat, {"net_pnl_cents": 0, "n_markets": 0})
        c["net_pnl_cents"] += m["net_pnl_cents"]
        c["n_markets"] += 1
    return {"time": time_split, "category": by_cat}


def compute_metrics(result: BacktestResult) -> dict[str, Any]:
    return {
        "pnl": pnl_summary(result),
        "brier": brier_vs_baseline(result),
        "calibration": calibration(result),
        "drawdown": max_drawdown(result.equity_curve),
        "concentration": concentration(result),
        "trade_stats": trade_stats(result),
        "splits": splits(result),
    }


# ---------------------------------------------------------------------------
# Full report: base run + capacity curve + fee stress
# ---------------------------------------------------------------------------

def full_report(
    provider,
    strategy_factory: Callable[[], Strategy],
    config: EngineConfig | None = None,
    out_dir: str | None = None,
    market_filters: dict | None = None,
    capacity_multipliers: tuple[float, ...] = (1.0, 3.0, 10.0),
    fee_stress_multiplier: float = 1.5,
    label: str = "backtest",
) -> dict[str, Any]:
    """Run base + capacity + fee-stress backtests and build the full report.

    strategy_factory must return a FRESH strategy per call (reruns must not
    share state). Writes report.json and report.md to out_dir when given.
    """
    config = config or EngineConfig()
    base = run_backtest(provider, strategy_factory(), config, market_filters)
    report: dict[str, Any] = {"label": label, "base": compute_metrics(base)}

    capacity = []
    for mult in capacity_multipliers:
        if mult == 1.0:
            r = base
        else:
            cfg = replace(config, risk=replace(config.risk, depth_multiplier=mult),
                          event_log_path=None)
            r = run_backtest(provider, strategy_factory(), cfg, market_filters)
        capacity.append(
            {
                "depth_multiplier": mult,
                "net_pnl_cents_after_inference": r.net_pnl_after_inference_cents,
                "contracts_filled": sum(f.count for f in r.fills),
            }
        )
    report["capacity_curve"] = capacity

    stress_cfg = replace(config, fee_stress=fee_stress_multiplier, event_log_path=None)
    stressed = run_backtest(provider, strategy_factory(), stress_cfg, market_filters)
    report["fee_stress"] = {
        "multiplier": fee_stress_multiplier,
        "net_pnl_cents_after_inference": stressed.net_pnl_after_inference_cents,
        "still_positive": stressed.net_pnl_after_inference_cents > 0,
    }

    if out_dir is not None:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "report.json"), "w") as fh:
            json.dump(report, fh, indent=2)
        with open(os.path.join(out_dir, "report.md"), "w") as fh:
            fh.write(render_markdown(report))
    return report


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def _usd(cents) -> str:
    return f"${cents / 100:,.2f}" if cents is not None else "n/a"


def _pct(x) -> str:
    return f"{x * 100:.2f}%" if x is not None else "n/a"


def _num(x, digits=4) -> str:
    return f"{x:.{digits}f}" if x is not None else "n/a"


def render_markdown(report: dict[str, Any]) -> str:
    b = report["base"]
    pnl, ts = b["pnl"], b["trade_stats"]
    lines = [
        f"# Backtest report - {report.get('label', 'run')}",
        "",
        "## P&L",
        f"- Net P&L (after fees, gross of inference): "
        f"{_usd(pnl['net_pnl_cents_gross_of_inference'])}",
        f"- Net P&L (after fees + inference): "
        f"{_usd(pnl['net_pnl_cents_after_inference'])} "
        f"({_pct(pnl['net_pnl_pct_of_bankroll'])} of {_usd(pnl['bankroll_cents'])})",
        f"- Fees: {_usd(pnl['fees_cents'])} | Inference: "
        f"{_usd(pnl['inference_cost_cents'])}",
        f"- Annualized return on avg deployed "
        f"({_usd(int(pnl['avg_deployed_cents']))}): "
        f"{_pct(pnl['annualized_return_on_avg_deployed'])}",
        "",
        "## Forecast quality",
        f"- Brier (n={b['brier']['n']}): strategy {_num(b['brier']['strategy'])} "
        f"vs market baseline {_num(b['brier']['market_baseline'])} -> "
        f"{'BEATS' if b['brier']['beats_baseline'] else 'does NOT beat'} baseline"
        if b["brier"]["n"]
        else "- Brier: no scored p_hat decisions",
        f"- Calibration ECE: {_num(b['calibration']['ece'])} "
        f"(n={b['calibration']['n']})",
    ]
    if b["calibration"]["curve"]:
        lines += [
            "",
            "| p_hat bin | n | mean p_hat | observed freq |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {r['bin']} | {r['n']} | {r['mean_p_hat']:.3f} | "
            f"{r['observed_freq']:.3f} |"
            for r in b["calibration"]["curve"]
        ]
    dd, cc = b["drawdown"], b["concentration"]
    lines += [
        "",
        "## Risk",
        f"- Max drawdown: {_usd(dd['max_drawdown_cents'])} "
        f"({_pct(dd['max_drawdown_frac_of_peak'])} of peak)",
        f"- Worst single-market loss: {_usd(cc['worst_market_loss_cents'])}",
        f"- Top-5 markets' share of net P&L: {_pct(cc['top5_share_of_net'])} "
        f"across {cc['n_markets_traded']} traded markets",
        "",
        "## Trade stats",
        f"- Decisions: {ts['n_decision_calls']} | Intents: {ts['n_intents']} | "
        f"Orders: {ts['n_orders']}",
        f"- Contracts requested/ordered/filled: {ts['contracts_requested']}/"
        f"{ts['contracts_ordered']}/{ts['contracts_filled']} "
        f"(fill rate vs ordered: {_pct(ts['fill_rate_vs_ordered'])})",
        f"- Maker share of filled contracts: {_pct(ts['maker_share'])}",
        f"- Avg edge at entry: "
        f"{_num(ts['avg_edge_at_entry_cents'], 2)}c/contract",
        f"- Avg holding period: "
        + (
            f"{ts['avg_holding_period_s'] / 3600:.1f}h"
            if ts["avg_holding_period_s"] is not None
            else "n/a"
        ),
        "",
        "## Capacity curve (depth caps scaled)",
        "| depth x | net P&L (after inference) | contracts filled |",
        "|---|---|---|",
    ]
    lines += [
        f"| {c['depth_multiplier']:g}x | "
        f"{_usd(c['net_pnl_cents_after_inference'])} | {c['contracts_filled']} |"
        for c in report.get("capacity_curve", [])
    ]
    fs = report.get("fee_stress")
    if fs:
        lines += [
            "",
            f"## Fee stress (x{fs['multiplier']:g})",
            f"- Net P&L after inference: {_usd(fs['net_pnl_cents_after_inference'])} "
            f"-> {'still positive' if fs['still_positive'] else 'NEGATIVE'}",
        ]
    sp = b["splits"]
    if sp.get("time"):
        tr, te = sp["time"]["train"], sp["time"]["test"]
        lines += [
            "",
            "## Robustness splits",
            f"- Time split (60/40 by first entry): train "
            f"{_usd(tr['net_pnl_cents'])} over {tr['n_markets']} markets | "
            f"test {_usd(te['net_pnl_cents'])} over {te['n_markets']} markets",
        ]
        if sp["category"]:
            lines += [
                "",
                "| category | markets | net P&L |",
                "|---|---|---|",
            ]
            lines += [
                f"| {cat} | {v['n_markets']} | {_usd(v['net_pnl_cents'])} |"
                for cat, v in sorted(sp["category"].items())
            ]
    lines.append("")
    return "\n".join(lines)

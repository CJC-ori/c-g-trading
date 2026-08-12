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

#: Forecast-strategy sample gate (SPEC §7, amended 2026-08-11): measured
#: paired-difference SD on real ForecastBench 2026 data is ~0.0698, so the
#: minimum detectable ΔBrier at n=100 (~0.0137) is 3x the superforecaster-
#: vs-market edge (0.004). research/benchmarks.md §4.3.
BRIER_N_GATE = 500

#: Default traded-subset gate: |p_hat - mid| >= 4c (below ~4 points, fees
#: eat the edge — research/cost-architecture.md §4.3).
DEFAULT_TRADED_GATE_CENTS = 4.0

#: ΔBrier sanity scale (research/benchmarks.md §4.3): +0.004 is
#: superforecaster-class, +0.02 is top-bot-class (be suspicious),
#: >+0.05 means the run is leaking.
DELTA_BRIER_SUSPICIOUS = 0.02
DELTA_BRIER_LEAK = 0.05


def _paired_delta(pairs: list[tuple[float, float, int]]) -> dict[str, Any]:
    """Brier stats for (p_hat, mid_prob, outcome) pairs, with the paired-
    difference 95% CI on ΔBrier = Brier(market) - Brier(strategy)
    (positive = strategy better). Always report the CI, never just the
    point estimate (SPEC §7)."""
    n = len(pairs)
    if n == 0:
        return {"n": 0, "strategy": None, "market_baseline": None,
                "delta_brier": None, "delta_ci95": None, "beats_baseline": None}
    strat = sum((p - o) ** 2 for p, _, o in pairs) / n
    base = sum((m - o) ** 2 for _, m, o in pairs) / n
    diffs = [(m - o) ** 2 - (p - o) ** 2 for p, m, o in pairs]
    mean_d = sum(diffs) / n
    ci = None
    if n >= 2:
        var = sum((d - mean_d) ** 2 for d in diffs) / (n - 1)
        half = 1.96 * (var ** 0.5) / (n ** 0.5)
        ci = [mean_d - half, mean_d + half]
    return {
        "n": n,
        "strategy": strat,
        "market_baseline": base,
        "delta_brier": mean_d,
        "delta_ci95": ci,
        "beats_baseline": strat <= base,
    }


def brier_vs_baseline(
    result: BacktestResult,
    traded_gate_cents: float = DEFAULT_TRADED_GATE_CENTS,
    n_gate: int = BRIER_N_GATE,
) -> dict[str, Any]:
    """The three-number Brier block (SPEC §6.2) — always reported together:

    (a) pooled ΔBrier vs the market mid at the same instants, paired CI;
    (b) ΔBrier on the traded subset (|p_hat - mid| >= gate) — the skill claim;
    (c) simulated post-fee P&L on the markets actually traded — the only
        number that pays rent.
    Pooled-neutral but subset-positive is what success looks like
    (research/benchmarks.md §4.2). Top-level keys keep the original
    pooled-stats shape for backward compatibility.
    """
    pairs: list[tuple[float, float, int]] = []  # (p_hat, mid_prob, outcome)
    for d in result.decisions:
        s = result.settlements.get(d.ticker)
        if s is None or s.result == "void" or d.market_mid_cents is None:
            continue
        outcome = 1 if s.result == "yes" else 0
        pairs.append((d.p_hat, d.market_mid_cents / 100.0, outcome))
    pooled = _paired_delta(pairs)
    traded_pairs = [
        (p, m, o) for p, m, o in pairs
        if abs(p * 100 - m * 100) >= traded_gate_cents
    ]
    traded = _paired_delta(traded_pairs)
    traded["gate_cents"] = traded_gate_cents

    traded_markets = [
        m for m in result.per_market.values() if m.get("contracts_traded")
    ]
    trade_pnl = {
        "n_markets_traded": len(traded_markets),
        "net_pnl_cents_after_fees": sum(
            m["net_pnl_cents"] for m in traded_markets
        ),
        "net_pnl_cents_after_fees_and_inference": sum(
            m["net_pnl_cents"] for m in traded_markets
        ) - result.inference_cost_cents,
    }

    flags: list[str] = []
    if pooled["n"] and pooled["n"] < n_gate:
        flags.append(
            f"n={pooled['n']} < {n_gate}: below the forecast-strategy gate "
            "(minimum detectable dBrier at n=100 is ~0.0137, 3x the "
            "superforecaster edge)"
        )
    d = pooled["delta_brier"]
    if d is not None and d > DELTA_BRIER_LEAK:
        flags.append(
            f"LEAK WARNING: pooled dBrier +{d:.4f} > +{DELTA_BRIER_LEAK} — "
            "no known forecaster is this good; audit for lookahead/contamination"
        )
    elif d is not None and d > DELTA_BRIER_SUSPICIOUS:
        flags.append(
            f"pooled dBrier +{d:.4f} > +{DELTA_BRIER_SUSPICIOUS}: "
            "top-2026-bot territory — be suspicious"
        )

    out = dict(pooled)
    out.update(
        {
            "n_gate": n_gate,
            "meets_n_gate": pooled["n"] >= n_gate,
            "traded_subset": traded,
            "trade_pnl": trade_pnl,
            "flags": flags,
        }
    )
    return out


def calibration(result: BacktestResult, n_bins: int = 10) -> dict[str, Any]:
    """Reliability curve + ECE + Murphy calibration/refinement decomposition.

    Brier = reliability - resolution + uncertainty (binned). Reliability
    (lower better) is miscalibration; resolution (higher better) is
    sharpness that pays — frontier forecasters are all roughly calibrated
    and differ in resolution, which is what generates edge
    (research/futuresearch.md §6.2).
    """
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
    reliability = 0.0
    resolution = 0.0
    base_rate = (
        sum(o for b in binned for _, o in b) / total if total else None
    )
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
        w = len(b) / total
        ece += w * abs(freq - conf)
        reliability += w * (conf - freq) ** 2
        resolution += w * (freq - base_rate) ** 2
    uncertainty = base_rate * (1 - base_rate) if total else None
    return {
        "n": total,
        "ece": ece if total else None,
        "curve": curve,
        "reliability": reliability if total else None,
        "resolution": resolution if total else None,
        "uncertainty": uncertainty,
        "brier_from_decomposition": (
            reliability - resolution + uncertainty if total else None
        ),
    }


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

    # Simulated maker fill rate (SPEC §3): maker-filled contracts over
    # contracts that ever rested (rest-tif order size minus its taker leg).
    # Sanity band 40-50% (FutureSearch's own positions table implies ~50%);
    # >60% means the fill model is lying (SPEC §7 kill criterion).
    taker_by_order: dict[int, int] = {}
    maker_by_order: dict[int, int] = {}
    for f in result.fills:
        d = taker_by_order if f.is_taker else maker_by_order
        d[f.order_id] = d.get(f.order_id, 0) + f.count
    maker_exposed = 0
    maker_filled = 0
    for oid, o in result.orders.items():
        size = o.get("size")
        if size is None or o.get("tif") != "rest":
            continue
        maker_exposed += max(0, size - taker_by_order.get(oid, 0))
        maker_filled += maker_by_order.get(oid, 0)
    maker_fill_rate = maker_filled / maker_exposed if maker_exposed else None

    lifetimes = sorted(
        o["opportunity_lifetime_s"]
        for o in result.orders.values()
        if o.get("opportunity_lifetime_s") is not None
    )

    def _q(quantile: float) -> float | None:
        if not lifetimes:
            return None
        return lifetimes[min(len(lifetimes) - 1, int(quantile * len(lifetimes)))]

    flags: list[str] = []
    if maker_fill_rate is not None and maker_fill_rate > 0.60:
        flags.append(
            f"maker fill rate {maker_fill_rate:.0%} > 60% — the fill model "
            "is lying (sanity band is 40-50%); do not trust maker P&L"
        )
    median_life = _q(0.5)
    if median_life is not None and median_life < 5:
        flags.append(
            f"median opportunity lifetime {median_life}s < 5s — this edge "
            "belongs to colocated bots, not us (research/oss-arb.md §7.3)"
        )

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
        "maker_fill_rate": maker_fill_rate,
        "maker_fill_rate_sanity_band": [0.40, 0.50],
        "avg_edge_at_entry_cents": edge_num / edge_den if edge_den else None,
        "avg_holding_period_s": sum(holds) / len(holds) if holds else None,
        "opportunity_lifetime_s": {
            "n": len(lifetimes),
            "p25": _q(0.25),
            "median": median_life,
            "p75": _q(0.75),
        },
        "flags": flags,
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


def cost_ratios(result: BacktestResult) -> dict[str, Any]:
    """LLM cost-honesty ratios (SPEC §6.8; research/cost-architecture.md §5.2,
    §7.4): inference/net-P&L must stay < 20%, inference/notional-traded < 1%
    (fee drag is ~3.5% of notional at mid — inference approaching it means
    the architecture is wrong)."""
    inf = result.inference_cost_cents
    net = result.net_pnl_cents  # after fees, gross of inference
    notional = sum(f.count * f.price_cents for f in result.fills)
    over_net = inf / net if net > 0 else None
    over_notional = inf / notional if notional > 0 else None
    flags: list[str] = []
    if inf:
        if over_net is None:
            flags.append("inference cost with non-positive net P&L")
        elif over_net > 0.20:
            flags.append(
                f"inference/net-P&L {over_net:.0%} > 20% — pipeline too "
                "expensive for its edge"
            )
        if over_notional is not None and over_notional > 0.01:
            flags.append(
                f"inference/notional {over_notional:.2%} > 1% — approaching "
                "fee-drag scale (~3.5%); architecture is wrong"
            )
    return {
        "inference_cost_cents": inf,
        "net_pnl_cents": net,
        "notional_traded_cents": notional,
        "inference_over_net_pnl": over_net,
        "inference_over_net_pnl_target": 0.20,
        "inference_over_notional": over_notional,
        "inference_over_notional_target": 0.01,
        "flags": flags,
    }


def compute_metrics(result: BacktestResult) -> dict[str, Any]:
    return {
        "pnl": pnl_summary(result),
        "brier": brier_vs_baseline(result),
        "calibration": calibration(result),
        "drawdown": max_drawdown(result.equity_curve),
        "concentration": concentration(result),
        "trade_stats": trade_stats(result),
        "cost_ratios": cost_ratios(result),
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
    inference_stress_multiplier: float = 2.0,
    fee_schedule=None,
    label: str = "backtest",
) -> dict[str, Any]:
    """Run base + capacity + fee-stress backtests and build the full report.

    strategy_factory must return a FRESH strategy per call (reruns must not
    share state). Writes report.json and report.md to out_dir when given.
    fee_schedule (a fees.FeeSchedule) switches the engine to the exact
    per-series fee model — pass FeeSchedule.load_default() for real-data
    runs. The inference stress (SPEC §7) re-prices the inference line at
    x2 without a rerun (inference is an additive P&L line item).
    """
    config = config or EngineConfig()
    if fee_schedule is not None:
        config = replace(config, fee_schedule=fee_schedule)
    base = run_backtest(provider, strategy_factory(), config, market_filters)
    report: dict[str, Any] = {
        "label": label,
        "fee_mode": (
            "exact-per-series" if config.fee_schedule is not None
            else "legacy-per-contract-ceil"
        ),
        "base": compute_metrics(base),
    }

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

    # Inference stress (SPEC §7): inference is an additive P&L line, so
    # x2 model prices = subtract the inference line twice — no rerun needed.
    stressed_net = base.net_pnl_cents - round(
        inference_stress_multiplier * base.inference_cost_cents
    )
    report["inference_stress"] = {
        "multiplier": inference_stress_multiplier,
        "inference_cost_cents": round(
            inference_stress_multiplier * base.inference_cost_cents
        ),
        "net_pnl_cents_after_inference": stressed_net,
        "still_positive": stressed_net > 0,
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
    ]
    br = b["brier"]
    if br["n"]:
        ci = br.get("delta_ci95")
        ci_txt = f" (95% CI [{ci[0]:+.4f}, {ci[1]:+.4f}])" if ci else ""
        lines += [
            "The three-number block (always read together, SPEC §6.2):",
            f"- (a) Pooled dBrier (n={br['n']}): {br['delta_brier']:+.4f}{ci_txt} "
            f"— strategy {_num(br['strategy'])} vs market {_num(br['market_baseline'])} "
            f"-> {'BEATS' if br['beats_baseline'] else 'does NOT beat'} baseline",
        ]
        tr_sub = br.get("traded_subset") or {}
        if tr_sub.get("n"):
            tci = tr_sub.get("delta_ci95")
            tci_txt = f" (95% CI [{tci[0]:+.4f}, {tci[1]:+.4f}])" if tci else ""
            lines.append(
                f"- (b) Traded-subset dBrier (|p_hat-mid| >= "
                f"{tr_sub.get('gate_cents', 4):g}c, n={tr_sub['n']}): "
                f"{tr_sub['delta_brier']:+.4f}{tci_txt}"
            )
        else:
            lines.append("- (b) Traded-subset dBrier: no decisions past the gate")
        tp = br.get("trade_pnl") or {}
        lines.append(
            f"- (c) Trade P&L (the number that pays rent): "
            f"{_usd(tp.get('net_pnl_cents_after_fees_and_inference'))} after "
            f"fees+inference across {tp.get('n_markets_traded', 0)} traded markets"
        )
        if not br.get("meets_n_gate", True):
            lines.append(
                f"- n gate: {br['n']} < {br.get('n_gate', 500)} resolved — "
                "NOT enough for a forecast-skill claim (SPEC §7)"
            )
        for fl in br.get("flags", []):
            lines.append(f"- FLAG: {fl}")
    else:
        lines.append("- Brier: no scored p_hat decisions")
    cal = b["calibration"]
    lines.append(f"- Calibration ECE: {_num(cal['ece'])} (n={cal['n']})")
    if cal.get("reliability") is not None:
        lines.append(
            f"- Murphy decomposition: reliability {_num(cal['reliability'])} "
            f"(miscalibration, lower better) | resolution "
            f"{_num(cal['resolution'])} (sharpness, higher better) | "
            f"uncertainty {_num(cal['uncertainty'])}"
        )
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
        f"- Simulated maker fill rate: {_pct(ts.get('maker_fill_rate'))} "
        f"(sanity band 40-50%; >60% = fill model lying)",
        f"- Avg edge at entry: "
        f"{_num(ts['avg_edge_at_entry_cents'], 2)}c/contract",
        f"- Avg holding period: "
        + (
            f"{ts['avg_holding_period_s'] / 3600:.1f}h"
            if ts["avg_holding_period_s"] is not None
            else "n/a"
        ),
    ]
    life = ts.get("opportunity_lifetime_s") or {}
    if life.get("n"):
        lines.append(
            f"- Opportunity lifetime (s): median {life['median']} "
            f"[p25 {life['p25']}, p75 {life['p75']}] over {life['n']} orders"
        )
    for fl in ts.get("flags", []):
        lines.append(f"- FLAG: {fl}")
    cr = b.get("cost_ratios") or {}
    if cr.get("inference_cost_cents"):
        lines += [
            "",
            "## Cost ratios (LLM strategies)",
            f"- Inference / net P&L: {_pct(cr['inference_over_net_pnl'])} "
            f"(target < 20%)",
            f"- Inference / notional traded: {_pct(cr['inference_over_notional'])} "
            f"(target < 1%; fee drag is ~3.5% at mid)",
        ]
        for fl in cr.get("flags", []):
            lines.append(f"- FLAG: {fl}")
    lines += [
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
    infs = report.get("inference_stress")
    if infs:
        lines += [
            "",
            f"## Inference stress (x{infs['multiplier']:g} model prices)",
            f"- Net P&L after stressed inference: "
            f"{_usd(infs['net_pnl_cents_after_inference'])} "
            f"-> {'still positive' if infs['still_positive'] else 'NEGATIVE'}",
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

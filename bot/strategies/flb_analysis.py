"""Analysis extras for the FLB backtests (beyond metrics.compute_metrics).

Everything here consumes a finished BacktestResult (plus, for opportunity
lifetimes, the HistoryProvider) and produces JSON-safe dicts:

- episodes():            one row per traded market (an "episode" in GWU
                         terms: buy, hold to resolution).
- clustered_mean_se():   event-clustered mean/SE of per-episode returns
                         (brackets of one event are correlated; GWU
                         clusters the same way).
- fill_stats():          order-level and contract-level fill rates split
                         maker/taker (SPEC §2.2 flags maker fill > 60%).
- winrate_by_price():    win-rate-vs-entry-price curve by category with
                         per-bucket breakeven (price + fee), the R5
                         deliverable.
- opportunity_lifetime():hours the entry condition persisted per market,
                         measured from the candle history independently of
                         what the strategy actually did (SPEC §2.2).
"""
from __future__ import annotations

import math
from typing import Any, Callable, Iterable

from bot.backtest.engine import BacktestResult
from bot.backtest.types import Candle

HOUR = 3600
DAY = 86400


# ---------------------------------------------------------------------------
# Episodes
# ---------------------------------------------------------------------------

def episodes(result: BacktestResult) -> list[dict[str, Any]]:
    """One row per market that traded. All FLB variants are buy-and-hold
    (the only cash-out is settlement), so an episode's cost basis is the
    sum of buy-fill premiums and its net P&L is realized minus fees."""
    by_ticker: dict[str, dict[str, Any]] = {}
    for f in result.fills:
        row = by_ticker.setdefault(
            f.ticker,
            {
                "ticker": f.ticker,
                "basis_cents": 0,
                "contracts": 0,
                "entry_ts": f.ts,
                "maker_contracts": 0,
                "taker_contracts": 0,
                "wsum_price": 0,
                "side": f.side,
            },
        )
        if f.action != "buy":  # defensive: FLB strategies never sell
            continue
        row["basis_cents"] += f.price_cents * f.count
        row["contracts"] += f.count
        row["wsum_price"] += f.price_cents * f.count
        row["entry_ts"] = min(row["entry_ts"], f.ts)
        if f.is_taker:
            row["taker_contracts"] += f.count
        else:
            row["maker_contracts"] += f.count
    out = []
    for ticker, row in sorted(by_ticker.items()):
        pm = result.per_market.get(ticker, {})
        info = result.markets.get(ticker)
        settle = result.settlements.get(ticker)
        net = pm.get("realized_pnl_cents", 0) - pm.get("fees_cents", 0)
        basis = row["basis_cents"]
        out.append(
            {
                "ticker": ticker,
                "event_ticker": info.event_ticker if info else "",
                "category": (info.category if info else "") or "(uncategorized)",
                "side": row["side"],
                "contracts": row["contracts"],
                "maker_contracts": row["maker_contracts"],
                "taker_contracts": row["taker_contracts"],
                "avg_entry_price_cents": (
                    row["wsum_price"] / row["contracts"] if row["contracts"] else None
                ),
                "basis_cents": basis,
                "fees_cents": pm.get("fees_cents", 0),
                "net_pnl_cents": net,
                "return": net / basis if basis else None,
                "entry_ts": row["entry_ts"],
                "exit_ts": pm.get("exit_ts"),
                "holding_s": (
                    pm["exit_ts"] - row["entry_ts"]
                    if pm.get("exit_ts") is not None
                    else None
                ),
                "result": settle.result if settle else None,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Event-clustered statistics
# ---------------------------------------------------------------------------

def clustered_mean_se(
    values: list[float], clusters: list[str]
) -> dict[str, Any]:
    """Mean and cluster-robust SE of `values`, clustering by `clusters`
    (event_ticker). SE^2 = G/(G-1) * sum_g (sum_{i in g} e_i)^2 / n^2 with
    e_i the demeaned values - the standard one-way CR SE of a mean. Falls
    back to the iid SE when there is a single cluster."""
    n = len(values)
    if n == 0:
        return {"n": 0, "n_clusters": 0, "mean": None, "se": None,
                "ci95_lo": None, "ci95_hi": None}
    mean = sum(values) / n
    groups: dict[str, float] = {}
    for v, g in zip(values, clusters):
        groups[g] = groups.get(g, 0.0) + (v - mean)
    G = len(groups)
    if G > 1:
        var = (G / (G - 1)) * sum(s * s for s in groups.values()) / (n * n)
    elif n > 1:  # single cluster: iid fallback, clearly imperfect
        var = sum((v - mean) ** 2 for v in values) / (n - 1) / n
    else:
        var = float("nan")
    se = math.sqrt(var) if var == var else None  # NaN guard
    return {
        "n": n,
        "n_clusters": G,
        "mean": mean,
        "se": se,
        "ci95_lo": mean - 1.96 * se if se is not None else None,
        "ci95_hi": mean + 1.96 * se if se is not None else None,
    }


def episode_return_stats(eps: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-episode return distribution + event-clustered mean/SE +
    dollar-weighted aggregate return."""
    rows = [e for e in eps if e["return"] is not None]
    rets = [e["return"] for e in rows]
    clusters = [e["event_ticker"] or e["ticker"] for e in rows]
    stats = clustered_mean_se(rets, clusters)
    total_basis = sum(e["basis_cents"] for e in rows)
    total_net = sum(e["net_pnl_cents"] for e in rows)
    dist = {}
    if rets:
        s = sorted(rets)
        q = lambda f: s[min(len(s) - 1, int(f * len(s)))]
        dist = {
            "min": s[0], "p25": q(0.25), "median": q(0.5),
            "p75": q(0.75), "max": s[-1],
            "sd": (
                math.sqrt(sum((r - stats["mean"]) ** 2 for r in rets) / (len(rets) - 1))
                if len(rets) > 1 else None
            ),
            "frac_positive": sum(1 for r in rets if r > 0) / len(rets),
        }
    return {
        **stats,
        "distribution": dist,
        "dollar_weighted_return": total_net / total_basis if total_basis else None,
        "total_basis_cents": total_basis,
        "total_net_pnl_cents": total_net,
    }


def per_category_stats(eps: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for e in eps:
        by_cat.setdefault(e["category"], []).append(e)
    return {cat: episode_return_stats(rows) for cat, rows in sorted(by_cat.items())}


# ---------------------------------------------------------------------------
# Fill stats
# ---------------------------------------------------------------------------

def fill_stats(result: BacktestResult) -> dict[str, Any]:
    """Order- and contract-level fill rates, maker/taker split. The SPEC
    §2.2 sanity band for maker fill rates is 40-50%; >60% means the fill
    model is probably lying (kill criterion c)."""
    orders = result.orders
    filled_by_order: dict[int, int] = {}
    for f in result.fills:
        filled_by_order[f.order_id] = filled_by_order.get(f.order_id, 0) + f.count
    maker_orders = [oid for oid, o in orders.items() if not o["is_taker_at_entry"]]
    taker_orders = [oid for oid, o in orders.items() if o["is_taker_at_entry"]]

    def _order_rate(ids: list[int]) -> float | None:
        if not ids:
            return None
        return sum(1 for oid in ids if filled_by_order.get(oid, 0) > 0) / len(ids)

    def _contract_rate(ids: list[int]) -> float | None:
        ordered = sum(orders[oid]["size"] for oid in ids)
        if not ordered:
            return None
        return sum(filled_by_order.get(oid, 0) for oid in ids) / ordered

    maker_contracts = sum(f.count for f in result.fills if not f.is_taker)
    taker_contracts = sum(f.count for f in result.fills if f.is_taker)
    return {
        "n_orders": len(orders),
        "n_maker_orders": len(maker_orders),
        "n_taker_orders": len(taker_orders),
        "maker_order_fill_rate": _order_rate(maker_orders),
        "taker_order_fill_rate": _order_rate(taker_orders),
        "maker_contract_fill_rate": _contract_rate(maker_orders),
        "taker_contract_fill_rate": _contract_rate(taker_orders),
        "maker_contracts_filled": maker_contracts,
        "taker_contracts_filled": taker_contracts,
        "maker_fill_rate_flag_gt60": (
            _order_rate(maker_orders) is not None
            and _order_rate(maker_orders) > 0.60
        ),
    }


# ---------------------------------------------------------------------------
# Win-rate vs entry price (R5 deliverable, useful for all variants)
# ---------------------------------------------------------------------------

def winrate_by_price(
    result: BacktestResult, bucket_cents: int = 1
) -> list[dict[str, Any]]:
    """Contract-weighted realized win value vs entry price, by category.

    "Win value" per contract is the settlement payout fraction for the side
    bought (1 for a win, 0 for a loss, v/100 for scalar settlements), so
    the curve is directly comparable to breakeven = price/100 (+ fee for
    taker entries, charged as actually paid)."""
    buckets: dict[tuple[str, int], dict[str, float]] = {}
    for f in result.fills:
        if f.action != "buy":
            continue
        s = result.settlements.get(f.ticker)
        if s is None or s.result == "void":
            continue
        if s.result == "scalar":
            v = (s.settlement_value_cents or 0) / 100.0
            win_val = v if f.side == "yes" else 1.0 - v
        else:
            win_val = 1.0 if s.result == f.side else 0.0
        info = result.markets.get(f.ticker)
        cat = (info.category if info else "") or "(uncategorized)"
        key = (cat, (f.price_cents // bucket_cents) * bucket_cents)
        b = buckets.setdefault(
            key, {"contracts": 0, "win_value": 0.0, "fees_cents": 0.0,
                  "n_markets": set()},  # type: ignore[dict-item]
        )
        b["contracts"] += f.count
        b["win_value"] += win_val * f.count
        b["fees_cents"] += f.fee_cents
        b["n_markets"].add(f.ticker)  # type: ignore[union-attr]
    rows = []
    for (cat, price), b in sorted(buckets.items()):
        n = b["contracts"]
        fee_per = b["fees_cents"] / n if n else 0.0
        breakeven = price / 100.0 + fee_per / 100.0
        win_rate = b["win_value"] / n if n else None
        rows.append(
            {
                "category": cat,
                "entry_price_cents": price,
                "contracts": n,
                "n_markets": len(b["n_markets"]),  # type: ignore[arg-type]
                "win_rate": win_rate,
                "breakeven_win_rate": breakeven,
                "above_breakeven": win_rate is not None and win_rate > breakeven,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Opportunity lifetime (how long did the trigger condition persist?)
# ---------------------------------------------------------------------------

QualifierFn = Callable[[Candle, int], bool]  # (candle, close_ts) -> bool


def r1_qualifier(band: tuple[int, int], days_window: float) -> QualifierFn:
    lo, hi = band

    def q(c: Candle, close_ts: int) -> bool:
        ttc = close_ts - c.end_ts
        if ttc <= 0 or ttc > days_window * DAY:
            return False
        if c.yes_ask_close is not None and lo <= c.yes_ask_close <= hi:
            return True
        return c.yes_bid_close is not None and lo <= 100 - c.yes_bid_close <= hi

    return q


def r2_qualifier(
    min_side_price: int, standdown_s: int, days_window: float
) -> QualifierFn:
    def q(c: Candle, close_ts: int) -> bool:
        ttc = close_ts - c.end_ts
        if ttc <= standdown_s or ttc > days_window * DAY:
            return False
        if c.yes_bid_close is None or c.yes_ask_close is None:
            return False
        mid = (c.yes_bid_close + c.yes_ask_close) / 2
        join = c.yes_bid_close if mid >= 50 else 100 - c.yes_ask_close
        return join >= min_side_price

    return q


def r5_qualifier(band: tuple[int, int], window_s: int) -> QualifierFn:
    lo, hi = band

    def q(c: Candle, close_ts: int) -> bool:
        ttc = close_ts - c.end_ts
        if ttc <= 0 or ttc > window_s:
            return False
        if c.yes_bid_close is not None and lo <= c.yes_bid_close <= hi:
            return True
        return c.yes_ask_close is not None and lo <= 100 - c.yes_ask_close <= hi

    return q


def opportunity_lifetime(
    provider,
    tickers: Iterable[str],
    close_ts_by_ticker: dict[str, int],
    qualifier: QualifierFn,
    period_s: int = HOUR,
) -> dict[str, Any]:
    """Per-market hours the entry condition held, measured from candles
    (granularity = the candle period; with hourly candles a lifetime of
    one candle means 'persisted for about an hour', which comfortably
    clears the ~5s not-ours-to-trade bar of research/oss-arb.md §7.3 -
    reported anyway per SPEC §2.2)."""
    hours: list[float] = []
    for t in tickers:
        close_ts = close_ts_by_ticker.get(t)
        if close_ts is None:
            continue
        n = sum(
            1 for c in provider.candles(t, period_s) if qualifier(c, close_ts)
        )
        if n:
            hours.append(n * period_s / HOUR)
    if not hours:
        return {"n_markets": 0, "median_hours": None, "mean_hours": None,
                "min_hours": None, "max_hours": None}
    s = sorted(hours)
    return {
        "n_markets": len(s),
        "median_hours": s[len(s) // 2],
        "mean_hours": sum(s) / len(s),
        "min_hours": s[0],
        "max_hours": s[-1],
    }

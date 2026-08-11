"""R7 backtest: divergence-following on the verified Kalshi<->PM pairs.

    python -m bot.strategies.structure.run_r7 [--gate 4] [--persist-min 10]

Feeds the harness (``bot.backtest.engine``) a
1-minute provider built from this package's own Kalshi store, with the
Polymarket 1-minute midpoint series injected as a point-in-time reference
price. Runs, on the same markets and the same clock:

  * ``ReferenceDivergence``  — trade Kalshi toward the Polymarket mid;
  * ``DirectionShuffled``    — identical triggers, direction randomised
                               (the no-signal baseline the kill criterion
                               names);
  * a buy-and-hold-nothing null is implicit (P&L 0).

Costs: Kalshi fees come from the harness's exact per-series centicent model
(``FeeSchedule.from_store``); entries are maker-style at the near touch and
fill only when the tape prints through, so the Kalshi spread is paid in the
form of adverse selection rather than assumed away. Polymarket-side P&L is
NOT simulated — R7 is a one-legged signal-following trade on Kalshi, not a
two-venue arb (which `research/oss-arb.md` §5-6 already priced out) — but
the Polymarket realized-vs-current fee question still matters for the
*signal*, so both fee regimes are reported for the reference venue.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
from bisect import bisect_left

from bot.backtest.dataport import InMemoryHistoryProvider
from bot.backtest.engine import EngineConfig
from bot.backtest.fees import FeeSchedule
from bot.backtest.engine import run_backtest
from bot.backtest.risk import RiskConfig
from bot.backtest.types import Candle, MarketInfo, SettlementResult
from bot.strategies.structure import pairs as P
from bot.strategies.structure.kalshi_candles import DEFAULT_STORE
from bot.strategies.structure.kalshi_events import iso_to_ts
from bot.strategies.structure.pull_r7 import PM_STORE
from bot.strategies.structure.strategy import DirectionShuffled, ReferenceDivergence


def _cents_floor(d: float) -> int:
    import math

    return math.floor(round(d * 100, 6))


def _cents_ceil(d: float) -> int:
    import math

    return math.ceil(round(d * 100, 6))


def load_provider(
    tickers: list[str], store: str, db: str, period_s: int = 60
) -> tuple[InMemoryHistoryProvider, dict[str, MarketInfo]]:
    """1-minute Kalshi candles + market metadata + settlement.

    Quote conversion mirrors ``bot.backtest.dataport``: bids floor, asks
    ceil, so the harness never sees a tighter book than existed.
    """
    conn = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    kconn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    kconn.row_factory = sqlite3.Row
    infos, candles, settlements = [], [], []
    for tk in tickers:
        r = kconn.execute(
            "SELECT ticker, event_ticker, series_ticker, category, title,"
            " open_time, close_time, settlement_ts, result FROM markets"
            " WHERE ticker = ?",
            (tk,),
        ).fetchone()
        if r is None:
            continue
        close_ts = iso_to_ts(r["close_time"]) or 2**62
        infos.append(
            MarketInfo(
                ticker=tk,
                event_ticker=r["event_ticker"] or "",
                category=r["category"] or "",
                title=r["title"] or "",
                open_ts=iso_to_ts(r["open_time"]) or 0,
                close_ts=close_ts,
                series_ticker=r["series_ticker"] or "",
            )
        )
        result = (r["result"] or "").lower()
        if result in ("yes", "no"):
            settlements.append(
                SettlementResult(
                    ticker=tk,
                    result=result,
                    settled_ts=iso_to_ts(r["settlement_ts"]) or close_ts,
                )
            )
        rows = conn.execute(
            "SELECT ts, yes_bid_close, yes_ask_close, price_close, volume"
            " FROM candles WHERE ticker = ? AND period_interval = 1 ORDER BY ts",
            (tk,),
        ).fetchall()
        for ts, bid, ask, close, vol in rows:
            if bid is None or ask is None:
                continue
            b, a = _cents_floor(bid), _cents_ceil(ask)
            mid = (b + a) // 2
            c = int(round((close or 0) * 100)) if close is not None else mid
            volume = int(vol or 0)
            if close is None:
                volume = 0            # no trade in this minute -> no fills
            lo, hi = min(c, mid), max(c, mid)
            candles.append(
                Candle(
                    ticker=tk, start_ts=int(ts) - period_s, period_s=period_s,
                    open=c, high=hi, low=lo, close=c, volume=volume,
                    yes_bid_close=b, yes_ask_close=a,
                )
            )
    conn.close()
    kconn.close()
    return (
        InMemoryHistoryProvider(markets=infos, candles=candles, settlements=settlements),
        {m.ticker: m for m in infos},
    )


class PmReference:
    """Point-in-time Polymarket midpoint, in Kalshi cents.

    Strictly ``< t``: the value returned is the last CLOB midpoint stamped
    before the decision instant. Also exposes coverage stats so a run can
    report how often the reference was actually available.
    """

    def __init__(self, series: dict[str, list[tuple[int, float]]], max_stale_s: int = 900):
        self.series = {k: sorted(v) for k, v in series.items()}
        self.ts = {k: [t for t, _ in v] for k, v in self.series.items()}
        self.max_stale_s = max_stale_s
        self.hits = 0
        self.misses = 0

    def __call__(self, ticker: str, t: int) -> float | None:
        pts = self.series.get(ticker)
        if not pts:
            self.misses += 1
            return None
        i = bisect_left(self.ts[ticker], t) - 1
        if i < 0:
            self.misses += 1
            return None
        ts, p = pts[i]
        if t - ts > self.max_stale_s:
            self.misses += 1
            return None
        self.hits += 1
        return p * 100.0


def load_pm_series(pm_store: str, specs: list[P.PairSpec]) -> dict[str, list[tuple[int, float]]]:
    conn = sqlite3.connect(f"file:{pm_store}?mode=ro", uri=True)
    out: dict[str, list[tuple[int, float]]] = {}
    try:
        for s in specs:
            row = conn.execute(
                "SELECT yes_token FROM markets WHERE pair_id = ?", (s.pair_id,)
            ).fetchone()
            if row is None:
                continue
            pts = conn.execute(
                "SELECT ts, p FROM prices WHERE token_id = ? ORDER BY ts", (row[0],)
            ).fetchall()
            if pts:
                out[s.kalshi_ticker] = [(int(t), float(p)) for t, p in pts]
    finally:
        conn.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/kalshi.db")
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--pm-store", default=PM_STORE)
    ap.add_argument("--gate", type=float, default=4.0)
    ap.add_argument("--exit-gap", type=float, default=1.0)
    ap.add_argument("--persist-min", type=int, default=10)
    ap.add_argument("--size", type=int, default=200)
    ap.add_argument("--train-frac", type=float, default=0.6)
    ap.add_argument("--out-dir", default="reports/structure/r7")
    args = ap.parse_args()

    audit = json.load(open("reports/structure/r7_pair_audit.json"))
    ok_ids = {p["pair_id"] for p in audit["pairs"] if p["ok"]}
    specs = [s for s in P.curated_pairs() if s.pair_id in ok_ids]
    pm_series = load_pm_series(args.pm_store, specs)
    specs = [s for s in specs if s.kalshi_ticker in pm_series]
    tickers = [s.kalshi_ticker for s in specs]
    provider, infos = load_provider(tickers, args.store, args.db)
    tickers = [t for t in tickers if t in infos]
    print(f"pairs with both sides of history: {len(tickers)}")

    # Time split by resolution date (SPEC §7: train first 60%, test last 40%).
    dated = sorted(specs, key=lambda s: s.resolution_date)
    cut = int(len(dated) * args.train_frac)
    train = {s.kalshi_ticker for s in dated[:cut]}
    test = {s.kalshi_ticker for s in dated[cut:]}
    print(
        f"train (first {args.train_frac:.0%} by resolution date): {len(train)} | "
        f"test: {len(test)} (from {dated[cut].resolution_date if cut < len(dated) else '-'})"
    )

    fee_sched = FeeSchedule.from_store(args.db)
    os.makedirs(args.out_dir, exist_ok=True)

    # One engine run PER PAIR. The engine advances every loaded market on
    # every event, so a 23-market 1-minute run is quadratic (~14 min/run);
    # per-pair runs are linear and the pairs are independent anyway (each
    # gets the full bankroll, so P&L is per-pair and is summed, and the
    # capacity curve is reported separately).
    per_market: dict[str, dict] = {}
    for label, cls in (
        ("divergence", ReferenceDivergence),
        ("no_signal_control", DirectionShuffled),
    ):
        for stress_label, depth_mult, fee_stress in (
            ("base", 1.0, 1.0), ("depth3x", 3.0, 1.0),
            ("depth10x", 10.0, 1.0), ("fee1.5x", 1.0, 1.5),
        ):
            for tk in tickers:
                ref = PmReference(pm_series)
                strat = cls(
                    ref, gate_cents=args.gate, exit_gap_cents=args.exit_gap,
                    persist_s=args.persist_min * 60, size=args.size,
                )
                cfg = EngineConfig(
                    risk=RiskConfig(depth_multiplier=depth_mult),
                    candle_period_s=60,
                    keep_events_in_memory=False,
                    fee_stress=fee_stress,
                    fee_schedule=fee_sched,
                )
                res = run_backtest(provider, strat, cfg, {"tickers": [tk]})
                maker_orders = [
                    (oid, o) for oid, o in res.orders.items()
                    if not o["is_taker_at_entry"]
                ]
                filled_ids = {f.order_id for f in res.fills}
                per_market[f"{label}|{stress_label}|{tk}"] = {
                    "label": label, "stress": stress_label, "ticker": tk,
                    "split": "train" if tk in train else "test",
                    "net_pnl_cents": res.net_pnl_cents,
                    "fees_cents": res.fees_cents,
                    "n_fills": len(res.fills),
                    "n_orders": len(res.orders),
                    "n_signals": strat.n_signals,
                    "maker_fill_rate": (
                        len([1 for oid, _ in maker_orders if oid in filled_ids])
                        / len(maker_orders)
                        if maker_orders
                        else None
                    ),
                    "taker_fills": sum(1 for f in res.fills if f.is_taker),
                    "contracts": sum(f.count for f in res.fills),
                    "ref_hits": ref.hits, "ref_misses": ref.misses,
                    "opportunity_lifetime_s": [
                        o["opportunity_lifetime_s"] for o in res.orders.values()
                    ],
                }
            print(f"done {label}/{stress_label}", flush=True)

    summary = _aggregate(per_market, train, test)
    with open(os.path.join(args.out_dir, "summary.json"), "w") as fh:
        json.dump(
            {
                "params": vars(args),
                "n_pairs": len(tickers),
                "train_tickers": sorted(train),
                "test_tickers": sorted(test),
                "aggregate": summary,
                "per_market": per_market,
            },
            fh,
            indent=2,
        )
    print(json.dumps(summary, indent=2))


def _aggregate(per_market: dict[str, dict], train: set, test: set) -> dict:
    import statistics

    out: dict = {}
    for key, row in per_market.items():
        label, stress = row["label"], row["stress"]
        for split in ("all", row["split"]):
            bucket = out.setdefault(
                f"{label}|{stress}|{split}",
                {
                    "net_pnl_cents": 0, "fees_cents": 0, "n_fills": 0,
                    "n_orders": 0, "n_signals": 0, "contracts": 0,
                    "n_markets": 0, "n_markets_positive": 0,
                    "maker_fill_rates": [], "lifetimes": [], "per_market": {},
                },
            )
            bucket["net_pnl_cents"] += row["net_pnl_cents"]
            bucket["fees_cents"] += row["fees_cents"]
            bucket["n_fills"] += row["n_fills"]
            bucket["n_orders"] += row["n_orders"]
            bucket["n_signals"] += row["n_signals"]
            bucket["contracts"] += row["contracts"]
            bucket["n_markets"] += 1
            bucket["n_markets_positive"] += int(row["net_pnl_cents"] > 0)
            if row["maker_fill_rate"] is not None:
                bucket["maker_fill_rates"].append(row["maker_fill_rate"])
            bucket["lifetimes"] += [x for x in row["opportunity_lifetime_s"] if x is not None]
            bucket["per_market"][row["ticker"]] = row["net_pnl_cents"]
    for b in out.values():
        r = b.pop("maker_fill_rates")
        lt = b.pop("lifetimes")
        b["maker_fill_rate_mean"] = round(statistics.fmean(r), 3) if r else None
        b["opportunity_lifetime_median_s"] = statistics.median(lt) if lt else None
        b["net_pnl_dollars"] = round(b["net_pnl_cents"] / 100, 2)
        pm = b["per_market"]
        if pm:
            worst = min(pm.items(), key=lambda kv: kv[1])
            best = max(pm.items(), key=lambda kv: kv[1])
            b["worst_market"] = [worst[0], round(worst[1] / 100, 2)]
            b["best_market"] = [best[0], round(best[1] / 100, 2)]
            top5 = sorted(pm.values(), reverse=True)[:5]
            gross_pos = sum(v for v in pm.values() if v > 0) or 1
            b["top5_share_of_gross_profit"] = round(sum(top5) / gross_pos, 3)
    return out


if __name__ == "__main__":
    main()

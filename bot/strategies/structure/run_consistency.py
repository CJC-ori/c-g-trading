"""Divergence-trade the binary-control markets against their seat ladders.

    python -m bot.strategies.structure.run_consistency

Reuses ``ReferenceDivergence`` (the same tested body as R7) with the
reference price supplied by the ladder instead of by Polymarket: the
midpoint of the ladder-implied band ``[Σ bid(control legs),
Σ ask(control ∪ ambiguous legs)]``, computed only from quotes stamped
strictly before the decision instant. Trades the *binary* leg, which is the
liquid one; the ladder legs are the reference, not the hedge, so this is a
directional divergence trade, not the riskless combination (whose fillable
size ``scan_consistency`` shows is ~$0).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from bisect import bisect_left

from bot.backtest.engine import EngineConfig, run_backtest
from bot.backtest.fees import FeeSchedule
from bot.backtest.risk import RiskConfig
from bot.strategies.structure import consistency as C
from bot.strategies.structure import overround as ov
from bot.strategies.structure.kalshi_candles import DEFAULT_STORE
from bot.strategies.structure.run_r3 import build_provider
from bot.strategies.structure.kalshi_events import MultiOutcomeEvent, EventLeg, iso_to_ts
from bot.strategies.structure.scan_r3 import forward_filled_grid, load_leg_candles
from bot.strategies.structure.strategy import DirectionShuffled, ReferenceDivergence


class LadderReference:
    """Point-in-time ladder-implied fair price for the binary, in cents."""

    def __init__(self, readings: list[C.CoherenceReading], max_stale_s: int = 7200):
        self.ts = [r.ts for r in readings]
        self.vals = [
            (r.ladder_bid_sum_cc + r.ladder_ask_sum_upper_cc) / 2 / 100 for r in readings
        ]
        self.max_stale_s = max_stale_s
        self.hits = self.misses = 0

    def __call__(self, ticker: str, t: int) -> float | None:
        i = bisect_left(self.ts, t) - 1
        if i < 0 or t - self.ts[i] > self.max_stale_s:
            self.misses += 1
            return None
        self.hits += 1
        return self.vals[i]


def _binary_event(db: str, pair: C.ControlPair) -> MultiOutcomeEvent | None:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        r = conn.execute(
            "SELECT ticker, event_ticker, series_ticker, category, title, result,"
            " volume, open_time, close_time FROM markets WHERE ticker = ?",
            (pair.binary_ticker,),
        ).fetchone()
    finally:
        conn.close()
    if r is None:
        return None
    return MultiOutcomeEvent(
        event_ticker=r[1] or pair.pair_id,
        series_ticker=r[2] or "",
        category=r[3] or "",
        title=r[4] or "",
        mutually_exclusive=True,
        legs=(EventLeg(r[0], (r[5] or "").lower(), float(r[6] or 0)),),
        open_ts=iso_to_ts(r[7]),
        close_ts=iso_to_ts(r[8]),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/kalshi.db")
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--gate", type=float, default=4.0)
    ap.add_argument("--persist-h", type=int, default=2)
    ap.add_argument("--size", type=int, default=200)
    ap.add_argument("--out", default="reports/structure/consistency_trade.json")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{args.store}?mode=ro", uri=True)
    fee_sched = FeeSchedule.from_store(args.db)
    out: dict = {}
    for pair in C.CONTROL_PAIRS:
        tickers = [pair.ladder_prefix + s for s in pair.control_legs + pair.ambiguous_legs]
        tickers.append(pair.binary_ticker)
        series = load_leg_candles(conn, tickers)
        if len(series) < len(tickers):
            out[pair.pair_id] = {"skipped": "missing-candles"}
            continue
        readings = []
        for ts, row in forward_filled_grid(series, 24 * 3600):
            qs = {
                tk: ov.LegQuote(tk, b, a, v, st)
                for tk, (b, a, v, st, _x) in row.items()
            }
            ladder = {
                s: qs[pair.ladder_prefix + s]
                for s in pair.control_legs + pair.ambiguous_legs
            }
            r = C.coherence_reading(ts, pair, qs[pair.binary_ticker], ladder)
            if r is not None:
                readings.append(r)
        ev = _binary_event(args.db, pair)
        if ev is None or not readings:
            out[pair.pair_id] = {"skipped": "no-binary-or-readings"}
            continue
        provider = build_provider([ev], args.store)
        res_by = {}
        for name, cls in (("divergence", ReferenceDivergence), ("no_signal", DirectionShuffled)):
            ref = LadderReference(readings)
            strat = cls(
                ref, gate_cents=args.gate, exit_gap_cents=1.0,
                persist_s=args.persist_h * 3600, size=args.size,
                decision_interval_s=3600, candle_period_s=3600,
            )
            res = run_backtest(
                provider, strat,
                EngineConfig(risk=RiskConfig(), candle_period_s=3600,
                             keep_events_in_memory=False, fee_schedule=fee_sched),
            )
            res_by[name] = {
                "net_pnl_dollars": round(res.net_pnl_cents / 100, 2),
                "settled": pair.settled,
                "n_signals": strat.n_signals,
                "n_orders": len(res.orders),
                "n_fills": len(res.fills),
                "contracts": sum(f.count for f in res.fills),
                "fees_dollars": round(res.fees_cents / 100, 2),
                "ref_hits": ref.hits,
                "note": None if pair.settled else "unsettled: P&L is realized-only "
                                                  "(open position marked out at 0)",
            }
        out[pair.pair_id] = res_by
        print(pair.pair_id, json.dumps(res_by), flush=True)
    conn.close()
    with open(args.out, "w") as fh:
        json.dump(out, fh, indent=2)


if __name__ == "__main__":
    main()

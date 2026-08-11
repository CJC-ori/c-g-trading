"""R3 strategy backtest: does the scan's signal survive honest execution?

    python -m bot.strategies.structure.run_r3

Universe: every event the scan flagged at least once (``r3_firings.json``),
replayed hourly through the harness with the two R3 variants:

  * ``NoSetSweep``  — the riskless full-NO-set sweep, executed leg by leg;
  * ``BracketFade`` — the higher-capacity single-bracket fade.

Both are run on the same universe with the same clock, plus a train/test
split by event close date (SPEC §7: first 60% / last 40% by time). This is
the number that decides whether R3 is a strategy or a monitor.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict

from bot.backtest.dataport import InMemoryHistoryProvider
from bot.backtest.engine import EngineConfig, run_backtest
from bot.backtest.fees import FeeSchedule
from bot.backtest.risk import RiskConfig
from bot.backtest.types import Candle, MarketInfo, SettlementResult
from bot.strategies.structure import kalshi_events as ke
from bot.strategies.structure.kalshi_candles import DEFAULT_STORE
from bot.strategies.structure.strategy import BracketFade, NoSetSweep


def build_provider(events: list[ke.MultiOutcomeEvent], store: str, period_s: int = 3600):
    conn = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    infos, candles, settlements = [], [], []
    for ev in events:
        for leg in ev.legs:
            infos.append(
                MarketInfo(
                    ticker=leg.ticker,
                    event_ticker=ev.event_ticker,
                    category=ev.category,
                    title=ev.title,
                    open_ts=ev.open_ts or 0,
                    close_ts=ev.close_ts or 2**62,
                    series_ticker=ev.series_ticker,
                )
            )
            if leg.result in ("yes", "no"):
                settlements.append(
                    SettlementResult(leg.ticker, leg.result, ev.close_ts or 0)
                )
            rows = conn.execute(
                "SELECT ts, yes_bid_close, yes_ask_close, price_close, volume"
                " FROM candles WHERE ticker = ? AND period_interval = 60 ORDER BY ts",
                (leg.ticker,),
            ).fetchall()
            for ts, bid, ask, close, vol in rows:
                if bid is None or ask is None:
                    continue
                import math

                b = math.floor(round(bid * 100, 6))
                a = math.ceil(round(ask * 100, 6))
                mid = (b + a) // 2
                c = int(round(close * 100)) if close is not None else mid
                volume = int(vol or 0) if close is not None else 0
                candles.append(
                    Candle(
                        ticker=leg.ticker, start_ts=int(ts) - period_s,
                        period_s=period_s, open=c, high=max(c, mid), low=min(c, mid),
                        close=c, volume=volume, yes_bid_close=b, yes_ask_close=a,
                    )
                )
    conn.close()
    return InMemoryHistoryProvider(markets=infos, candles=candles, settlements=settlements)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/kalshi.db")
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--firings", default="reports/structure/r3_firings.json")
    ap.add_argument("--size", type=int, default=200)
    ap.add_argument("--out", default="reports/structure/r3_strategy.json")
    args = ap.parse_args()

    firings = json.load(open(args.firings))
    fired_events = {f["event_ticker"] for f in firings}
    all_events = {e.event_ticker: e for e in ke.load_events(args.db)}
    events = [all_events[e] for e in sorted(fired_events) if e in all_events]
    closes = sorted(e.close_ts for e in events if e.close_ts)
    cut = closes[int(0.6 * len(closes))]
    print(f"universe: {len(events)} flagged events, {sum(e.n for e in events)} legs")
    print(f"train/test cut at close_ts {cut}")

    fee_sched = FeeSchedule.from_store(args.db)
    out: dict = {"universe_events": len(events), "train_test_cut_ts": cut, "runs": {}}
    for split, keep in (
        ("all", events),
        ("train", [e for e in events if (e.close_ts or 0) <= cut]),
        ("test", [e for e in events if (e.close_ts or 0) > cut]),
    ):
        for name, cls in (("no_set_sweep", NoSetSweep), ("bracket_fade", BracketFade)):
            # One engine run PER EVENT: the engine advances every loaded
            # market on every clock event, so a single 500-market hourly run
            # is quadratic. Events are independent for a set arb anyway, and
            # each gets the full bankroll (P&L is summed; the per-event and
            # concentration stats below are what the verdict rests on).
            net = fees = n_fills = n_orders = contracts = 0
            pm: dict[str, int] = {}
            by_event: dict[str, int] = defaultdict(int)
            for ev in keep:
                res = run_backtest(
                    build_provider([ev], args.store),
                    cls({ev.event_ticker: ev.tickers}, size=args.size),
                    EngineConfig(
                        risk=RiskConfig(), candle_period_s=3600,
                        keep_events_in_memory=False, fee_schedule=fee_sched,
                    ),
                )
                net += res.net_pnl_cents
                fees += res.fees_cents
                n_fills += len(res.fills)
                n_orders += len(res.orders)
                contracts += sum(f.count for f in res.fills)
                for tk, v in res.per_market.items():
                    if v["contracts_traded"]:
                        pm[tk] = v["net_pnl_cents"]
                        by_event[ev.event_ticker] += v["net_pnl_cents"]
            out["runs"][f"{name}|{split}"] = {
                "n_events": len(keep),
                "net_pnl_dollars": round(net / 100, 2),
                "fees_dollars": round(fees / 100, 2),
                "n_fills": n_fills,
                "n_orders": n_orders,
                "contracts": contracts,
                "markets_traded": len(pm),
                "events_traded": len(by_event),
                "events_positive": sum(1 for v in by_event.values() if v > 0),
                "median_event_pnl_dollars": (
                    round(statistics.median(by_event.values()) / 100, 2) if by_event else None
                ),
                "best_event": (
                    max(by_event.items(), key=lambda kv: kv[1])[0] if by_event else None
                ),
                "best_event_dollars": (
                    round(max(by_event.values()) / 100, 2) if by_event else None
                ),
                "worst_event_dollars": (
                    round(min(by_event.values()) / 100, 2) if by_event else None
                ),
            }
            print(f"{name:<14} {split:<6} {out['runs'][f'{name}|{split}']}", flush=True)
    with open(args.out, "w") as fh:
        json.dump(out, fh, indent=2)


if __name__ == "__main__":
    main()

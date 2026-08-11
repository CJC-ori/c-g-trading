"""Pull + scan the cross-instrument consistency pairs.

    python -m bot.strategies.structure.scan_consistency [--pull] [--staleness-h 24]

Covers both relations in ``consistency.py``:
  (A) monotone threshold ladders — every ``greater``/``greater_or_equal``
      event in the local DB with >=3 legs and non-trivial volume;
  (B) the four hand-specified binary-control vs seat-ladder pairs.
Reports the full coherence history (how far the binary sat outside the
ladder-implied band, hour by hour), every riskless break with its net edge,
and the divergence-trade backtest for the settled pairs.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict

from bot.strategies.structure import consistency as C
from bot.strategies.structure import kalshi_candles as kc
from bot.strategies.structure import kalshi_events as ke
from bot.strategies.structure import overround as ov
from bot.strategies.structure.scan_r3 import forward_filled_grid, load_leg_candles

HOUR = 3600


def _tickers_for_pairs() -> list[tuple[str, list[str], int, int]]:
    """Pull jobs for the control pairs (binary + every ladder leg)."""
    conn = sqlite3.connect("file:data/kalshi.db?mode=ro", uri=True)
    jobs = []
    try:
        for pair in C.CONTROL_PAIRS:
            legs = [
                r[0]
                for r in conn.execute(
                    "SELECT ticker FROM markets WHERE ticker LIKE ?",
                    (pair.ladder_prefix + "%",),
                )
            ]
            wanted = {pair.ladder_prefix + s for s in pair.control_legs + pair.ambiguous_legs}
            legs = [t for t in legs if t in wanted]
            row = conn.execute(
                "SELECT open_time, close_time FROM markets WHERE ticker = ?",
                (pair.binary_ticker,),
            ).fetchone()
            if row is None or not legs:
                continue
            o, cl = ke.iso_to_ts(row[0]), ke.iso_to_ts(row[1])
            end = min(cl or 0, 1786000000 if not pair.settled else (cl or 0))
            start = max(o or 0, end - 180 * 86400)
            jobs.append(
                (f"consistency:{pair.pair_id}", legs + [pair.binary_ticker], start, end)
            )
    finally:
        conn.close()
    return jobs


def scan_control_pairs(store: str, staleness_s: int, db: str) -> dict:
    conn = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    kconn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out: dict = {}
    for pair in C.CONTROL_PAIRS:
        tickers = [pair.ladder_prefix + s for s in pair.control_legs + pair.ambiguous_legs]
        tickers.append(pair.binary_ticker)
        series = load_leg_candles(conn, tickers)
        missing = [t for t in tickers if t not in series]
        if missing:
            out[pair.pair_id] = {"skipped": "missing-candles", "missing": missing}
            continue
        grid = forward_filled_grid(series, staleness_s)
        readings: list[C.CoherenceReading] = []
        for ts, row in grid:
            quotes = {
                tk: ov.LegQuote(tk, bid, ask, vol, stale)
                for tk, (bid, ask, vol, stale, _v24) in row.items()
            }
            ladder = {
                s: quotes[pair.ladder_prefix + s]
                for s in pair.control_legs + pair.ambiguous_legs
            }
            r = C.coherence_reading(ts, pair, quotes[pair.binary_ticker], ladder)
            if r is not None:
                readings.append(r)
        if not readings:
            out[pair.pair_id] = {"skipped": "no-overlapping-hours"}
            continue
        div = [r.divergence_cc / 100 for r in readings]
        arb_a = [r.arb_buy_ladder_net_cc for r in readings]
        arb_b = [r.arb_buy_binary_net_cc for r in readings]
        incoh = [r for r in readings if r.divergence_cc != 0]
        # Forward-filled quotes are how phantom arbs are manufactured: a
        # 20-hour-old bid on a thin ladder leg summed against a fresh binary
        # ask is not a tradeable state. Re-report the riskless claims using
        # only instants where EVERY leg quoted in the same hour.
        fresh = [r for r in readings if r.max_stale_s <= HOUR]
        fresh_a = [r.arb_buy_ladder_net_cc for r in fresh]
        fresh_b = [r.arb_buy_binary_net_cc for r in fresh]
        out[pair.pair_id] = {
            "binary": pair.binary_ticker,
            "ladder": pair.ladder_event,
            "control_rule": pair.rule,
            "settled": pair.settled,
            "n_hours": len(readings),
            "first_ts": readings[0].ts,
            "last_ts": readings[-1].ts,
            "pct_hours_outside_band": round(100 * len(incoh) / len(readings), 2),
            "divergence_cents": {
                "mean_abs": round(statistics.fmean(abs(x) for x in div), 3),
                "median_abs": round(statistics.median(abs(x) for x in div), 3),
                "max_abs": round(max(abs(x) for x in div), 3),
                "p95_abs": round(sorted(abs(x) for x in div)[int(0.95 * len(div))], 3),
            },
            "riskless_buy_ladder_sell_binary": {
                "n_hours_positive": sum(1 for x in arb_a if x > 0),
                "best_net_cents": round(max(arb_a) / 100, 3),
                "median_net_cents": round(statistics.median(arb_a) / 100, 3),
            },
            "riskless_buy_binary_sell_ladder": {
                "n_hours_positive": sum(1 for x in arb_b if x > 0),
                "best_net_cents": round(max(arb_b) / 100, 3),
                "median_net_cents": round(statistics.median(arb_b) / 100, 3),
            },
            "fresh_quotes_only": {
                "n_hours": len(fresh),
                "buy_ladder_sell_binary_positive": sum(1 for x in fresh_a if x > 0),
                "buy_binary_sell_ladder_positive": sum(1 for x in fresh_b if x > 0),
                "best_net_cents": round(max(fresh_a + fresh_b) / 100, 3) if fresh else None,
                "median_fillable_sets": (
                    statistics.median(r.fillable_sets for r in fresh) if fresh else None
                ),
                "max_fillable_sets": max((r.fillable_sets for r in fresh), default=0),
            },
            "median_fillable_sets_same_hour": statistics.median(
                r.fillable_sets for r in readings
            ),
            "divergence_episodes": _episodes(readings),
        }
    conn.close()
    kconn.close()
    return out


def _episodes(readings: list[C.CoherenceReading], min_cc: int = 200,
              min_hours: int = 2) -> dict:
    """Runs of >=min_hours consecutive hourly readings with |divergence| >=
    min_cc (2¢ default). This is the tradeable-signal shape: a persistent
    band violation, not a one-print blip."""
    eps, cur = [], []
    for r in readings:
        if abs(r.divergence_cc) >= min_cc:
            cur.append(r)
        else:
            if len(cur) >= min_hours:
                eps.append(cur)
            cur = []
    if len(cur) >= min_hours:
        eps.append(cur)
    return {
        "min_divergence_cents": min_cc / 100,
        "min_hours": min_hours,
        "n_episodes": len(eps),
        "median_length_h": statistics.median([len(e) for e in eps]) if eps else None,
        "max_length_h": max([len(e) for e in eps]) if eps else None,
        "median_peak_cents": (
            round(statistics.median([max(abs(x.divergence_cc) for x in e) for e in eps]) / 100, 2)
            if eps
            else None
        ),
    }


def scan_ladders(store: str, db: str, staleness_s: int, min_volume: float) -> dict:
    """Relation (A) across every monotone ladder event we have candles for."""
    evs = [
        e
        for e in ke.load_events(db, mutually_exclusive=False, min_legs=3, settled_only=True)
        if e.total_volume >= min_volume
        and all(l.strike_type in ("greater", "greater_or_equal") for l in e.legs)
        and all(l.floor_strike is not None for l in e.legs)
    ]
    conn = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    n_scanned = n_hours = 0
    breaks: list[dict] = []
    by_event: dict[str, int] = defaultdict(int)
    for ev in evs:
        series = load_leg_candles(conn, ev.tickers)
        if len(series) < ev.n:
            continue
        n_scanned += 1
        grid = forward_filled_grid(series, staleness_s)
        strike_of = {l.ticker: float(l.floor_strike) for l in ev.legs}
        for ts, row in grid:
            n_hours += 1
            legs = [
                (strike_of[tk], ov.LegQuote(tk, bid, ask, vol, stale))
                for tk, (bid, ask, vol, stale, _v) in row.items()
            ]
            for b in C.ladder_breaks(legs):
                if b.fires:
                    by_event[ev.event_ticker] += 1
                    breaks.append(
                        {
                            "event": ev.event_ticker,
                            "category": ev.category,
                            "ts": ts,
                            "low": b.low_ticker,
                            "high": b.high_ticker,
                            "low_strike": b.low_strike,
                            "high_strike": b.high_strike,
                            "net_cents": round(b.net_cc / 100, 3),
                        }
                    )
    conn.close()
    nets = [b["net_cents"] for b in breaks]
    return {
        "events_in_universe": len(evs),
        "events_with_complete_candles": n_scanned,
        "event_hours_scanned": n_hours,
        "n_breaks": len(breaks),
        "n_events_with_break": len(by_event),
        "net_cents": {
            "median": round(statistics.median(nets), 3) if nets else None,
            "max": max(nets) if nets else None,
        },
        "top_events": sorted(by_event.items(), key=lambda kv: -kv[1])[:10],
        "examples": sorted(breaks, key=lambda b: -b["net_cents"])[:10],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/kalshi.db")
    ap.add_argument("--store", default=kc.DEFAULT_STORE)
    ap.add_argument("--staleness-h", type=int, default=24)
    ap.add_argument("--min-ladder-volume", type=float, default=20000)
    ap.add_argument("--pull", action="store_true")
    ap.add_argument("--pull-ladders", type=int, default=0)
    ap.add_argument("--out", default="reports/structure/consistency_scan.json")
    args = ap.parse_args()

    if args.pull:
        jobs = _tickers_for_pairs()
        print(f"control-pair pull jobs: {[(j[0], len(j[1])) for j in jobs]}")
        kc.pull_events_parallel(args.store, jobs, workers=4, progress_every=1)
    if args.pull_ladders:
        evs = [
            e
            for e in ke.load_events(args.db, mutually_exclusive=False, min_legs=3)
            if e.total_volume >= args.min_ladder_volume
            and all(l.strike_type in ("greater", "greater_or_equal") for l in e.legs)
        ]
        evs.sort(key=lambda e: -e.total_volume)
        jobs = ke.pull_jobs(evs[: args.pull_ladders], window_days=90)
        print(f"ladder pull: {len(jobs)} events, {sum(len(j[1]) for j in jobs)} legs")
        kc.pull_events_parallel(args.store, jobs, workers=8)

    report = {
        "control_pairs": scan_control_pairs(args.store, args.staleness_h * HOUR, args.db),
        "monotone_ladders": scan_ladders(
            args.store, args.db, args.staleness_h * HOUR, args.min_ladder_volume
        ),
    }
    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=2)
    print(json.dumps(report, indent=2)[:6000])


if __name__ == "__main__":
    main()

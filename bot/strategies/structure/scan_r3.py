"""R3 scan: replay Σ-quotes over every settled multi-outcome event.

    python -m bot.strategies.structure.scan_r3 [--staleness-h 24] [--out ...]

For each event, every leg's top-of-book is re-indexed onto a regular hourly
grid (Kalshi candles are sparse — they exist only for hours with a trade or
a quote change — so the last known quote is carried forward, bounded by
``--staleness-h``). Only hours where *every* leg has a quote inside the
staleness bound are evaluated: a partial leg set makes Σ meaningless.

Outputs a JSON blob with, per family (executable NO-set, executable YES-set
on structurally exhaustive events, and the literal ask-sum diagnostic):
frequency, net edge distribution, volume-capped fillable size, and splits by
category, by year-month, and by train/test time split.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass

from bot.backtest.fees import FeeSchedule
from bot.strategies.structure import kalshi_events as ke
from bot.strategies.structure import overround as ov
from bot.strategies.structure.kalshi_candles import DEFAULT_STORE

HOUR = 3600


@dataclass
class Firing:
    event_ticker: str
    series: str
    category: str
    ts: int
    kind: str
    n_legs: int
    quote_sum_cc: int
    gross_edge_cc: int
    fee_cc: int
    net_edge_cc: int
    cost_cc: int
    max_stale_s: int
    fillable_sets: int
    fillable_notional_cc: int
    fillable_sets_24h: int
    n_yes_resolved: int
    fade_ticker: str | None = None
    fade_share: float | None = None


def load_leg_candles(
    conn: sqlite3.Connection, tickers: list[str]
) -> dict[str, list[tuple]]:
    """ticker -> [(ts, bid_cc, ask_cc, volume)] sorted, quotes present only."""
    out: dict[str, list[tuple]] = {}
    for i in range(0, len(tickers), 400):
        chunk = tickers[i : i + 400]
        q = (
            "SELECT ticker, ts, yes_bid_close, yes_ask_close, volume FROM candles"
            f" WHERE period_interval = 60 AND ticker IN ({','.join('?' * len(chunk))})"
            " ORDER BY ticker, ts"
        )
        for tk, ts, bid, ask, vol in conn.execute(q, chunk):
            if bid is None or ask is None:
                continue
            out.setdefault(tk, []).append(
                (int(ts), ov.dollars_to_cc(bid), ov.dollars_to_cc(ask), float(vol or 0.0))
            )
    return out


def forward_filled_grid(
    legs: dict[str, list[tuple]], staleness_s: int
) -> list[tuple[int, dict[str, tuple[int, int, float, int, float]]]]:
    """Re-index sparse candles onto the hourly union grid.

    Yields (ts, {ticker: (bid_cc, ask_cc, vol_this_hour, stale_s, vol_24h)})
    for hours where EVERY leg has a quote no older than ``staleness_s``.
    """
    if not legs:
        return []
    all_ts = sorted({t for series in legs.values() for t, *_ in series})
    idx = {tk: 0 for tk in legs}
    last: dict[str, tuple[int, int, int]] = {}      # tk -> (ts, bid, ask)
    vol_hist: dict[str, list[tuple[int, float]]] = {tk: [] for tk in legs}
    out = []
    for ts in all_ts:
        row: dict[str, tuple[int, int, float, int, float]] = {}
        ok = True
        for tk, series in legs.items():
            i = idx[tk]
            vol_here = 0.0
            while i < len(series) and series[i][0] <= ts:
                c_ts, bid, ask, vol = series[i]
                last[tk] = (c_ts, bid, ask)
                vol_hist[tk].append((c_ts, vol))
                if c_ts == ts:
                    vol_here = vol
                i += 1
            idx[tk] = i
            if tk not in last:
                ok = False
                break
            l_ts, bid, ask = last[tk]
            stale = ts - l_ts
            if stale > staleness_s:
                ok = False
                break
            hist = vol_hist[tk]
            cutoff = ts - 24 * HOUR
            while hist and hist[0][0] < cutoff:
                hist.pop(0)
            vol24 = sum(v for _, v in hist)
            row[tk] = (bid, ask, vol_here, stale, vol24)
        if ok:
            out.append((ts, row))
    return out


def scan_event(
    ev: ke.MultiOutcomeEvent,
    grid,
    fee_multiplier: float = 1.0,
    buffer_cc: int = 100,
    depth_frac: float = 0.25,
    fee_stress: float = 1.0,
    exhaustive: bool = False,
) -> tuple[list[Firing], dict]:
    """Evaluate every gridded hour of one event. Returns (firings, coverage)."""
    firings: list[Firing] = []
    n_hours = 0
    best_no = best_yes = -10**9
    literal_hours = 0
    for ts, row in grid:
        if ev.close_ts is not None and ts > ev.close_ts:
            continue
        legs = [
            ov.LegQuote(tk, bid, ask, vol, stale)
            for tk, (bid, ask, vol, stale, _v24) in row.items()
        ]
        vol24 = {tk: v[4] for tk, v in row.items()}
        n_hours += 1
        no_sig = ov.no_set_signal(
            legs, fee_multiplier, buffer_cc, depth_frac, fee_stress=fee_stress
        )
        best_no = max(best_no, no_sig.net_edge_cc)
        lit = ov.literal_ask_sum_signal(legs, fee_multiplier, buffer_cc)
        if lit.fires:
            literal_hours += 1
        sigs = [no_sig]
        if exhaustive:
            ysig = ov.yes_set_signal(
                legs, fee_multiplier, buffer_cc, depth_frac, fee_stress=fee_stress
            )
            best_yes = max(best_yes, ysig.net_edge_cc)
            sigs.append(ysig)
        for sig in sigs:
            if not sig.fires:
                continue
            sets24 = int(min(depth_frac * vol24[l.ticker] for l in legs))
            fade = ov.dominant_cheap_bracket(legs) if sig.kind == "no_set" else None
            firings.append(
                Firing(
                    event_ticker=ev.event_ticker,
                    series=ev.series_ticker,
                    category=ev.category,
                    ts=ts,
                    kind=sig.kind,
                    n_legs=sig.n_legs,
                    quote_sum_cc=sig.quote_sum_cc,
                    gross_edge_cc=sig.gross_edge_cc,
                    fee_cc=sig.fee_cc,
                    net_edge_cc=sig.net_edge_cc,
                    cost_cc=sig.cost_cc,
                    max_stale_s=sig.max_stale_s,
                    fillable_sets=sig.fillable_sets,
                    fillable_notional_cc=sig.fillable_notional_cc,
                    fillable_sets_24h=sets24,
                    n_yes_resolved=ev.n_yes,
                    fade_ticker=fade.ticker if fade else None,
                    fade_share=round(fade.share, 3) if fade else None,
                )
            )
    return firings, {
        "event_ticker": ev.event_ticker,
        "category": ev.category,
        "n_legs": ev.n,
        "n_hours": n_hours,
        "literal_hours": literal_hours,
        "best_no_net_cc": best_no if n_hours else None,
        "best_yes_net_cc": best_yes if exhaustive and n_hours else None,
        "close_ts": ev.close_ts,
        "total_volume": ev.total_volume,
        "exhaustive": exhaustive,
    }


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = min(len(xs) - 1, max(0, int(math.ceil(p * len(xs)) - 1)))
    return xs[k]


def summarize(firings: list[Firing], coverage: list[dict], kind: str) -> dict:
    f = [x for x in firings if x.kind == kind]
    ev_hours = sum(c["n_hours"] for c in coverage)
    events_fired = {x.event_ticker for x in f}
    ts_all = [c["close_ts"] for c in coverage if c["close_ts"]]
    span_weeks = ((max(ts_all) - min(ts_all)) / (7 * 86400)) if len(ts_all) > 1 else 0
    net = [x.net_edge_cc / 100 for x in f]
    fill = [x.fillable_notional_cc / 1e6 for x in f]
    fill24 = [x.fillable_sets_24h * x.cost_cc / 1e6 for x in f]
    by_cat: dict[str, int] = defaultdict(int)
    for x in f:
        by_cat[x.category or "?"] += 1
    return {
        "kind": kind,
        "n_events_scanned": len(coverage),
        "n_event_hours_scanned": ev_hours,
        "n_firing_hours": len(f),
        "n_firing_events": len(events_fired),
        "pct_hours_firing": round(100 * len(f) / ev_hours, 4) if ev_hours else 0,
        "pct_events_firing": round(100 * len(events_fired) / len(coverage), 3)
        if coverage
        else 0,
        "calendar_span_weeks": round(span_weeks, 1),
        "firing_events_per_week": round(len(events_fired) / span_weeks, 3)
        if span_weeks
        else None,
        "net_edge_cents": {
            "mean": round(statistics.fmean(net), 3) if net else None,
            "median": round(statistics.median(net), 3) if net else None,
            "p90": round(_pct(net, 0.9), 3) if net else None,
            "max": round(max(net), 3) if net else None,
        },
        "fillable_dollars_same_hour": {
            "median": round(statistics.median(fill), 2) if fill else None,
            "mean": round(statistics.fmean(fill), 2) if fill else None,
            "p90": round(_pct(fill, 0.9), 2) if fill else None,
            "max": round(max(fill), 2) if fill else None,
            "pct_zero": round(100 * sum(1 for x in fill if x == 0) / len(fill), 1)
            if fill
            else None,
        },
        "fillable_dollars_trailing24h": {
            "median": round(statistics.median(fill24), 2) if fill24 else None,
            "p90": round(_pct(fill24, 0.9), 2) if fill24 else None,
            "max": round(max(fill24), 2) if fill24 else None,
        },
        "by_category": dict(sorted(by_cat.items(), key=lambda kv: -kv[1])),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=ke.DEFAULT_DB)
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--staleness-h", type=int, default=24)
    ap.add_argument("--buffer-cents", type=float, default=1.0)
    ap.add_argument("--depth-frac", type=float, default=0.25)
    ap.add_argument("--fee-stress", type=float, default=1.0)
    ap.add_argument("--out", default="reports/structure/r3_scan.json")
    ap.add_argument("--firings-out", default="reports/structure/r3_firings.json")
    args = ap.parse_args()

    events = ke.load_events(args.db)
    fee_sched = FeeSchedule.from_store(args.db)
    conn = sqlite3.connect(f"file:{args.store}?mode=ro", uri=True)
    all_firings: list[Firing] = []
    coverage: list[dict] = []
    lit_firings = 0
    for i, ev in enumerate(events, 1):
        legs = load_leg_candles(conn, ev.tickers)
        if len(legs) < ev.n:      # incomplete leg set -> Σ is meaningless
            coverage.append(
                {
                    "event_ticker": ev.event_ticker,
                    "category": ev.category,
                    "n_legs": ev.n,
                    "n_hours": 0,
                    "literal_hours": 0,
                    "best_no_net_cc": None,
                    "best_yes_net_cc": None,
                    "close_ts": ev.close_ts,
                    "total_volume": ev.total_volume,
                    "exhaustive": False,
                    "skipped": "incomplete-legs",
                }
            )
            continue
        grid = forward_filled_grid(legs, args.staleness_h * HOUR)
        exhaustive = ov.is_exhaustive_by_structure(
            [(l.strike_type, l.floor_strike, l.cap_strike) for l in ev.legs]
        )
        cfg = fee_sched.for_series(ev.series_ticker)
        fired, cov = scan_event(
            ev,
            grid,
            fee_multiplier=cfg.fee_multiplier,
            buffer_cc=int(args.buffer_cents * 100),
            depth_frac=args.depth_frac,
            fee_stress=args.fee_stress,
            exhaustive=exhaustive,
        )
        all_firings += fired
        coverage.append(cov)
        lit_firings += cov["literal_hours"]
        if i % 500 == 0:
            print(f"[{i}/{len(events)}] firings={len(all_firings)}", flush=True)
    conn.close()

    scanned = [c for c in coverage if c["n_hours"] > 0]
    exh = [c for c in scanned if c.get("exhaustive")]
    summary = {
        "params": vars(args),
        "coverage": {
            "events_in_universe": len(events),
            "events_with_complete_leg_candles": len(scanned),
            "events_skipped_incomplete": len(coverage) - len(scanned),
            "event_hours_scanned": sum(c["n_hours"] for c in scanned),
            "structurally_exhaustive_events": len(exh),
        },
        "no_set_executable": summarize(all_firings, scanned, "no_set"),
        "yes_set_exhaustive_only": summarize(all_firings, exh, "yes_set"),
        "literal_ask_sum_diagnostic": {
            "n_firing_hours": lit_firings,
            "pct_hours_firing": round(
                100 * lit_firings / max(1, sum(c["n_hours"] for c in scanned)), 2
            ),
            "note": "Σ best-ask YES > 100c + fees + 1c — NOT executable "
            "(buying NO takes the NO ask = 100 − yes_bid). Diagnostic only.",
        },
    }
    with open(args.out, "w") as fh:
        json.dump(summary, fh, indent=2)
    with open(args.firings_out, "w") as fh:
        json.dump([asdict(x) for x in all_firings], fh, indent=1)
    print(json.dumps(summary, indent=2)[:4000])


if __name__ == "__main__":
    main()

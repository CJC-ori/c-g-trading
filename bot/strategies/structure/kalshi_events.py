"""Multi-outcome event universe, read-only over ``data/kalshi.db``.

Two structural families matter to this package, and Kalshi's own metadata
distinguishes them cleanly (verified over 317,058 markets / 49,744 events):

* ``events.mutually_exclusive = 1`` — at most one leg resolves YES. This is
  the R3 NO-set family (``between``/``custom``/``structured`` strike types:
  candidate fields, bracket ranges, matchups).
* ``events.mutually_exclusive = 0`` with ``strike_type in (greater,
  greater_or_equal)`` — a *monotone threshold ladder* (P(X>=s) is
  non-increasing in s). NOT exclusive; this is the consistency-monitor
  family (``consistency.py``).

Empirical validation of the flag (run ``python -m
bot.strategies.structure.kalshi_events``): of 2,896 settled
mutually-exclusive events with >=2 settled legs, 2,816 resolved exactly one
YES and 80 resolved zero — **never two**. So the NO-set payoff floor of
(n-1)x100 holds in every observed event, and the 80 zero-YES events pay
n x 100 (strictly better).
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass, field

DEFAULT_DB = "data/kalshi.db"
SETTLED = ("yes", "no", "scalar")


def iso_to_ts(value) -> int | None:
    if not value:
        return None
    try:
        return int(
            dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        )
    except ValueError:
        return None


@dataclass(frozen=True)
class EventLeg:
    ticker: str
    result: str
    volume: float
    yes_sub_title: str = ""
    strike_type: str = ""
    floor_strike: float | None = None
    cap_strike: float | None = None


@dataclass(frozen=True)
class MultiOutcomeEvent:
    event_ticker: str
    series_ticker: str
    category: str
    title: str
    mutually_exclusive: bool
    legs: tuple[EventLeg, ...] = field(default_factory=tuple)
    open_ts: int | None = None
    close_ts: int | None = None

    @property
    def n(self) -> int:
        return len(self.legs)

    @property
    def tickers(self) -> list[str]:
        return [l.ticker for l in self.legs]

    @property
    def total_volume(self) -> float:
        return sum(l.volume or 0.0 for l in self.legs)

    @property
    def n_yes(self) -> int:
        return sum(1 for l in self.legs if l.result == "yes")


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_events(
    db_path: str = DEFAULT_DB,
    mutually_exclusive: bool = True,
    min_legs: int = 2,
    settled_only: bool = True,
    exclude_categories: tuple[str, ...] = (),
) -> list[MultiOutcomeEvent]:
    """All events matching the structural filter, legs attached."""
    conn = _connect(db_path)
    try:
        where = ["e.mutually_exclusive = ?"]
        params: list = [1 if mutually_exclusive else 0]
        if settled_only:
            where.append(f"m.result IN ({','.join('?' * len(SETTLED))})")
            params += list(SETTLED)
        for cat in exclude_categories:
            where.append("(e.category IS NULL OR e.category != ?)")
            params.append(cat)
        sql = f"""
            SELECT m.event_ticker, e.series_ticker, e.category, e.title,
                   m.ticker, m.result, m.volume, m.yes_sub_title,
                   m.strike_type, m.floor_strike, m.cap_strike,
                   m.open_time, m.close_time
            FROM markets m JOIN events e ON e.event_ticker = m.event_ticker
            WHERE {' AND '.join(where)}
            ORDER BY m.event_ticker, m.ticker
        """
        by_event: dict[str, dict] = {}
        for r in conn.execute(sql, params):
            et = r["event_ticker"]
            d = by_event.setdefault(
                et,
                {
                    "series": r["series_ticker"] or "",
                    "category": r["category"] or "",
                    "title": r["title"] or "",
                    "legs": [],
                    "open": None,
                    "close": None,
                },
            )
            d["legs"].append(
                EventLeg(
                    ticker=r["ticker"],
                    result=(r["result"] or "").lower(),
                    volume=float(r["volume"] or 0.0),
                    yes_sub_title=r["yes_sub_title"] or "",
                    strike_type=r["strike_type"] or "",
                    floor_strike=r["floor_strike"],
                    cap_strike=r["cap_strike"],
                )
            )
            o, c = iso_to_ts(r["open_time"]), iso_to_ts(r["close_time"])
            if o is not None:
                d["open"] = o if d["open"] is None else min(d["open"], o)
            if c is not None:
                d["close"] = c if d["close"] is None else max(d["close"], c)
        out = []
        for et, d in by_event.items():
            if len(d["legs"]) < min_legs:
                continue
            out.append(
                MultiOutcomeEvent(
                    event_ticker=et,
                    series_ticker=d["series"],
                    category=d["category"],
                    title=d["title"],
                    mutually_exclusive=mutually_exclusive,
                    legs=tuple(d["legs"]),
                    open_ts=d["open"],
                    close_ts=d["close"],
                )
            )
        out.sort(key=lambda e: e.event_ticker)
        return out
    finally:
        conn.close()


def pull_jobs(
    events: list[MultiOutcomeEvent], window_days: int = 90
) -> list[tuple[str, list[str], int, int]]:
    """(event, tickers, start_ts, end_ts) jobs for kalshi_candles.

    The window is the last ``window_days`` of the event's life (the liquid
    period) — a documented bound, not a filter on the signal: 75% of these
    events live <=22 days and are covered end to end.
    """
    jobs = []
    for ev in events:
        if ev.close_ts is None:
            continue
        start = ev.open_ts or (ev.close_ts - window_days * 86400)
        start = max(start, ev.close_ts - window_days * 86400)
        if ev.close_ts <= start:
            start = ev.close_ts - 3600
        jobs.append((ev.event_ticker, ev.tickers, int(start), int(ev.close_ts)))
    return jobs


if __name__ == "__main__":  # exclusivity audit
    evs = load_events()
    from collections import Counter

    c = Counter(e.n_yes for e in evs)
    print(f"settled mutually-exclusive events with >=2 legs: {len(evs)}")
    print(f"legs: {sum(e.n for e in evs)}")
    print(f"n_yes distribution: {dict(sorted(c.items()))}")
    cats = Counter(e.category for e in evs)
    print(f"categories: {cats.most_common()}")

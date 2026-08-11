"""Data adapter between the backtest harness and any history store.

The harness only ever talks to a HistoryProvider. bot/data/ (built by a
separate agent) will expose Kalshi history in SQLite; SqliteHistoryProvider
below is the stub adapter for it, written against the expected schema and
clearly marked TODO(wire) until bot/data/ lands. Tests and strategy smoke
runs use InMemoryHistoryProvider.

`until` semantics everywhere: exclusive point-in-time bound at `until`.
- candles: only candles fully completed by `until` (end_ts <= until).
- trades: only trades with ts < until.
None = no bound (the engine applies its own slicing per decision point;
strategies never see a provider).
"""
from __future__ import annotations

from typing import Iterable, Protocol, Sequence, runtime_checkable

from bot.backtest.types import Candle, MarketInfo, SettlementResult, Trade


@runtime_checkable
class HistoryProvider(Protocol):
    def iter_markets(self, **filters) -> Iterable[MarketInfo]:
        """Yield markets. Recognized filters (all optional): category,
        event_ticker, tickers (iterable), settled_after (unix ts),
        settled_before (unix ts)."""
        ...

    def candles(
        self, ticker: str, period_s: int, until: int | None = None
    ) -> Sequence[Candle]: ...

    def trades(self, ticker: str, until: int | None = None) -> Sequence[Trade]: ...

    def settlement(self, ticker: str) -> SettlementResult | None: ...


# ---------------------------------------------------------------------------
# In-memory fake (used by unit tests and synthetic smoke runs)
# ---------------------------------------------------------------------------

class InMemoryHistoryProvider:
    def __init__(
        self,
        markets: Iterable[MarketInfo] = (),
        candles: Iterable[Candle] = (),
        trades: Iterable[Trade] = (),
        settlements: Iterable[SettlementResult] = (),
    ):
        self._markets: dict[str, MarketInfo] = {m.ticker: m for m in markets}
        self._candles: dict[tuple[str, int], list[Candle]] = {}
        for c in candles:
            self._candles.setdefault((c.ticker, c.period_s), []).append(c)
        for series in self._candles.values():
            series.sort(key=lambda c: c.start_ts)
        self._trades: dict[str, list[Trade]] = {}
        for t in trades:
            self._trades.setdefault(t.ticker, []).append(t)
        for series in self._trades.values():
            series.sort(key=lambda t: t.ts)
        self._settlements: dict[str, SettlementResult] = {
            s.ticker: s for s in settlements
        }

    def iter_markets(self, **filters) -> Iterable[MarketInfo]:
        tickers = filters.get("tickers")
        tickers = set(tickers) if tickers is not None else None
        for m in self._markets.values():
            if tickers is not None and m.ticker not in tickers:
                continue
            if "category" in filters and m.category != filters["category"]:
                continue
            if "event_ticker" in filters and m.event_ticker != filters["event_ticker"]:
                continue
            s = self._settlements.get(m.ticker)
            if "settled_after" in filters and (
                s is None or s.settled_ts <= filters["settled_after"]
            ):
                continue
            if "settled_before" in filters and (
                s is None or s.settled_ts >= filters["settled_before"]
            ):
                continue
            yield m

    def candles(
        self, ticker: str, period_s: int, until: int | None = None
    ) -> Sequence[Candle]:
        series = self._candles.get((ticker, period_s), [])
        if until is None:
            return list(series)
        return [c for c in series if c.end_ts <= until]

    def trades(self, ticker: str, until: int | None = None) -> Sequence[Trade]:
        series = self._trades.get(ticker, [])
        if until is None:
            return list(series)
        return [t for t in series if t.ts < until]

    def settlement(self, ticker: str) -> SettlementResult | None:
        return self._settlements.get(ticker)


# ---------------------------------------------------------------------------
# SQLite adapter for bot/data's store — STUB until bot/data/ lands
# ---------------------------------------------------------------------------

DEFAULT_DB_PATH = "data/kalshi.db"

# TODO(wire): validate against the actual schema created by bot/data/.
# Assumed schema (from ORCHESTRATION.md; column names to be confirmed):
#   markets(ticker PRIMARY KEY, event_ticker, category, title,
#           open_time, close_time, result, settled_time, rules_primary?)
#   candlesticks(ticker, period_s, start_ts, open, high, low, close,
#                volume, yes_bid_close, yes_ask_close)
#   trades(ticker, ts, yes_price, count, taker_side)
# Prices are assumed to be integer YES cents and times unix seconds; if
# bot/data stores ISO strings or dollars, convert in the row adapters below.


class SqliteHistoryProvider:
    """Adapter over bot/data's SQLite store. Lazily imports/connects so the
    harness works (tests, synthetic runs) with no DB present."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._conn = None

    def _connect(self):
        if self._conn is None:
            import os
            import sqlite3  # stdlib; imported lazily on first real use

            if not os.path.exists(self.db_path):
                raise FileNotFoundError(
                    f"history DB not found at {self.db_path} - has bot/data/ "
                    "pulled data yet? (see bot/backtest/README.md)"
                )
            self._conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    # -- row adapters (single place to fix if bot/data's schema differs) ----

    @staticmethod
    def _market_row(r) -> MarketInfo:
        return MarketInfo(
            ticker=r["ticker"],
            event_ticker=r["event_ticker"] or "",
            category=r["category"] or "",
            title=r["title"] or "",
            open_ts=int(r["open_time"] or 0),
            close_ts=int(r["close_time"] or 2**62),
        )

    def iter_markets(self, **filters) -> Iterable[MarketInfo]:
        conn = self._connect()
        clauses, params = [], []
        if "category" in filters:
            clauses.append("category = ?")
            params.append(filters["category"])
        if "event_ticker" in filters:
            clauses.append("event_ticker = ?")
            params.append(filters["event_ticker"])
        if "tickers" in filters:
            tickers = list(filters["tickers"])
            clauses.append(f"ticker IN ({','.join('?' * len(tickers))})")
            params.extend(tickers)
        if "settled_after" in filters:
            clauses.append("settled_time > ?")
            params.append(filters["settled_after"])
        if "settled_before" in filters:
            clauses.append("settled_time < ?")
            params.append(filters["settled_before"])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        # TODO(wire): confirm table/column names vs bot/data schema.
        for r in conn.execute(f"SELECT * FROM markets {where} ORDER BY ticker", params):
            yield self._market_row(r)

    def candles(
        self, ticker: str, period_s: int, until: int | None = None
    ) -> Sequence[Candle]:
        conn = self._connect()
        q = (
            "SELECT * FROM candlesticks WHERE ticker = ? AND period_s = ?"
            + (" AND start_ts + period_s <= ?" if until is not None else "")
            + " ORDER BY start_ts"
        )
        params = [ticker, period_s] + ([until] if until is not None else [])
        out = []
        for r in conn.execute(q, params):
            out.append(
                Candle(
                    ticker=r["ticker"],
                    start_ts=int(r["start_ts"]),
                    period_s=int(r["period_s"]),
                    open=int(r["open"]),
                    high=int(r["high"]),
                    low=int(r["low"]),
                    close=int(r["close"]),
                    volume=int(r["volume"]),
                    yes_bid_close=(
                        int(r["yes_bid_close"]) if r["yes_bid_close"] is not None else None
                    ),
                    yes_ask_close=(
                        int(r["yes_ask_close"]) if r["yes_ask_close"] is not None else None
                    ),
                )
            )
        return out

    def trades(self, ticker: str, until: int | None = None) -> Sequence[Trade]:
        conn = self._connect()
        q = (
            "SELECT * FROM trades WHERE ticker = ?"
            + (" AND ts < ?" if until is not None else "")
            + " ORDER BY ts"
        )
        params = [ticker] + ([until] if until is not None else [])
        return [
            Trade(
                ticker=r["ticker"],
                ts=int(r["ts"]),
                yes_price=int(r["yes_price"]),
                count=int(r["count"]),
                taker_side=r["taker_side"],
            )
            for r in conn.execute(q, params)
        ]

    def settlement(self, ticker: str) -> SettlementResult | None:
        conn = self._connect()
        r = conn.execute(
            "SELECT ticker, result, settled_time FROM markets "
            "WHERE ticker = ? AND result IS NOT NULL",
            (ticker,),
        ).fetchone()
        if r is None:
            return None
        result = str(r["result"]).lower()
        if result not in ("yes", "no", "void"):
            # TODO(wire): map bot/data's result vocabulary (e.g. '', 'scalar')
            return None
        return SettlementResult(
            ticker=r["ticker"], result=result, settled_ts=int(r["settled_time"] or 0)
        )

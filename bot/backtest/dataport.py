"""Data adapter between the backtest harness and any history store.

The harness only ever talks to a HistoryProvider. SqliteHistoryProvider is
the adapter over bot/data's real store (data/kalshi.db) — see its section
header for the unit-conversion and settlement-vocabulary decisions, which
were validated against the live pull (bot/backtest/validate_wiring.py).
Tests and strategy smoke runs use InMemoryHistoryProvider.
Market-universe selection (which tickers to feed a run) lives in
bot/backtest/universe.py and plugs in via iter_markets(tickers=...).

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
# SQLite adapter for bot/data's store (the real data/kalshi.db schema)
# ---------------------------------------------------------------------------
#
# Wiring decisions (validated against the real DB, see bot/data/NOTES.md and
# bot/backtest/validate_wiring.py):
#
# UNITS. The harness stays integer-cent (prices 1..99, money in cents); the
# store keeps decimal dollars with sub-cent (deci-cent) ticks and fractional
# sizes. We convert at this boundary with *conservative directional rounding*:
#   - quotes: bid floors, ask ceils -> the harness never sees a tighter book
#     than actually existed (a sub-cent quote can only make our simulated
#     entry worse, never better);
#   - trade prints & candle trade-closes: round to nearest cent (unbiased
#     marks; these drive marks/qualification, not our execution price);
#   - candle price extremes: low ceils, high floors -> the maker-through rule
#     ("low strictly below limit") can only under-fill, never over-fill;
#   - sizes/volumes: floor to whole contracts -> volume caps only shrink.
# Empirically ~5-6% of candle quotes and ~32% of trade prints carry a
# sub-cent component (deci-cent ticks; mean |rounding delta| ~0.24c, max
# 0.5c on the affected prices — measured in validate_wiring.py); switching
# the whole harness to deci-cents was rejected as not worth 10x-ing every
# constant for a sub-cent effect that our rounding direction makes strictly
# pessimistic where it touches execution.
# NOTE: converted quote closes may legitimately be 0 (no bid) or 100 (no
# ask); order *limits* remain 1..99 so such quotes can never be crossed,
# which is exactly right — there was nobody to trade with.
#
# NO-TRADE CANDLES. 86.5% of candles have no trade in the period; the API
# omits the price OHLC block and the store leaves price_* NULL (volume is 0
# for exactly those rows). Candle.open/high/low/close are then synthesized
# from the bid/ask OHLC midpoints so marks/last_price track the quoted
# market, and volume=0 structurally prevents any fill against them.
#
# SETTLEMENT VOCABULARY (markets.result):
#   'yes' / 'no'  -> SettlementResult yes/no (settlement_value is 1.0/0.0 in
#                    the DB, redundantly).
#   'scalar'      -> fractional payout: settlement_value (dollars, 0..0.99)
#                    -> settlement_value_cents, payout v per YES contract and
#                    100-v per NO contract. 483 markets, mostly sports pushes.
#   ''            -> NOT settled. Two sub-cases, verified in the data: markets
#                    still trading (status active/initialized/inactive), and
#                    status='closed' zombies that closed without ever settling
#                    (all zero-volume). Neither has a payout event, so
#                    settlement() returns None; the engine then simply closes
#                    the market (cancels orders) and never pays out. The
#                    research universe filters to settled markets, so this
#                    path only matters if a caller opts into unsettled ones.
#   status='determined' (result already yes/no, payout pending) counts as
#   settled; its settlement_ts is NULL so we fall back to close_time.
#
# TIME. The store keeps RFC3339 strings for market times (settlement_ts with
# fractional seconds) and unix seconds for candle/trade timestamps; we parse
# ISO -> unix here. Candles are keyed by end_period_ts and period_interval
# is minutes (1/60/1440): start_ts = ts - 60*period_interval.

DEFAULT_DB_PATH = "data/kalshi.db"

_FAR_FUTURE = 2**62


def _iso_ts(value, default: int | None = None) -> int | None:
    """RFC3339 (with or without fractional seconds / 'Z') -> unix seconds."""
    if not value:
        return default
    import datetime as _dt

    try:
        return int(_dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return default


def _cents_floor(dollars: float) -> int:
    import math

    return math.floor(round(dollars * 100, 6))


def _cents_ceil(dollars: float) -> int:
    import math

    return math.ceil(round(dollars * 100, 6))


def _cents_round(dollars: float) -> int:
    import math

    return math.floor(round(dollars * 100, 6) + 0.5)


class SqliteHistoryProvider:
    """Adapter over bot/data's SQLite store (see module notes above for the
    unit/settlement conventions). Lazily connects so the harness works
    (tests, synthetic runs) with no DB present."""

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

    # -- row adapters -------------------------------------------------------

    @staticmethod
    def _market_row(r) -> MarketInfo:
        return MarketInfo(
            ticker=r["ticker"],
            event_ticker=r["event_ticker"] or "",
            category=r["category"] or "",
            title=r["title"] or "",
            open_ts=_iso_ts(r["open_time"], 0),
            close_ts=_iso_ts(r["close_time"], _FAR_FUTURE),
        )

    @staticmethod
    def _candle_row(r) -> Candle:
        period_s = int(r["period_interval"]) * 60
        end_ts = int(r["ts"])
        bid_c = _cents_floor(r["yes_bid_close"]) if r["yes_bid_close"] is not None else None
        ask_c = _cents_ceil(r["yes_ask_close"]) if r["yes_ask_close"] is not None else None
        volume = int(r["volume"] or 0)  # floor: fractional contracts exist
        if r["price_close"] is not None:
            # A trade happened this period: real trade OHLC (nearest-cent for
            # open/close marks; low ceils / high floors so the strictly-through
            # maker rule can only under-fill).
            o = _cents_round(r["price_open"])
            h = _cents_floor(r["price_high"])
            l = _cents_ceil(r["price_low"])
            c = _cents_round(r["price_close"])
        else:
            # No trade this period (the 86.5% case): synthesize OHLC from
            # bid/ask midpoints; volume is 0 so no fill can come from this
            # candle - it only drives clocks, quotes and marks.
            def _mid(bid_col, ask_col):
                b, a = r[bid_col], r[ask_col]
                if b is None or a is None:
                    return None
                return _cents_round((b + a) / 2)

            o = _mid("yes_bid_open", "yes_ask_open")
            h = _mid("yes_bid_high", "yes_ask_high")
            l = _mid("yes_bid_low", "yes_ask_low")
            c = _mid("yes_bid_close", "yes_ask_close")
            if c is None:  # never observed in the real DB; guard anyway
                o = h = l = c = _cents_round(r["price_previous"] or 0.5)
            volume = 0
        l = min(v for v in (l, o, c) if v is not None)
        h = max(v for v in (h, o, c) if v is not None)
        return Candle(
            ticker=r["ticker"],
            start_ts=end_ts - period_s,
            period_s=period_s,
            open=o if o is not None else c,
            high=h,
            low=l,
            close=c,
            volume=volume,
            yes_bid_close=bid_c,
            yes_ask_close=ask_c,
        )

    # -- HistoryProvider ----------------------------------------------------

    def iter_markets(self, **filters) -> Iterable[MarketInfo]:
        conn = self._connect()
        clauses, params = [], []
        if "category" in filters:
            clauses.append("category = ?")
            params.append(filters["category"])
        if "event_ticker" in filters:
            clauses.append("event_ticker = ?")
            params.append(filters["event_ticker"])
        tickers = filters.get("tickers")
        settled_after = filters.get("settled_after")
        settled_before = filters.get("settled_before")
        if settled_after is not None or settled_before is not None:
            clauses.append("result IN ('yes','no','scalar')")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT ticker, event_ticker, category, title, open_time, close_time,"
            " settlement_ts, result FROM markets " + where + " ORDER BY ticker"
        )

        def _rows():
            if tickers is None:
                yield from conn.execute(sql, params)
                return
            # Chunk the IN() list to stay under SQLite's parameter limit.
            tick_list = list(tickers)
            base = clauses + ["ticker IN ({ph})"]
            for i in range(0, len(tick_list), 500):
                chunk = tick_list[i : i + 500]
                w = "WHERE " + " AND ".join(base).format(ph=",".join("?" * len(chunk)))
                yield from conn.execute(
                    "SELECT ticker, event_ticker, category, title, open_time,"
                    f" close_time, settlement_ts, result FROM markets {w} ORDER BY ticker",
                    params + chunk,
                )

        for r in _rows():
            if settled_after is not None or settled_before is not None:
                s_ts = _iso_ts(r["settlement_ts"]) or _iso_ts(r["close_time"])
                if s_ts is None:
                    continue
                if settled_after is not None and s_ts <= settled_after:
                    continue
                if settled_before is not None and s_ts >= settled_before:
                    continue
            yield self._market_row(r)

    def candles(
        self, ticker: str, period_s: int, until: int | None = None
    ) -> Sequence[Candle]:
        if period_s % 60 != 0:
            return []  # store keys candles by whole-minute intervals
        conn = self._connect()
        q = (
            "SELECT * FROM candlesticks WHERE ticker = ? AND period_interval = ?"
            + (" AND ts <= ?" if until is not None else "")
            + " ORDER BY ts"
        )
        params = [ticker, period_s // 60] + ([until] if until is not None else [])
        return [self._candle_row(r) for r in conn.execute(q, params)]

    def trades(self, ticker: str, until: int | None = None) -> Sequence[Trade]:
        conn = self._connect()
        q = (
            "SELECT ticker, ts, yes_price, count, taker_side FROM trades"
            " WHERE ticker = ?"
            + (" AND ts < ?" if until is not None else "")
            + " ORDER BY ts"
        )
        params = [ticker] + ([until] if until is not None else [])
        return [
            Trade(
                ticker=r["ticker"],
                ts=int(r["ts"]),
                yes_price=_cents_round(r["yes_price"]),
                count=int(r["count"] or 0),  # floor fractional contracts
                taker_side=r["taker_side"] if r["taker_side"] in ("yes", "no") else None,
            )
            for r in conn.execute(q, params)
        ]

    def settlement(self, ticker: str) -> SettlementResult | None:
        conn = self._connect()
        r = conn.execute(
            "SELECT ticker, result, settlement_ts, close_time, settlement_value"
            " FROM markets WHERE ticker = ?",
            (ticker,),
        ).fetchone()
        if r is None:
            return None
        result = str(r["result"] or "").lower()
        if result not in ("yes", "no", "scalar"):
            return None  # '' = not settled (active, or closed-without-settling)
        settled_ts = _iso_ts(r["settlement_ts"]) or _iso_ts(r["close_time"])
        if settled_ts is None:
            return None
        value_cents = None
        if result == "scalar":
            v = r["settlement_value"]
            if v is None:
                return None  # unusable without the payout; not seen in real DB
            value_cents = max(0, min(100, _cents_round(v)))
        return SettlementResult(
            ticker=r["ticker"],
            result=result,
            settled_ts=settled_ts,
            settlement_value_cents=value_cents,
        )

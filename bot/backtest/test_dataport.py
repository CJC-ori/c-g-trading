"""Tests for the HistoryProvider adapters."""
import sqlite3

import pytest

from bot.backtest.dataport import (
    HistoryProvider,
    InMemoryHistoryProvider,
    SqliteHistoryProvider,
)
from bot.backtest.testkit import HOUR, mk_candle, mk_market, mk_trade
from bot.backtest.types import SettlementResult


class TestInMemory:
    def make(self):
        markets = [
            mk_market("A", event="E1", category="politics"),
            mk_market("B", event="E1", category="sports"),
        ]
        candles = [mk_candle("A", start=i * HOUR, c=50 + i) for i in range(4)]
        trades = [mk_trade("A", ts=i * HOUR + 30, price=50, count=10) for i in range(4)]
        settles = [SettlementResult("A", "yes", 10 * HOUR)]
        return InMemoryHistoryProvider(markets, candles, trades, settles)

    def test_protocol_conformance(self):
        assert isinstance(self.make(), HistoryProvider)

    def test_candles_until_is_end_ts_inclusive_bound(self):
        p = self.make()
        got = p.candles("A", HOUR, until=2 * HOUR)
        assert [c.end_ts for c in got] == [HOUR, 2 * HOUR]

    def test_trades_until_is_strict(self):
        p = self.make()
        got = p.trades("A", until=HOUR + 30)
        assert [t.ts for t in got] == [30]

    def test_market_filters(self):
        p = self.make()
        assert [m.ticker for m in p.iter_markets(category="sports")] == ["B"]
        assert [m.ticker for m in p.iter_markets(tickers=["A"])] == ["A"]
        assert [m.ticker for m in p.iter_markets(settled_after=5 * HOUR)] == ["A"]
        assert list(p.iter_markets(settled_after=20 * HOUR)) == []

    def test_settlement_lookup(self):
        p = self.make()
        assert p.settlement("A").result == "yes"
        assert p.settlement("B") is None

    def test_unknown_ticker_empty(self):
        p = self.make()
        assert p.candles("ZZZ", HOUR) == []
        assert p.trades("ZZZ") == []


# ---------------------------------------------------------------------------
# SQLite adapter: exercised against the ASSUMED bot/data schema.
# TODO(wire): once bot/data/ lands, point this at its real schema/fixtures.
# ---------------------------------------------------------------------------

ASSUMED_SCHEMA = """
CREATE TABLE markets (
    ticker TEXT PRIMARY KEY, event_ticker TEXT, category TEXT, title TEXT,
    open_time INTEGER, close_time INTEGER, result TEXT, settled_time INTEGER
);
CREATE TABLE candlesticks (
    ticker TEXT, period_s INTEGER, start_ts INTEGER,
    open INTEGER, high INTEGER, low INTEGER, close INTEGER, volume INTEGER,
    yes_bid_close INTEGER, yes_ask_close INTEGER
);
CREATE TABLE trades (
    ticker TEXT, ts INTEGER, yes_price INTEGER, count INTEGER, taker_side TEXT
);
"""


@pytest.fixture
def sqlite_db(tmp_path):
    path = str(tmp_path / "kalshi.db")
    conn = sqlite3.connect(path)
    conn.executescript(ASSUMED_SCHEMA)
    conn.execute(
        "INSERT INTO markets VALUES ('A','E1','politics','Market A',0,36000,'yes',36000)"
    )
    conn.execute(
        "INSERT INTO markets VALUES ('B','E2','sports','Market B',0,36000,NULL,NULL)"
    )
    conn.executemany(
        "INSERT INTO candlesticks VALUES ('A',3600,?,?,?,?,?,?,?,?)",
        [(0, 50, 52, 49, 51, 300, 50, 52), (3600, 51, 53, 50, 52, 200, 51, 53)],
    )
    conn.executemany(
        "INSERT INTO trades VALUES ('A',?,?,?,?)",
        [(100, 50, 25, "yes"), (4000, 52, 10, "no")],
    )
    conn.commit()
    conn.close()
    return path


class TestSqliteProvider:
    def test_markets(self, sqlite_db):
        p = SqliteHistoryProvider(sqlite_db)
        ms = list(p.iter_markets())
        assert [m.ticker for m in ms] == ["A", "B"]
        assert ms[0].category == "politics"
        assert [m.ticker for m in p.iter_markets(category="sports")] == ["B"]

    def test_candles(self, sqlite_db):
        p = SqliteHistoryProvider(sqlite_db)
        got = p.candles("A", 3600)
        assert len(got) == 2
        assert (got[0].open, got[0].close, got[0].volume) == (50, 51, 300)
        assert got[0].yes_ask_close == 52
        assert [c.end_ts for c in p.candles("A", 3600, until=3600)] == [3600]

    def test_trades(self, sqlite_db):
        p = SqliteHistoryProvider(sqlite_db)
        got = p.trades("A")
        assert [(t.ts, t.yes_price, t.count) for t in got] == [(100, 50, 25), (4000, 52, 10)]
        assert [t.ts for t in p.trades("A", until=4000)] == [100]

    def test_settlement(self, sqlite_db):
        p = SqliteHistoryProvider(sqlite_db)
        s = p.settlement("A")
        assert (s.result, s.settled_ts) == ("yes", 36000)
        assert p.settlement("B") is None

    def test_missing_db_raises_only_on_use(self, tmp_path):
        p = SqliteHistoryProvider(str(tmp_path / "nope.db"))  # construct OK
        with pytest.raises(FileNotFoundError):
            list(p.iter_markets())

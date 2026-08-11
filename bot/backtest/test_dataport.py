"""Tests for the HistoryProvider adapters."""
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
# SQLite adapter: exercised against the REAL bot/data schema, built through
# bot.data.store.Store from API-shaped payloads so the whole
# API-JSON -> store -> provider -> harness-types path is what's tested.
# ---------------------------------------------------------------------------

# Unix anchors used below (UTC): 2026-06-01T00:00:00Z
T0 = 1780272000
HOUR_ISO = lambda h: __import__("datetime").datetime.fromtimestamp(  # noqa: E731
    T0 + h * 3600, __import__("datetime").timezone.utc
).strftime("%Y-%m-%dT%H:%M:%SZ")


def _candle(end_offset_h, *, price=None, bid, ask, volume="0.00"):
    """API-shaped candlestick dict (dollar strings, end_period_ts keyed)."""
    c = {
        "end_period_ts": T0 + end_offset_h * 3600,
        "volume_fp": volume,
        "open_interest_fp": "100.00",
        "yes_bid": {
            "open_dollars": bid[0], "high_dollars": bid[1],
            "low_dollars": bid[2], "close_dollars": bid[3],
        },
        "yes_ask": {
            "open_dollars": ask[0], "high_dollars": ask[1],
            "low_dollars": ask[2], "close_dollars": ask[3],
        },
    }
    if price is not None:
        c["price"] = {
            "open_dollars": price[0], "high_dollars": price[1],
            "low_dollars": price[2], "close_dollars": price[3],
        }
    else:
        c["price"] = {"previous_dollars": "0.5000"}
    return c


@pytest.fixture
def sqlite_db(tmp_path):
    from bot.data.store import Store

    path = str(tmp_path / "kalshi.db")
    with Store(path) as store:
        store.upsert_markets(
            [
                {
                    "ticker": "AAA-26-X",
                    "event_ticker": "AAA-26",
                    "title": "Market A",
                    "status": "finalized",
                    "open_time": HOUR_ISO(-48),
                    "close_time": HOUR_ISO(10),
                    "settlement_ts": HOUR_ISO(11).replace("Z", ".445274Z"),
                    "result": "yes",
                    "settlement_value_dollars": "1.0000",
                    "volume_fp": "5000.00",
                },
                {
                    "ticker": "BBB-26-Y",
                    "event_ticker": "BBB-26",
                    "title": "Market B (unsettled)",
                    "status": "active",
                    "open_time": HOUR_ISO(-48),
                    "close_time": HOUR_ISO(100),
                    "result": "",
                    "volume_fp": "10.00",
                },
                {
                    "ticker": "CCC-26-Z",
                    "event_ticker": "CCC-26",
                    "title": "Market C (scalar push)",
                    "status": "finalized",
                    "open_time": HOUR_ISO(-48),
                    "close_time": HOUR_ISO(10),
                    "settlement_ts": HOUR_ISO(12),
                    "result": "scalar",
                    "settlement_value_dollars": "0.5000",
                    "volume_fp": "2000.00",
                },
                {
                    "ticker": "DDD-26-W",
                    "event_ticker": "DDD-26",
                    "title": "Market D (determined, payout pending)",
                    "status": "determined",
                    "open_time": HOUR_ISO(-48),
                    "close_time": HOUR_ISO(10),
                    "result": "no",
                    "settlement_value_dollars": "0.0000",
                    "volume_fp": "2000.00",
                },
            ],
            category_by_series={"AAA": "Politics", "BBB": "Sports", "CCC": "Politics", "DDD": "Politics"},
        )
        # AAA candles: h1 = real trade candle with deci-cent prices;
        # h2 = no-trade candle (price OHLC absent, volume 0).
        store.upsert_candlesticks(
            "AAA-26-X",
            60,
            [
                _candle(
                    1,
                    price=("0.5000", "0.5230", "0.4910", "0.5150"),
                    bid=("0.50", "0.52", "0.49", "0.5050"),  # close 50.50 -> floor 50
                    ask=("0.52", "0.54", "0.51", "0.5250"),  # close 52.50 -> ceil 53
                    volume="300.70",
                ),
                _candle(
                    2,
                    bid=("0.50", "0.50", "0.50", "0.5000"),
                    ask=("0.54", "0.54", "0.54", "0.5400"),
                ),
            ],
        )
        store.upsert_trades(
            [
                {
                    "trade_id": "t1",
                    "ticker": "AAA-26-X",
                    "created_time": HOUR_ISO(1).replace("Z", "+00:00"),
                    "yes_price_dollars": "0.5020",  # -> 50c nearest
                    "no_price_dollars": "0.4980",
                    "count_fp": "25.70",  # -> 25 floor
                    "taker_side": "yes",
                },
                {
                    "trade_id": "t2",
                    "ticker": "AAA-26-X",
                    "created_time": HOUR_ISO(2),
                    "yes_price_dollars": "0.5250",  # -> 53c nearest (52.5 rounds up)
                    "no_price_dollars": "0.4750",
                    "count_fp": "10.00",
                    "taker_side": "no",
                },
            ]
        )
    return path


class TestSqliteProvider:
    def test_markets(self, sqlite_db):
        p = SqliteHistoryProvider(sqlite_db)
        ms = list(p.iter_markets())
        assert [m.ticker for m in ms] == ["AAA-26-X", "BBB-26-Y", "CCC-26-Z", "DDD-26-W"]
        a = ms[0]
        assert a.category == "Politics"
        assert a.event_ticker == "AAA-26"
        assert a.open_ts == T0 - 48 * 3600  # ISO parsed to unix
        assert a.close_ts == T0 + 10 * 3600
        assert [m.ticker for m in p.iter_markets(category="Sports")] == ["BBB-26-Y"]
        assert [m.ticker for m in p.iter_markets(tickers=["CCC-26-Z"])] == ["CCC-26-Z"]

    def test_settled_window_filters(self, sqlite_db):
        p = SqliteHistoryProvider(sqlite_db)
        # AAA settles h11, CCC h12, DDD falls back to close_time h10;
        # BBB is unsettled and never matches a settled window.
        got = [m.ticker for m in p.iter_markets(settled_after=T0 + 10 * 3600)]
        assert got == ["AAA-26-X", "CCC-26-Z"]
        got = [m.ticker for m in p.iter_markets(settled_before=T0 + 12 * 3600)]
        assert got == ["AAA-26-X", "DDD-26-W"]

    def test_candles_trade_candle_rounding(self, sqlite_db):
        p = SqliteHistoryProvider(sqlite_db)
        got = p.candles("AAA-26-X", 3600)
        assert len(got) == 2
        c1 = got[0]
        assert (c1.start_ts, c1.end_ts, c1.period_s) == (T0, T0 + 3600, 3600)
        # price OHLC: open/close nearest, high floors, low ceils
        assert (c1.open, c1.high, c1.low, c1.close) == (50, 52, 50, 52)
        assert c1.volume == 300  # fractional volume floors
        # quotes: bid floors (50.50 -> 50), ask ceils (52.50 -> 53)
        assert (c1.yes_bid_close, c1.yes_ask_close) == (50, 53)

    def test_candles_no_trade_candle_synthesized_from_mid(self, sqlite_db):
        p = SqliteHistoryProvider(sqlite_db)
        c2 = p.candles("AAA-26-X", 3600)[1]
        assert c2.volume == 0  # structurally unfillable
        assert c2.close == 52  # mid of 50/54
        assert (c2.yes_bid_close, c2.yes_ask_close) == (50, 54)

    def test_candles_until_bound(self, sqlite_db):
        p = SqliteHistoryProvider(sqlite_db)
        assert [c.end_ts for c in p.candles("AAA-26-X", 3600, until=T0 + 3600)] == [
            T0 + 3600
        ]

    def test_trades(self, sqlite_db):
        p = SqliteHistoryProvider(sqlite_db)
        got = p.trades("AAA-26-X")
        assert [(t.yes_price, t.count, t.taker_side) for t in got] == [
            (50, 25, "yes"),
            (53, 10, "no"),
        ]
        assert [t.ts for t in p.trades("AAA-26-X", until=T0 + 2 * 3600)] == [T0 + 3600]

    def test_settlement_vocabulary(self, sqlite_db):
        p = SqliteHistoryProvider(sqlite_db)
        s = p.settlement("AAA-26-X")
        assert (s.result, s.settled_ts) == ("yes", T0 + 11 * 3600)  # fractional-sec ISO ok
        assert p.settlement("BBB-26-Y") is None  # '' = not settled
        sc = p.settlement("CCC-26-Z")
        assert (sc.result, sc.settlement_value_cents) == ("scalar", 50)
        d = p.settlement("DDD-26-W")  # determined: settlement_ts NULL -> close_time
        assert (d.result, d.settled_ts) == ("no", T0 + 10 * 3600)
        assert p.settlement("NOPE") is None

    def test_missing_db_raises_only_on_use(self, tmp_path):
        p = SqliteHistoryProvider(str(tmp_path / "nope.db"))  # construct OK
        with pytest.raises(FileNotFoundError):
            list(p.iter_markets())

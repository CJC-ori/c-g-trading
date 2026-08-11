"""Tests for market-universe selection (bot/backtest/universe.py)."""
import datetime as dt

import pytest

from bot.backtest.universe import UniverseConfig, select_universe

T0 = 1780272000  # 2026-06-01T00:00:00Z


def _iso(h):
    return dt.datetime.fromtimestamp(T0 + h * 3600, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _market(ticker, *, category, status="finalized", result="yes", volume="5000.00",
            close_h=0):
    return {
        "ticker": ticker,
        "event_ticker": ticker.rsplit("-", 1)[0],
        "title": ticker,
        "status": status,
        "open_time": _iso(-100),
        "close_time": _iso(close_h),
        "result": result,
        "volume_fp": volume,
    }, category


def _candle(end_h, bid="0.90", ask="0.94"):
    return {
        "end_period_ts": T0 + end_h * 3600,
        "volume_fp": "0.00",
        "open_interest_fp": "10.00",
        "price": {"previous_dollars": "0.90"},
        "yes_bid": {k + "_dollars": bid for k in ("open", "high", "low", "close")},
        "yes_ask": {k + "_dollars": ask for k in ("open", "high", "low", "close")},
    }


@pytest.fixture
def db(tmp_path):
    from bot.data.store import Store

    path = str(tmp_path / "kalshi.db")
    rows = [
        _market("POL-26-A", category="Politics"),                       # in
        _market("ELEC-26-B", category="Elections", result="scalar"),    # in (scalar settled)
        _market("SPORT-26-C", category="Sports"),                       # out: category
        _market("CRYP-26-D", category="Crypto"),                        # out: category
        _market("KXMVEFOO-26-E", category="Politics"),                  # out: parlay prefix
        _market("POL-26-F", category="Politics", status="active", result=""),  # out: unsettled
        _market("POL-26-G", category="Politics", volume="10.00"),       # out: volume
        _market("POL-26-H", category="Politics"),                       # out: no candles
        _market("POL-26-I", category="Politics", close_h=500),          # in (close window tests)
    ]
    with Store(path) as store:
        store.upsert_markets(
            [m for m, _ in rows],
            category_by_series={m["ticker"].split("-")[0]: c for m, c in rows},
        )
        for t in ("POL-26-A", "ELEC-26-B", "SPORT-26-C", "CRYP-26-D",
                  "KXMVEFOO-26-E", "POL-26-F", "POL-26-G", "POL-26-I"):
            store.upsert_candlesticks(t, 60, [_candle(-2), _candle(-1)])
        # A cheap-price market for the price-band test.
        store.upsert_candlesticks(
            "POL-26-I", 60, [_candle(-1, bid="0.10", ask="0.14")]
        )
    return path


class TestResearchUniverse:
    def test_default_filters(self, db):
        got = select_universe(db, UniverseConfig())
        assert got == ["ELEC-26-B", "POL-26-A", "POL-26-I"]

    def test_sorted_and_deterministic(self, db):
        assert select_universe(db) == select_universe(db)

    def test_category_include(self, db):
        got = select_universe(db, UniverseConfig(categories=("Elections",)))
        assert got == ["ELEC-26-B"]

    def test_min_volume_zero_admits_small(self, db):
        got = select_universe(db, UniverseConfig(min_volume=0))
        assert "POL-26-G" in got

    def test_require_candles_off_admits_candleless(self, db):
        got = select_universe(db, UniverseConfig(require_candles=False))
        assert "POL-26-H" in got

    def test_settled_only_off_admits_active(self, db):
        got = select_universe(db, UniverseConfig(settled_only=False))
        assert "POL-26-F" in got

    def test_close_window(self, db):
        got = select_universe(db, UniverseConfig(close_before=T0 + 100 * 3600))
        assert "POL-26-I" not in got and "POL-26-A" in got
        got = select_universe(db, UniverseConfig(close_after=T0 + 100 * 3600))
        assert got == ["POL-26-I"]

    def test_price_band(self, db):
        # At T0, POL-26-I's latest candle mid is 12c; others sit at 92c.
        got = select_universe(
            db, UniverseConfig(price_band=(85, 99), price_band_ref_ts=T0)
        )
        assert got == ["ELEC-26-B", "POL-26-A"]
        got = select_universe(
            db, UniverseConfig(price_band=(5, 20), price_band_ref_ts=T0)
        )
        assert got == ["POL-26-I"]

    def test_price_band_requires_ref_ts(self, db):
        with pytest.raises(ValueError):
            select_universe(db, UniverseConfig(price_band=(1, 99)))

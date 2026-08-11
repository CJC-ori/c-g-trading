"""Tests for the weather strategies (no network, no DB).

End-to-end cases run the real engine over InMemoryHistoryProvider data so
the maker-first order path, phase clock, and settlement accounting are
exercised exactly as in a real run.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from bot.backtest.dataport import InMemoryHistoryProvider
from bot.backtest.engine import EngineConfig, run_backtest
from bot.backtest.types import Candle, MarketInfo, SettlementResult
from bot.groundtruth.weather import BiasFit
from bot.strategies.weather.strategy import (
    CityModel,
    Strike,
    WeatherDayAheadStrategy,
    WeatherIntradayStrategy,
    target_date_from_ticker,
)

TZ = "America/New_York"


def _ts(iso: str) -> int:
    return int(dt.datetime.fromisoformat(iso).replace(tzinfo=ZoneInfo(TZ)).timestamp())


def test_target_date_from_ticker():
    assert target_date_from_ticker("KXHIGHNY-26AUG10-T89") == "2026-08-10"
    assert target_date_from_ticker("KXHIGHCHI-26JUN09-B84.5") == "2026-06-09"
    assert target_date_from_ticker("KXHIGHTDAL-26MAY21-T98") == "2026-05-21"
    assert target_date_from_ticker("KXHIGHINFLATION") is None


def _strike(ticker="KXHIGHNY-26AUG10-T89", st="greater", lo=89.0, hi=None):
    return Strike(
        ticker=ticker, series_ticker="KXHIGHNY", target_date="2026-08-10",
        strike_type=st, floor_strike=lo, cap_strike=hi,
    )


def _model(offset=0.0, sd=2.0, lead_fc=None):
    lead_fc = lead_fc or {1: {"2026-08-10": 92.0}, 2: {"2026-08-10": 92.0}}
    bias = {n: BiasFit(offset=offset, sd=sd, mae=sd, n=100) for n in (1, 2)}
    return CityModel(tz=TZ, bias=bias, forecasts=lead_fc)


# ---------------------------------------------------------------------------
# Phase clock
# ---------------------------------------------------------------------------

def test_phases_day_ahead_and_morning():
    strat = WeatherDayAheadStrategy({}, {})
    # day before the target date -> d1 (any hour)
    assert strat._phase(_ts("2026-08-09T15:00:00"), TZ, "2026-08-10") == "d1"
    # target-day 07:00 local -> d0am
    assert strat._phase(_ts("2026-08-10T07:00:00"), TZ, "2026-08-10") == "d0am"
    # target-day before 06:00 local -> nothing (model availability slack)
    assert strat._phase(_ts("2026-08-10T04:00:00"), TZ, "2026-08-10") is None
    # target-day afternoon -> no new entries
    assert strat._phase(_ts("2026-08-10T14:00:00"), TZ, "2026-08-10") is None


def test_intraday_phase_window():
    strat = WeatherIntradayStrategy({}, {}, {}, {})
    assert strat._phase(_ts("2026-08-10T16:00:00"), TZ, "2026-08-10") is None
    assert strat._phase(_ts("2026-08-10T17:00:00"), TZ, "2026-08-10") == "pm4"
    assert strat._phase(_ts("2026-08-10T22:30:00"), TZ, "2026-08-10") is None
    assert strat._phase(_ts("2026-08-09T17:00:00"), TZ, "2026-08-10") is None


# ---------------------------------------------------------------------------
# Fair probability plumbing
# ---------------------------------------------------------------------------

def test_fair_prob_uses_bias_correction():
    # forecast 92, offset -2 -> mu 90; P(high > 89) = P(X >= 89.5) with sd 2
    strat = WeatherDayAheadStrategy({}, {"KXHIGHNY": _model(offset=-2.0, sd=2.0)})
    p = strat.fair_prob(_strike(), "d1")
    from bot.groundtruth.weather import strike_prob
    assert p == pytest.approx(strike_prob(90.0, 2.0, "greater", 89.0, None))


def test_fair_prob_missing_forecast_returns_none():
    strat = WeatherDayAheadStrategy({}, {"KXHIGHNY": _model(lead_fc={1: {}, 2: {}})})
    assert strat.fair_prob(_strike(), "d1") is None


def test_intraday_fair_prob_from_pmf():
    pmf = {0: 0.7, 1: 0.2, 2: 0.1}
    strat = WeatherIntradayStrategy(
        {}, {"KXHIGHNY": _model()},
        max16={("KXHIGHNY", "2026-08-10"): 89.0},
        rise_pmf={"KXHIGHNY": pmf},
    )
    # P(final > 89) = P(rise >= 1) = 0.3
    assert strat.fair_prob(_strike(), "pm4") == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# Maker-first intent construction
# ---------------------------------------------------------------------------

def test_make_intent_yes_side_maker():
    strat = WeatherDayAheadStrategy({}, {})
    # fair 60c, book 40/44: rest YES buy at 41; edge 19c > 2c
    intent = strat._make_intent("X", 0.60, 40, 44, "d1")
    assert intent is not None
    assert intent.side == "yes" and intent.action == "buy"
    assert intent.limit_price_cents == 41
    assert intent.tif == "rest"
    assert intent.p_hat == pytest.approx(0.60)


def test_make_intent_no_side_maker():
    strat = WeatherDayAheadStrategy({}, {})
    # fair 20c, book 40/44: rest sell-YES at 43 == buy NO at 57
    intent = strat._make_intent("X", 0.20, 40, 44, "d1")
    assert intent is not None
    assert intent.side == "no" and intent.action == "buy"
    assert intent.limit_price_cents == 57


def test_make_intent_no_edge_no_order():
    strat = WeatherDayAheadStrategy({}, {})
    # fair 42c on a 40/44 book: no side clears the 2c buffer
    assert strat._make_intent("X", 0.42, 40, 44, "d1") is None


def test_make_intent_needs_both_quotes():
    strat = WeatherDayAheadStrategy({}, {})
    assert strat._make_intent("X", 0.9, None, 44, "d1") is None
    assert strat._make_intent("X", 0.9, 40, None, "d1") is None


# ---------------------------------------------------------------------------
# End-to-end through the engine
# ---------------------------------------------------------------------------

def _mk_market(ticker="KXHIGHNY-26AUG10-T89"):
    return MarketInfo(
        ticker=ticker,
        event_ticker="KXHIGHNY-26AUG10",
        category="Climate and Weather",
        title="high temp NYC >89",
        open_ts=_ts("2026-08-09T10:00:00"),
        close_ts=_ts("2026-08-10T23:59:00"),
    )


def _candles(ticker, start_iso, hours, price=50, bid=48, ask=52, volume=400):
    t0 = _ts(start_iso)
    return [
        Candle(
            ticker=ticker, start_ts=t0 + i * 3600, period_s=3600,
            open=price, high=price + 2, low=price - 4, close=price,
            volume=volume, yes_bid_close=bid, yes_ask_close=ask,
        )
        for i in range(hours)
    ]


def test_day_ahead_end_to_end_wins_settlement():
    """Forecast 95 vs strikes at 89: strategy buys YES as maker, fills
    (candle low goes strictly through the resting bid), settles YES."""
    ticker = "KXHIGHNY-26AUG10-T89"
    market = _mk_market(ticker)
    candles = _candles(ticker, "2026-08-09T10:00:00", 30)
    settle = SettlementResult(
        ticker=ticker, result="yes", settled_ts=_ts("2026-08-11T07:00:00")
    )
    provider = InMemoryHistoryProvider([market], candles, [], [settle])
    strikes = {ticker: _strike(ticker)}
    strat = WeatherDayAheadStrategy(
        strikes, {"KXHIGHNY": _model(offset=0.0, sd=2.0,
                                     lead_fc={1: {"2026-08-10": 95.0},
                                              2: {"2026-08-10": 95.0}})},
    )
    result = run_backtest(provider, strat, EngineConfig())
    assert result.fills, "expected maker fills"
    assert all(not f.is_taker for f in result.fills)
    assert all(f.side == "yes" for f in result.fills)
    # maker on weather: zero fees
    assert result.fees_cents == 0
    assert result.net_pnl_cents > 0
    # p_hat was logged with the market mid for Brier scoring
    assert strat.p_hat_log
    assert {r["phase"] for r in strat.p_hat_log} <= {"d1", "d0am"}


def test_day_ahead_no_trade_when_fair_matches_market():
    ticker = "KXHIGHNY-26AUG10-T89"
    market = _mk_market(ticker)
    candles = _candles(ticker, "2026-08-09T10:00:00", 30, price=50, bid=48, ask=52)
    settle = SettlementResult(
        ticker=ticker, result="no", settled_ts=_ts("2026-08-11T07:00:00")
    )
    provider = InMemoryHistoryProvider([market], candles, [], [settle])
    # mu == 89.5 -> fair 0.5 == mid: no edge either side
    strat = WeatherDayAheadStrategy(
        {ticker: _strike(ticker)},
        {"KXHIGHNY": _model(offset=0.0, sd=2.0,
                            lead_fc={1: {"2026-08-10": 89.5},
                                     2: {"2026-08-10": 89.5}})},
    )
    result = run_backtest(provider, strat, EngineConfig())
    assert not result.fills
    assert not result.orders


def test_intraday_end_to_end():
    """Max-so-far 92 at 4 PM with P(rise>=1) small: strike >89 is
    already decided -> strategy buys the YES side cheap if offered."""
    ticker = "KXHIGHNY-26AUG10-T89"
    market = _mk_market(ticker)
    # underpriced YES all day: bid 80 ask 84 while truth is ~certain
    candles = _candles(ticker, "2026-08-10T12:00:00", 10, price=82, bid=80, ask=84)
    settle = SettlementResult(
        ticker=ticker, result="yes", settled_ts=_ts("2026-08-11T07:00:00")
    )
    provider = InMemoryHistoryProvider([market], candles, [], [settle])
    strat = WeatherIntradayStrategy(
        {ticker: _strike(ticker)},
        {"KXHIGHNY": _model()},
        max16={("KXHIGHNY", "2026-08-10"): 92.0},
        rise_pmf={"KXHIGHNY": {0: 0.9, 1: 0.1}},
    )
    result = run_backtest(provider, strat, EngineConfig())
    assert result.fills
    assert all(f.side == "yes" and not f.is_taker for f in result.fills)
    assert result.net_pnl_cents > 0
    # entries only in the pm4 window (>= 16:30 local)
    for oid, o in result.orders.items():
        local = dt.datetime.fromtimestamp(o["ts"], tz=ZoneInfo(TZ))
        assert local.hour >= 16


def test_one_order_per_phase():
    ticker = "KXHIGHNY-26AUG10-T89"
    market = _mk_market(ticker)
    # book never moves through the resting bid -> order rests unfilled;
    # strategy must not stack a new order every hour
    t0 = _ts("2026-08-09T10:00:00")
    candles = [
        Candle(ticker=ticker, start_ts=t0 + i * 3600, period_s=3600,
               open=50, high=50, low=50, close=50, volume=100,
               yes_bid_close=48, yes_ask_close=52)
        for i in range(30)
    ]
    settle = SettlementResult(
        ticker=ticker, result="yes", settled_ts=_ts("2026-08-11T07:00:00")
    )
    provider = InMemoryHistoryProvider([market], candles, [], [settle])
    strat = WeatherDayAheadStrategy(
        {ticker: _strike(ticker)},
        {"KXHIGHNY": _model(lead_fc={1: {"2026-08-10": 95.0},
                                     2: {"2026-08-10": 95.0}})},
    )
    result = run_backtest(provider, strat, EngineConfig())
    # one d1 order + one d0am order at most
    assert len(result.orders) <= 2

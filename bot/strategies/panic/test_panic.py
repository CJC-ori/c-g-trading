"""Tests for P-3 panic-ladder strategies (synthetic fixtures + pure units).

Engine-level tests build a synthetic Michigan-shaped episode (pre-event
plateau ~98c, panic wick, V-recovery, settle YES) and its adverse twin
(wick that never comes back, settle NO) through the real engine with the
exact fee schedule, so fills/fees/settlement all exercise production code.
"""
from __future__ import annotations

import os

import pytest

from bot.backtest.dataport import InMemoryHistoryProvider
from bot.backtest.engine import EngineConfig, run_backtest
from bot.backtest.fees import FeeSchedule
from bot.backtest.risk import RiskConfig
from bot.backtest.types import Candle, MarketInfo, MarketView, SettlementResult, Trade
from bot.strategies.panic import episodes as epi
from bot.strategies.panic.provider import EpisodeProvider, synth_minute_candles
from bot.strategies.panic.strategy import (
    EpisodeWindow,
    R4PanicDipLadder,
    R4bConvexityNo,
    view_pre_avg,
)

HOUR = 3600
T0 = 1_700_000_000 - (1_700_000_000 % HOUR)  # aligned base
WS = T0 + 26 * HOUR                          # window start
WE = WS + 24 * HOUR
TICKER = "PANICTEST-1"


# ---------------------------------------------------------------------------
# Pure units: episodes.py helpers
# ---------------------------------------------------------------------------

def test_time_weighted_avg_carries_forward():
    pts = [(0, 90.0), (50, 100.0)]
    avg, cov = epi.time_weighted_avg(pts, 0, 100)
    assert avg == pytest.approx(95.0)
    assert cov == pytest.approx(100 / 3600)


def test_time_weighted_avg_uses_point_before_range():
    # A value set before t0 carries into the range even with no point inside.
    pts = [(-500, 97.0)]
    avg, cov = epi.time_weighted_avg(pts, 0, 3600)
    assert avg == pytest.approx(97.0)
    assert cov == pytest.approx(1.0)


def test_time_weighted_avg_empty():
    assert epi.time_weighted_avg([], 0, 100) == (None, 0.0)
    assert epi.time_weighted_avg([(200, 50.0)], 0, 100) == (None, 0.0)


def test_classify_outcome():
    assert epi.classify_outcome("yes", 90.0, 85.0) == "no-dip"
    assert epi.classify_outcome("yes", 85.0, 85.0) == "dip-and-revert"
    assert epi.classify_outcome("no", 74.0, 85.0) == "dip-and-die"
    assert epi.classify_outcome("no", 99.0, 85.0) == "died-without-dip"
    assert epi.classify_outcome("no", None, 85.0) == "died-without-dip"


# ---------------------------------------------------------------------------
# view_pre_avg (strategy-side qualification, point-in-time)
# ---------------------------------------------------------------------------

def _mk_view(candles, trades, t):
    info = MarketInfo(ticker=TICKER, category="Elections")
    return MarketView(
        market=info,
        t=t,
        candles_by_period={60: tuple(candles)},
        trades_before_t=tuple(trades),
    )


def test_view_pre_avg_ignores_window_data():
    pre = [
        Candle(TICKER, WS - 24 * HOUR + i * HOUR, 60, 98, 98, 98, 98, 5)
        for i in range(24)
    ]
    # A deep dip AFTER window start must not touch the qualification avg.
    dip = [Candle(TICKER, WS + 600, 60, 50, 50, 50, 50, 1000)]
    avg, cov = view_pre_avg(_mk_view(pre + dip, [], WS + 3600), WS, 24)
    assert avg == pytest.approx(98.0)
    assert cov >= 23.0


def test_view_pre_avg_merges_trades():
    trades = [Trade(TICKER, WS - 12 * HOUR + i * HOUR, 96, 10) for i in range(12)]
    avg, cov = view_pre_avg(_mk_view([], trades, WS + 60), WS, 24)
    assert avg == pytest.approx(96.0)
    assert cov == pytest.approx(12.0, abs=0.1)


# ---------------------------------------------------------------------------
# Synthetic episodes through the real engine
# ---------------------------------------------------------------------------

def _minute_candles(spans):
    """spans: list of (t_from, t_to, close, low, high, vol, bid, ask)."""
    out = []
    for t_from, t_to, close, low, high, vol, bid, ask in spans:
        t = t_from
        while t < t_to:
            out.append(
                Candle(TICKER, t, 60, close, high, low, close, vol,
                       yes_bid_close=bid, yes_ask_close=ask)
            )
            t += 60
    return out


def _run(candles, result="yes", settle_at=None, strategy_cls=R4PanicDipLadder,
         maker_queue=None):
    from bot.backtest import fills as fills_mod

    settle_at = settle_at or (WE + HOUR)
    info = MarketInfo(
        ticker=TICKER, event_ticker="PANICTEST", category="Elections",
        open_ts=T0 - 30 * 86400, close_ts=settle_at, series_ticker="PANICTEST",
    )
    provider = InMemoryHistoryProvider(
        markets=[info],
        candles=candles,
        settlements=[SettlementResult(TICKER, result, settle_at)],
    )
    windows = {TICKER: EpisodeWindow(TICKER, WS, WE)}
    strat = strategy_cls(windows, bankroll_cents=1_000_000, candle_period_s=60)
    config = EngineConfig(
        risk=RiskConfig(),
        candle_period_s=60,
        fee_schedule=FeeSchedule.load_default(),
        maker_queue=maker_queue or fills_mod.MakerQueueConfig(enabled=False),
    )
    return run_backtest(provider, strat, config,
                        market_filters={"tickers": [TICKER]})


PLATEAU = (WS - 26 * HOUR, WS + 2 * HOUR, 98, 98, 99, 5, 97, 99)


def test_r4_michigan_shape_catches_wick_and_exits():
    candles = _minute_candles([
        PLATEAU,
        # panic: 10 minutes with lows through all three rungs
        (WS + 2 * HOUR, WS + 2 * HOUR + 600, 78, 74, 85, 5000, 74, 76),
        # V-recovery above the exit price (pre-avg 98 -> exit 96)
        (WS + 3 * HOUR, WS + 3 * HOUR + 600, 97, 96, 98, 5000, 96, 98),
        # calm to window end
        (WS + 3 * HOUR + 600, WE, 98, 98, 99, 5, 97, 99),
    ])
    res = _run(candles, result="yes")
    buys = [f for f in res.fills if f.action == "buy"]
    sells = [f for f in res.fills if f.action == "sell"]
    assert {f.price_cents for f in buys} == {85, 80, 75}  # all rungs caught
    assert sells and all(f.price_cents == 96 for f in sells)
    bought = sum(f.count for f in buys)
    assert sum(f.count for f in sells) == bought
    # ladder cost ~49,950c, exit at 96 -> profit; election series: 0 maker fee
    assert res.fees_cents == 0
    assert res.net_pnl_cents > 0
    expected = sum(f.count * (96 - f.price_cents) for f in buys)
    assert res.net_pnl_cents == expected


def test_r4_dip_and_die_full_loss():
    candles = _minute_candles([
        PLATEAU,
        (WS + 2 * HOUR, WS + 2 * HOUR + 600, 60, 55, 85, 5000, 55, 60),
        (WS + 2 * HOUR + 600, WS + 4 * HOUR, 5, 2, 10, 5000, 2, 5),
    ])
    res = _run(candles, result="no", settle_at=WS + 5 * HOUR)
    buys = [f for f in res.fills if f.action == "buy"]
    assert buys, "ladder should fill in the crash"
    assert not [f for f in res.fills if f.action == "sell"]
    # total loss of every filled contract's premium
    assert res.net_pnl_cents == -sum(f.count * f.price_cents for f in buys)


def test_r4_disarms_below_95():
    candles = _minute_candles([
        (WS - 26 * HOUR, WS + 2 * HOUR, 90, 89, 91, 5, 89, 91),
        (WS + 2 * HOUR, WS + 2 * HOUR + 600, 70, 65, 90, 5000, 65, 70),
    ])
    res = _run(candles, result="yes")
    assert not res.fills
    assert not res.orders


def test_r4_no_fills_when_wick_after_window_end():
    candles = _minute_candles([
        PLATEAU,
        (WS + 2 * HOUR, WE, 98, 97, 99, 5, 97, 99),
        # wick AFTER the window: unfilled rungs were canceled
        (WE + 600, WE + 1200, 80, 74, 98, 5000, 74, 80),
        (WE + 1200, WE + 2 * HOUR, 98, 97, 99, 5, 97, 99),
    ])
    res = _run(candles, result="yes", settle_at=WE + 3 * HOUR)
    assert not res.fills
    cancels = [o for o in res.orders.values()]
    assert cancels, "ladder was placed"


def test_r4_maker_queue_default_still_fills_big_wick():
    """With the default maker-queue model on, a Michigan-sized wick (huge
    prints vs a quiet pre-event tape) still reaches the ladder."""
    from bot.backtest.fills import MakerQueueConfig

    candles = _minute_candles([
        PLATEAU,
        (WS + 2 * HOUR, WS + 2 * HOUR + 600, 78, 74, 85, 20000, 74, 76),
        (WS + 3 * HOUR, WS + 3 * HOUR + 600, 97, 96, 98, 50000, 96, 98),
        (WS + 3 * HOUR + 600, WE, 98, 98, 99, 5, 97, 99),
    ])
    res = _run(candles, result="yes", maker_queue=MakerQueueConfig())
    buys = [f for f in res.fills if f.action == "buy"]
    assert buys and res.net_pnl_cents > 0


def test_r4b_convexity_entry_ladder_and_payoff():
    candles = _minute_candles([
        (WS - 26 * HOUR, WS + 2 * HOUR, 98, 98, 99, 20000, 97, 99),
        # crash through all three NO rungs (yes 85/80/75)
        (WS + 2 * HOUR, WS + 2 * HOUR + 600, 74, 73, 85, 50000, 73, 75),
        (WS + 3 * HOUR, WE, 98, 97, 99, 5, 97, 99),
    ])
    res = _run(candles, result="yes", strategy_cls=R4bConvexityNo)
    buys = [f for f in res.fills if f.action == "buy"]      # NO entries
    sells = [f for f in res.fills if f.action == "sell"]    # NO exits
    assert buys and all(f.side == "no" and f.price_cents <= 3 for f in buys)
    assert {f.price_cents for f in sells} == {15, 20, 25}
    assert res.net_pnl_cents > 0
    # entry was taker: taker fee at ~3c must be charged (exact schedule)
    assert res.fees_cents > 0


def test_r4b_bleed_to_zero_counted():
    candles = _minute_candles([
        (WS - 26 * HOUR, WS + 2 * HOUR, 98, 98, 99, 20000, 97, 99),
        (WS + 2 * HOUR, WE, 99, 98, 100, 5, 98, 99),  # no scare, ever
    ])
    res = _run(candles, result="yes", strategy_cls=R4bConvexityNo)
    buys = [f for f in res.fills if f.action == "buy"]
    assert buys, "entry should fill on the quiet pre-event tape"
    assert not [f for f in res.fills if f.action == "sell"]
    cost = sum(f.count * f.price_cents for f in buys)
    assert res.net_pnl_cents == -(cost + res.fees_cents)
    assert res.net_pnl_cents <= -cost


def test_r4b_skips_non_favorite():
    candles = _minute_candles([
        (WS - 26 * HOUR, WS + 2 * HOUR, 60, 59, 61, 20000, 59, 61),
    ])
    res = _run(candles, result="yes", strategy_cls=R4bConvexityNo)
    assert not res.fills


# ---------------------------------------------------------------------------
# Provider units
# ---------------------------------------------------------------------------

def test_synth_minute_candles():
    trades = [
        Trade(TICKER, 120, 98, 10),
        Trade(TICKER, 130, 95, 5),
        Trade(TICKER, 190, 97, 2),
    ]
    cs = synth_minute_candles(trades, TICKER)
    assert len(cs) == 2
    c0 = cs[0]
    assert (c0.start_ts, c0.open, c0.low, c0.close, c0.volume) == (120, 98, 95, 95, 15)
    assert cs[1].volume == 2


@pytest.mark.skipif(
    not os.path.exists("data/kalshi.db"), reason="real DB not present"
)
def test_episode_provider_michigan_wiring():
    import json

    with open("reports/panic/episodes.json") as fh:
        eps = json.load(fh)["episodes"]
    mi = next(e for e in eps if e["ticker"] == "KXSENATEMID-26-AELS")
    p = EpisodeProvider(
        mi["ticker"], mi["window_start"], mi["window_end"],
        bool(mi["trades_cover_window"]),
    )
    assert p.chosen_period_s == 60
    trades = p.trades(mi["ticker"])
    assert trades, "Michigan trades must be served (they cover the window)"
    assert min(t.yes_price for t in trades
               if mi["window_start"] <= t.ts <= mi["window_end"]) == 74
    infos = list(p.iter_markets())
    assert infos[0].ticker == mi["ticker"]
    s = p.settlement(mi["ticker"])
    assert s is not None and s.result == "yes"

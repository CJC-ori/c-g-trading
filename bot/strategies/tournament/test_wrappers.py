"""Tests for the Round-2 integration wrappers (synthetic fixtures only)."""
from __future__ import annotations

from bot.backtest.testkit import mk_candle, mk_market
from bot.backtest.types import MarketView, Portfolio, Position
from bot.strategies.flb import R5Endgame
from bot.strategies.tournament.wrappers import (
    CombinedStrategy,
    R5PanicStandDown,
    R5SwingFiltered,
)

HOUR = 3600
T0 = 1_000_000 * HOUR


def _market(ticker="MKT", close_ts=None, category="Climate and Weather"):
    m = mk_market(ticker=ticker, close_ts=close_ts or T0 + 4 * HOUR)
    return type(m)(
        ticker=m.ticker, event_ticker=m.event_ticker, category=category,
        title=m.title, open_ts=m.open_ts, close_ts=m.close_ts,
    )


def view_with_history(closes, t=None, category="Climate and Weather",
                      bid=None, ask=None, ticker="MKT", close_ts=None):
    """MarketView whose hourly candles end at t with the given closes;
    the LAST close carries the quoted bid/ask."""
    t = t or T0
    candles = []
    n = len(closes)
    for i, c in enumerate(closes):
        end = t - (n - 1 - i) * HOUR
        last = i == n - 1
        candles.append(mk_candle(
            ticker=ticker, start=end - HOUR, period=HOUR, o=c, c=c, vol=500,
            bid=(bid if last and bid is not None else max(1, c - 1)),
            ask=(ask if last and ask is not None else min(99, c + 1)),
        ))
    market = _market(ticker=ticker, category=category,
                     close_ts=close_ts or t + 4 * HOUR)
    return MarketView(market=market, t=t,
                      candles_by_period={HOUR: tuple(candles)})


def flat():
    return Portfolio(cash_cents=1_000_000)


def holding(ticker="MKT"):
    return Portfolio(cash_cents=1_000_000,
                     positions=(Position(ticker, 10, 900),))


# ---------------------------------------------------------------------------
# I-b swing filter
# ---------------------------------------------------------------------------

class TestSwingFilter:
    def test_base_r5_quotes_without_swing(self):
        v = view_with_history([95] * 30, bid=95, ask=97)
        assert R5Endgame(window_s=6 * HOUR).on_decision_point(v, flat())
        assert R5SwingFiltered(window_s=6 * HOUR).on_decision_point(v, flat())

    def test_blocks_after_adverse_swing_and_recovery(self):
        # favorite dropped 95 -> 70 six hours ago, recovered to 95: the
        # census says that swing was information — no entry for 24h.
        closes = [95] * 20 + [70] + [95] * 6
        v = view_with_history(closes, bid=95, ask=97)
        assert R5Endgame(window_s=6 * HOUR).on_decision_point(v, flat())
        assert R5SwingFiltered(window_s=6 * HOUR).on_decision_point(
            v, flat()) == []

    def test_no_side_swing_is_yes_rise(self):
        # buying NO at 95 (yes ask 5); adverse swing = yes RISING >= 20c.
        closes = [5] * 20 + [30] + [5] * 6
        v = view_with_history(closes, bid=3, ask=5)
        base = R5Endgame(window_s=6 * HOUR).on_decision_point(v, flat())
        assert base and base[0].side == "no"
        assert R5SwingFiltered(window_s=6 * HOUR).on_decision_point(
            v, flat()) == []

    def test_old_swing_does_not_block(self):
        # swing 30h ago: outside the 24h block window.
        closes = [95] * 5 + [70] + [95] * 30
        v = view_with_history(closes, bid=95, ask=97)
        assert R5SwingFiltered(window_s=6 * HOUR).on_decision_point(v, flat())

    def test_small_swing_does_not_block(self):
        closes = [95] * 20 + [80] + [95] * 6   # 15c < 20c
        v = view_with_history(closes, bid=95, ask=97)
        assert R5SwingFiltered(window_s=6 * HOUR).on_decision_point(v, flat())

    def test_block_cancels_resting_quote(self):
        s = R5SwingFiltered(window_s=6 * HOUR)
        v0 = view_with_history([95] * 30, bid=95, ask=97)
        assert s.on_decision_point(v0, flat())          # quote placed
        closes = [95] * 24 + [70, 95, 95]               # fresh swing
        v1 = view_with_history(closes, t=T0 + 3 * HOUR,
                               close_ts=T0 + 7 * HOUR, bid=95, ask=97)
        out = s.on_decision_point(v1, flat())
        assert len(out) == 1 and out[0].size == 0 and out[0].replace
        # once blocked+canceled, subsequent blocked ticks emit nothing
        assert s.on_decision_point(v1, flat()) == []

    def test_holding_passes_through(self):
        closes = [95] * 20 + [70] + [95] * 6
        v = view_with_history(closes, bid=95, ask=97)
        # base R5 returns [] when holding; the filter must not crash/divert
        assert R5SwingFiltered(window_s=6 * HOUR).on_decision_point(
            v, holding()) == []


# ---------------------------------------------------------------------------
# I-c panic stand-down
# ---------------------------------------------------------------------------

class TestPanicStandDown:
    def _strategy(self, **kw):
        return R5PanicStandDown(
            window_s=6 * HOUR,
            night_dates=frozenset({"2022-11-08"}),
            et_date_fn=lambda ts: "2022-11-08",
            **kw,
        )

    def test_blocks_politics_on_event_night(self):
        v = view_with_history([96] * 5, bid=96, ask=98, category="Politics")
        assert R5Endgame(window_s=6 * HOUR).on_decision_point(v, flat())
        assert self._strategy().on_decision_point(v, flat()) == []

    def test_elections_blocked_weather_not(self):
        ve = view_with_history([96] * 5, bid=96, ask=98, category="Elections")
        vw = view_with_history([96] * 5, bid=96, ask=98,
                               category="Climate and Weather")
        s = self._strategy()
        assert s.on_decision_point(ve, flat()) == []
        assert s.on_decision_point(vw, flat())

    def test_offnight_politics_trades(self):
        s = R5PanicStandDown(window_s=6 * HOUR,
                             night_dates=frozenset({"2022-11-08"}),
                             et_date_fn=lambda ts: "2023-01-01")
        v = view_with_history([96] * 5, bid=96, ask=98, category="Politics")
        assert s.on_decision_point(v, flat())

    def test_ticker_window_blocks_any_category(self):
        s = R5PanicStandDown(window_s=6 * HOUR, night_dates=frozenset(),
                             windows={"MKT": (T0 - HOUR, T0 + HOUR)},
                             et_date_fn=lambda ts: "2023-01-01")
        v = view_with_history([96] * 5, bid=96, ask=98,
                              category="Climate and Weather")
        assert s.on_decision_point(v, flat()) == []


# ---------------------------------------------------------------------------
# I-d/I-e combinator
# ---------------------------------------------------------------------------

class _StubLeg:
    decision_interval_s = 3600
    candle_period_s = 3600
    last_inference_cost_cents = 0

    def __init__(self, intents):
        self._intents = intents

    def on_decision_point(self, view, portfolio):
        return list(self._intents)


class TestCombinedStrategy:
    def test_merges_intents_in_leg_order(self):
        v = view_with_history([95] * 5, bid=95, ask=97)
        c = CombinedStrategy([_StubLeg(["a"]), _StubLeg(["b", "c"])])
        assert c.on_decision_point(v, flat()) == ["a", "b", "c"]

    def test_cadence_from_legs(self):
        c = CombinedStrategy([_StubLeg([]), _StubLeg([])])
        assert c.decision_interval_s == 3600
        assert c.candle_period_s == 3600

    def test_r5_leg_still_quotes(self):
        v = view_with_history([95] * 30, bid=95, ask=97)
        c = CombinedStrategy([R5Endgame(window_s=6 * HOUR), _StubLeg([])])
        out = c.on_decision_point(v, flat())
        assert out and out[0].rationale.startswith("R5-endgame")

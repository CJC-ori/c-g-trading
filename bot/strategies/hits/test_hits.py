"""Tests for the hits candidate screen + trade rule logic.

Run: python -m pytest bot/strategies/hits/test_hits.py
"""
from __future__ import annotations

from dataclasses import dataclass, field

from bot.backtest.types import Candle, MarketInfo, MarketView
from bot.forecaster import hits as H
from bot.strategies.hits.strategy import (
    AlwaysFadeStrategy,
    HitsForecast,
    HitsStrategy,
    MarketAsForecastStrategy,
    payoff_multiple,
)

DAY = 86400


# ---------------------------------------------------------------------------
# screen logic
# ---------------------------------------------------------------------------

def test_extreme_band_edges():
    assert H.is_extreme(0.0)
    assert H.is_extreme(0.15)
    assert not H.is_extreme(0.1501)
    assert not H.is_extreme(0.50)
    assert not H.is_extreme(0.8499)
    assert H.is_extreme(0.85)
    assert H.is_extreme(1.0)


def _cand(ticker="T1", event="E1", price=0.10, volume=5000.0) -> H.Candidate:
    return H.Candidate(
        ticker=ticker, event_ticker=event, series_ticker="T", window="A",
        title="Will X happen?", category="Politics", rules="",
        open_ts=0, close_ts=100 * DAY, close_time="2026-03-01T00:00:00Z",
        decision_ts=90 * DAY, price_at_d=price,
        cheap_side="yes" if price <= H.EXTREME_LO else "no",
        volume=volume, result="no",
    )


def test_cheap_side_and_price():
    lo = _cand(price=0.08)
    hi = _cand(price=0.92)
    assert lo.cheap_side == "yes" and abs(lo.cheap_price - 0.08) < 1e-9
    assert hi.cheap_side == "no" and abs(hi.cheap_price - 0.08) < 1e-9


def test_dedupe_by_event_keeps_max_volume():
    a = _cand(ticker="T1", event="E1", volume=100)
    b = _cand(ticker="T2", event="E1", volume=900)
    c = _cand(ticker="T3", event="E2", volume=50)
    out = H.dedupe_by_event([a, b, c])
    assert {x.ticker for x in out} == {"T2", "T3"}


def test_screen_parse_fail_closed():
    # malformed JSON -> every element rejected
    vs = H.parse_hits_screen_response("not json at all", 3)
    assert len(vs) == 3
    assert all(not v.passed and v.parse_failed for v in vs)
    # partial array -> missing elements rejected, present ones honored
    text = '[{"i": 1, "pass": true, "code": null, "path": "vote on Mar 3"}]'
    vs = H.parse_hits_screen_response(text, 2)
    assert vs[0].passed and vs[0].path == "vote on Mar 3"
    assert not vs[1].passed and vs[1].parse_failed
    # pass must be literal true (fail closed on strings etc.)
    vs = H.parse_hits_screen_response('[{"i": 1, "pass": "yes"}]', 1)
    assert not vs[0].passed
    # out-of-range indices ignored
    vs = H.parse_hits_screen_response('[{"i": 9, "pass": true}]', 1)
    assert not vs[0].passed


def test_screen_prompt_never_contains_result():
    c = _cand()
    prompt = H.build_hits_screen_prompt([c])
    assert "result" not in prompt.lower().replace("resolves", "")
    assert c.result not in ("",) and " no\n" not in prompt  # outcome not leaked
    assert "10c" in prompt or "10" in prompt  # price is included


# ---------------------------------------------------------------------------
# trade rule
# ---------------------------------------------------------------------------

def _view(t=90 * DAY, bid=7, ask=9, ticker="T1") -> MarketView:
    c = Candle(ticker=ticker, start_ts=t - 3600, period_s=3600,
               open=8, high=8, low=8, close=8, volume=100,
               yes_bid_close=bid, yes_ask_close=ask)
    return MarketView(
        market=MarketInfo(ticker=ticker, event_ticker="E1", close_ts=100 * DAY),
        t=t, candles_by_period={3600: (c,)},
    )


@dataclass
class _FakePortfolio:
    positions: dict = field(default_factory=dict)

    def position(self, ticker):
        return self.positions.get(ticker)


def _fc(p_yes: float, ticker="T1", asof=90 * DAY, cost=10) -> HitsForecast:
    return HitsForecast(ticker=ticker, p_yes=p_yes, asof_ts=asof, cost_cents=cost)


def test_payoff_multiple():
    assert abs(payoff_multiple(25) - 3.0) < 1e-9
    assert abs(payoff_multiple(10) - 9.0) < 1e-9
    assert payoff_multiple(50) < 3.0


def test_fires_on_big_disagreement_cheap_side():
    # mid 8c, forecast 40% -> edge +32pts, buy YES at bid 7c (payoff 13x)
    s = HitsStrategy({"T1": _fc(0.40)})
    out = s.on_decision_point(_view(), _FakePortfolio())
    assert len(out) == 1
    o = out[0]
    assert o.side == "yes" and o.action == "buy"
    assert o.limit_price_cents == 7 and o.tif == "rest"
    assert o.p_hat == 0.40
    # inference charged once, at first usable decision point
    assert s.last_inference_cost_cents == 10


def test_fires_no_side_when_fading_high_extreme():
    # mid 92c, forecast 60% -> edge -32pts, buy NO at 100-93=7c
    s = HitsStrategy({"T1": _fc(0.60)})
    out = s.on_decision_point(_view(bid=91, ask=93), _FakePortfolio())
    assert len(out) == 1
    assert out[0].side == "no"
    assert out[0].limit_price_cents == 7


def test_gate_blocks_small_disagreement():
    # mid 8c, forecast 20% -> edge +12 < 15 -> no trade
    s = HitsStrategy({"T1": _fc(0.20)})
    assert s.on_decision_point(_view(), _FakePortfolio()) == []
    # but the inference cost is still charged (research spend is real)
    assert s.last_inference_cost_cents == 10


def test_payoff_bound_blocks_non_asymmetric_entry():
    # mid 40c, forecast 90% -> edge +50 >= 15 BUT entry at 39c pays 1.56x < 3x
    s = HitsStrategy({"T1": _fc(0.90)})
    assert s.on_decision_point(_view(bid=39, ask=41), _FakePortfolio()) == []


def test_no_trade_before_asof_and_one_episode():
    s = HitsStrategy({"T1": _fc(0.40, asof=95 * DAY)})
    assert s.on_decision_point(_view(t=94 * DAY), _FakePortfolio()) == []
    assert s.last_inference_cost_cents == 0        # not yet usable
    out = s.on_decision_point(_view(t=95 * DAY), _FakePortfolio())
    assert len(out) == 1
    # second look: already quoted -> nothing, no double charge
    assert s.on_decision_point(_view(t=96 * DAY), _FakePortfolio()) == []
    assert s.last_inference_cost_cents == 0


def test_zero_bid_rests_at_one_cent():
    s = HitsStrategy({"T1": _fc(0.40)})
    out = s.on_decision_point(_view(bid=0, ask=2), _FakePortfolio())
    assert len(out) == 1 and out[0].limit_price_cents == 1


def test_freshness_gate_default_off_preserves_frozen_behaviour():
    # frozen runs never pass freshness_h: a forecast fires days after D,
    # exactly as the frozen engine did (documented, not endorsed)
    s = HitsStrategy({"T1": _fc(0.40, asof=90 * DAY)})
    out = s.on_decision_point(_view(t=95 * DAY), _FakePortfolio())
    assert len(out) == 1


def test_freshness_gate_blocks_stale_forecast():
    # D at day 90, execution 3 days later -> stale under a 24h window
    s = HitsStrategy({"T1": _fc(0.40, asof=90 * DAY)}, freshness_h=24.0)
    assert s.on_decision_point(_view(t=93 * DAY), _FakePortfolio()) == []
    # research spend is still charged at the first usable decision point
    assert s.last_inference_cost_cents == 10
    # inside the window it fires as before
    s2 = HitsStrategy({"T1": _fc(0.40, asof=90 * DAY)}, freshness_h=24.0)
    out = s2.on_decision_point(_view(t=90 * DAY + 12 * 3600), _FakePortfolio())
    assert len(out) == 1


def test_freshness_gate_revalidates_price_gap():
    # the Clayton wrong-side entry: forecast 18% vs 93.5c at D; by execution
    # the price collapsed THROUGH the forecast to ~0.5c. |edge| = 17.5 >= 15
    # would fire without re-validation; the gate must refuse it.
    fc = HitsForecast(ticker="T1", p_yes=0.18, asof_ts=90 * DAY,
                      cost_cents=10, price_at_d_cents=93.5)
    s = HitsStrategy({"T1": fc}, freshness_h=24.0)
    crashed = _view(t=90 * DAY + 3600, bid=0, ask=1)     # mid 0.5c
    assert s.on_decision_point(crashed, _FakePortfolio()) == []
    # same forecast, price still on the rich side -> normal fire (buy NO)
    s2 = HitsStrategy({"T1": fc}, freshness_h=24.0)
    out = s2.on_decision_point(_view(t=90 * DAY + 3600, bid=91, ask=93),
                               _FakePortfolio())
    assert len(out) == 1 and out[0].side == "no"
    # without price_at_d recorded (frozen forecasts), only staleness applies
    s3 = HitsStrategy({"T1": _fc(0.18)}, freshness_h=24.0)
    out = s3.on_decision_point(crashed, _FakePortfolio())
    assert len(out) == 1     # documents the residual gap for legacy rows


def test_market_as_forecast_null_never_fires():
    s = MarketAsForecastStrategy({"T1": 90 * DAY})
    for bid, ask in [(0, 2), (7, 9), (49, 51), (91, 93), (97, 99)]:
        assert s.on_decision_point(_view(bid=bid, ask=ask), _FakePortfolio()) == []
    assert s.last_inference_cost_cents == 0


def test_always_fade_fires_everywhere_cheap():
    s = AlwaysFadeStrategy({"T1": 90 * DAY})
    out = s.on_decision_point(_view(bid=7, ask=9), _FakePortfolio())
    assert len(out) == 1
    assert out[0].side == "yes" and out[0].limit_price_cents == 7
    # claimed edge is exactly the gate (minimum-conviction sizing)
    assert abs(out[0].p_hat - 0.23) < 1e-9


def test_always_fade_high_extreme_buys_no():
    s = AlwaysFadeStrategy({"T1": 90 * DAY})
    out = s.on_decision_point(_view(bid=91, ask=93), _FakePortfolio())
    assert len(out) == 1 and out[0].side == "no"


def test_always_fade_respects_payoff_bound():
    # candidate drifted back to mid-range by decision time -> entry too rich
    s = AlwaysFadeStrategy({"T1": 90 * DAY})
    assert s.on_decision_point(_view(bid=39, ask=41), _FakePortfolio()) == []

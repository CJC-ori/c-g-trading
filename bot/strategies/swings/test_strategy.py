"""Engine-level tests for SwingFade on synthetic data (no DB required).

Verifies the strategy arms exactly on census-shaped triggers, rests the
frozen rung ladder, exits at the 50% retrace, and respects arm_before.
"""
from __future__ import annotations

from bot.backtest.dataport import InMemoryHistoryProvider
from bot.backtest.engine import EngineConfig, run_backtest
from bot.backtest.risk import RiskConfig
from bot.backtest.types import Candle, MarketInfo, SettlementResult
from bot.strategies.swings.strategy import SwingFade

HOUR = 3600
T0 = 1_700_000_000


def mk_candle(tkr, h, close, vol=2000, low=None, high=None, bid=None, ask=None):
    b = bid if bid is not None else max(1, close - 1)
    a = ask if ask is not None else min(99, close + 1)
    lo = low if low is not None else close
    hi = high if high is not None else close
    return Candle(ticker=tkr, start_ts=T0 + h * HOUR, period_s=HOUR,
                  open=close, high=max(hi, close), low=min(lo, close),
                  close=close, volume=vol, yes_bid_close=b, yes_ask_close=a)


def build_provider(closes_and_lows, category="Politics", result="yes",
                   tkr="SWING-TEST"):
    """closes_and_lows: list of (close, low) hourly points."""
    n = len(closes_and_lows)
    candles = [
        mk_candle(tkr, h, c, low=lo)
        for h, (c, lo) in enumerate(closes_and_lows)
    ]
    market = MarketInfo(ticker=tkr, category=category, open_ts=T0,
                       close_ts=T0 + n * HOUR)
    settle = SettlementResult(ticker=tkr, result=result,
                              settled_ts=T0 + n * HOUR)
    return InMemoryHistoryProvider([market], candles, [], [settle])


def run(provider, arm_before=None):
    strategy = SwingFade(bankroll_cents=1_000_000, arm_before=arm_before)
    config = EngineConfig(risk=RiskConfig(bankroll_cents=1_000_000),
                          candle_period_s=HOUR)
    return run_backtest(provider, strategy, config), strategy


class TestSwingFadeEngine:
    def _panic_dip_series(self):
        # 30h at 95c, drop to 82c (13c move, ref 95 >= 90), prints down to
        # 71c next hours (through both rungs 77/72), then revert to 90+.
        pts = [(95, 95)] * 30
        pts += [(82, 80)]                 # trigger hour
        pts += [(75, 71), (74, 71)]       # deep prints: fills 77c and 72c rungs
        pts += [(90, 88)] * 6             # reversion through target (89c)
        pts += [(95, 95)] * 4
        return pts

    def test_panic_dip_arms_fills_and_exits(self):
        res, strat = run(build_provider(self._panic_dip_series()))
        buys = [f for f in res.fills if f.action == "buy"]
        sells = [f for f in res.fills if f.action == "sell"]
        assert buys, "rungs should fill on the deep prints"
        assert {f.price_cents for f in buys} <= {77, 72}
        assert all(not f.is_taker for f in buys), "entries must be maker"
        assert sells, "exit at the retrace target should fill"
        # target = ref - ceil(move/2) = 95 - 7 = 88... floor formula:
        # ref - (move+1)//2 = 95 - 7 = 88
        assert all(f.price_cents >= 88 for f in sells)
        assert res.net_pnl_cents > 0

    def test_no_arm_below_ref_gate(self):
        # same shape but ref 85c < 90: not a panic_dip cell for Politics
        pts = [(85, 85)] * 30 + [(72, 70), (65, 61), (64, 61)] + [(80, 78)] * 8
        res, _ = run(build_provider(pts))
        assert res.fills == []

    def test_no_arm_when_illiquid(self):
        pts = self._panic_dip_series()
        candles_provider = build_provider(pts)
        # rebuild with zero volume (quote-only drift)
        tkr = "SWING-TEST"
        candles = [mk_candle(tkr, h, c, vol=0, low=lo)
                   for h, (c, lo) in enumerate(pts)]
        provider = InMemoryHistoryProvider(
            list(candles_provider.iter_markets()), candles, [],
            [candles_provider.settlement(tkr)])
        res, _ = run(provider)
        assert res.fills == []

    def test_arm_before_blocks_heldout_triggers(self):
        pts = self._panic_dip_series()
        res, _ = run(build_provider(pts), arm_before=T0)  # nothing may arm
        assert res.fills == []
        assert all(o["size"] == 0 or o for o in res.orders.values())
        assert len([o for o in res.orders.values() if o["size"] > 0]) == 0

    def test_spike_fade_buys_no_on_upspike(self):
        # 3c market spikes to 18c (15c move, ref 3 <= 15), prints up through
        # NO rungs (yes 23/28), then fades back to 5c. Settles NO.
        pts = [(3, 3)] * 30
        pts += [(18, 18)]
        pts += [(25, 25), (30, 30)]       # highs print through yes 23 and 28
        # give the high prints real highs:
        candles = []
        tkr = "SWING-TEST"
        for h, (c, lo) in enumerate(pts):
            hi = 30 if h in (31, 32) else c
            candles.append(mk_candle(tkr, h, c, low=lo, high=hi))
        for h in range(len(pts), len(pts) + 8):
            candles.append(mk_candle(tkr, h, 5))
        n = len(candles)
        market = MarketInfo(ticker=tkr, category="Economics", open_ts=T0,
                           close_ts=T0 + n * HOUR)
        settle = SettlementResult(ticker=tkr, result="no",
                                  settled_ts=T0 + n * HOUR)
        provider = InMemoryHistoryProvider([market], candles, [], [settle])
        res, _ = run(provider)
        buys = [f for f in res.fills if f.action == "buy"]
        assert buys, "NO rungs should fill on the spike prints"
        assert all(f.side == "no" for f in buys)
        assert res.net_pnl_cents > 0  # exits at retrace or settles NO

    def test_weather_category_never_arms(self):
        pts = self._panic_dip_series()
        provider = build_provider(pts, category="Climate and Weather")
        res, _ = run(provider)
        assert res.fills == []

"""Engine tests: no-lookahead property, hand-computed P&L, order lifecycle."""
import pytest

from bot.backtest.dataport import InMemoryHistoryProvider
from bot.backtest.engine import EngineConfig, build_decision_times, run_backtest
from bot.backtest.testkit import HOUR, ScriptedStrategy, mk_candle, mk_market, mk_trade
from bot.backtest.types import OrderIntent, SettlementResult

BANKROLL = 1_000_000  # default $10k


def candles_only_market(
    ticker="A",
    closes=(60, 61, 62),
    vols=None,
    lows=None,
    close_ts=None,
    result="yes",
):
    """Hourly candles starting at t=0; settlement at close_ts."""
    vols = vols if vols is not None else (400,) * len(closes)
    candles = []
    prev = closes[0]
    for i, (c, v) in enumerate(zip(closes, vols)):
        low = lows[i] if lows else min(prev, c) - 1
        candles.append(
            mk_candle(ticker=ticker, start=i * HOUR, o=prev, c=c,
                      h=max(prev, c) + 1, l=low, vol=v)
        )
        prev = c
    close_ts = close_ts or (len(closes) + 1) * HOUR
    market = mk_market(ticker=ticker, close_ts=close_ts)
    settle = SettlementResult(ticker, result, close_ts)
    return market, candles, settle


def buy_yes(ticker="A", limit=61, size=50, tif="ioc", p_hat=0.8):
    return OrderIntent(ticker, "yes", "buy", limit, size, tif=tif, p_hat=p_hat)


# ---------------------------------------------------------------------------
# The no-lookahead property (SPEC §1): data at >= t must be invisible.
# ---------------------------------------------------------------------------

class TestNoLookahead:
    def test_views_never_contain_data_at_or_after_t(self):
        market, candles, settle = candles_only_market(
            ticker="A", closes=(60, 61, 62, 63, 64), close_ts=6 * HOUR
        )
        trades = [mk_trade("A", ts, 60 + i, 50) for i, ts in
                  enumerate([1800, HOUR, 2 * HOUR, 3 * HOUR + 1, 4 * HOUR + 30])]
        provider = InMemoryHistoryProvider([market], candles, trades, [settle])
        strat = ScriptedStrategy()  # emits nothing, records every view
        run_backtest(provider, strat)

        assert strat.views, "strategy was never called"
        for view in strat.views:
            for c in view.candles(HOUR):
                assert c.end_ts <= view.t, "candle from the future leaked"
            for tr in view.trades_before_t:
                assert tr.ts < view.t, "trade at >= t leaked"
            if view.last_trade is not None:
                assert view.last_trade.ts < view.t

    def test_trade_exactly_at_t_is_invisible(self):
        market, candles, settle = candles_only_market(ticker="A")
        trades = [mk_trade("A", HOUR, 30, 400)]  # exactly at first decision
        provider = InMemoryHistoryProvider([market], candles, trades, [settle])
        strat = ScriptedStrategy()
        run_backtest(provider, strat)
        first_view = strat.views[0]
        assert first_view.t == HOUR
        assert first_view.last_trade is None

    def test_view_exposes_no_provider_handle(self):
        market, candles, settle = candles_only_market(ticker="A")
        provider = InMemoryHistoryProvider([market], candles, [], [settle])
        strat = ScriptedStrategy()
        run_backtest(provider, strat)
        view = strat.views[0]
        assert not any("provider" in a.lower() for a in dir(view))


# ---------------------------------------------------------------------------
# Hand-computed taker scenario (candles only)
# ---------------------------------------------------------------------------

class TestTakerEndToEnd:
    """Buy 50 YES @ 61 (crossing ask) at t=1h; settle YES.

    C0 closes 60 -> ask estimate 61; the intent crosses => taker.
    Taker window is the following candle (vol 400, cap 100 >= 50): full fill
    at 61c. Fee = ceil(0.07*0.61*0.39 dollars)=2c x 50 = 100c.
    P&L = 50*(100-61) - 100 = 1850c.
    """

    def run(self, result="yes"):
        market, candles, settle = candles_only_market(
            ticker="A", closes=(60, 61, 62), close_ts=4 * HOUR, result=result
        )
        provider = InMemoryHistoryProvider([market], candles, [], [settle])
        strat = ScriptedStrategy({(HOUR, "A"): [buy_yes()]})
        return run_backtest(provider, strat)

    def test_fill_details(self):
        res = self.run()
        assert len(res.fills) == 1
        f = res.fills[0]
        assert (f.count, f.price_cents, f.is_taker, f.fee_cents) == (50, 61, True, 100)
        assert f.ts == 2 * HOUR  # fills in the following candle period

    def test_exact_pnl(self):
        res = self.run()
        assert res.final_cash_cents == BANKROLL - 50 * 61 - 100 + 50 * 100
        assert res.net_pnl_cents == 1850
        assert res.fees_cents == 100

    def test_losing_settlement(self):
        res = self.run(result="no")
        assert res.final_cash_cents == BANKROLL - 50 * 61 - 100
        assert res.net_pnl_cents == -(50 * 61 + 100)

    def test_void_refunds_at_cost(self):
        """Voided market refunds premium; only the fee is lost."""
        res = self.run(result="void")
        assert res.final_cash_cents == BANKROLL - 100
        assert res.net_pnl_cents == -100

    def test_equity_curve_ends_at_cash(self):
        res = self.run()
        assert res.equity_curve[-1][1] == res.final_cash_cents


# ---------------------------------------------------------------------------
# Maker resting order vs subsequent trades
# ---------------------------------------------------------------------------

class TestMakerEndToEnd:
    """Rest a buy 40 YES @ 40 at t=1h against a known tape.

    Tape: 39x80 @t=5000 (fill 20), 41x100 (no), 38x40 @t=8000 (fill 10).
    A trade exactly at placement time must NOT fill (could be concurrent).
    Remaining 10 canceled at close. Settle NO: lose the 30*40=1200 premium.
    """

    def run(self):
        market, candles, settle = candles_only_market(
            ticker="B", closes=(60, 60, 60), close_ts=4 * HOUR, result="no"
        )
        trades = [
            mk_trade("B", HOUR, 30, 400),   # exactly at placement: excluded
            mk_trade("B", 5000, 39, 80),
            mk_trade("B", 6000, 41, 100),
            mk_trade("B", 8000, 38, 40),
        ]
        provider = InMemoryHistoryProvider([market], candles, trades, [settle])
        intent = OrderIntent("B", "yes", "buy", 40, 40, tif="rest", p_hat=0.6)
        strat = ScriptedStrategy({(HOUR, "B"): [intent]})
        return run_backtest(provider, strat)

    def test_fills(self):
        res = self.run()
        assert [(f.count, f.price_cents, f.ts, f.is_taker) for f in res.fills] == [
            (20, 40, 5000, False),
            (10, 40, 8000, False),
        ]
        assert res.fees_cents == 0  # maker fee is 0 by default

    def test_cancel_at_close_and_pnl(self):
        res = self.run()
        cancels = [e for e in res.events if e["type"] == "cancel"]
        assert len(cancels) == 1
        assert cancels[0]["reason"] == "market-closed"
        assert cancels[0]["unfilled"] == 10
        assert res.final_cash_cents == BANKROLL - 30 * 40  # settled NO, premium lost
        # After close+settlement nothing is committed: equity == cash.
        assert res.equity_curve[-1][1] == res.final_cash_cents


class TestBuyNoComplementarity:
    """Buying NO at p == selling YES exposure at 100 - p, through the engine.

    Quotes are 59 bid / 61 ask. A buy NO @ 35 (yes-space sell at 65) is
    passive and rests; it fills only when YES prints AT or THROUGH 65.
    """

    def run(self, intent):
        market, candles, settle = candles_only_market(
            ticker="B", closes=(60, 60, 60), close_ts=4 * HOUR, result="no"
        )
        trades = [
            mk_trade("B", 5000, 66, 80),   # through 65 -> fill 20
            mk_trade("B", 6000, 64, 999),  # below complement -> no fill
            mk_trade("B", 8000, 65, 80),   # at 65 -> fill 20
        ]
        provider = InMemoryHistoryProvider([market], candles, trades, [settle])
        strat = ScriptedStrategy({(HOUR, "B"): [intent]})
        return run_backtest(provider, strat)

    def test_buy_no_fills_when_yes_prints_at_or_above_complement(self):
        res = self.run(OrderIntent("B", "no", "buy", 35, 40, tif="rest", p_hat=0.2))
        assert [(f.count, f.price_cents, f.side, f.is_taker) for f in res.fills] == [
            (20, 35, "no", False),
            (20, 35, "no", False),
        ]
        # 40 NO contracts @35c cost 1400; settle NO pays 4000: pnl +2600.
        assert res.net_pnl_cents == 40 * 100 - 40 * 35

    def test_fills_happen_in_yes_space_at_complement(self):
        res = self.run(OrderIntent("B", "no", "buy", 35, 40, tif="rest", p_hat=0.2))
        fill_events = [e for e in res.events if e["type"] == "fill"]
        assert fill_events and all(e["yes_price_cents"] == 65 for e in fill_events)

    def test_aggressive_buy_no_is_a_taker_at_the_bid(self):
        """Buy NO @ 60 (yes-sell at 40) crosses the 59 bid => taker at 59."""
        res = self.run(OrderIntent("B", "no", "buy", 60, 40, tif="ioc", p_hat=0.2))
        taker_fills = [f for f in res.fills if f.is_taker]
        assert taker_fills
        # executes at the quote it crossed: yes 59 == NO price 41 (price
        # improvement vs the 60c limit), not at the limit itself
        assert all(f.price_cents == 41 for f in taker_fills)


# ---------------------------------------------------------------------------
# Partial fills, ioc vs rest, fills spanning periods
# ---------------------------------------------------------------------------

class TestPartialFillsAndTif:
    """Order 200 vs thin taker window (vol 200 -> cap 50).

    C0 vol 800 (so the pre-trade depth clamp allows 200), C1 vol 200,
    C2/C3 vol 400 with lows below the limit.
    """

    def run(self, tif):
        market, candles, settle = candles_only_market(
            ticker="A",
            closes=(60, 61, 62, 61),
            vols=(800, 200, 400, 400),
            lows=(59, 58, 60, 59),
            close_ts=5 * HOUR,
            result="yes",
        )
        provider = InMemoryHistoryProvider([market], candles, [], [settle])
        strat = ScriptedStrategy({(HOUR, "A"): [buy_yes(size=200, tif=tif)]})
        return run_backtest(provider, strat)

    def test_ioc_partial_fill_then_cancel(self):
        res = self.run("ioc")
        assert [(f.count, f.is_taker) for f in res.fills] == [(50, True)]
        cancels = [e for e in res.events if e["type"] == "cancel"]
        assert cancels and cancels[0]["reason"] == "ioc-expired"
        assert cancels[0]["unfilled"] == 150

    def test_rest_remainder_fills_across_periods(self):
        """Taker leg in the window, maker legs across later candles."""
        res = self.run("rest")
        assert [(f.count, f.is_taker, f.ts) for f in res.fills] == [
            (50, True, 2 * HOUR),    # taker: 25% of the following candle
            (100, False, 3 * HOUR),  # maker: 25% of C2 (low 60 < 61)
            (50, False, 4 * HOUR),   # maker: remainder from C3
        ]
        # All 200 filled at 61: pnl = 200*39 - taker fee 50*2 = 7700
        assert res.net_pnl_cents == 200 * 39 - 100

    def test_maker_candle_touch_does_not_fill(self):
        """Remainder resting at 61 does NOT fill on a candle with low == 61."""
        market, candles, settle = candles_only_market(
            ticker="A",
            closes=(60, 61, 63, 64),
            vols=(800, 200, 400, 400),
            lows=(59, 58, 61, 62),  # C2/C3 lows never strictly below 61
            close_ts=5 * HOUR,
        )
        provider = InMemoryHistoryProvider([market], candles, [], [settle])
        strat = ScriptedStrategy({(HOUR, "A"): [buy_yes(size=200, tif="rest")]})
        res = run_backtest(provider, strat)
        assert sum(f.count for f in res.fills) == 50  # taker leg only

    def test_zero_volume_window_fills_nothing(self):
        market, candles, settle = candles_only_market(
            ticker="A", closes=(60, 61, 62), vols=(800, 0, 0), close_ts=4 * HOUR
        )
        provider = InMemoryHistoryProvider([market], candles, [], [settle])
        strat = ScriptedStrategy({(HOUR, "A"): [buy_yes(size=50)]})
        res = run_backtest(provider, strat)
        assert res.fills == []
        assert res.final_cash_cents == BANKROLL


# ---------------------------------------------------------------------------
# Inference cost + accounting identities
# ---------------------------------------------------------------------------

class TestInferenceCost:
    def test_charged_per_decision(self):
        market, candles, settle = candles_only_market(ticker="A")
        provider = InMemoryHistoryProvider([market], candles, [], [settle])
        strat = ScriptedStrategy(inference_cost_cents=7)
        res = run_backtest(provider, strat)
        assert res.n_decision_calls == 3
        assert res.inference_cost_cents == 21
        assert res.net_pnl_after_inference_cents == res.net_pnl_cents - 21


class TestEventLog:
    def test_jsonl_written(self, tmp_path):
        market, candles, settle = candles_only_market(ticker="A")
        provider = InMemoryHistoryProvider([market], candles, [], [settle])
        path = str(tmp_path / "events.jsonl")
        strat = ScriptedStrategy({(HOUR, "A"): [buy_yes()]})
        res = run_backtest(provider, strat, EngineConfig(event_log_path=path))
        import json

        lines = [json.loads(l) for l in open(path)]
        assert lines == res.events
        types = {l["type"] for l in lines}
        assert {"run_start", "decision", "intent", "order_placed", "fill",
                "settlement", "run_end"} <= types


# ---------------------------------------------------------------------------
# Decision clock
# ---------------------------------------------------------------------------

class TestDecisionClock:
    CANDLES = [
        mk_candle(start=i * HOUR, c=c)
        for i, c in enumerate([50, 51, 55, 56, 60])
    ]

    def test_fixed_interval(self):
        times = build_decision_times(self.CANDLES, 2 * HOUR, None, 100 * HOUR)
        assert times == [HOUR, 3 * HOUR, 5 * HOUR]

    def test_price_move_trigger(self):
        times = build_decision_times(self.CANDLES, None, 4, 100 * HOUR)
        # first always fires (close 50); then |55-50|>=4; then |60-55|>=4
        assert times == [HOUR, 3 * HOUR, 5 * HOUR]

    def test_interval_or_trigger(self):
        times = build_decision_times(self.CANDLES, HOUR, 4, 100 * HOUR)
        assert times == [HOUR, 2 * HOUR, 3 * HOUR, 4 * HOUR, 5 * HOUR]

    def test_cutoff_at_market_end(self):
        times = build_decision_times(self.CANDLES, HOUR, None, 3 * HOUR)
        assert times == [HOUR, 2 * HOUR]  # decision at t == end excluded

    def test_no_candles_no_decisions(self):
        assert build_decision_times([], HOUR, None, 100 * HOUR) == []

    def test_strategy_cannot_peek_between_ticks(self):
        """The engine's clock is the only clock: exactly the declared cadence."""
        market, candles, settle = candles_only_market(
            ticker="A", closes=(60, 60, 60, 60, 60), close_ts=6 * HOUR
        )
        provider = InMemoryHistoryProvider([market], candles, [], [settle])
        strat = ScriptedStrategy()
        strat.decision_interval_s = 2 * HOUR
        run_backtest(provider, strat)
        assert [t for t, _ in strat.calls] == [HOUR, 3 * HOUR, 5 * HOUR]


# ---------------------------------------------------------------------------
# Risk integration: clamps flow through the engine
# ---------------------------------------------------------------------------

class TestRiskIntegration:
    def test_oversized_intent_is_clamped_not_rejected(self):
        market, candles, settle = candles_only_market(
            ticker="A", closes=(50, 50, 50), vols=(10_000, 10_000, 10_000)
        )
        provider = InMemoryHistoryProvider([market], candles, [], [settle])
        strat = ScriptedStrategy(
            {(HOUR, "A"): [buy_yes(limit=51, size=100_000, p_hat=0.9)]}
        )
        res = run_backtest(provider, strat)
        intent = res.intents[0]
        assert intent["requested"] == 100_000
        assert 0 < intent["clamped"] < 100_000
        # per-market cap: 5% of $10k = $500 at ~51c+2c fee -> <= 943 contracts
        assert intent["clamped"] <= 50_000 // 53

    def test_missing_p_hat_blocks_opening(self):
        market, candles, settle = candles_only_market(ticker="A")
        provider = InMemoryHistoryProvider([market], candles, [], [settle])
        strat = ScriptedStrategy({(HOUR, "A"): [buy_yes(p_hat=None)]})
        res = run_backtest(provider, strat)
        assert res.fills == []
        assert res.intents[0]["clamped"] == 0


# ---------------------------------------------------------------------------
# Exact per-series fee mode (SYNTHESIS §2.1): centicent ceiling + per-order
# accumulator, maker fees on quadratic_with_maker_fees series
# ---------------------------------------------------------------------------

class TestExactFeeMode:
    def run_taker(self, fee_schedule):
        from bot.backtest.fees import FeeSchedule

        market, candles, settle = candles_only_market(
            ticker="A", closes=(60, 61, 62), close_ts=4 * HOUR, result="yes"
        )
        provider = InMemoryHistoryProvider([market], candles, [], [settle])
        strat = ScriptedStrategy({(HOUR, "A"): [buy_yes()]})
        cfg = EngineConfig(fee_schedule=fee_schedule)
        return run_backtest(provider, strat, cfg)

    def test_taker_fee_centicent_ceiled_then_cent_ceiled(self):
        from bot.backtest.fees import FeeSchedule

        res = self.run_taker(FeeSchedule({}))
        # 50 @ 61c: 0.07*50*0.61*0.39 = $0.832665 -> ceil $0.8327 -> 84c
        # (legacy per-contract path charges 100c).
        assert res.fees_cents == 84
        assert res.net_pnl_cents == 50 * (100 - 61) - 84

    def test_zero_multiplier_series_pays_no_fee(self):
        from bot.backtest.fees import FeeSchedule, SeriesFeeConfig

        sched = FeeSchedule({"A": SeriesFeeConfig("quadratic", 0.0)})
        res = self.run_taker(sched)
        assert res.fees_cents == 0

    def test_fee_stress_applies_in_exact_mode(self):
        from dataclasses import replace as dc_replace

        from bot.backtest.fees import FeeSchedule

        market, candles, settle = candles_only_market(
            ticker="A", closes=(60, 61, 62), close_ts=4 * HOUR, result="yes"
        )
        provider = InMemoryHistoryProvider([market], candles, [], [settle])
        strat = ScriptedStrategy({(HOUR, "A"): [buy_yes()]})
        cfg = EngineConfig(fee_schedule=FeeSchedule({}), fee_stress=1.5)
        res = run_backtest(provider, strat, cfg)
        # 1.5 * $0.832665 = $1.2489975 -> ceil cc $1.2490 -> 125c
        assert res.fees_cents == 125

    def test_maker_fee_charged_on_maker_fee_series_with_accumulator(self):
        from bot.backtest.fees import FeeSchedule, SeriesFeeConfig

        market, candles, settle = candles_only_market(
            ticker="B", closes=(60, 60, 60), close_ts=4 * HOUR, result="no"
        )
        trades = [
            mk_trade("B", 5000, 39, 80),   # maker fill 20 @ 40
            mk_trade("B", 8000, 38, 40),   # maker fill 10 @ 40
        ]
        provider = InMemoryHistoryProvider([market], candles, trades, [settle])
        intent = OrderIntent("B", "yes", "buy", 40, 40, tif="rest", p_hat=0.6)
        strat = ScriptedStrategy({(HOUR, "B"): [intent]})
        sched = FeeSchedule(
            {"B": SeriesFeeConfig("quadratic_with_maker_fees", 1.0)}
        )
        res = run_backtest(provider, strat, EngineConfig(fee_schedule=sched))
        # fill 1: 0.0175*20*0.4*0.6 = $0.084 -> 840cc, ceil -> 9c charged
        # fill 2: +0.0175*10*0.24 = $0.042 -> total 1260cc = 12.6c -> 13c
        #         -> charges the 4c delta (per-order accumulator converges
        #            to the single-fill-equivalent 13c, not 9+5=14c)
        assert [f.fee_cents for f in res.fills] == [9, 4]
        assert res.fees_cents == 13

    def test_maker_fee_still_zero_on_plain_quadratic(self):
        from bot.backtest.fees import FeeSchedule

        market, candles, settle = candles_only_market(
            ticker="B", closes=(60, 60, 60), close_ts=4 * HOUR, result="no"
        )
        trades = [mk_trade("B", 5000, 39, 80)]
        provider = InMemoryHistoryProvider([market], candles, trades, [settle])
        intent = OrderIntent("B", "yes", "buy", 40, 40, tif="rest", p_hat=0.6)
        strat = ScriptedStrategy({(HOUR, "B"): [intent]})
        res = run_backtest(
            provider, strat, EngineConfig(fee_schedule=FeeSchedule({}))
        )
        assert res.fees_cents == 0

    def test_legacy_default_unchanged_without_schedule(self):
        res = self.run_taker(None)
        assert res.fees_cents == 100  # per-contract 2c x 50, as before


# ---------------------------------------------------------------------------
# Opportunity lifetime (SYNTHESIS §2.2)
# ---------------------------------------------------------------------------

class TestOpportunityLifetime:
    def test_lifetime_from_trades(self):
        market, candles, settle = candles_only_market(
            ticker="B", closes=(60, 60, 60), close_ts=4 * HOUR, result="no"
        )
        trades = [
            mk_trade("B", HOUR + 100, 39, 10),  # still at/below 40: persists
            mk_trade("B", HOUR + 900, 41, 10),  # above 40: condition dies
        ]
        provider = InMemoryHistoryProvider([market], candles, trades, [settle])
        intent = OrderIntent("B", "yes", "buy", 40, 40, tif="rest", p_hat=0.6)
        strat = ScriptedStrategy({(HOUR, "B"): [intent]})
        res = run_backtest(provider, strat)
        (oid,) = res.orders
        assert res.orders[oid]["opportunity_lifetime_s"] == 900

    def test_lifetime_capped_at_market_end_when_never_dies(self):
        market, candles, settle = candles_only_market(
            ticker="B", closes=(60, 60, 60), close_ts=4 * HOUR, result="no"
        )
        trades = [mk_trade("B", HOUR + 100, 39, 10)]
        provider = InMemoryHistoryProvider([market], candles, trades, [settle])
        intent = OrderIntent("B", "yes", "buy", 40, 40, tif="rest", p_hat=0.6)
        strat = ScriptedStrategy({(HOUR, "B"): [intent]})
        res = run_backtest(provider, strat)
        (oid,) = res.orders
        assert res.orders[oid]["opportunity_lifetime_s"] == 3 * HOUR

    def test_lifetime_from_candles_when_no_trades(self):
        # Candle-only market: buy @ 61; C2 (2h-3h) has low 58 (persists),
        # then close_ts. lows: C1 low is from lows param.
        market, candles, settle = candles_only_market(
            ticker="A",
            closes=(60, 61, 65, 65),
            lows=(59, 58, 63, 64),  # C2 low 63 > 61 -> dies at C2 end (3h)
            close_ts=5 * HOUR,
            result="yes",
        )
        provider = InMemoryHistoryProvider([market], candles, [], [settle])
        strat = ScriptedStrategy({(HOUR, "A"): [buy_yes(tif="rest")]})
        res = run_backtest(provider, strat)
        (oid,) = res.orders
        # placed at 1h; first candle starting >= 1h with low > 61 is C2
        # [2h,3h) -> death at its end 3h -> lifetime 2h
        assert res.orders[oid]["opportunity_lifetime_s"] == 2 * HOUR


# ---------------------------------------------------------------------------
# Price quantization to per-market tick structures (SYNTHESIS §2.2)
# ---------------------------------------------------------------------------

class TestPriceQuantization:
    def test_whole_cent_limits_are_on_grid_for_tapered(self):
        from bot.backtest.types import MarketInfo, PriceRange, PriceStructure

        tapered = PriceStructure(
            "tapered_deci_cent",
            (
                PriceRange(0, 1000, 10),      # 0-10c in 0.1c ticks
                PriceRange(1000, 9000, 100),  # 10-90c in 1c ticks
                PriceRange(9000, 10000, 10),  # 90-100c in 0.1c ticks
            ),
        )
        market = MarketInfo(
            ticker="A", event_ticker="EV", category="test", title="A",
            open_ts=0, close_ts=100 * HOUR, price_structure=tapered,
        )
        _, candles, settle = candles_only_market(ticker="A")
        provider = InMemoryHistoryProvider([market], candles, [], [settle])
        strat = ScriptedStrategy({(HOUR, "A"): [buy_yes()]})
        res = run_backtest(provider, strat)
        # 61c is a valid tick: no quantization event, order at 61 unchanged
        assert not [e for e in res.events if e["type"] == "price_quantized"]
        assert res.fills and res.fills[0].price_cents == 61

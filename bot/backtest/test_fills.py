"""Exhaustive unit tests for fill simulation edge cases (SPEC §3)."""
import pytest

from bot.backtest import fills
from bot.backtest.fills import (
    apply_fill_to_position,
    candle_qualifies,
    crosses,
    maker_fill_from_candle,
    maker_fill_from_trade,
    normalize,
    settlement_payout_cents,
    side_price,
    taker_fill_count,
    taker_price,
    trade_qualifies,
    volume_cap,
)
from bot.backtest.types import Candle, OrderIntent, Trade
from bot.backtest.testkit import mk_candle, mk_trade


def oi(side="yes", action="buy", limit=50, size=100, **kw):
    return OrderIntent("MKT", side, action, limit, size, **kw)


# ---------------------------------------------------------------------------
# Normalization + NO-side complementarity
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_buy_yes(self):
        n = normalize(oi("yes", "buy", 30))
        assert n.yes_buy and n.yes_limit == 30 and n.qty_sign == 1

    def test_sell_yes(self):
        n = normalize(oi("yes", "sell", 70))
        assert not n.yes_buy and n.yes_limit == 70 and n.qty_sign == -1

    def test_buy_no_equals_sell_yes_complement(self):
        """Buying NO at p is selling YES exposure at 100 - p."""
        n_no = normalize(oi("no", "buy", 30))
        n_yes = normalize(oi("yes", "sell", 70))
        assert (n_no.yes_buy, n_no.yes_limit) == (n_yes.yes_buy, n_yes.yes_limit)

    def test_sell_no_equals_buy_yes_complement(self):
        n_no = normalize(oi("no", "sell", 40))
        n_yes = normalize(oi("yes", "buy", 60))
        assert (n_no.yes_buy, n_no.yes_limit) == (n_yes.yes_buy, n_yes.yes_limit)

    @pytest.mark.parametrize("limit", [0, 100, -5, 150])
    def test_price_out_of_range_rejected(self, limit):
        with pytest.raises(ValueError):
            normalize(oi(limit=limit))

    def test_negative_size_rejected(self):
        with pytest.raises(ValueError):
            normalize(oi(size=-1))

    def test_side_price_complement(self):
        assert side_price("yes", 70) == 70
        assert side_price("no", 70) == 30


# ---------------------------------------------------------------------------
# Crossing / taker
# ---------------------------------------------------------------------------

class TestTaker:
    def test_buy_crosses_ask(self):
        n = normalize(oi("yes", "buy", 61))
        assert crosses(n, 58, 61)
        assert taker_price(n, 58, 61) == 61

    def test_buy_below_ask_does_not_cross(self):
        assert not crosses(normalize(oi("yes", "buy", 60)), 58, 61)

    def test_sell_crosses_bid(self):
        n = normalize(oi("yes", "sell", 58))
        assert crosses(n, 58, 61)
        assert taker_price(n, 58, 61) == 58

    def test_sell_above_bid_does_not_cross(self):
        assert not crosses(normalize(oi("yes", "sell", 59)), 58, 61)

    def test_buy_no_crossing_mirrors_sell_yes(self):
        """NO-side complementarity at the crossing level."""
        n_no = normalize(oi("no", "buy", 42))     # yes-sell at 58
        n_yes = normalize(oi("yes", "sell", 58))
        assert crosses(n_no, 58, 61) == crosses(n_yes, 58, 61) is True
        assert taker_price(n_no, 58, 61) == taker_price(n_yes, 58, 61) == 58

    def test_unknown_quotes_never_cross(self):
        assert not crosses(normalize(oi("yes", "buy", 99)), None, None)
        assert not crosses(normalize(oi("yes", "sell", 1)), None, None)

    def test_taker_fill_count_caps_at_quarter_volume(self):
        assert taker_fill_count(remaining=500, window_volume=400) == 100
        assert taker_fill_count(remaining=50, window_volume=400) == 50

    def test_taker_fill_zero_volume_window(self):
        assert taker_fill_count(remaining=100, window_volume=0) == 0


class TestVolumeCap:
    def test_basic(self):
        assert volume_cap(100) == 25
        assert volume_cap(99) == 24  # floor

    def test_small_volume_floors_to_zero(self):
        assert volume_cap(3) == 0

    def test_zero_and_negative(self):
        assert volume_cap(0) == 0
        assert volume_cap(-5) == 0

    def test_depth_multiplier_scales(self):
        assert volume_cap(100, depth_multiplier=3.0) == 75

    def test_depth_multiplier_never_exceeds_tape(self):
        assert volume_cap(100, depth_multiplier=10.0) == 100


# ---------------------------------------------------------------------------
# Maker fills vs trades
# ---------------------------------------------------------------------------

class TestMakerTrades:
    def test_buy_fills_at_limit_print(self):
        n = normalize(oi("yes", "buy", 40))
        assert trade_qualifies(n, mk_trade(price=40))

    def test_buy_fills_through_limit(self):
        n = normalize(oi("yes", "buy", 40))
        assert maker_fill_from_trade(n, mk_trade(price=39, count=80), 100) == 20

    def test_buy_does_not_fill_above_limit(self):
        n = normalize(oi("yes", "buy", 40))
        assert maker_fill_from_trade(n, mk_trade(price=41, count=80), 100) == 0

    def test_sell_side_symmetry(self):
        n = normalize(oi("yes", "sell", 60))
        assert maker_fill_from_trade(n, mk_trade(price=60, count=80), 100) == 20
        assert maker_fill_from_trade(n, mk_trade(price=61, count=80), 100) == 20
        assert maker_fill_from_trade(n, mk_trade(price=59, count=80), 100) == 0

    def test_buy_no_mirrors_sell_yes(self):
        """Buy NO @ 40 == sell YES @ 60: identical fills vs the same tape."""
        n_no = normalize(oi("no", "buy", 40))
        n_yes = normalize(oi("yes", "sell", 60))
        for price in (58, 60, 62):
            t = mk_trade(price=price, count=80)
            assert maker_fill_from_trade(n_no, t, 100) == maker_fill_from_trade(
                n_yes, t, 100
            )

    def test_remaining_caps_fill(self):
        n = normalize(oi("yes", "buy", 40))
        assert maker_fill_from_trade(n, mk_trade(price=39, count=400), 30) == 30

    def test_zero_remaining(self):
        n = normalize(oi("yes", "buy", 40))
        assert maker_fill_from_trade(n, mk_trade(price=39, count=400), 0) == 0

    def test_fills_span_multiple_prints(self):
        """A resting order accumulates partial fills across the tape."""
        n = normalize(oi("yes", "buy", 40, size=50))
        remaining = 50
        tape = [mk_trade(price=39, count=80), mk_trade(price=41, count=999),
                mk_trade(price=40, count=80), mk_trade(price=38, count=40)]
        filled = []
        for t in tape:
            got = maker_fill_from_trade(n, t, remaining)
            filled.append(got)
            remaining -= got
        assert filled == [20, 0, 20, 10]
        assert remaining == 0


# ---------------------------------------------------------------------------
# Maker fills vs candles (conservative OHLC rules)
# ---------------------------------------------------------------------------

class TestMakerCandles:
    def test_buy_needs_low_strictly_below_limit(self):
        n = normalize(oi("yes", "buy", 40))
        assert not candle_qualifies(n, mk_candle(o=45, h=46, l=40, c=45, vol=100))
        assert candle_qualifies(n, mk_candle(o=45, h=46, l=39, c=45, vol=100))

    def test_limit_exactly_at_low_no_fill(self):
        """A touch of the limit is NOT a fill under candle rules."""
        n = normalize(oi("yes", "buy", 40))
        assert maker_fill_from_candle(n, mk_candle(l=40, o=45, c=44, vol=400), 100) == 0

    def test_fill_through_low_is_quarter_volume(self):
        n = normalize(oi("yes", "buy", 40))
        assert maker_fill_from_candle(n, mk_candle(l=39, o=45, c=44, vol=400), 100) == 100

    def test_zero_volume_candle_never_fills(self):
        n = normalize(oi("yes", "buy", 40))
        assert maker_fill_from_candle(n, mk_candle(l=10, o=45, c=44, vol=0), 100) == 0

    def test_sell_side_symmetry_vs_high(self):
        n = normalize(oi("yes", "sell", 60))
        assert maker_fill_from_candle(n, mk_candle(h=61, o=55, l=54, c=56, vol=400), 100) == 100
        assert maker_fill_from_candle(n, mk_candle(h=60, o=55, l=54, c=56, vol=400), 100) == 0

    def test_buy_no_mirrors_sell_yes_on_candles(self):
        n_no = normalize(oi("no", "buy", 40))   # yes-sell at 60
        n_yes = normalize(oi("yes", "sell", 60))
        c = mk_candle(h=61, o=55, l=54, c=56, vol=400)
        assert maker_fill_from_candle(n_no, c, 100) == maker_fill_from_candle(
            n_yes, c, 100
        ) == 100

    def test_fills_span_multiple_candles(self):
        n = normalize(oi("yes", "buy", 40))
        remaining = 150
        for c, expect in [
            (mk_candle(l=39, o=45, c=44, vol=400), 100),
            (mk_candle(l=41, o=44, c=44, vol=400), 0),
            (mk_candle(l=38, o=44, c=42, vol=100), 25),
        ]:
            got = maker_fill_from_candle(n, c, remaining)
            assert got == expect
            remaining -= got
        assert remaining == 25


# ---------------------------------------------------------------------------
# Position accounting
# ---------------------------------------------------------------------------

class TestApplyFill:
    def test_open_long_yes(self):
        assert apply_fill_to_position(0, 0, 10, 60) == (10, 600, -600, 0)

    def test_add_to_long(self):
        assert apply_fill_to_position(10, 600, 5, 80) == (15, 1000, -400, 0)

    def test_partial_reduce_realizes_pnl(self):
        qty, basis, cash, realized = apply_fill_to_position(15, 1000, -5, 70)
        assert (qty, basis) == (10, 667)   # floor-prorated release
        assert cash == 350
        assert realized == 350 - 333

    def test_flip_through_zero(self):
        qty, basis, cash, realized = apply_fill_to_position(10, 600, -15, 50)
        assert qty == -5
        assert basis == 5 * 50            # new NO position at yes 50 costs 50
        assert cash == 500 - 250
        assert realized == 500 - 600

    def test_open_no_position_premium_is_complement(self):
        assert apply_fill_to_position(0, 0, -10, 30) == (-10, 700, -700, 0)

    def test_reduce_no_position(self):
        qty, basis, cash, realized = apply_fill_to_position(-10, 700, 4, 20)
        assert (qty, basis) == (-6, 420)
        assert cash == 4 * 80
        assert realized == 320 - 280

    def test_close_exactly(self):
        qty, basis, cash, realized = apply_fill_to_position(10, 600, -10, 75)
        assert (qty, basis) == (0, 0)
        assert cash == 750 and realized == 150


# ---------------------------------------------------------------------------
# Settlement
# ---------------------------------------------------------------------------

class TestSettlement:
    def test_yes_pays_long_yes(self):
        assert settlement_payout_cents(10, 600, "yes") == 1000

    def test_yes_pays_nothing_to_long_no(self):
        assert settlement_payout_cents(-10, 700, "yes") == 0

    def test_no_pays_long_no(self):
        assert settlement_payout_cents(-10, 700, "no") == 1000

    def test_no_pays_nothing_to_long_yes(self):
        assert settlement_payout_cents(10, 600, "no") == 0

    def test_void_refunds_at_cost_long(self):
        assert settlement_payout_cents(10, 613, "void") == 613

    def test_void_refunds_at_cost_short(self):
        assert settlement_payout_cents(-10, 700, "void") == 700

    def test_flat_position(self):
        for r in ("yes", "no"):
            assert settlement_payout_cents(0, 0, r) == 0

    def test_unknown_result_raises(self):
        with pytest.raises(ValueError):
            settlement_payout_cents(1, 50, "maybe")

    # -- scalar (fractional payout: Kalshi pushes/ties) ---------------------

    def test_scalar_pays_value_to_long_yes(self):
        assert settlement_payout_cents(10, 600, "scalar", 50) == 500

    def test_scalar_pays_complement_to_long_no(self):
        assert settlement_payout_cents(-10, 700, "scalar", 50) == 500
        assert settlement_payout_cents(-10, 700, "scalar", 30) == 700

    def test_scalar_extremes_match_yes_no(self):
        assert settlement_payout_cents(10, 600, "scalar", 100) == settlement_payout_cents(
            10, 600, "yes"
        )
        assert settlement_payout_cents(10, 600, "scalar", 0) == settlement_payout_cents(
            10, 600, "no"
        )

    def test_scalar_flat_position(self):
        assert settlement_payout_cents(0, 0, "scalar", 50) == 0

    def test_scalar_requires_value(self):
        with pytest.raises(ValueError):
            settlement_payout_cents(1, 50, "scalar")
        with pytest.raises(ValueError):
            settlement_payout_cents(1, 50, "scalar", 101)


# ---------------------------------------------------------------------------
# Price tick structures (types.PriceStructure) + opportunity lifetime
# ---------------------------------------------------------------------------

class TestPriceStructure:
    def tapered(self):
        from bot.backtest.types import PriceRange, PriceStructure

        return PriceStructure(
            "tapered_deci_cent",
            (
                PriceRange(0, 1000, 10),      # <=10c: 0.1c ticks
                PriceRange(1000, 9000, 100),  # mid: 1c ticks
                PriceRange(9000, 10000, 10),  # >=90c: 0.1c ticks
            ),
        )

    def test_from_raw_parses_dollar_strings(self):
        from bot.backtest.types import PriceStructure

        ps = PriceStructure.from_raw(
            "tapered_deci_cent",
            [
                {"start": "0.0000", "end": "0.1000", "step": "0.0010"},
                {"start": "0.1000", "end": "0.9000", "step": "0.0100"},
                {"start": "0.9000", "end": "1.0000", "step": "0.0010"},
            ],
        )
        assert ps == self.tapered()

    def test_whole_cents_always_valid(self):
        ps = self.tapered()
        for cents in range(0, 101):
            assert ps.is_valid_tick(cents * 100)

    def test_deci_cent_ticks_valid_only_in_tails(self):
        ps = self.tapered()
        assert ps.is_valid_tick(150)    # 1.5c: in the 0.1c tail
        assert ps.is_valid_tick(9550)   # 95.5c
        assert not ps.is_valid_tick(5550)  # 55.5c: mid-range is 1c ticks
        assert not ps.is_valid_tick(155)   # 1.55c: below 0.1c granularity

    def test_quantize_directions(self):
        ps = self.tapered()
        # 55.5c in the 1c mid-range: buys snap down to 55, sells up to 56
        assert ps.quantize_cc(5550, round_down=True) == 5500
        assert ps.quantize_cc(5550, round_down=False) == 5600
        # 1.55c in the 0.1c tail: snaps to 1.5c / 1.6c
        assert ps.quantize_cc(155, round_down=True) == 150
        assert ps.quantize_cc(155, round_down=False) == 160

    def test_quantize_clamps_out_of_range(self):
        ps = self.tapered()
        assert ps.quantize_cc(-50, round_down=True) == 0
        assert ps.quantize_cc(11_000, round_down=False) == 10_000

    def test_linear_default(self):
        from bot.backtest.types import LINEAR_CENT

        assert LINEAR_CENT.is_valid_tick(6100)
        assert not LINEAR_CENT.is_valid_tick(6150)
        assert LINEAR_CENT.quantize_cc(6150, round_down=True) == 6100


class TestOpportunityLifetimeFn:
    def norm_buy(self, limit=40):
        return fills.NormalizedIntent("T", True, limit, 10)

    def norm_sell(self, limit=60):
        return fills.NormalizedIntent("T", False, limit, 10)

    def test_trades_condition_dies_on_adverse_print(self):
        trades = [
            Trade("T", 100, 39, 5),
            Trade("T", 200, 40, 5),
            Trade("T", 300, 41, 5),  # > 40: buy opportunity gone
        ]
        assert fills.opportunity_lifetime_s(
            self.norm_buy(), [], trades, 0, 10_000
        ) == 300

    def test_sell_side_symmetric(self):
        trades = [Trade("T", 100, 61, 5), Trade("T", 250, 59, 5)]
        assert fills.opportunity_lifetime_s(
            self.norm_sell(), [], trades, 0, 10_000
        ) == 250

    def test_never_dies_runs_to_end(self):
        trades = [Trade("T", 100, 39, 5)]
        assert fills.opportunity_lifetime_s(
            self.norm_buy(), [], trades, 0, 10_000
        ) == 10_000

    def test_trades_before_placement_ignored(self):
        trades = [Trade("T", 50, 99, 5), Trade("T", 300, 41, 5)]
        assert fills.opportunity_lifetime_s(
            self.norm_buy(), [], trades, 100, 10_000
        ) == 200

    def test_candles_used_when_no_trades(self):
        candles = [
            Candle("T", 0, 100, 40, 41, 39, 40, 10),     # low 39 <= 40: alive
            Candle("T", 100, 100, 42, 43, 41, 42, 10),   # low 41 > 40: dead
        ]
        assert fills.opportunity_lifetime_s(
            self.norm_buy(), candles, [], 0, 10_000
        ) == 200


# ---------------------------------------------------------------------------
# Maker queue model primitives (SPEC §3, amended 2026-08-12)
# ---------------------------------------------------------------------------

class TestMakerQueuePrimitives:
    def test_initial_queue_ahead_scales_window_volume(self):
        cfg = fills.MakerQueueConfig(depth_windows=2.0)
        assert fills.initial_queue_ahead(100, cfg) == 200
        assert fills.initial_queue_ahead(0, cfg) == 0

    def test_initial_queue_ahead_ceils_and_floors(self):
        cfg = fills.MakerQueueConfig(depth_windows=0.5, min_depth_contracts=10)
        assert fills.initial_queue_ahead(3, cfg) == 10   # floor applies
        assert fills.initial_queue_ahead(25, cfg) == 13  # ceil(12.5)

    def test_initial_queue_ahead_none_means_unknown(self):
        assert fills.initial_queue_ahead(None, fills.MakerQueueConfig()) is None

    def test_queued_fill_consumes_queue_first(self):
        # queue 100, event 60: all absorbed, no fill.
        assert fills.queued_maker_fill(50, 60, 100) == (0, 40)
        # queue 40, event 100: 40 absorbed, excess 60 -> 25% cap = 15.
        assert fills.queued_maker_fill(50, 100, 40) == (15, 0)
        # queue 0: legacy behavior on the full event volume.
        assert fills.queued_maker_fill(50, 100, 0) == (25, 0)

    def test_queued_fill_respects_remaining_and_zero_volume(self):
        assert fills.queued_maker_fill(5, 1000, 0) == (5, 0)
        assert fills.queued_maker_fill(50, 0, 30) == (0, 30)

    def test_queued_fill_depth_multiplier(self):
        # excess 100 at 25% cap x3 multiplier -> 75.
        assert fills.queued_maker_fill(500, 140, 40, 0.25, 3.0) == (75, 0)

    def test_unknown_depth_gate_is_deterministic(self):
        cfg = fills.MakerQueueConfig()
        a = [fills.unknown_depth_event_reaches(cfg, 7, ts) for ts in range(100)]
        b = [fills.unknown_depth_event_reaches(cfg, 7, ts) for ts in range(100)]
        assert a == b
        assert any(a) and not all(a)

    def test_unknown_depth_gate_rate_tracks_probability(self):
        cfg = fills.MakerQueueConfig(unknown_depth_fill_prob=0.45)
        n = 20_000
        hits = sum(
            fills.unknown_depth_event_reaches(cfg, oid, ts)
            for oid in range(20) for ts in range(1000, 1000 + n // 20)
        )
        assert abs(hits / n - 0.45) < 0.02  # deterministic, no flake

    def test_unknown_depth_prob_extremes(self):
        never = fills.MakerQueueConfig(unknown_depth_fill_prob=0.0)
        always = fills.MakerQueueConfig(unknown_depth_fill_prob=1.0)
        assert not any(
            fills.unknown_depth_event_reaches(never, 1, ts) for ts in range(50)
        )
        assert all(
            fills.unknown_depth_event_reaches(always, 1, ts) for ts in range(50)
        )

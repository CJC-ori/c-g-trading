"""Unit tests for the Kalshi fee model (SPEC §4)."""
import pytest

from bot.backtest.fees import (
    DEFAULT_FEE_CONFIG,
    FeeSchedule,
    SeriesFeeConfig,
    cents_ceil_from_centicents,
    maker_fee_cents,
    per_contract_fee_cents,
    series_ticker_of,
    taker_fee_cents,
    trade_fee_centicents,
)


class TestPerContract:
    def test_at_50c_is_2c(self):
        # 0.07 * 0.5 * 0.5 = $0.0175 -> ceil to 2c
        assert per_contract_fee_cents(50) == 2

    def test_at_10c_rounds_up_to_1c(self):
        # 0.07 * 0.1 * 0.9 = $0.0063 -> 1c
        assert per_contract_fee_cents(10) == 1

    def test_extreme_price_still_at_least_1c(self):
        # 0.07 * 0.99 * 0.01 = $0.000693 -> 1c (ceil never gives 0 fee)
        assert per_contract_fee_cents(99) == 1
        assert per_contract_fee_cents(1) == 1

    def test_at_61c(self):
        # 0.07 * 0.61 * 0.39 = $0.0166533 -> 2c
        assert per_contract_fee_cents(61) == 2

    def test_symmetric_under_complement(self):
        """Fee at P equals fee at 1-P: NO and YES executions cost the same."""
        for p in range(1, 100):
            assert per_contract_fee_cents(p) == per_contract_fee_cents(100 - p)

    def test_no_float_dust_at_exact_cent(self):
        # rate 0.04 at 50c: 0.04*0.25 = $0.01 exactly -> 1c, not 2c
        assert per_contract_fee_cents(50, rate=0.04) == 1

    @pytest.mark.parametrize("price", [0, 100, -3])
    def test_price_out_of_range(self, price):
        with pytest.raises(ValueError):
            per_contract_fee_cents(price)


class TestTakerFee:
    def test_scales_with_count(self):
        assert taker_fee_cents(50, 50) == 100

    def test_zero_count(self):
        assert taker_fee_cents(50, 0) == 0

    def test_stress_multiplier(self):
        # 1.5 * 0.07 * 0.25 = $0.02625 -> 3c per contract
        assert taker_fee_cents(50, 1, stress_multiplier=1.5) == 3

    def test_rate_override(self):
        # 0.035 * 0.25 = $0.00875 -> 1c
        assert taker_fee_cents(50, 1, rate=0.035) == 1

    def test_category_default_falls_back(self):
        assert taker_fee_cents(50, 1, category="unknown-category") == 2


class TestMakerFee:
    def test_default_is_zero(self):
        assert maker_fee_cents(50, 1000) == 0

    def test_override_rate(self):
        assert maker_fee_cents(50, 1, rate=0.07) == 2


# ---------------------------------------------------------------------------
# Exact per-series model (SYNTHESIS §2.1): centicent ceiling, not cent round
# ---------------------------------------------------------------------------

class TestTradeFeeCenticents:
    def test_at_50c_one_contract_is_exact(self):
        # 0.07 * 0.5 * 0.5 = $0.0175 exactly -> 175 centicents (no rounding)
        assert trade_fee_centicents(50, 1) == 175

    def test_ceil_to_centicent_not_cent(self):
        # 50 contracts @ 61c: 0.07*50*0.61*0.39 = $0.832665 -> ceil $0.8327
        # (= 8327 cc = 83.27c). The legacy per-contract path charged 100c.
        assert trade_fee_centicents(61, 50) == 8327

    def test_extreme_price_ceils_to_sub_cent(self):
        # 1 @ 1c: 0.07*0.01*0.99 = $0.000693 -> ceil $0.0007 = 7 cc,
        # nowhere near the legacy 1c minimum.
        assert trade_fee_centicents(1, 1) == 7

    def test_symmetric_under_complement(self):
        for p in range(1, 100):
            assert trade_fee_centicents(p, 3) == trade_fee_centicents(100 - p, 3)

    def test_fee_multiplier_scales(self):
        half = SeriesFeeConfig("quadratic", 0.5)
        zero = SeriesFeeConfig("quadratic", 0.0)
        assert trade_fee_centicents(50, 2, config=half) == 175  # 350 * 0.5
        assert trade_fee_centicents(50, 100, config=zero) == 0

    def test_maker_zero_on_plain_quadratic(self):
        assert trade_fee_centicents(50, 100, is_taker=False) == 0

    def test_maker_coefficient_on_maker_fee_series(self):
        # UNVERIFIED 0.0175 coefficient: 1 @ 50c -> 0.0175*0.25 = $0.004375
        # -> ceil $0.0044 = 44 cc
        cfg = SeriesFeeConfig("quadratic_with_maker_fees", 1.0)
        assert trade_fee_centicents(50, 1, is_taker=False, config=cfg) == 44

    def test_stress_multiplier(self):
        # 1.5 * $0.0175 = $0.02625 -> 263 cc (ceil), vs legacy 3c = 300 cc
        assert trade_fee_centicents(50, 1, stress_multiplier=1.5) == 263

    def test_zero_count(self):
        assert trade_fee_centicents(50, 0) == 0

    def test_price_out_of_range(self):
        with pytest.raises(ValueError):
            trade_fee_centicents(0, 1)
        with pytest.raises(ValueError):
            trade_fee_centicents(100, 1)

    def test_flat_falls_back_to_quadratic(self):
        cfg = SeriesFeeConfig("flat", 1.0)
        assert trade_fee_centicents(50, 1, config=cfg) == 175


class TestCentsCeil:
    @pytest.mark.parametrize("cc,cents", [(0, 0), (1, 1), (99, 1), (100, 1),
                                          (101, 2), (8327, 84), (17500, 175)])
    def test_values(self, cc, cents):
        assert cents_ceil_from_centicents(cc) == cents


class TestFeeSchedule:
    def test_bundled_snapshot_loads_with_real_coverage(self):
        sched = FeeSchedule.load_default()
        # 162 non-default series observed in the 2026-08-11 /series pull
        assert len(sched) == 162

    def test_known_overrides(self):
        sched = FeeSchedule.load_default()
        # CPI charges makers (research/ground-truth.md §6.2)
        assert sched.for_series("KXCPIYOY").charges_maker
        assert sched.for_series("KXFEDDECISION").charges_maker
        # two crypto series at multiplier 0 (docs/prior-art.md correction)
        assert sched.for_series("KXBTCY").fee_multiplier == 0.0
        # MLB props at 0.5
        assert sched.for_series("KXMLBGAME").fee_multiplier == 0.5

    def test_unknown_series_default_quadratic_x1(self):
        sched = FeeSchedule.load_default()
        cfg = sched.for_series("KXSENATEMID")
        assert cfg == DEFAULT_FEE_CONFIG
        assert not cfg.charges_maker

    def test_for_market_ticker_uses_prefix(self):
        sched = FeeSchedule(
            {"KXCPIYOY": SeriesFeeConfig("quadratic_with_maker_fees", 1.0)}
        )
        assert sched.for_market_ticker("KXCPIYOY-26-T3.0").charges_maker
        assert not sched.for_market_ticker("PRES-2024-DJT").charges_maker

    def test_no_multiplier_above_one_in_snapshot(self):
        sched = FeeSchedule.load_default()
        assert all(c.fee_multiplier <= 1.0 for c in sched.overrides.values())

    def test_series_ticker_of(self):
        assert series_ticker_of("KXSENATEMID-26-AELS") == "KXSENATEMID"
        assert series_ticker_of("NODASHTICKER") == "NODASHTICKER"

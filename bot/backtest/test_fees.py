"""Unit tests for the Kalshi fee model (SPEC §4)."""
import pytest

from bot.backtest.fees import (
    maker_fee_cents,
    per_contract_fee_cents,
    taker_fee_cents,
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

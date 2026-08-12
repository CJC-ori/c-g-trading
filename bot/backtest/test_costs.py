"""CostLedger tests (research/cost-architecture.md §7): truthful, never-
understated inference cost accounting."""
import pytest

from bot.backtest.costs import (
    BATCH_MULTIPLIER,
    PRICES,
    WEB_SEARCH_USD,
    CostLedger,
    resolve_model,
)


class TestPrices:
    def test_spec_prices(self):
        # (input, 5m write, 1h write, cache read, output) $/MTok
        assert PRICES["claude-opus-5"] == (5.0, 6.25, 10.0, 0.50, 25.0)
        assert PRICES["claude-sonnet-5"] == (2.0, 2.50, 4.0, 0.20, 10.0)
        assert PRICES["claude-haiku-4-5"] == (1.0, 1.25, 2.0, 0.10, 5.0)

    def test_cache_multipliers_hold(self):
        for inp, w5, w1, rd, _out in PRICES.values():
            assert w5 == pytest.approx(1.25 * inp)
            assert w1 == pytest.approx(2.0 * inp)
            assert rd == pytest.approx(0.1 * inp)

    def test_batch_and_search_constants(self):
        assert BATCH_MULTIPLIER == 0.5
        assert WEB_SEARCH_USD == 0.010


class TestResolveModel:
    def test_exact_and_dated(self):
        assert resolve_model("claude-opus-5") == "claude-opus-5"
        assert resolve_model("claude-opus-5-20260115") == "claude-opus-5"
        assert resolve_model("claude-haiku-4.5") == "claude-haiku-4-5"

    def test_unknown_model_raises_never_zero(self):
        with pytest.raises(KeyError):
            resolve_model("gpt-5.6-sol")


class TestCharge:
    def test_hand_computed_sonnet_call(self):
        led = CostLedger()
        # 10k in + 2k out on Sonnet 5: 10k*$2/M + 2k*$10/M = $0.02 + $0.02
        # = $0.04 = 4 cents exactly
        cents = led.charge(
            {"input_tokens": 10_000, "output_tokens": 2_000}, "claude-sonnet-5"
        )
        assert cents == 4
        assert led.total_cents == 4

    def test_cache_reads_priced_at_tenth(self):
        led = CostLedger()
        # 100k cache-read on Opus 5 = 100k * $0.50/M = $0.05 = 5 cents
        led.charge({"cache_read_input_tokens": 100_000}, "claude-opus-5")
        assert led.total_cents == 5

    def test_cache_write_5m_via_legacy_field(self):
        led = CostLedger()
        # cache_creation_input_tokens (no breakdown) = 5m writes at 1.25x
        led.charge({"cache_creation_input_tokens": 80_000}, "claude-sonnet-5")
        # 80k * $2.50/M = $0.20 = 20 cents
        assert led.total_cents == 20

    def test_cache_write_breakdown_1h(self):
        led = CostLedger()
        led.charge(
            {"cache_creation": {"ephemeral_1h_input_tokens": 50_000,
                                "ephemeral_5m_input_tokens": 0}},
            "claude-sonnet-5",
        )
        # 50k * $4/M = $0.20 = 20 cents
        assert led.total_cents == 20

    def test_batch_halves(self):
        led = CostLedger()
        led.charge(
            {"input_tokens": 10_000, "output_tokens": 2_000},
            "claude-sonnet-5",
            batch=True,
        )
        assert led.total_cents == 2

    def test_web_search_charged(self):
        led = CostLedger()
        led.charge(
            {"input_tokens": 0, "server_tool_use": {"web_search_requests": 5}},
            "claude-opus-5",
        )
        assert led.total_cents == 5  # 5 * $0.01 = 5 cents
        led2 = CostLedger()
        assert led2.charge_web_searches(3) == 3

    def test_tiny_charge_rounds_up_never_zero(self):
        led = CostLedger()
        # 400 in + 30 out on Haiku 4.5 = $0.00055 -> must charge 1 cent
        # at the report boundary, not 0 (never understate).
        led.charge({"input_tokens": 400, "output_tokens": 30}, "claude-haiku-4-5")
        assert led.total_cents == 1

    def test_tiny_charges_do_not_double_round(self):
        # Exact precision inside the ledger: 10 screens of $0.00055 total
        # $0.0055 -> 1 cent, not 10 cents.
        led = CostLedger()
        for _ in range(10):
            led.charge(
                {"input_tokens": 400, "output_tokens": 30}, "claude-haiku-4-5"
            )
        assert led.total_cents == 1

    def test_object_style_usage(self):
        class Usage:
            input_tokens = 10_000
            output_tokens = 2_000
            cache_creation_input_tokens = 0
            cache_read_input_tokens = 0
            server_tool_use = None

        led = CostLedger()
        led.charge(Usage(), "claude-sonnet-5")
        assert led.total_cents == 4

    def test_price_stress_multiplier(self):
        led = CostLedger(price_multiplier=2.0)
        led.charge(
            {"input_tokens": 10_000, "output_tokens": 2_000}, "claude-sonnet-5"
        )
        assert led.total_cents == 8


class TestDrain:
    def test_drains_sum_to_total(self):
        led = CostLedger()
        drains = []
        for _ in range(5):
            led.charge(
                {"input_tokens": 4_321, "output_tokens": 777}, "claude-opus-5"
            )
            drains.append(led.drain_cents())
        assert sum(drains) == led.total_cents
        assert led.drain_cents() == 0


class TestAmortize:
    def test_shared_dossier_split(self):
        led = CostLedger()
        # $0.24 dossier = 24 cents, shared by 12 brackets -> 2 cents each
        led.charge(
            {"input_tokens": 100_000, "output_tokens": 4_000},
            "claude-sonnet-5",
            job_id="dossier-EV1",
        )
        # job cost not in total until amortized
        assert led.total_cents == 0
        share = led.amortize("dossier-EV1", n_consumers=12)
        assert share == 2
        assert led.total_cents == 24

    def test_amortize_idempotent_on_total(self):
        led = CostLedger()
        led.charge({"input_tokens": 100_000}, "claude-sonnet-5", job_id="j")
        led.amortize("j", 4)
        led.amortize("j", 4)
        assert led.total_cents == 20  # 100k * $2/M = 20c, counted once

    def test_bad_n_raises(self):
        led = CostLedger()
        with pytest.raises(ValueError):
            led.amortize("nope", 0)

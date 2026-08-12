"""Metrics tests on a tiny hand-computable result (SPEC §6)."""
import pytest

from bot.backtest.engine import BacktestResult, DecisionRecord, EngineConfig
from bot.backtest.metrics import (
    brier_vs_baseline,
    calibration,
    compute_metrics,
    concentration,
    max_drawdown,
    pnl_summary,
    render_markdown,
    splits,
    trade_stats,
)
from bot.backtest.types import Fill, SettlementResult


def tiny_result() -> BacktestResult:
    """Hand-built run: bankroll $10k, one taker fill, three markets settled."""
    return BacktestResult(
        config=EngineConfig(),
        markets={},
        settlements={
            "M1": SettlementResult("M1", "yes", 30),
            "M2": SettlementResult("M2", "no", 30),
            "M3": SettlementResult("M3", "no", 30),
        },
        fills=[
            Fill(order_id=1, ticker="M1", side="yes", action="buy",
                 price_cents=60, count=40, ts=10, is_taker=True, fee_cents=10),
        ],
        orders={1: {"p_hat": 0.8}},
        intents=[{"requested": 100, "clamped": 80}],
        decisions=[
            DecisionRecord(10, "M1", 0.8, 60.0),
            DecisionRecord(12, "M2", 0.3, 40.0),
        ],
        equity_curve=[(0, 1_000_000), (10, 1_000_200), (20, 999_900), (30, 1_000_100)],
        deployed_curve=[(0, 0), (10, 2400), (30, 2400)],
        per_market={
            "M1": {"realized_pnl_cents": 110, "fees_cents": 10, "net_pnl_cents": 100,
                   "first_entry_ts": 10, "exit_ts": 30, "contracts_traded": 40,
                   "category": "a", "event_ticker": "E1"},
            "M2": {"realized_pnl_cents": 50, "fees_cents": 0, "net_pnl_cents": 50,
                   "first_entry_ts": 12, "exit_ts": 30, "contracts_traded": 10,
                   "category": "a", "event_ticker": "E2"},
            "M3": {"realized_pnl_cents": -30, "fees_cents": 0, "net_pnl_cents": -30,
                   "first_entry_ts": 28, "exit_ts": 30, "contracts_traded": 5,
                   "category": "b", "event_ticker": "E3"},
        },
        final_cash_cents=1_000_100,
        fees_cents=10,
        inference_cost_cents=30,
        realized_pnl_cents=130,
        n_decision_calls=6,
        events=[],
    )


class TestBrier:
    def test_hand_computed_vs_baseline(self):
        b = brier_vs_baseline(tiny_result())
        # strategy: ((0.8-1)^2 + (0.3-0)^2)/2 = (0.04+0.09)/2 = 0.065
        # baseline: ((0.6-1)^2 + (0.4-0)^2)/2 = 0.16
        assert b["n"] == 2
        assert b["strategy"] == pytest.approx(0.065)
        assert b["market_baseline"] == pytest.approx(0.16)
        assert b["beats_baseline"] is True

    def test_void_and_unresolved_excluded(self):
        r = tiny_result()
        r.decisions.append(DecisionRecord(13, "MVOID", 0.5, 50.0))
        r.decisions.append(DecisionRecord(14, "MUNKNOWN", 0.5, 50.0))
        r.settlements["MVOID"] = SettlementResult("MVOID", "void", 30)
        assert brier_vs_baseline(r)["n"] == 2

    def test_empty(self):
        r = tiny_result()
        r.decisions = []
        assert brier_vs_baseline(r)["n"] == 0


class TestCalibration:
    def test_hand_computed_ece(self):
        c = calibration(tiny_result())
        # bin [0.8,0.9): conf 0.8 freq 1.0 -> gap 0.2, weight 1/2
        # bin [0.3,0.4): conf 0.3 freq 0.0 -> gap 0.3, weight 1/2
        assert c["n"] == 2
        assert c["ece"] == pytest.approx(0.25)
        assert len(c["curve"]) == 2


class TestDrawdown:
    def test_hand_computed(self):
        dd = max_drawdown([(0, 100), (10, 120), (20, 90), (30, 110)])
        assert dd["max_drawdown_cents"] == 30
        assert dd["max_drawdown_frac_of_peak"] == pytest.approx(0.25)

    def test_monotone_up_has_zero_drawdown(self):
        assert max_drawdown([(0, 10), (1, 20)])["max_drawdown_cents"] == 0


class TestConcentration:
    def test_top5_share_and_worst(self):
        c = concentration(tiny_result())
        assert c["n_markets_traded"] == 3
        assert c["top5_share_of_net"] == pytest.approx(1.0)  # all 3 in top-5
        assert c["worst_market_loss_cents"] == -30

    def test_six_markets_concentration(self):
        r = tiny_result()
        r.per_market = {
            f"M{i}": {"net_pnl_cents": pnl, "contracts_traded": 1,
                      "first_entry_ts": 1, "exit_ts": 2, "category": "x",
                      "event_ticker": "e", "realized_pnl_cents": pnl,
                      "fees_cents": 0}
            for i, pnl in enumerate([100, 90, 80, 70, 60, 10])
        }
        c = concentration(r)
        # top-5 = 400 of total 410
        assert c["top5_share_of_net"] == pytest.approx(400 / 410)


class TestTradeStats:
    def test_fill_rates_and_edge(self):
        ts = trade_stats(tiny_result())
        assert ts["contracts_requested"] == 100
        assert ts["contracts_ordered"] == 80
        assert ts["contracts_filled"] == 40
        assert ts["fill_rate_vs_ordered"] == pytest.approx(0.5)
        assert ts["taker_contracts"] == 40 and ts["maker_contracts"] == 0
        # edge at entry: p_hat 0.8 buying yes at 60 -> 80 - 60 = 20c
        assert ts["avg_edge_at_entry_cents"] == pytest.approx(20.0)
        # holding periods: M1 30-10=20, M2 30-12=18, M3 30-28=2 -> mean 13.33
        assert ts["avg_holding_period_s"] == pytest.approx(40 / 3)


class TestPnlAndSplits:
    def test_pnl_summary(self):
        p = pnl_summary(tiny_result())
        assert p["net_pnl_cents_gross_of_inference"] == 100
        assert p["net_pnl_cents_after_inference"] == 70
        assert p["fees_cents"] == 10
        # avg deployed: (0*10 + 2400*20)/30 = 1600
        assert p["avg_deployed_cents"] == pytest.approx(1600.0)
        assert p["annualized_return_on_avg_deployed"] > 0

    def test_time_and_category_splits(self):
        s = splits(tiny_result())
        # span 0..30, split at 18: M1(10), M2(12) train; M3(28) test
        assert s["time"]["train"] == {"net_pnl_cents": 150, "n_markets": 2}
        assert s["time"]["test"] == {"net_pnl_cents": -30, "n_markets": 1}
        assert s["category"]["a"]["net_pnl_cents"] == 150
        assert s["category"]["b"]["net_pnl_cents"] == -30


class TestReportRendering:
    def test_compute_metrics_is_json_safe(self):
        import json

        json.dumps(compute_metrics(tiny_result()))

    def test_markdown_renders(self):
        report = {
            "label": "tiny",
            "base": compute_metrics(tiny_result()),
            "capacity_curve": [
                {"depth_multiplier": 1.0, "net_pnl_cents_after_inference": 70,
                 "contracts_filled": 40},
            ],
            "fee_stress": {"multiplier": 1.5,
                           "net_pnl_cents_after_inference": 50,
                           "still_positive": True},
        }
        md = render_markdown(report)
        assert "# Backtest report - tiny" in md
        assert "Brier" in md and "Capacity curve" in md and "Fee stress" in md


# ---------------------------------------------------------------------------
# Three-number Brier block, decomposition, gates, flags (SPEC §6-7 amended)
# ---------------------------------------------------------------------------

class TestThreeNumberBrierBlock:
    def test_pooled_delta_and_paired_ci(self):
        b = brier_vs_baseline(tiny_result())
        # diffs: M1 (0.16-0.04)=0.12, M2 (0.16-0.09)=0.07 -> mean 0.095
        assert b["delta_brier"] == pytest.approx(0.095)
        lo, hi = b["delta_ci95"]
        # sd 0.035355, half-width 1.96*sd/sqrt(2) = 0.049
        assert lo == pytest.approx(0.095 - 0.049, abs=1e-3)
        assert hi == pytest.approx(0.095 + 0.049, abs=1e-3)

    def test_traded_subset_present_with_gate(self):
        b = brier_vs_baseline(tiny_result())
        t = b["traded_subset"]
        # both decisions clear the 4c gate (20c and 10c disagreements)
        assert t["n"] == 2 and t["gate_cents"] == 4.0
        assert t["delta_brier"] == pytest.approx(0.095)

    def test_traded_subset_respects_gate(self):
        b = brier_vs_baseline(tiny_result(), traded_gate_cents=15.0)
        assert b["traded_subset"]["n"] == 1  # only M1's 20c disagreement

    def test_trade_pnl_third_number(self):
        b = brier_vs_baseline(tiny_result())
        tp = b["trade_pnl"]
        assert tp["n_markets_traded"] == 3
        assert tp["net_pnl_cents_after_fees"] == 120
        assert tp["net_pnl_cents_after_fees_and_inference"] == 90

    def test_n_gate_is_500_not_100(self):
        b = brier_vs_baseline(tiny_result())
        assert b["n_gate"] == 500
        assert b["meets_n_gate"] is False
        assert any("< 500" in f for f in b["flags"])

    def test_leak_flag_fires_above_0_05(self):
        b = brier_vs_baseline(tiny_result())  # delta 0.095 > 0.05
        assert any("LEAK" in f for f in b["flags"])

    def test_no_leak_flag_for_sane_delta(self):
        r = tiny_result()
        # p_hat == mid: delta 0
        r.decisions = [DecisionRecord(10, "M1", 0.6, 60.0),
                       DecisionRecord(12, "M2", 0.4, 40.0)]
        b = brier_vs_baseline(r)
        assert not any("LEAK" in f for f in b["flags"])


class TestCalibrationDecomposition:
    def test_murphy_identity_on_tiny_result(self):
        c = calibration(tiny_result())
        # single-decision bins: reliability == raw Brier gap terms
        assert c["reliability"] == pytest.approx(0.065)
        assert c["resolution"] == pytest.approx(0.25)
        assert c["uncertainty"] == pytest.approx(0.25)
        # Brier = REL - RES + UNC = strategy Brier here
        assert c["brier_from_decomposition"] == pytest.approx(0.065)


class TestCostRatios:
    def test_hand_computed(self):
        from bot.backtest.metrics import cost_ratios

        cr = cost_ratios(tiny_result())
        assert cr["inference_cost_cents"] == 30
        # inference/net-P&L: 30/100 = 30% > 20% target -> flag
        assert cr["inference_over_net_pnl"] == pytest.approx(0.30)
        assert any("20%" in f for f in cr["flags"])
        # inference/notional: 30 / (40*60) = 1.25% > 1% target -> flag
        assert cr["notional_traded_cents"] == 2400
        assert cr["inference_over_notional"] == pytest.approx(0.0125)
        assert any("1%" in f for f in cr["flags"])

    def test_silent_when_no_inference(self):
        from bot.backtest.metrics import cost_ratios

        r = tiny_result()
        r.inference_cost_cents = 0
        assert cost_ratios(r)["flags"] == []


class TestMakerFillRateFlag:
    def test_flags_above_60pct(self):
        from bot.backtest.types import Fill

        r = tiny_result()
        r.orders = {
            1: {"p_hat": 0.8, "size": 100, "tif": "rest",
                "is_taker_at_entry": False},
        }
        r.fills = [
            Fill(order_id=1, ticker="M1", side="yes", action="buy",
                 price_cents=60, count=70, ts=10, is_taker=False, fee_cents=0),
        ]
        ts = trade_stats(r)
        assert ts["maker_fill_rate"] == pytest.approx(0.70)
        assert any("fill model is lying" in f for f in ts["flags"])

    def test_sane_fill_rate_no_flag(self):
        from bot.backtest.types import Fill

        r = tiny_result()
        r.orders = {
            1: {"p_hat": 0.8, "size": 100, "tif": "rest",
                "is_taker_at_entry": False},
        }
        r.fills = [
            Fill(order_id=1, ticker="M1", side="yes", action="buy",
                 price_cents=60, count=45, ts=10, is_taker=False, fee_cents=0),
        ]
        ts = trade_stats(r)
        assert ts["maker_fill_rate"] == pytest.approx(0.45)
        assert not any("fill model" in f for f in ts["flags"])

    def test_none_when_no_size_info(self):
        # legacy orders dicts without size/tif keys must not crash
        assert trade_stats(tiny_result())["maker_fill_rate"] is None


class TestOpportunityLifetimeAggregation:
    def test_median_and_short_lifetime_flag(self):
        r = tiny_result()
        r.orders = {
            1: {"p_hat": 0.8, "opportunity_lifetime_s": 3},
            2: {"p_hat": 0.8, "opportunity_lifetime_s": 4},
            3: {"p_hat": 0.8, "opportunity_lifetime_s": 100},
        }
        ts = trade_stats(r)
        assert ts["opportunity_lifetime_s"]["median"] == 4
        assert any("colocated" in f for f in ts["flags"])

    def test_long_lifetimes_no_flag(self):
        r = tiny_result()
        r.orders = {1: {"p_hat": 0.8, "opportunity_lifetime_s": 600}}
        ts = trade_stats(r)
        assert ts["opportunity_lifetime_s"]["median"] == 600
        assert not any("colocated" in f for f in ts["flags"])


class TestInferenceStressAndFeeMode:
    def test_full_report_sections(self, tmp_path):
        from bot.backtest.fees import FeeSchedule
        from bot.backtest.metrics import full_report
        from bot.backtest.testkit import ScriptedStrategy, synthetic_provider

        report = full_report(
            synthetic_provider(seed=7, n_random=1, n_favorites=1, hours=12),
            lambda: ScriptedStrategy(inference_cost_cents=5),
            fee_schedule=FeeSchedule({}),
            label="stress-smoke",
        )
        assert report["fee_mode"] == "exact-per-series"
        infs = report["inference_stress"]
        assert infs["multiplier"] == 2.0
        base_inf = report["base"]["pnl"]["inference_cost_cents"]
        assert infs["inference_cost_cents"] == 2 * base_inf
        # no trades, only inference cost: x2 stress doubles the loss
        assert infs["net_pnl_cents_after_inference"] == -2 * base_inf
        assert infs["still_positive"] is False

    def test_default_fee_mode_is_legacy(self):
        from bot.backtest.metrics import full_report
        from bot.backtest.testkit import ScriptedStrategy, synthetic_provider

        report = full_report(
            synthetic_provider(seed=7, n_random=1, n_favorites=1, hours=6),
            lambda: ScriptedStrategy(),
            label="legacy-smoke",
        )
        assert report["fee_mode"] == "legacy-per-contract-ceil"

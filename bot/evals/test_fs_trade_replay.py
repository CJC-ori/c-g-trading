"""Tests for rung 1 — the FutureSearch trade replay.

The headline assertion is the reconciliation itself: this file fails if our
P&L accounting stops reproducing FutureSearch's published book.
"""
from __future__ import annotations

import math

import pytest

from bot.evals import fs_trade_replay as ftr


@pytest.fixture(scope="module")
def positions():
    if not ftr.POSITIONS_PATH.exists():
        pytest.skip(f"staged positions file missing: {ftr.POSITIONS_PATH}")
    return ftr.load_positions()


@pytest.fixture(scope="module")
def result(positions):
    return ftr.replay(positions, ftr.load_fee_schedule())


# ---------------------------------------------------------------------------
# The external ground truth (SYNTHESIS §2.6)
# ---------------------------------------------------------------------------

def test_loads_exactly_the_128_resolved_positions(positions):
    assert len(positions) == 128
    assert sum(1 for p in positions if p.venue == "Kalshi") == 83
    assert sum(1 for p in positions if p.venue == "Polymarket") == 45


def test_reproduces_published_kalshi_and_polymarket_books(result):
    recon = result["reconciliation"]
    assert recon["Kalshi"]["our_gross_pnl"] == pytest.approx(27798.0, abs=1.0)
    assert recon["Polymarket"]["our_gross_pnl"] == pytest.approx(-19829.0, abs=1.0)
    assert recon["Combined"]["our_gross_pnl"] == pytest.approx(7969.0, abs=1.0)
    assert all(v["reconciles"] for v in recon.values())


def test_reproduces_published_cost_bases(result):
    # research/futuresearch.md §4.2: $85,610 Kalshi / $111,141 Polymarket.
    agg = result["aggregates"]
    assert agg["Kalshi"]["cost_basis"] == pytest.approx(85610.0, abs=1.0)
    assert agg["Polymarket"]["cost_basis"] == pytest.approx(111140.0, abs=2.0)
    assert agg["Kalshi"]["gross_roi"] == pytest.approx(0.325, abs=0.002)
    assert agg["Polymarket"]["gross_roi"] == pytest.approx(-0.178, abs=0.002)


def test_their_pnl_field_is_gross_of_fees(result):
    """pnl == shares*(1[won] - entry_price) to the cent, for all 128 positions.

    This is what proves their book charges no fees — and therefore what the
    Pass A -> Pass B residual means.
    """
    ident = result["gross_pnl_identity"]
    assert ident["n_positions_off_by_more_than_1c"] == 0
    assert ident["max_abs_position_residual_usd"] < 0.01


def test_payout_is_one_dollar_per_winning_share(positions):
    for p in positions:
        assert p.payout == (p.shares if p.won else 0.0)
        assert p.published_proceeds == pytest.approx(p.payout, abs=0.01)


def test_recomputes_their_forecast_vs_market_brier(result):
    """research/futuresearch.md §4.2 [derived]: 0.0963 vs 0.1033 on n=21."""
    fvm = result["fs_forecast_vs_market"]
    assert fvm["n"] == 21
    assert fvm["our_brier"] == pytest.approx(0.0963, abs=0.001)
    assert fvm["baseline_brier"] == pytest.approx(0.1033, abs=0.001)
    assert fvm["gate"]["verdict"] == "UNDERPOWERED"


def test_concentration_flags_the_cautionary_book(result):
    comb = result["concentration"]["Combined"]
    # §4.2: top-5 P&L +$46,782 against a combined total of +$7,969.
    assert comb["top_5_pnl"] == pytest.approx(46782.0, abs=2.0)
    assert comb["top_5_share_of_pnl"] > 0.60  # SPEC §7's concentration gate


# ---------------------------------------------------------------------------
# Fee engines
# ---------------------------------------------------------------------------

DEFAULT_CFG = ftr.harness_fees.DEFAULT_FEE_CONFIG


def test_spec_fee_matches_kalshi_formula():
    # 0.07 * 100 * 0.5 * 0.5 = 1.75 exactly; already on the $0.0001 grid.
    assert ftr.spec_taker_fee_usd(0.50, 100, DEFAULT_CFG) == pytest.approx(1.75)
    assert ftr.spec_taker_fee_usd(0.50, 1, DEFAULT_CFG) == pytest.approx(0.0175)


def test_independent_engine_agrees_with_the_harness_on_whole_cent_prices():
    """Where the price is already on the cent grid, the two must agree exactly."""
    for price in (0.01, 0.10, 0.33, 0.50, 0.67, 0.95, 0.99):
        for n in (1, 7, 250, 4414):
            assert ftr.spec_taker_fee_usd(price, n, DEFAULT_CFG) == pytest.approx(
                ftr.independent_taker_fee_usd(price, n), abs=1e-9
            ), (price, n)


def test_independent_fee_ceilings_to_a_centicent_not_a_cent():
    # raw = 0.07 * 1 * 0.33 * 0.67 = 0.0154770 -> ceil to 0.0155
    got = ftr.independent_taker_fee_usd(0.33, 1)
    assert got == pytest.approx(0.0155, abs=1e-9)
    assert math.isclose(round(got / ftr.FEE_QUANTUM_USD), got / ftr.FEE_QUANTUM_USD, abs_tol=1e-6)


def test_fee_is_symmetric_in_price():
    assert ftr.spec_taker_fee_usd(0.30, 500, DEFAULT_CFG) == pytest.approx(
        ftr.spec_taker_fee_usd(0.70, 500, DEFAULT_CFG)
    )


def test_fee_respects_the_per_series_multiplier():
    zero = ftr.harness_fees.SeriesFeeConfig("quadratic", 0.0)
    half = ftr.harness_fees.SeriesFeeConfig("quadratic", 0.5)
    assert ftr.spec_taker_fee_usd(0.50, 100, zero) == 0.0
    assert ftr.spec_taker_fee_usd(0.50, 100, half) == pytest.approx(0.875)
    assert ftr.independent_taker_fee_usd(0.50, 100, fee_multiplier=0.0) == 0.0


def test_fee_zero_for_nonpositive_size():
    assert ftr.spec_taker_fee_usd(0.5, 0, DEFAULT_CFG) == 0.0
    assert ftr.independent_taker_fee_usd(0.5, -10) == 0.0


def test_legacy_engine_overcharges_relative_to_the_exact_model(result):
    """fees.taker_fee_cents ceilings per contract; Kalshi ceilings per order.

    Reported upward, not fixed here — bot/backtest/ is owned elsewhere. The
    exact path (fees.trade_fee_centicents + FeeSchedule) already exists; the
    point of this assertion is that runs which do NOT pass a FeeSchedule pay a
    materially different fee.
    """
    fd = result["fee_engine_divergence"]
    assert fd["legacy_total_usd"] > fd["spec_total_usd"]
    assert fd["ratio_legacy_over_spec"] > 1.5
    # A single 1000-contract order at 50c: exact 17.50, legacy 1000 * ceil(1.75c) = 20.00
    assert ftr.spec_taker_fee_usd(0.50, 1000, DEFAULT_CFG) == pytest.approx(17.50)
    assert ftr.legacy_taker_fee_usd(0.50, 1000) == pytest.approx(20.00)


def test_price_quantization_is_the_only_spec_vs_independent_gap(result):
    fd = result["fee_engine_divergence"]
    # Sub-cent entry prices (0.7011) round to the cent grid before the harness
    # charges; the resulting gap must be small relative to the total.
    assert abs(fd["spec_vs_independent_usd"]) < 0.01 * fd["spec_total_usd"]


def test_polymarket_positions_pay_no_fee(result):
    poly = [r for r in result["positions"] if r["venue"] == "Polymarket"]
    assert poly and all(r["fee_spec"] == 0.0 for r in poly)
    assert all(r["fee_legacy"] == 0.0 for r in poly)
    assert result["aggregates"]["Polymarket"]["net_pnl_spec"] == pytest.approx(
        result["aggregates"]["Polymarket"]["gross_pnl"]
    )


def test_fees_only_reduce_pnl(result):
    agg = result["aggregates"]["Combined"]
    assert agg["net_pnl_spec"] < agg["gross_pnl"]
    assert agg["fees_spec"] > 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_main_exits_zero_and_writes_reports(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_REPORTS_DIR", str(tmp_path))
    assert ftr.main(["--quiet"]) == 0
    assert (tmp_path / "fs_trade_replay.json").exists()
    assert (tmp_path / "fs_trade_replay.md").exists()

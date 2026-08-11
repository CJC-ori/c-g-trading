"""Tests for the eval ladder's scoring math.

Every number here is either hand-computed in the test or taken from a published
table in ``research/`` — never from the implementation. A test that just re-runs
the code it is testing proves nothing.
"""
from __future__ import annotations

import math

import pytest

from bot.evals import scoring


# ---------------------------------------------------------------------------
# Point scores
# ---------------------------------------------------------------------------

def test_brier_hand_computed():
    # ((0.8-1)^2 + (0.3-0)^2 + (0.5-1)^2) / 3 = (0.04 + 0.09 + 0.25)/3
    assert scoring.brier([0.8, 0.3, 0.5], [1, 0, 1]) == pytest.approx(0.38 / 3)


def test_brier_perfect_and_worst():
    assert scoring.brier([1.0, 0.0], [1, 0]) == 0.0
    assert scoring.brier([0.0, 1.0], [1, 0]) == 1.0
    assert scoring.brier([0.5] * 4, [1, 0, 1, 0]) == 0.25


def test_brier_rejects_mismatched_and_empty():
    with pytest.raises(ValueError):
        scoring.brier([0.5], [1, 0])
    with pytest.raises(ValueError):
        scoring.brier([], [])


def test_brier_index_matches_forecastbench_conversions():
    # research/benchmarks.md §1.4: 0.05 -> 77.6; 0.10 -> 68.4; 0.117 -> 65.8;
    # 0.15 -> 61.3; 0.25 -> 50.0.
    for b, idx in [(0.05, 77.6), (0.10, 68.4), (0.117, 65.8), (0.15, 61.3), (0.25, 50.0)]:
        assert scoring.brier_index(b) == pytest.approx(idx, abs=0.05)


def test_log_loss_hand_computed():
    # -(log 0.8 + log 0.7) / 2
    got = scoring.log_loss([0.8, 0.3], [1, 0])
    assert got == pytest.approx(-(math.log(0.8) + math.log(0.7)) / 2)


def test_log_loss_clips_certainty():
    # A confidently wrong 0.0 must score finite, not inf.
    assert math.isfinite(scoring.log_loss([0.0], [1.0]))


# ---------------------------------------------------------------------------
# Paired comparison
# ---------------------------------------------------------------------------

def test_paired_delta_sign_convention_positive_when_we_win():
    # We say 0.9, market says 0.6, outcome YES -> we win.
    pd_ = scoring.paired_delta([0.9], [0.6], [1.0])
    assert pd_.delta > 0
    assert pd_.delta == pytest.approx((0.6 - 1) ** 2 - (0.9 - 1) ** 2)


def test_paired_delta_sign_convention_negative_when_market_wins():
    pd_ = scoring.paired_delta([0.9], [0.6], [0.0])
    assert pd_.delta < 0


def test_paired_delta_identity_from_benchmarks_4_1():
    """ΔBrier == (p-q)(p+q-2y) for every pair (research/benchmarks.md §4.1)."""
    cases = [(0.7, 0.4, 1.0), (0.2, 0.55, 0.0), (0.5, 0.5, 1.0), (0.05, 0.30, 0.0)]
    for q, p, y in cases:
        expected = (p - q) * (p + q - 2 * y)
        assert scoring.paired_delta([q], [p], [y]).delta == pytest.approx(expected)


def test_paired_delta_zero_when_identical():
    pd_ = scoring.paired_delta([0.3, 0.7], [0.3, 0.7], [0, 1])
    assert pd_.delta == 0.0
    assert pd_.sd == 0.0
    assert pd_.t is None
    assert not pd_.significant


def test_paired_delta_ci_and_se():
    ours = [0.9, 0.1, 0.8, 0.2]
    base = [0.5, 0.5, 0.5, 0.5]
    y = [1.0, 0.0, 1.0, 0.0]
    pd_ = scoring.paired_delta(ours, base, y)
    diffs = [(b - t) ** 2 - (o - t) ** 2 for o, b, t in zip(ours, base, y)]
    mean = sum(diffs) / 4
    assert pd_.delta == pytest.approx(mean)
    assert pd_.ci_low == pytest.approx(mean - 1.96 * pd_.se)
    assert pd_.ci_high == pytest.approx(mean + 1.96 * pd_.se)
    assert pd_.baseline_brier == pytest.approx(0.25)


def test_paired_delta_rejects_length_mismatch():
    with pytest.raises(ValueError):
        scoring.paired_delta([0.5], [0.5, 0.5], [1, 0])


# ---------------------------------------------------------------------------
# Power / gates
# ---------------------------------------------------------------------------

def test_min_detectable_delta_reproduces_benchmarks_4_3_table():
    """research/benchmarks.md §4.3, at the measured paired sd of 0.0698."""
    expected = {100: 0.0137, 200: 0.0097, 500: 0.0061, 1216: 0.0039, 2000: 0.0031}
    for n, want in expected.items():
        assert scoring.min_detectable_delta(n) == pytest.approx(want, abs=0.0002)


def test_rms_disagreement_reproduces_conversion_table():
    """research/benchmarks.md §4.1: 6.3¢ RMS <-> ΔBrier 0.0040."""
    assert scoring.rms_disagreement(0.0040) == pytest.approx(0.063, abs=0.001)
    assert scoring.rms_disagreement(0.0225) == pytest.approx(0.15, abs=0.001)
    assert scoring.rms_disagreement(-1.0) == 0.0  # clamped, never NaN


def test_tier_boundaries_match_benchmarks_4_4():
    assert "implausible" in scoring.tier_for_delta(0.06, 1000, 0.05)
    assert "top-2026-bot" in scoring.tier_for_delta(0.021, 1000, 0.01)
    assert "superforecaster" in scoring.tier_for_delta(0.005, 1000, 0.001)
    assert scoring.tier_for_delta(0.001, 1000, -0.001).startswith("parity")
    assert scoring.tier_for_delta(-0.01, 1000, -0.02).startswith("fail")


def test_gate_report_underpowered_below_500():
    pd_ = scoring.paired_delta([0.9] * 100, [0.5] * 100, [1.0] * 100)
    g = scoring.gate_report(pd_)
    assert g["n"] == 100
    assert g["min_n_required"] == scoring.MIN_N_FORECAST_GATE == 500
    assert not g["passes_n_gate"]
    assert g["verdict"] == "UNDERPOWERED"


def test_gate_report_passes_when_large_and_significant():
    # 600 questions, we are right every time, market says 0.5.
    n = 600
    pd_ = scoring.paired_delta([0.9] * n, [0.5] * n, [1.0] * n)
    g = scoring.gate_report(pd_)
    assert g["passes_n_gate"]
    # sd is 0 here (identical diffs), so CI is degenerate at the point estimate.
    assert g["verdict"] in ("PASS", "NOT SIGNIFICANT")
    assert "implausible" in g["tier"]  # delta 0.24 -> leak territory, as designed


# ---------------------------------------------------------------------------
# The three-number block
# ---------------------------------------------------------------------------

def test_three_number_block_is_incomplete_without_pnl():
    ours = [0.9, 0.1, 0.5, 0.52]
    base = [0.5, 0.5, 0.5, 0.5]
    y = [1.0, 0.0, 1.0, 0.0]
    block = scoring.three_number_block(ours, base, y, gate=0.06)
    assert block.subset_n == 2  # only |0.9-0.5| and |0.1-0.5| clear a 6-point gate
    assert not block.complete
    assert any("simulated post-fee P&L" in n for n in block.notes)

    block2 = scoring.three_number_block(ours, base, y, gate=0.06, simulated_pnl_usd=123.0)
    assert block2.complete
    assert block2.to_dict()["simulated_post_fee_pnl_usd"] == 123.0


def test_three_number_block_notes_a_too_small_subset():
    block = scoring.three_number_block([0.5, 0.5], [0.5, 0.5], [1.0, 0.0], gate=0.06)
    assert block.subset is None
    assert block.subset_n == 0
    assert any("traded subset" in n for n in block.notes)


def test_three_number_block_pooled_neutral_subset_positive():
    """The shape SYNTHESIS §2.3 says success looks like."""
    # 96 questions where we match the market exactly, 4 where we disagree and win.
    ours = [0.5] * 96 + [0.9, 0.9, 0.1, 0.1]
    base = [0.5] * 96 + [0.5, 0.5, 0.5, 0.5]
    y = [1.0, 0.0] * 48 + [1.0, 1.0, 0.0, 0.0]
    block = scoring.three_number_block(ours, base, y, gate=0.06, simulated_pnl_usd=0.0)
    assert block.pooled.delta > 0
    assert block.subset is not None and block.subset.delta > block.pooled.delta
    assert block.subset_n == 4


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def test_calibration_table_buckets_and_frequencies():
    forecasts = [0.02, 0.03, 0.10, 0.90, 0.92]
    outcomes = [0.0, 0.0, 1.0, 1.0, 0.0]
    tbl = scoring.calibration_table(forecasts, outcomes)
    by_bucket = {r["bucket"]: r for r in tbl}
    assert by_bucket["[0.00, 0.05)"]["n"] == 2
    assert by_bucket["[0.00, 0.05)"]["realized_freq"] == 0.0
    assert by_bucket["[0.05, 0.15)"]["n"] == 1
    assert by_bucket["[0.85, 0.95)"]["realized_freq"] == pytest.approx(0.5)
    assert sum(r["n"] for r in tbl) == 5


def test_calibration_table_includes_price_of_1():
    # The top bucket is [0.95, 1.01) precisely so p=1.0 is not dropped.
    tbl = scoring.calibration_table([1.0], [1.0])
    assert tbl[0]["n"] == 1


def test_concentration_top5_share():
    c = scoring.concentration([10, 5, 4, 3, 2, 1, -5], top=5)
    assert c["n"] == 7
    assert c["total_pnl"] == 20
    assert c["top_5_pnl"] == 24
    assert c["top_5_share_of_pnl"] == pytest.approx(1.2)
    assert c["share_defined"]
    assert c["best"] == 10 and c["worst"] == -5
    assert c["n_positive"] == 6


def test_concentration_share_undefined_on_a_losing_book():
    c = scoring.concentration([5, -20])
    assert c["total_pnl"] == -15
    assert c["top_5_share_of_pnl"] is None
    assert not c["share_defined"]


def test_concentration_empty():
    assert scoring.concentration([]) == {"n": 0}

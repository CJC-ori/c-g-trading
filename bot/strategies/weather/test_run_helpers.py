"""Tests for the runner's scoring helpers (pure functions, no DB)."""
from __future__ import annotations

import pytest

from bot.backtest.types import SettlementResult
from bot.strategies.weather.run_backtest import brier_table, calibration_curve


def _settle(ticker, result):
    return SettlementResult(ticker=ticker, result=result, settled_ts=0)


def test_brier_table_dedupes_and_scores():
    log = [
        # first look wins; second look on same (ticker, phase) ignored
        {"t": 1, "ticker": "A", "phase": "d1", "p_hat": 0.9, "mid_cents": 60.0},
        {"t": 2, "ticker": "A", "phase": "d1", "p_hat": 0.5, "mid_cents": 70.0},
        {"t": 3, "ticker": "B", "phase": "d1", "p_hat": 0.2, "mid_cents": 40.0},
        # no mid -> skipped
        {"t": 4, "ticker": "C", "phase": "d1", "p_hat": 0.5, "mid_cents": None},
    ]
    settlements = {"A": _settle("A", "yes"), "B": _settle("B", "no"),
                   "C": _settle("C", "yes")}
    out = brier_table(log, settlements, traded={("A", "d1")})
    p = out["pooled"]
    assert p["n"] == 2
    # ours: A (0.9-1)^2=0.01, B (0.2-0)^2=0.04 -> 0.025
    assert p["brier_ours"] == pytest.approx(0.025)
    # market: A (0.6-1)^2=0.16, B (0.4-0)^2=0.16 -> 0.16
    assert p["brier_market"] == pytest.approx(0.16)
    assert p["we_beat_market"]
    assert out["traded_subset"]["n"] == 1
    lo, hi = p["delta_ci95"]
    assert lo <= p["delta_market_minus_ours"] <= hi


def test_brier_table_unsettled_skipped():
    log = [{"t": 1, "ticker": "X", "phase": "d1", "p_hat": 0.7, "mid_cents": 50.0}]
    out = brier_table(log, {}, traded=set())
    assert out["pooled"]["n"] == 0


def test_walk_forward_offsets_point_in_time():
    from bot.groundtruth.weather import BiasFit
    from bot.strategies.weather.run_backtest import (
        WALK_FORWARD_PRIOR_N,
        _walk_forward_offsets,
    )
    dates = [f"2026-06-{d:02d}" for d in range(1, 11)]
    # constant true offset +2 in the window; prior says 0
    fc = {d: 80.0 for d in dates}
    stn = {d: 82.0 for d in dates}
    prior = BiasFit(offset=0.0, sd=2.0, mae=1.5, n=200)
    off = _walk_forward_offsets(fc, stn, prior, dates)
    # first date: no settled window residuals -> pure prior
    assert off[dates[0]] == pytest.approx(0.0)
    # third date may use dates <= D-2 (i.e. the first date only)
    assert off[dates[2]] == pytest.approx(2.0 / (WALK_FORWARD_PRIOR_N + 1))
    # offsets drift monotonically toward +2, never using residuals > D-2
    assert off[dates[9]] > off[dates[5]] > off[dates[2]] > 0.0
    for i, d in enumerate(dates):
        k = max(0, i - 2 + 1)  # residuals from dates[0..i-2]
        assert off[d] == pytest.approx(2.0 * k / (WALK_FORWARD_PRIOR_N + k))


def test_calibration_curve():
    rows = [
        {"p": 0.05, "o": 0.0}, {"p": 0.05, "o": 0.0},
        {"p": 0.95, "o": 1.0}, {"p": 0.95, "o": 1.0},
    ]
    c = calibration_curve(rows)
    assert c["n"] == 4
    assert c["ece"] == pytest.approx(0.05)
    assert len(c["curve"]) == 2

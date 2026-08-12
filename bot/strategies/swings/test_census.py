"""Unit tests for the swing-census math (bot/strategies/swings/census.py).

All tests run on synthetic Hour series — no DB required.
"""
from __future__ import annotations

import math

import pytest

from bot.strategies.swings.census import (
    CAPTURE_FRAC,
    Episode,
    Hour,
    U_GRID,
    annotate,
    detect_swings,
    measure_episode,
    _bin_label,
    DTC_BINS_D,
    PRICE_BINS,
    shortlist,
)

HOUR = 3600
T0 = 1_700_000_000  # arbitrary aligned base


def mk(h: int, mid: int, vol: int = 0, low: int | None = None,
       high: int | None = None, bid: int | None = None,
       ask: int | None = None) -> Hour:
    """Synthetic hourly candle h hours after T0. Defaults: 2c-wide book
    around mid; trade low/high default to mid when volume printed."""
    b = bid if bid is not None else max(1, mid - 1)
    a = ask if ask is not None else min(99, mid + 1)
    tlo = low if low is not None else (mid if vol > 0 else None)
    thi = high if high is not None else (mid if vol > 0 else None)
    return Hour(ts=T0 + h * HOUR, mid=mid, bid=b, ask=a,
                trade_low=tlo, trade_high=thi, volume=vol,
                notional=vol * mid / 100)


META = {
    "ticker": "TEST-1", "category": "Politics", "series_ticker": "TEST",
    "result": "yes", "payout_c": 100, "close_ts": T0 + 1000 * HOUR,
    "volume": 10_000,
}


def flat_then_drop(pre_mid=80, post_mid=60, pre_h=30, post_h=10, vol=2000):
    """pre_h hours flat at pre_mid, then post_h hours at post_mid, liquid."""
    hours = [mk(i, pre_mid, vol=vol) for i in range(pre_h)]
    hours += [mk(pre_h + i, post_mid, vol=vol) for i in range(post_h)]
    return hours


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------

class TestDetect:
    def test_down_swing_detected_at_first_crossing(self):
        hours = flat_then_drop(80, 60)
        idx = detect_swings(hours, x=20, t_h=6, direction="down")
        assert idx == [30]  # the first 60c hour

    def test_ref_is_window_max(self):
        hours = flat_then_drop(80, 60)
        ep = measure_episode(hours, 30, x=20, t_h=6, direction="down")
        assert ep.ref_c == 80
        assert ep.trig_c == 60
        assert ep.move_c == 20

    def test_no_trigger_below_threshold(self):
        hours = flat_then_drop(80, 65)  # 15c move < X=20
        assert detect_swings(hours, x=20, t_h=6, direction="down") == []

    def test_move_outside_window_not_a_swing(self):
        # slow drift: 1c per hour for 30 hours => never >=20c within 6h
        hours = [mk(i, 90 - i, vol=2000) for i in range(31)]
        assert detect_swings(hours, x=20, t_h=6, direction="down") == []
        # but it IS a swing at T=24 (24c within 24h)
        assert detect_swings(hours, x=20, t_h=24, direction="down") != []

    def test_up_swing_mirror(self):
        hours = [mk(i, 20, vol=2000) for i in range(30)]
        hours += [mk(30 + i, 45, vol=2000) for i in range(5)]
        idx = detect_swings(hours, x=20, t_h=6, direction="up")
        assert idx == [30]
        ep = measure_episode(hours, 30, x=20, t_h=6, direction="up")
        assert ep.ref_c == 20 and ep.trig_c == 45 and ep.move_c == 25

    def test_illiquid_swing_rejected(self):
        hours = flat_then_drop(80, 60, vol=0)
        hours[30] = mk(30, 60, vol=10)  # $6 traded, < $500 gate
        assert detect_swings(hours, x=20, t_h=6, direction="down") == []

    def test_one_sided_book_rejected(self):
        hours = flat_then_drop(80, 60)
        # empty bid at the trigger candle
        hours[30] = mk(30, 60, vol=2000, bid=0)
        idx = detect_swings(hours, x=20, t_h=6, direction="down")
        assert 30 not in idx

    def test_cooldown_one_displacement_one_episode(self):
        # drop and STAY down for 50 hours: exactly one episode
        hours = flat_then_drop(80, 60, post_h=80)
        idx = detect_swings(hours, x=20, t_h=6, direction="down")
        # after 72h cooldown the window restarts from ~60c: no second trigger
        assert len(idx) == 1

    def test_second_episode_after_reversion(self):
        # drop, revert fully, then drop again -> two episodes
        hours = [mk(i, 80, vol=2000) for i in range(10)]
        hours += [mk(10 + i, 60, vol=2000) for i in range(2)]
        hours += [mk(12 + i, 80, vol=2000) for i in range(10)]
        hours += [mk(22 + i, 60, vol=2000) for i in range(2)]
        idx = detect_swings(hours, x=20, t_h=6, direction="down")
        assert len(idx) == 2

    def test_gap_larger_than_window_no_ref(self):
        hours = [mk(0, 80, vol=2000), mk(10, 60, vol=2000)]
        # 10h gap > T=6h window: no reference, no trigger
        assert detect_swings(hours, x=20, t_h=6, direction="down") == []


# ---------------------------------------------------------------------------
# reversion measurement
# ---------------------------------------------------------------------------

class TestReversion:
    def test_revert50_flags_by_horizon(self):
        hours = [mk(i, 80, vol=2000) for i in range(30)]
        hours += [mk(30, 60, vol=2000)]
        hours += [mk(31 + i, 60, vol=2000) for i in range(9)]   # stays 10h
        hours += [mk(40 + i, 71, vol=2000) for i in range(40)]  # 50% retrace at +10h
        ep = measure_episode(hours, 30, x=20, t_h=6, direction="down")
        # target50 = 80 - 10 = 70; first mid >= 70 at +10h
        assert ep.revert50_by == {6: False, 24: True, 72: True}
        assert ep.time_to_50_h == 10.0
        assert ep.revert_full_by == {6: False, 24: False, 72: False}

    def test_full_reversion(self):
        hours = [mk(i, 80, vol=2000) for i in range(30)]
        hours += [mk(30, 60, vol=2000)]
        hours += [mk(31 + i, 79, vol=2000) for i in range(10)]  # back within 2c at +1h
        ep = measure_episode(hours, 30, x=20, t_h=6, direction="down")
        assert ep.revert50_by[6] is True
        assert ep.revert_full_by == {6: True, 24: True, 72: True}

    def test_no_reversion(self):
        hours = flat_then_drop(80, 60, post_h=80)
        ep = measure_episode(hours, 30, x=20, t_h=6, direction="down")
        assert not any(ep.revert50_by.values())
        assert ep.time_to_50_h is None
        assert ep.post_extreme_c == 60

    def test_post_extreme_tracks_deeper_low(self):
        hours = [mk(i, 80, vol=2000) for i in range(30)]
        hours += [mk(30, 60, vol=2000), mk(31, 45, vol=2000)]
        hours += [mk(32 + i, 75, vol=2000) for i in range(5)]
        ep = measure_episode(hours, 30, x=20, t_h=6, direction="down")
        assert ep.post_extreme_c == 45

    def test_up_swing_reversion_mirror(self):
        hours = [mk(i, 20, vol=2000) for i in range(30)]
        hours += [mk(30, 44, vol=2000)]                          # +24c
        hours += [mk(31 + i, 31, vol=2000) for i in range(10)]  # retrace to 31 <= 32
        ep = measure_episode(hours, 30, x=20, t_h=6, direction="up")
        # target50 = 20 + 12 = 32; 31 crosses it (m <= target)
        assert ep.revert50_by[6] is True


# ---------------------------------------------------------------------------
# settlement annotation + fade P&L
# ---------------------------------------------------------------------------

class TestAnnotateAndFades:
    def test_settled_with_move_down_no(self):
        hours = flat_then_drop(80, 60, post_h=80)
        ep = measure_episode(hours, 30, x=20, t_h=6, direction="down")
        meta = dict(META, result="no", payout_c=0)
        annotate(ep, meta)
        assert ep.settled_with_move is True     # payout 0 < target50 70
        assert ep.reverted50_by_settle is False
        # d0 fade: taker entry at ask=61, rides to settlement, pnl = 0-61
        d0 = ep.fades[0]
        assert d0["filled"] and d0["exit_kind"] == "settlement"
        assert d0["entry_c"] == 61
        assert d0["pnl_c"] == -61

    def test_settled_against_move_yes(self):
        hours = flat_then_drop(80, 60, post_h=80)
        ep = measure_episode(hours, 30, x=20, t_h=6, direction="down")
        annotate(ep, dict(META))  # yes, payout 100
        assert ep.settled_with_move is False
        assert ep.reverted50_by_settle is True   # payout beyond target50
        assert ep.fades[0]["pnl_c"] == 100 - 61

    def test_revert50_exit_at_target(self):
        hours = [mk(i, 80, vol=2000) for i in range(30)]
        hours += [mk(30, 60, vol=2000)]
        hours += [mk(31 + i, 72, vol=2000) for i in range(10)]
        ep = measure_episode(hours, 30, x=20, t_h=6, direction="down")
        annotate(ep, dict(META))
        d0 = ep.fades[0]
        assert d0["exit_kind"] == "revert50"
        assert d0["exit_c"] == 70.0             # exit AT the target, not at 72
        assert d0["pnl_c"] == pytest.approx(70.0 - 61)

    def test_depth_fill_requires_trade_through(self):
        hours = [mk(i, 80, vol=2000) for i in range(30)]
        hours += [mk(30, 60, vol=2000)]
        # next hour prints down to 52: through the 55c rung (d=5), not 50 (d=10)
        hours += [mk(31, 58, vol=1000, low=52, high=60)]
        hours += [mk(32 + i, 75, vol=2000) for i in range(5)]
        ep = measure_episode(hours, 30, x=20, t_h=6, direction="down")
        annotate(ep, dict(META))
        assert ep.fades[5]["filled"] is True     # 52 <= 55-1
        assert ep.fades[10]["filled"] is False   # 52 > 50-1
        assert ep.fades[5]["capturable_contracts"] == int(1000 * CAPTURE_FRAC)
        # maker entry at 55, exit at 70 target
        assert ep.fades[5]["entry_c"] == 55
        assert ep.fades[5]["pnl_c"] == pytest.approx(15.0)

    def test_depth_fill_no_volume_no_fill(self):
        hours = [mk(i, 80, vol=2000) for i in range(30)]
        hours += [mk(30, 60, vol=2000)]
        # quote-only candle at 50: mid moved but nothing printed
        hours += [mk(31, 50, vol=0)]
        hours += [mk(32 + i, 75, vol=2000) for i in range(5)]
        ep = measure_episode(hours, 30, x=20, t_h=6, direction="down")
        assert ep.fades[5]["filled"] is False
        assert ep.fades[10]["filled"] is False

    def test_scalar_payout(self):
        hours = flat_then_drop(80, 60, post_h=80)
        ep = measure_episode(hours, 30, x=20, t_h=6, direction="down")
        annotate(ep, dict(META, result="scalar", payout_c=50))
        assert ep.payout_c == 50
        assert ep.fades[0]["pnl_c"] == 50 - 61

    def test_classification_bins(self):
        hours = flat_then_drop(80, 60, post_h=80)
        ep = measure_episode(hours, 30, x=20, t_h=6, direction="down")
        meta = dict(META, close_ts=T0 + 30 * HOUR + 36 * HOUR)  # 36h to close
        annotate(ep, meta)
        assert ep.price_bin == "70-90"
        assert ep.dtc_bin == "1-7"
        assert ep.near_close is True            # 36h <= 48h
        # window = 7 candles: 6 x 2000 x $0.80 + 1 x 2000 x $0.60 = $10,800
        assert ep.liq_tier == "10000+"

    def test_bin_label_edges(self):
        assert _bin_label(0, PRICE_BINS) == "0-10"
        assert _bin_label(90, PRICE_BINS) == "90-100"
        assert _bin_label(45.0, DTC_BINS_D) == "30+"


# ---------------------------------------------------------------------------
# shortlist gating
# ---------------------------------------------------------------------------

def _mk_episode(swm: bool, revert: bool, n_id: int) -> Episode:
    hours = [mk(i, 80, vol=2000) for i in range(30)]
    hours += [mk(30, 60, vol=2000)]
    hours += [mk(31, 58, vol=1000, low=52, high=60)]  # print through 55c rung
    if revert:
        hours += [mk(32 + i, 75, vol=2000) for i in range(5)]
    else:
        hours += [mk(32 + i, 58, vol=2000) for i in range(80)]
    ep = measure_episode(hours, 30, x=20, t_h=6, direction="down")
    meta = dict(META, result=("no" if swm else "yes"),
                payout_c=(0 if swm else 100), ticker=f"T{n_id}")
    annotate(ep, meta)
    return ep


class TestShortlist:
    def test_min_n_and_adverse_gates(self):
        good = [_mk_episode(swm=False, revert=True, n_id=i) for i in range(40)]
        rows = shortlist(good, span_weeks=100.0, min_n=30)
        assert len(rows) == 1
        assert rows[0]["settled_with_move"] == 0.0
        # under min_n: excluded
        assert shortlist(good[:20], span_weeks=100.0, min_n=30) == []

    def test_adverse_cell_excluded(self):
        bad = [_mk_episode(swm=True, revert=False, n_id=i) for i in range(40)]
        assert shortlist(bad, span_weeks=100.0, min_n=30) == []

"""Tests for bot/groundtruth/weather.py (no network required).

CLI fixtures under testdata/ are real productText captures from
api.weather.gov on 2026-08-11.
"""
from __future__ import annotations

import math
import os

import pytest

from bot.groundtruth import weather as wx

TESTDATA = os.path.join(os.path.dirname(__file__), "testdata")
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "kalshi.db")


def _fixture(name: str) -> str:
    with open(os.path.join(TESTDATA, name)) as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Station map
# ---------------------------------------------------------------------------

def test_station_map_covers_all_20_series():
    assert len(wx.STATIONS) == 20
    # the classic 12 from research/ground-truth.md §2.2 all present
    for s in [
        "KXHIGHNY", "KXHIGHCHI", "KXHIGHAUS", "KXHIGHMIA", "KXHIGHLAX",
        "KXHIGHPHIL", "KXHIGHDEN", "KXHIGHTDC", "KXHIGHTPHX", "KXHIGHTSEA",
        "KXHIGHTATL", "KXHIGHTDAL",
    ]:
        assert s in wx.STATIONS


def test_station_map_critical_stations():
    # Chicago settles on Midway, NOT O'Hare (research/ground-truth.md §1)
    assert wx.STATIONS["KXHIGHCHI"].sid == "KMDW"
    # NYC = Central Park KNYC
    assert wx.STATIONS["KXHIGHNY"].sid == "KNYC"
    # Houston = Hobby (issuedby=HOU), not IAH; Dallas = DFW; DC = DCA
    assert wx.STATIONS["KXHIGHTHOU"].sid == "KHOU"
    assert wx.STATIONS["KXHIGHTDAL"].sid == "KDFW"
    assert wx.STATIONS["KXHIGHTDC"].sid == "KDCA"
    # Phoenix has no DST
    assert wx.STATIONS["KXHIGHTPHX"].tz == "America/Phoenix"


@pytest.mark.skipif(not os.path.exists(DB_PATH), reason="kalshi.db not pulled")
def test_station_map_from_db_agrees_with_canonical():
    from_db = wx.station_map_from_db(DB_PATH)
    assert len(from_db) == 20
    for series, st in from_db.items():
        canon = wx.STATIONS[series]
        assert st.sid == canon.sid, series
        assert st.cli_office == canon.cli_office, series
        assert st.cli_loc == canon.cli_loc, series


# ---------------------------------------------------------------------------
# ACIS value parsing
# ---------------------------------------------------------------------------

def test_acis_value_handles_trace_and_missing():
    assert wx._acis_value("M") is None
    assert wx._acis_value("") is None
    assert wx._acis_value("T") == 0.0
    assert wx._acis_value("85") == 85.0
    assert wx._acis_value("-3") == -3.0


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------

def test_parse_nyc_intermediate():
    r = wx.parse_cli_product(_fixture("cli_nyc_intermediate.txt"))
    assert r.summary_date == "2026-08-11"
    assert r.max_temp == 85
    assert r.max_time_local == "13:26"   # "126 PM"
    assert r.as_of_local == "16:00"      # "VALID TODAY AS OF 0400 PM"
    assert not r.is_final


def test_parse_nyc_final():
    r = wx.parse_cli_product(_fixture("cli_nyc_final.txt"))
    assert r.summary_date == "2026-08-10"
    assert r.max_temp == 85
    assert r.max_time_local == "15:19"   # "319 PM"
    assert r.is_final


def test_parse_mdw_intermediate_colon_time():
    r = wx.parse_cli_product(_fixture("cli_mdw_intermediate.txt"))
    assert r.summary_date == "2026-08-11"
    assert r.max_temp == 80
    assert r.max_time_local == "14:59"   # "2:59 PM"
    assert not r.is_final


def test_parse_lax_intermediate_5pm():
    r = wx.parse_cli_product(_fixture("cli_lax_intermediate.txt"))
    assert r.summary_date == "2026-08-10"
    assert r.max_temp == 79
    assert r.as_of_local == "17:00"
    assert not r.is_final


def test_parse_missing_max():
    text = (
        "...THE NOWHERE CLIMATE SUMMARY FOR AUGUST 5 2026...\n"
        "VALID TODAY AS OF 0400 PM LOCAL TIME.\n"
        "  MAXIMUM         MM\n"
    )
    r = wx.parse_cli_product(text)
    assert r.max_temp is None
    assert r.summary_date == "2026-08-05"


# ---------------------------------------------------------------------------
# Bias fit
# ---------------------------------------------------------------------------

def test_fit_bias_recovers_known_offset():
    fc = {f"2026-06-{d:02d}": 80.0 for d in range(1, 29)}
    stn = {f"2026-06-{d:02d}": 81.5 for d in range(1, 29)}
    fit = wx.fit_bias(fc, stn)
    assert fit.offset == pytest.approx(1.5)
    assert fit.sd == pytest.approx(0.0)
    assert fit.n == 28
    assert fit.correct(80.0) == pytest.approx(81.5)


def test_fit_bias_ignores_missing_station_days():
    fc = {"2026-06-01": 80.0, "2026-06-02": 82.0, "2026-06-03": 84.0,
          "2026-06-04": 80.0, "2026-06-05": 82.0, "2026-06-06": 84.0}
    stn = {k: v + 1.0 for k, v in fc.items()}
    stn["2026-06-03"] = None
    fit = wx.fit_bias(fc, stn)
    assert fit.n == 5
    assert fit.offset == pytest.approx(1.0)


def test_fit_bias_seasonal_harmonic():
    import datetime as dt
    import math as m
    fc, stn = {}, {}
    d0 = dt.date(2025, 1, 1)
    for i in range(360):
        d = (d0 + dt.timedelta(days=i)).isoformat()
        doy = (d0 + dt.timedelta(days=i)).timetuple().tm_yday
        fc[d] = 70.0
        stn[d] = 70.0 + 1.0 + 0.8 * m.cos(2 * m.pi * doy / 365.25)
    fit = wx.fit_bias(fc, stn)
    assert fit.seasonal is not None
    assert fit.offset == pytest.approx(1.0, abs=0.05)
    assert fit.seasonal[0] == pytest.approx(0.8, abs=0.05)
    # winter day gets a bigger correction than summer day
    assert fit.offset_for("2026-01-01") > fit.offset_for("2026-07-01")


# ---------------------------------------------------------------------------
# Strike probability integration
# ---------------------------------------------------------------------------

def test_strike_prob_between_sums_to_one_over_ladder():
    """A full ladder (less + betweens + greater) must sum to ~1."""
    mu, sd = 86.3, 2.5
    ladder = [("less", None, 84.0)]
    for lo in range(84, 92, 2):
        ladder.append(("between", float(lo), float(lo + 1)))
    ladder.append(("greater", 91.0, None))
    total = sum(wx.strike_prob(mu, sd, st, lo, hi) for st, lo, hi in ladder)
    assert total == pytest.approx(1.0, abs=1e-9)


def test_strike_prob_directionality():
    # mu far above the strike -> greater ~1, less ~0
    assert wx.strike_prob(95.0, 2.0, "greater", 85.0, None) > 0.99
    assert wx.strike_prob(95.0, 2.0, "less", None, 85.0) < 0.01
    # symmetric case: mu on a between-strike center
    # P(84.5 < X < 86.5) at mu=85.5, sd=2 = Phi(.5)-Phi(-.5) ~ 0.383
    p = wx.strike_prob(85.5, 2.0, "between", 85.0, 86.0)
    assert p == pytest.approx(0.3829, abs=1e-3)


def test_strike_prob_continuity_correction():
    # integer outcomes: P(high > 85) == P(high >= 86); at mu=85.5 exactly 0.5
    assert wx.strike_prob(85.5, 1.0, "greater", 85.0, None) == pytest.approx(0.5)


def test_strike_prob_ensemble_matches_gaussian_when_degenerate():
    members = [86.0] * 30
    bias = wx.BiasFit(offset=1.0, sd=0.0, mae=0.0, n=100)
    p_ens = wx.strike_prob_ensemble(members, 2.0, "greater", 87.0, None, bias)
    p_gauss = wx.strike_prob(87.0, 2.0, "greater", 87.0, None)
    assert p_ens == pytest.approx(p_gauss, abs=1e-12)


def test_strike_prob_ensemble_wider_than_single_member():
    members = [82.0, 84.0, 86.0, 88.0, 90.0]
    p_tail = wx.strike_prob_ensemble(members, 1.0, "greater", 91.0, None)
    p_single = wx.strike_prob(86.0, 1.0, "greater", 91.0, None)
    assert p_tail > p_single  # spread members put more mass in the tail


# ---------------------------------------------------------------------------
# Max-by-hour machinery
# ---------------------------------------------------------------------------

def _make_day(peak_idx: int, peak: float = 90.0) -> list[float]:
    """24 hourly obs with max at index peak_idx."""
    return [peak - abs(i - peak_idx) for i in range(24)]


def test_lst_cutoff_index_dst_awareness():
    # July NYC (EDT): by-4PM-wall = obs through 14:51 LST -> index 14
    assert wx._lst_cutoff_index("2026-07-15", "America/New_York", 16) == 14
    # January NYC (EST): index 15
    assert wx._lst_cutoff_index("2026-01-15", "America/New_York", 16) == 15
    # Phoenix never observes DST
    assert wx._lst_cutoff_index("2026-07-15", "America/Phoenix", 16) == 15


def test_running_max_by_wall_hour():
    day = _make_day(13)  # peak at 13:51 LST
    assert wx.running_max_by_wall_hour(day, "2026-07-15", "America/New_York", 16) == 90.0
    day_late = _make_day(20)  # evening peak
    assert wx.running_max_by_wall_hour(day_late, "2026-07-15", "America/New_York", 16) == 84.0


def test_max_by_hour_stats():
    hourly = {
        "2026-07-01": _make_day(13),  # max set well before 4pm
        "2026-07-02": _make_day(20),  # evening max
        "2026-01-05": _make_day(12),  # cool-season, set by 4pm
    }
    maxt = {"2026-07-01": 90.0, "2026-07-02": 90.0, "2026-01-05": 90.0}
    stats = wx.max_by_hour_stats(hourly, maxt, "America/New_York", wall_hours=(16,), min_obs=20)
    s = stats[16]
    assert s["all"]["n"] == 3
    assert s["all"]["frac_exact"] == pytest.approx(2 / 3)
    assert s["warm"]["n"] == 2
    assert s["warm"]["frac_exact"] == pytest.approx(1 / 2)
    assert s["cool"]["frac_exact"] == pytest.approx(1.0)


def test_rise_pmf_and_strike_prob():
    hourly = {f"2026-07-{d:02d}": _make_day(13) for d in range(1, 21)}
    maxt = {f"2026-07-{d:02d}": 90.0 for d in range(1, 21)}
    # 5 days where the max rose 2 degrees after 4pm
    for d in range(1, 6):
        maxt[f"2026-07-{d:02d}"] = 92.0
    pmf = wx.rise_after_cutoff_pmf(hourly, maxt, "America/New_York", laplace=0.0)
    assert pmf[0] == pytest.approx(15 / 20)
    assert pmf[2] == pytest.approx(5 / 20)
    # at 4pm with max-so-far 90: P(final > 91) = P(rise >= 2) = 0.25
    p = wx.strike_prob_from_rise_pmf(90.0, pmf, "greater", 91.0, None)
    assert p == pytest.approx(0.25)
    # P(final < 91) = P(rise == 0) = 0.75
    p2 = wx.strike_prob_from_rise_pmf(90.0, pmf, "less", None, 91.0)
    assert p2 == pytest.approx(0.75)
    # between 90-91 == rise 0 or 1 -> 0.75
    p3 = wx.strike_prob_from_rise_pmf(90.0, pmf, "between", 90.0, 91.0)
    assert p3 == pytest.approx(0.75)

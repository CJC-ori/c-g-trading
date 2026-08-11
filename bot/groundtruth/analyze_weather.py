"""Ground-truth analyses for the P-2 weather strategy.

Everything here reads only the local caches under data/weather/ (populate
with `python -m bot.groundtruth.pull_weather` first) and writes
reports/weather/groundtruth.json + a printed summary.

Analyses:
1. Per-city bias fits (station − previous-runs lead-N forecast), fit on a
   window strictly BEFORE the backtest window (no leakage into replay).
2. Replication of research/ground-truth.md §2.4's measured grid−station
   offsets (NYC +1.54, MIA −1.19, LAX −0.60, CHI −0.58 °F) on the same
   May-Aug 2026 window, for sign/magnitude validation.
3. The "is the daily max already set by 4 PM?" measurement (SYNTHESIS
   flags the prior as unverified) from ACIS hourly obs, by city/season.
4. Rise-after-4PM PMFs per city (the intraday variant's engine).
"""
from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any

from bot.groundtruth import weather as wx

REPORT_DIR = os.path.join("reports", "weather")

# Backtest window = the Kalshi KXHIGH history in data/kalshi.db
# (2026-05-21 .. today). All fitting stops strictly before it.
BACKTEST_START = "2026-05-21"

# research/ground-truth.md §2.4 measured offsets (grid − station, °F),
# 2026-05-01..2026-08-05, archived model analysis vs ACIS.
RESEARCH_OFFSETS = {
    "KXHIGHNY": 1.54,
    "KXHIGHMIA": -1.19,
    "KXHIGHLAX": -0.60,
    "KXHIGHCHI": -0.58,
}
RESEARCH_WINDOW = ("2026-05-01", "2026-08-05")


def _day_before(date: str) -> str:
    return (dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat()


def fit_all_biases(
    leads: tuple[int, ...] = (1, 2),
    fit_end: str = BACKTEST_START,
    fit_start: str = "2025-08-12",
    cache_dir: str = wx.DATA_DIR,
) -> dict[str, dict[int, wx.BiasFit]]:
    """Per-series, per-lead BiasFit on [fit_start, fit_end) only."""
    out: dict[str, dict[int, wx.BiasFit]] = {}
    for series, st in sorted(wx.STATIONS.items()):
        prev = wx.previous_runs_daily_max(st, leads=leads, cache_dir=cache_dir)
        stn = wx.acis_daily_maxt(st.sid, fit_start, _day_before(fit_end), cache_dir=cache_dir)
        fits: dict[int, wx.BiasFit] = {}
        for lead in leads:
            fc = {
                d: v for d, v in prev.get(lead, {}).items()
                if fit_start <= d < fit_end
            }
            fits[lead] = wx.fit_bias(fc, stn)
        out[series] = fits
    return out


def replicate_research_offsets(cache_dir: str = wx.DATA_DIR) -> dict[str, dict[str, Any]]:
    """Compare our lead-1 (grid − station) offset on the research window
    against research/ground-truth.md §2.4's archived-analysis numbers.

    Ours uses the previous-runs lead-1 *forecast* rather than the archived
    analysis, so exact equality is not expected — sign and rough magnitude
    are the validation.
    """
    s0, s1 = RESEARCH_WINDOW
    out: dict[str, dict[str, Any]] = {}
    for series, research_val in RESEARCH_OFFSETS.items():
        st = wx.STATIONS[series]
        prev = wx.previous_runs_daily_max(st, cache_dir=cache_dir)
        stn = wx.acis_daily_maxt(st.sid, s0, s1, cache_dir=cache_dir)
        diffs = [
            prev[1][d] - stn[d]
            for d in prev.get(1, {})
            if s0 <= d <= s1 and stn.get(d) is not None
        ]
        ours = sum(diffs) / len(diffs) if diffs else None
        out[series] = {
            "research_grid_minus_station": research_val,
            "ours_lead1_grid_minus_station": ours,
            "n": len(diffs),
            "sign_agrees": (ours is not None and (ours > 0) == (research_val > 0)),
        }
    return out


def measure_max_by_4pm(
    hourly_start: str = "2024-06-01",
    hourly_end: str | None = None,
    wall_hours: tuple[int, ...] = (16, 17, 18),
    cache_dir: str = wx.DATA_DIR,
) -> dict[str, dict]:
    """Fraction of days with the daily max already set by 4/5/6 PM local,
    per city and season, from ACIS hourly obs vs ACIS daily maxt."""
    hourly_end = hourly_end or (dt.date.today() - dt.timedelta(days=1)).isoformat()
    out: dict[str, dict] = {}
    for series, st in sorted(wx.STATIONS.items()):
        hourly = wx.acis_hourly_temps(st.sid, hourly_start, hourly_end, cache_dir=cache_dir)
        daily = wx.acis_daily_maxt(st.sid, hourly_start, hourly_end, cache_dir=cache_dir)
        out[series] = wx.max_by_hour_stats(hourly, daily, st.tz, wall_hours=wall_hours)
    return out


def fit_rise_pmfs(
    fit_start: str = "2024-06-01",
    fit_end: str = BACKTEST_START,
    wall_hour: int = 16,
    cache_dir: str = wx.DATA_DIR,
) -> dict[str, dict[int, float]]:
    """Per-city rise-after-4PM PMFs fit strictly before the backtest window."""
    out: dict[str, dict[int, float]] = {}
    end = _day_before(fit_end)
    for series, st in sorted(wx.STATIONS.items()):
        hourly = wx.acis_hourly_temps(st.sid, fit_start, end, cache_dir=cache_dir)
        daily = wx.acis_daily_maxt(st.sid, fit_start, end, cache_dir=cache_dir)
        out[series] = wx.rise_after_cutoff_pmf(hourly, daily, st.tz, wall_hour=wall_hour)
    return out


def main() -> None:
    os.makedirs(REPORT_DIR, exist_ok=True)
    print("== bias fits (fit window 2025-08-12 .. 2026-05-20, pre-backtest) ==")
    biases = fit_all_biases()
    for series, fits in sorted(biases.items()):
        f1, f2 = fits[1], fits[2]
        print(
            f"{series:14s} lead1: offset {f1.offset:+.2f} sd {f1.sd:.2f} mae {f1.mae:.2f}"
            f" n {f1.n} seas={'y' if f1.seasonal else 'n'} | "
            f"lead2: offset {f2.offset:+.2f} sd {f2.sd:.2f} n {f2.n}"
        )

    print("\n== replication of research measured offsets (grid - station) ==")
    rep = replicate_research_offsets()
    for series, r in rep.items():
        print(
            f"{series:14s} research {r['research_grid_minus_station']:+.2f}  "
            f"ours(lead1) {r['ours_lead1_grid_minus_station']:+.2f}  "
            f"n={r['n']}  sign_agrees={r['sign_agrees']}"
        )

    print("\n== max already set by 4 PM local? (ACIS hourly, 2024-06+) ==")
    m4 = measure_max_by_4pm()
    for series, stats in sorted(m4.items()):
        s = stats[16]
        print(
            f"{series:14s} 4pm: exact {s['all']['frac_exact']:.2f} "
            f"within1 {s['all']['frac_within1']:.2f} (n={s['all']['n']}) | "
            f"warm {s['warm']['frac_exact']:.2f} cool {s['cool']['frac_exact']:.2f}"
        )

    rise = fit_rise_pmfs()

    payload = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(),
        "backtest_start": BACKTEST_START,
        "bias_fits": {
            series: {
                str(lead): {
                    "offset": f.offset, "sd": f.sd, "mae": f.mae, "n": f.n,
                    "seasonal": f.seasonal,
                }
                for lead, f in fits.items()
            }
            for series, fits in biases.items()
        },
        "research_offset_replication": rep,
        "max_by_wall_hour": m4,
        "rise_pmf_4pm": {s: {str(k): v for k, v in p.items()} for s, p in rise.items()},
    }
    out_path = os.path.join(REPORT_DIR, "groundtruth.json")
    with open(out_path, "w") as fh:
        json.dump(payload, fh, indent=2)
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()

"""Populate the local weather ground-truth caches (data/weather/).

Pulls, for all 20 KXHIGH* settlement stations:
- ACIS daily max temps 2024-01-01..today  (settlement ground truth, ≥18mo)
- ACIS hourly temps    2024-06-01..today  (max-by-4pm measurement)
- Open-Meteo previous-runs lead-1/2 daily-max forecasts, past_days=365
  (point-in-time forecasts for bias fitting + backtest)

Idempotent: everything is cached under data/weather/ and only missing
ranges are re-fetched. Run: python -m bot.groundtruth.pull_weather
"""
from __future__ import annotations

import datetime as dt
import sys
import time

from bot.groundtruth import weather as wx


def main() -> int:
    today = dt.date.today()
    yesterday = (today - dt.timedelta(days=1)).isoformat()
    daily_start = "2024-01-01"
    hourly_start = "2024-06-01"
    ok, fail = 0, []
    for series, st in sorted(wx.STATIONS.items()):
        t0 = time.time()
        try:
            daily = wx.acis_daily_maxt(st.sid, daily_start, yesterday)
            n_daily = sum(1 for v in daily.values() if v is not None)
            hourly = wx.acis_hourly_temps(st.sid, hourly_start, yesterday)
            prev = wx.previous_runs_daily_max(st, leads=(1, 2), past_days=365)
            n_prev = {k: len(v) for k, v in prev.items()}
            print(
                f"{series:14s} {st.sid}  daily={n_daily}  hourly_days={len(hourly)}"
                f"  prev_runs={n_prev}  ({time.time()-t0:.1f}s)",
                flush=True,
            )
            ok += 1
        except Exception as e:  # noqa: BLE001 - report and continue
            print(f"{series:14s} {st.sid}  FAILED: {e}", flush=True)
            fail.append(series)
        time.sleep(1.0)  # ACIS rate-limits sustained polling
    print(f"done: {ok} ok, {len(fail)} failed {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())

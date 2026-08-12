"""Episode discovery for P-3 panic-wick ladders (SYNTHESIS §1 P-3).

An *episode* is one (market, scheduled-resolution event window) pair:

- the market is a settled binary in an election-like category whose YES
  price averaged >= 95c (time-weighted) over the 24h entering the window;
- the window is anchored on the market's hour of maximum traded volume
  over its covered life, skipping the first 24h after open (scheduled
  result nights are when the information arrives, which is when volume
  spikes; the calendar was public knowledge ex ante — poll-close nights,
  scheduled votes/rulings).

  AMENDED 2026-08-12 (coverage fix, before any strategy backtest was run):
  the original WIP anchored inside the final 10 days of market LIFE, which
  silently dropped every 2024 general-election market — PRES-2024-* settled
  at inauguration (Jan 20) and SENATE*-24 on Jan 3, months after election
  night, so the marquee event night fell outside the anchor search and the
  discovery instead picked up their settlement-week churn (the spurious
  late-Dec "nights"). The anchor now searches the whole covered life and
  merges per-hour volume across hourly candles, 1-min candles and tick
  trades (per-hour MAX across sources, never summed — sources overlap),
  because coverage is asymmetric per source (e.g. SENATETX-24-R: trades
  cover only Nov 4–6, hourly candles only Dec 19–Jan 3);
- windows are kept only when they cluster (>= CLUSTER_MIN distinct events
  sharing the same ET calendar date) or fall on a known scheduled event
  night. Clustering uses volume/settlement timing only — never prices —
  so the dip taxonomy below stays honest.

Outcome taxonomy per episode (the key deliverable — it measures the
adverse-selection rate):

- no-dip           : price never touched <= 85c in the window; settled YES.
- dip-and-revert   : touched <= 85c, settled YES (the Michigan shape).
- dip-and-die      : touched <= 85c, settled NO (the market was right).
- died-without-dip : settled NO but never printed <= 85c inside the window
                    (a gap-down, or the collapse happened outside our
                    window / data coverage — reported honestly, since a
                    ladder would have been jumped over, not filled).

All parameters are FROZEN before any results were looked at (2026-08-12);
rationale inline. Tuning anything here after seeing results voids the run
(SPEC §8).
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from dataclasses import asdict, dataclass
from typing import Iterable

DB_PATH = "/home/user/c-g-trading/data/kalshi.db"

HOUR = 3600
DAY = 86400

# ---------------------------------------------------------------------------
# FROZEN parameters (set 2026-08-12 BEFORE running discovery; do not tune)
# ---------------------------------------------------------------------------
FROZEN = dict(
    # Election-like categories. Politics includes scheduled votes/rulings
    # (the sanctioned generalization); Economics data releases are excluded
    # by design: a CPI print resolves atomically, so there is no
    # partial-information window in which a panic can revert — the
    # mechanism P-3 trades does not exist there.
    categories=("Elections", "Politics"),
    # Lifetime volume floor: excludes dead markets whose "95c average" is a
    # stale quote nobody would have filled against.
    min_lifetime_volume=1000.0,
    # Anchor search skips the first 24h after open (listing-day volume
    # bursts are onboarding flow, not scheduled events).
    anchor_skip_open_h=24,
    # Window = [anchor_hour - 6h, +24h]. 6h pre-buffer puts window start
    # before US poll closes when the volume peak is mid-count (Michigan:
    # peak 03-04 UTC, polls closed 00-01 UTC).
    window_pre_offset_h=6,
    window_hours=24,
    # Qualification: time-weighted YES average over the 24h entering the
    # window (SYNTHESIS: "YES averaged >= 95c over the prior 24h").
    pre_avg_hours=24,
    pre_avg_min_cents=95.0,
    # Need at least half the pre-window covered by data to trust the avg.
    pre_min_coverage_h=12,
    # Window must have real trading (contracts) to be an episode at all.
    min_window_volume=500.0,
    # Dip = touched the top ladder rung region.
    dip_threshold_cents=85.0,
    # A date counts as a scheduled event night if >= this many distinct
    # events' markets qualify on it, or it is in KNOWN_EVENT_NIGHTS.
    cluster_min_events=3,
)

# Major scheduled US event nights (public calendar, ex-ante knowledge).
# Used only to LABEL/ADMIT dates the cluster rule might miss (single-market
# nights); never to exclude clustered dates.
KNOWN_EVENT_NIGHTS = {
    "2021-11-02",  # VA/NJ governor
    "2022-11-08",  # midterms
    "2022-12-06",  # GA senate runoff
    "2023-11-07",  # KY/MS governor, VA legislature
    "2024-01-15", "2024-01-23", "2024-03-05",  # IA, NH, Super Tuesday
    "2024-11-05",  # general
    "2025-11-04",  # NJ/VA governor, NYC mayor
    "2026-08-04",  # MI/AZ/KS/MO/WA primaries (the Michigan night)
    "2026-11-03",  # midterms (future)
}

ET_OFFSET_S = 5 * HOUR  # fixed UTC-5 bucketing for calendar-date labels


@dataclass
class Episode:
    ticker: str
    event_ticker: str
    series_ticker: str
    category: str
    title: str
    result: str                 # 'yes' | 'no'
    et_date: str                # ET calendar date of the event window start
    window_start: int           # unix s
    window_end: int
    anchor_ts: int              # max-volume hour start
    pre_avg_cents: float        # time-weighted YES avg over prior 24h
    pre_coverage_h: float
    min_price_cents: float      # min traded price in window (best data)
    window_volume: float        # contracts traded in window
    depth_le85: float           # contracts traded at <= 85c in window
    depth_le80: float
    depth_le75: float
    outcome_class: str          # no-dip | dip-and-revert | dip-and-die | died-without-dip
    data_resolution: str        # trades | 1min | hourly (best window data)
    has_1min_window: bool
    trades_cover_window: bool
    settled_ts: int
    lifetime_volume: float
    clustered: bool             # met the cluster rule (vs known-night only)


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable without a DB)
# ---------------------------------------------------------------------------

def time_weighted_avg(points: list[tuple[int, float]], t0: int, t1: int) -> tuple[float | None, float]:
    """Step-function time-weighted average of (ts, value) over [t0, t1).

    Carries the last value forward. Returns (avg, covered_hours) where
    coverage starts at the first point at/before t0 (or the first point
    inside the range). None if no data intersects the range.
    """
    if t1 <= t0 or not points:
        return None, 0.0
    pts = sorted(points)
    prev_val: float | None = None
    for ts, v in pts:
        if ts <= t0:
            prev_val = v
        else:
            break
    total = 0.0
    weight = 0.0
    cur_t = t0
    cur_v = prev_val
    for ts, v in pts:
        if ts <= t0:
            continue
        if ts >= t1:
            break
        if cur_v is not None:
            total += cur_v * (ts - cur_t)
            weight += ts - cur_t
        cur_t = ts
        cur_v = v
    if cur_v is not None and cur_t < t1:
        total += cur_v * (t1 - cur_t)
        weight += t1 - cur_t
    if weight <= 0:
        return None, 0.0
    return total / weight, weight / HOUR


def classify_outcome(result: str, min_price_cents: float | None, dip_threshold: float) -> str:
    dipped = min_price_cents is not None and min_price_cents <= dip_threshold
    if result == "yes":
        return "dip-and-revert" if dipped else "no-dip"
    return "dip-and-die" if dipped else "died-without-dip"


def et_date(ts: int) -> str:
    return _dt.datetime.utcfromtimestamp(ts - ET_OFFSET_S).strftime("%Y-%m-%d")


def _iso_ts(value) -> int | None:
    if not value:
        return None
    try:
        return int(_dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _market_rows(conn: sqlite3.Connection) -> Iterable[sqlite3.Row]:
    q = """
    SELECT ticker, event_ticker, series_ticker, category, title, result,
           open_time, close_time, settlement_ts, volume
    FROM markets
    WHERE result IN ('yes','no')
      AND category IN ({ph})
      AND volume >= ?
    ORDER BY ticker
    """.format(ph=",".join("?" * len(FROZEN["categories"])))
    params = list(FROZEN["categories"]) + [FROZEN["min_lifetime_volume"]]
    return conn.execute(q, params)


def _load_series(conn, ticker: str, lo: int, hi: int):
    """Return (hourly, one_min, trades) rows within [lo, hi], cents units.

    hourly/one_min: (end_ts, close_c, low_c, vol); trades: (ts, price_c, count).
    Candle rows without a trade in the period carry close from bid/ask mid
    (price_* NULL) — used for price marks only; their volume is 0.
    """
    def candles(period):
        rows = []
        for r in conn.execute(
            "SELECT ts, price_close, price_low, yes_bid_close, yes_ask_close, volume"
            " FROM candlesticks WHERE ticker=? AND period_interval=? AND ts BETWEEN ? AND ?"
            " ORDER BY ts",
            (ticker, period, lo, hi),
        ):
            ts, pc, pl, bc, ac, vol = r
            if pc is not None:
                close = pc * 100.0
                low = (pl if pl is not None else pc) * 100.0
                v = float(vol or 0)
            elif bc is not None and ac is not None:
                close = (bc + ac) * 50.0
                low = close
                v = 0.0
            else:
                continue
            rows.append((int(ts), close, low, v))
        return rows

    trades = [
        (int(ts), p * 100.0, float(c or 0))
        for ts, p, c in conn.execute(
            "SELECT ts, yes_price, count FROM trades"
            " WHERE ticker=? AND ts BETWEEN ? AND ? ORDER BY ts",
            (ticker, lo, hi),
        )
    ]
    return candles(60), candles(1), trades


def discover(db_path: str = DB_PATH) -> list[Episode]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    episodes: list[Episode] = []
    f = FROZEN
    for m in list(_market_rows(conn)):
        close_ts = _iso_ts(m["close_time"])
        settled_ts = _iso_ts(m["settlement_ts"]) or close_ts
        open_ts = _iso_ts(m["open_time"]) or 0
        if close_ts is None:
            continue
        eff_end = min(close_ts, settled_ts or close_ts)
        hourly, one_min, trades = _load_series(conn, m["ticker"], 0, eff_end + HOUR)
        # ---- anchor: max-volume hour over the covered life ----------------
        # Per-hour volume computed per SOURCE, then merged with MAX across
        # sources (they overlap where coverage coincides; summing would
        # double-count, preferring one source drops the 2024 general night
        # whose tick trades are the only coverage of the event itself).
        anchor_min_ts = open_ts + f["anchor_skip_open_h"] * HOUR
        per_source: list[dict[int, float]] = []
        for rows, is_trades in ((hourly, False), (one_min, False), (trades, True)):
            if not rows:
                continue
            d: dict[int, float] = {}
            for row in rows:
                if is_trades:
                    ts, _p, v = row
                    h = ts // HOUR * HOUR
                else:
                    ts, _c, _l, v = row
                    h = (ts - 1) // HOUR * HOUR  # candle END ts -> its hour bucket
                if ts >= anchor_min_ts:
                    d[h] = d.get(h, 0.0) + v
            per_source.append(d)
        vol_by_hour: dict[int, float] = {}
        for d in per_source:
            for h, v in d.items():
                if v > vol_by_hour.get(h, 0.0):
                    vol_by_hour[h] = v
        if not vol_by_hour or max(vol_by_hour.values()) <= 0:
            continue
        anchor = max(vol_by_hour, key=lambda h: vol_by_hour[h])
        window_start = max(anchor - f["window_pre_offset_h"] * HOUR, open_ts + DAY)
        window_end = min(window_start + f["window_hours"] * HOUR, eff_end)
        if window_end <= window_start:
            continue
        # ---- qualification: prior-24h time-weighted YES average -----------
        t0 = window_start - f["pre_avg_hours"] * HOUR
        for source in (one_min, hourly):
            pts = [(ts, c) for ts, c, _l, _v in source if ts <= window_start]
            avg, cov = time_weighted_avg(pts, t0, window_start)
            if avg is not None:
                break
        else:
            avg, cov = None, 0.0
        if avg is None and trades:
            pts = [(ts, p) for ts, p, _c in trades if ts <= window_start]
            avg, cov = time_weighted_avg(pts, t0, window_start)
        if avg is None or cov < f["pre_min_coverage_h"] or avg < f["pre_avg_min_cents"]:
            continue
        # ---- window stats: min price, volume, ladder depth ----------------
        w_trades = [(ts, p, c) for ts, p, c in trades if window_start <= ts <= window_end]
        w_1m = [r for r in one_min if window_start < r[0] <= window_end]
        w_hr = [r for r in hourly if window_start < r[0] <= window_end]
        trades_vol = sum(c for _t, _p, c in w_trades)
        m1_vol = sum(v for _t, _c, _l, v in w_1m)
        hr_vol = sum(v for _t, _c, _l, v in w_hr)
        window_volume = max(trades_vol, m1_vol, hr_vol)
        if window_volume < f["min_window_volume"]:
            continue
        # Best-resolution window low + depth. Depth from trades when they
        # cover the window, else from candle lows (volume of bars whose low
        # touched the level — an upper bound on true at-level depth, noted
        # in the report).
        trades_cover = trades_vol >= 0.5 * max(m1_vol, hr_vol, 1.0)
        lows: list[float] = []
        if w_trades:
            lows.append(min(p for _t, p, _c in w_trades))
        if w_1m:
            lows.append(min(l for _t, _c, l, v in w_1m if v > 0) if any(v > 0 for *_x, v in w_1m) else min(l for _t, _c, l, _v in w_1m))
        if w_hr:
            traded = [l for _t, _c, l, v in w_hr if v > 0]
            if traded:
                lows.append(min(traded))
        min_price = min(lows) if lows else None
        if w_trades and trades_cover:
            resolution = "trades"
            def depth(x):
                return sum(c for _t, p, c in w_trades if p <= x)
        elif w_1m:
            resolution = "1min"
            def depth(x):
                return sum(v for _t, _c, l, v in w_1m if v > 0 and l <= x)
        else:
            resolution = "hourly"
            def depth(x):
                return sum(v for _t, _c, l, v in w_hr if v > 0 and l <= x)
        outcome = classify_outcome(m["result"], min_price, f["dip_threshold_cents"])
        episodes.append(
            Episode(
                ticker=m["ticker"],
                event_ticker=m["event_ticker"] or "",
                series_ticker=m["series_ticker"] or m["ticker"].split("-", 1)[0],
                category=m["category"],
                title=m["title"] or "",
                result=m["result"],
                et_date=et_date(window_start + f["window_pre_offset_h"] * HOUR),
                window_start=window_start,
                window_end=window_end,
                anchor_ts=anchor,
                pre_avg_cents=round(avg, 2),
                pre_coverage_h=round(cov, 1),
                min_price_cents=round(min_price, 1) if min_price is not None else 100.0,
                window_volume=round(window_volume, 0),
                depth_le85=round(depth(85.0), 0),
                depth_le80=round(depth(80.0), 0),
                depth_le75=round(depth(75.0), 0),
                outcome_class=outcome,
                data_resolution=resolution,
                has_1min_window=bool(w_1m),
                trades_cover_window=bool(w_trades and trades_cover),
                settled_ts=settled_ts or close_ts,
                lifetime_volume=float(m["volume"] or 0),
                clustered=False,  # filled below
            )
        )
    conn.close()
    # ---- event-night clustering (volume/settlement timing only) -----------
    by_date: dict[str, set[str]] = {}
    for ep in episodes:
        by_date.setdefault(ep.et_date, set()).add(ep.event_ticker or ep.ticker)
    keep: list[Episode] = []
    for ep in episodes:
        clustered = len(by_date[ep.et_date]) >= FROZEN["cluster_min_events"]
        if clustered or ep.et_date in KNOWN_EVENT_NIGHTS:
            ep.clustered = clustered
            keep.append(ep)
    keep.sort(key=lambda e: (e.window_start, e.ticker))
    return keep


# ---------------------------------------------------------------------------
# Taxonomy summary + CLI
# ---------------------------------------------------------------------------

def taxonomy(episodes: list[Episode]) -> dict:
    classes = ("no-dip", "dip-and-revert", "dip-and-die", "died-without-dip")
    counts = {c: 0 for c in classes}
    for ep in episodes:
        counts[ep.outcome_class] += 1
    dipped = counts["dip-and-revert"] + counts["dip-and-die"]
    return {
        "n_episodes": len(episodes),
        "n_event_nights": len({ep.et_date for ep in episodes}),
        "counts": counts,
        "dip_rate": round(dipped / len(episodes), 4) if episodes else None,
        "adverse_selection_rate_episode": (
            round(counts["dip-and-die"] / dipped, 4) if dipped else None
        ),
        "died_total": counts["dip-and-die"] + counts["died-without-dip"],
    }


def main() -> None:
    eps = discover()
    out = {
        "frozen_params": FROZEN,
        "generated_at": _dt.datetime.utcnow().isoformat() + "Z",
        "taxonomy": taxonomy(eps),
        "episodes": [asdict(e) for e in eps],
    }
    path = "/home/user/c-g-trading/reports/panic/episodes.json"
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    t = out["taxonomy"]
    print(f"episodes={t['n_episodes']} nights={t['n_event_nights']} classes={t['counts']}")
    print(f"dip_rate={t['dip_rate']} adverse_selection(episode)={t['adverse_selection_rate_episode']}")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()

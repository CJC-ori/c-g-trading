"""Swing census over the full 5-year hourly tape (data/kalshi.db).

Chris's general hypothesis: prediction markets have semi-frequent large
temporary swings that are tradeable — beyond the narrow "panic-wick on a
>=95c favorite entering a scheduled event night" case already tested in
bot/strategies/panic/. This module scans the ENTIRE local hourly tape
(3.07M hourly candles, ~44k settled markets, 2021-07 -> 2026-08) with no
prior on market state or event schedule, and answers:

  1. How often do large fast swings happen (per config X cents / T hours)?
  2. How often do they revert (50% / full, within U hours / by settlement)?
  3. Conditional on fading the swing at various depths, what would you have
     earned per contract, and what fraction of swings settled WITH the move
     (the fade's unconditional adverse-selection rate)?

FROZEN DEFINITIONS — registered 2026-08-12 BEFORE any strategy backtest was
run (SPEC.md par.8 discipline; the SwingFade strategy's parameters are
derived from this census's *structure*, never from strategy P&L):

- PRICE PATH. Hourly mark m(t) per candle, exactly the dataport convention
  (bot/backtest/dataport.py): trade close rounded to the nearest cent when
  the hour printed a trade, else the bid/ask-close midpoint. 100% of
  candles carry quotes; 54.3% of hourly candles carry a trade price
  (bot/data/NOTES.md).
- SWING (direction=down). At hour t: ref = max m(s) over s in [t-T, t)
  (sliding-window extreme). Trigger at the first t where
  ref - m(t) >= X. direction=up is the mirror (ref = window min).
  ref is the "pre-swing reference level"; move0 = |ref - m(t0)|.
- HONESTY GATES at trigger (recorded, applied before an episode counts):
  (a) real liquidity: traded notional >= $500 inside [t0 - T, t0]
      (sum of candle volume x price / 100 — quote drift with no prints is
      not a swing anyone could trade);
  (b) two-sided book at both the ref candle and the trigger candle
      (0 < bid and ask < 100): an emptied book's mid jumping to 50 is a
      data artifact, not a price.
- REVERSION (measured forward from t0, retrospective by design):
  revert50 within U:   exists u <= t0+U with m(u) back through
                       ref -/+ move0/2 (the 50% retrace of the TRIGGER move).
  revert_full within U: m(u) back within FULL_TOL=2c of ref.
  U grid: {6, 24, 72} hours; plus "by settlement" via the payout.
- SETTLED-WITH-MOVE (was the move information?): payout (yes=100c, no=0c,
  scalar=v) ended on the move's side of the 50% retrace line. down-swing:
  payout < ref - move0/2. This is exactly the case where a fade held to
  settlement is adversely selected.
- FADE ACCOUNTING (per contract, YES-space cents, gross of fees — exact
  fees belong to the engine backtest in strategy.py):
  entry depths d in {0, 5, 10}c beyond the trigger price:
    d=0: taker at the trigger candle's quote (ask for a down-swing fade,
         bid for an up-swing fade) — chosen to be brutal: you pay the
         spread to react instantly;
    d>0: resting maker order at m(t0) - d (down) / m(t0) + d (up), counted
         FILLED only if a later candle within FILL_WINDOW_H=24h both
         printed volume and traded strictly through the limit (trade-low
         <= limit-1), per the harness's conservative candle-fill rule.
  exit: resting order at the 50% retrace target; if the target trades
        within EXIT_HOLD_H=72h of t0 the exit fills AT the target price,
        else the position rides to settlement and takes the payout.
        No stop-loss (binary market, sized for total loss — same doctrine
        as panic R4).
- CAPACITY per depth: 25% of the volume printed in candles that traded
  strictly through the fade limit within FILL_WINDOW_H (SPEC par.3's
  we-can't-be-most-of-the-tape cap) — the honest "you could have been
  filled" number, as opposed to "price swung".
- COOLDOWN. After an episode triggers, scanning (per direction) resumes
  only once the episode closes — first 50% reversion or t0 + 72h,
  whichever is first — with the lookback window cleared. One displacement
  is one episode, never several.
- UNIVERSE. Settled markets only (result in yes/no/scalar) — the census
  needs the settlement to score adverse selection. The local tape is a
  *sample* of the exchange (full weather history; Politics/Elections/
  Economics deep series; volume-filtered pulls elsewhere — bot/data/
  NOTES.md); swings/week is therefore a LOWER bound on the exchange-wide
  rate, reported both per calendar week and per 1000 market-weeks of
  covered tape.

Outputs (reports/swings/):
  census.json        aggregate tables per config and per cut
  episodes.jsonl.gz  every episode, one JSON per line (all configs)

Usage:
  python -m bot.strategies.swings.census [--db data/kalshi.db]
      [--sample N] [--configs X:T,X:T,...]
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import sqlite3
from bisect import bisect_left, bisect_right
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Iterator

HOUR = 3600
WEEK = 7 * 24 * HOUR

# ---- frozen census parameters (see module docstring) -----------------------
X_GRID = (10, 15, 20, 30)          # swing size, cents
T_GRID = (1, 6, 24)                # swing window, hours
U_GRID = (6, 24, 72)               # reversion horizons, hours
MIN_NOTIONAL_DOLLARS = 500.0       # traded notional in [t0-T, t0]
FULL_TOL_C = 2                     # full reversion tolerance, cents
FADE_DEPTHS_C = (0, 5, 10)         # fade entry depth beyond trigger, cents
FILL_WINDOW_H = 24                 # maker depth-fill / capacity window
EXIT_HOLD_H = 72                   # fade exit window before riding to settle
COOLDOWN_MAX_H = 72                # episode closes at revert50 or +72h
CAPTURE_FRAC = 0.25                # SPEC par.3 volume cap

PRICE_BINS = ((0, 10), (10, 30), (30, 70), (70, 90), (90, 100))
DTC_BINS_D = ((0, 1), (1, 7), (7, 30), (30, 10_000))
LIQ_TIERS = ((500, 2_000), (2_000, 10_000), (10_000, float("inf")))
NEAR_CLOSE_H = 48                  # "scheduled-event-ish": t0 within 48h of close


@dataclass(frozen=True, slots=True)
class Hour:
    """One hourly observation on the census price path."""
    ts: int              # candle end ts
    mid: int             # mark, cents (dataport convention)
    bid: int | None
    ask: int | None
    trade_low: int | None   # only when the hour printed trades
    trade_high: int | None
    volume: int          # contracts
    notional: float      # dollars traded this hour


@dataclass(slots=True)
class Episode:
    ticker: str
    x: int
    t_h: int
    direction: str            # "down" | "up"
    ts_ref: int
    ts0: int                  # trigger hour (candle end ts)
    ref_c: int
    trig_c: int
    move_c: int
    spread0_c: int | None
    notional_window: float    # dollars traded in [t0-T, t0]
    # classification
    category: str = ""
    series: str = ""
    price_bin: str = ""
    dtc_days: float = 0.0
    dtc_bin: str = ""
    liq_tier: str = ""
    near_close: bool = False
    result: str = ""          # yes|no|scalar
    payout_c: int = 0
    settled_with_move: bool = False
    # reversion
    revert50_by: dict[int, bool] = field(default_factory=dict)
    revert_full_by: dict[int, bool] = field(default_factory=dict)
    reverted50_by_settle: bool = False
    time_to_50_h: float | None = None
    post_extreme_c: int = 0   # most adverse mid within FILL_WINDOW_H after t0
    # fades: depth -> record
    fades: dict[int, dict[str, Any]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# pure census math (unit-tested in test_census.py)
# ---------------------------------------------------------------------------

def detect_swings(hours: list[Hour], x: int, t_h: int, direction: str,
                  min_notional: float = MIN_NOTIONAL_DOLLARS) -> list[int]:
    """Return trigger indices for (x, t_h, direction) with cooldown applied.

    ref for "down" = sliding max of mid over [ts_i - t_h*3600, ts_i);
    trigger when ref - mid_i >= x (mirror for "up"). Gates: window traded
    notional >= min_notional; two-sided book at ref and trigger candles.
    After a trigger the scan resumes at the episode close (first 50%
    retrace of the trigger move or +COOLDOWN_MAX_H) with the window
    cleared.
    """
    sign = 1 if direction == "down" else -1
    # value to maximize: sign*mid (max mid for down, min mid for up)
    n = len(hours)
    ts = [h.ts for h in hours]
    pre_notional = [0.0]
    for h in hours:
        pre_notional.append(pre_notional[-1] + h.notional)

    out: list[int] = []
    dq: deque[int] = deque()   # indices, sign*mid decreasing
    i = 0
    while i < n:
        h = hours[i]
        lo_ts = h.ts - t_h * HOUR
        while dq and hours[dq[0]].ts < lo_ts:
            dq.popleft()
        if dq:
            j = dq[0]
            ref = hours[j].mid
            if sign * (ref - h.mid) >= x:
                # honesty gates
                lo_i = bisect_left(ts, lo_ts, 0, i)
                notional = pre_notional[i + 1] - pre_notional[lo_i]
                two_sided = (
                    h.bid is not None and h.ask is not None
                    and 0 < h.bid and h.ask < 100
                    and hours[j].bid is not None and hours[j].ask is not None
                    and 0 < hours[j].bid and hours[j].ask < 100
                )
                if notional >= min_notional and two_sided:
                    out.append(i)
                    # cooldown: resume after episode close, clear window
                    move0 = abs(ref - h.mid)
                    target = ref - sign * math.ceil(move0 / 2)
                    close_i = i + 1
                    limit_ts = h.ts + COOLDOWN_MAX_H * HOUR
                    while close_i < n and hours[close_i].ts <= limit_ts:
                        if sign * (hours[close_i].mid - target) >= 0:
                            break
                        close_i += 1
                    dq.clear()
                    i = close_i
                    continue
        # push i for future windows (mid always present)
        v = sign * h.mid
        while dq and sign * hours[dq[-1]].mid <= v:
            dq.pop()
        dq.append(i)
        i += 1
    return out


def measure_episode(hours: list[Hour], i0: int, x: int, t_h: int,
                    direction: str, ref_i: int | None = None) -> Episode:
    """Measure reversion / fade outcomes for a trigger at index i0.

    ref is re-derived (window max/min over [ts0 - t_h, ts0)) unless ref_i
    given. Settlement fields are filled in by the caller (annotate()).
    """
    sign = 1 if direction == "down" else -1
    h0 = hours[i0]
    lo_ts = h0.ts - t_h * HOUR
    if ref_i is None:
        best = None
        for j in range(i0 - 1, -1, -1):
            if hours[j].ts < lo_ts:
                break
            if best is None or sign * hours[j].mid > sign * hours[best].mid:
                best = j
        assert best is not None, "no reference candle in window"
        ref_i = best
    ref = hours[ref_i].mid
    move0 = abs(ref - h0.mid)
    target50 = ref - sign * (move0 / 2)      # float, 50% retrace line
    full_edge = ref - sign * FULL_TOL_C

    lo_i = bisect_left([h.ts for h in hours], lo_ts, 0, i0)
    notional = sum(h.notional for h in hours[lo_i:i0 + 1])

    ep = Episode(
        ticker="",  # caller sets via annotate()
        x=x, t_h=t_h, direction=direction,
        ts_ref=hours[ref_i].ts, ts0=h0.ts,
        ref_c=ref, trig_c=h0.mid, move_c=move0,
        spread0_c=(h0.ask - h0.bid) if (h0.ask is not None and h0.bid is not None) else None,
        notional_window=round(notional, 2),
    )

    t50: float | None = None       # first 50%-retrace time, hours after t0
    t_full: float | None = None    # first full-reversion time
    post_extreme = h0.mid
    fill_through: dict[int, tuple[bool, int]] = {}   # depth -> (filled, capturable vol)
    for d in FADE_DEPTHS_C:
        fill_through[d] = (d == 0, 0)

    max_u = max(U_GRID)
    for j in range(i0 + 1, len(hours)):
        dt_h = (hours[j].ts - h0.ts) / HOUR
        if dt_h > max_u:
            break
        m = hours[j].mid
        if t50 is None and sign * (m - target50) >= 0:
            t50 = dt_h
        if t_full is None and sign * (m - full_edge) >= 0:
            t_full = dt_h
        if dt_h <= FILL_WINDOW_H:
            # most adverse mid (further in move direction)
            if direction == "down":
                post_extreme = min(post_extreme, m)
            else:
                post_extreme = max(post_extreme, m)
            # maker depth fills: trade strictly through the limit
            if hours[j].volume > 0:
                for d in FADE_DEPTHS_C:
                    if d == 0:
                        continue
                    limit = h0.mid - sign * d
                    if not (1 <= limit <= 99):
                        continue
                    filled, cap = fill_through[d]
                    through = (
                        hours[j].trade_low is not None
                        and hours[j].trade_low <= limit - 1
                        if direction == "down"
                        else hours[j].trade_high is not None
                        and hours[j].trade_high >= limit + 1
                    )
                    if through:
                        fill_through[d] = (True, cap + hours[j].volume)

    ep.revert50_by = {u: (t50 is not None and t50 <= u) for u in U_GRID}
    ep.revert_full_by = {u: (t_full is not None and t_full <= u) for u in U_GRID}
    ep.time_to_50_h = t50
    ep.post_extreme_c = post_extreme

    # fade P&L per depth (exit filled in after settlement annotation if needed)
    for d in FADE_DEPTHS_C:
        filled, vol_through = fill_through[d]
        if d == 0:
            entry = h0.ask if direction == "down" else h0.bid
            if entry is None or not (1 <= entry <= 99):
                entry = h0.mid
            # capacity at d=0: prints at/beyond the trigger price within window
            cap_vol = 0
            for h in hours[i0 + 1:]:
                if h.ts - h0.ts > FILL_WINDOW_H * HOUR:
                    break
                if h.volume > 0 and (
                    (h.trade_low is not None and h.trade_low <= h0.mid)
                    if direction == "down"
                    else (h.trade_high is not None and h.trade_high >= h0.mid)
                ):
                    cap_vol += h.volume
        else:
            entry = h0.mid - sign * d
            cap_vol = vol_through
        rec: dict[str, Any] = {
            "filled": bool(filled) and 1 <= entry <= 99,
            "entry_c": entry,
            "capturable_contracts": int(cap_vol * CAPTURE_FRAC),
        }
        if rec["filled"]:
            if t50 is not None and t50 <= EXIT_HOLD_H:
                exit_c = target50
                rec["exit_kind"] = "revert50"
            else:
                exit_c = None       # rides to settlement; caller adds payout
                rec["exit_kind"] = "settlement"
            if exit_c is not None:
                rec["pnl_c"] = round(sign * (exit_c - entry), 2)
                rec["exit_c"] = exit_c
        ep.fades[d] = rec
    return ep


def _bin_label(v: float, bins) -> str:
    for lo, hi in bins:
        if lo <= v < hi:
            return f"{lo}-{hi}" if hi != float("inf") and hi < 10_000 else f"{lo}+"
    return f"{bins[-1][0]}+"


def annotate(ep: Episode, meta: dict[str, Any]) -> None:
    """Attach market metadata + settlement-dependent fields."""
    ep.ticker = meta["ticker"]
    ep.category = meta["category"] or ""
    ep.series = meta["series_ticker"] or ""
    ep.result = meta["result"]
    ep.payout_c = meta["payout_c"]
    sign = 1 if ep.direction == "down" else -1
    target50 = ep.ref_c - sign * (ep.move_c / 2)
    ep.settled_with_move = sign * (target50 - ep.payout_c) > 0
    ep.reverted50_by_settle = (
        any(ep.revert50_by.values()) or sign * (ep.payout_c - target50) >= 0
    )
    ep.price_bin = _bin_label(ep.ref_c, PRICE_BINS)
    close_ts = meta["close_ts"]
    ep.dtc_days = round(max(0.0, (close_ts - ep.ts0) / 86400), 2) if close_ts else 0.0
    ep.dtc_bin = _bin_label(ep.dtc_days, DTC_BINS_D)
    ep.liq_tier = _bin_label(ep.notional_window, LIQ_TIERS)
    ep.near_close = bool(close_ts) and (close_ts - ep.ts0) <= NEAR_CLOSE_H * HOUR
    # settlement-exit fades
    for d, rec in ep.fades.items():
        if rec.get("filled") and rec.get("exit_kind") == "settlement":
            rec["exit_c"] = ep.payout_c
            rec["pnl_c"] = round(sign * (ep.payout_c - rec["entry_c"]), 2)


# ---------------------------------------------------------------------------
# data loading
# ---------------------------------------------------------------------------

def _cents_round(dollars: float) -> int:
    return math.floor(round(dollars * 100, 6) + 0.5)


def _cents_floor(dollars: float) -> int:
    return math.floor(round(dollars * 100, 6))


def _cents_ceil(dollars: float) -> int:
    return math.ceil(round(dollars * 100, 6))


def load_market_meta(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    meta: dict[str, dict[str, Any]] = {}
    q = """
      SELECT ticker, category, series_ticker, result, settlement_value,
             close_time, volume
      FROM markets WHERE result IN ('yes','no','scalar')
    """
    import datetime as dt

    def iso(v):
        if not v:
            return None
        try:
            return int(dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp())
        except ValueError:
            return None

    for r in conn.execute(q):
        result = r[3]
        payout = 100 if result == "yes" else 0
        if result == "scalar":
            payout = max(0, min(100, _cents_round(r[4] or 0.0)))
        meta[r[0]] = {
            "ticker": r[0], "category": r[1], "series_ticker": r[2],
            "result": result, "payout_c": payout, "close_ts": iso(r[5]),
            "volume": r[6] or 0.0,
        }
    return meta


def iter_hour_series(conn: sqlite3.Connection,
                     tickers: set[str] | None = None
                     ) -> Iterator[tuple[str, list[Hour]]]:
    """Stream (ticker, hourly Hour list) from the store, dataport-convention
    marks. Candles missing both a trade close and a quote close are dropped.
    """
    q = """
      SELECT ticker, ts, price_low, price_high, price_close,
             yes_bid_close, yes_ask_close, volume
      FROM candlesticks WHERE period_interval = 60
      ORDER BY ticker, ts
    """
    cur_ticker: str | None = None
    buf: list[Hour] = []
    for (tkr, ts, plo, phi, pcl, bidc, askc, vol) in conn.execute(q):
        if tickers is not None and tkr not in tickers:
            continue
        if tkr != cur_ticker:
            if cur_ticker is not None and buf:
                yield cur_ticker, buf
            cur_ticker, buf = tkr, []
        bid = _cents_floor(bidc) if bidc is not None else None
        ask = _cents_ceil(askc) if askc is not None else None
        if pcl is not None:
            mid = _cents_round(pcl)
            tlo = _cents_ceil(plo) if plo is not None else None
            thi = _cents_floor(phi) if phi is not None else None
        else:
            if bid is None or ask is None:
                continue
            mid = _cents_round((bidc + askc) / 2)
            tlo = thi = None
        v = int(vol or 0)
        if pcl is not None:
            price_for_notional = pcl
        elif bidc is not None and askc is not None:
            price_for_notional = (bidc + askc) / 2
        else:
            price_for_notional = 0.5
        buf.append(Hour(
            ts=int(ts), mid=int(mid), bid=bid, ask=ask,
            trade_low=tlo, trade_high=thi, volume=v,
            notional=v * float(price_for_notional),
        ))
    if cur_ticker is not None and buf:
        yield cur_ticker, buf


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------

def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _dist(vals: list[float]) -> dict[str, float] | None:
    if not vals:
        return None
    vs = sorted(vals)
    n = len(vs)
    return {"n": n, "min": round(vs[0], 2), "p25": round(vs[n // 4], 2),
            "median": round(vs[n // 2], 2), "p75": round(vs[(3 * n) // 4], 2),
            "max": round(vs[-1], 2), "mean": round(sum(vs) / n, 3)}


def _cell_stats(eps: list[Episode]) -> dict[str, Any]:
    n = len(eps)
    settled_with = sum(1 for e in eps if e.settled_with_move)
    out: dict[str, Any] = {
        "n": n,
        "revert50": {f"U{u}": _rate(sum(1 for e in eps if e.revert50_by[u]), n) for u in U_GRID},
        "revert_full": {f"U{u}": _rate(sum(1 for e in eps if e.revert_full_by[u]), n) for u in U_GRID},
        "revert50_by_settle": _rate(sum(1 for e in eps if e.reverted50_by_settle), n),
        "settled_with_move": _rate(settled_with, n),
        "median_move_c": _dist([e.move_c for e in eps])["median"] if n else None,
        "median_spread0_c": _dist([e.spread0_c for e in eps if e.spread0_c is not None])["median"] if any(e.spread0_c is not None for e in eps) else None,
        "median_time_to_50_h": _dist([e.time_to_50_h for e in eps if e.time_to_50_h is not None])["median"] if any(e.time_to_50_h is not None for e in eps) else None,
    }
    fades = {}
    for d in FADE_DEPTHS_C:
        recs = [e.fades[d] for e in eps if e.fades.get(d, {}).get("filled") and "pnl_c" in e.fades[d]]
        pnls = [r["pnl_c"] for r in recs]
        caps = [r["capturable_contracts"] for r in recs]
        fades[f"d{d}"] = {
            "n_filled": len(recs),
            "fill_rate": _rate(len(recs), n),
            "pnl_c": _dist(pnls),
            "pct_pos": _rate(sum(1 for p in pnls if p > 0), len(pnls)),
            "capturable_contracts": _dist([float(c) for c in caps]),
            "capturable_dollar_pnl": _dist([
                r["pnl_c"] / 100 * r["capturable_contracts"] for r in recs
            ]),
        }
    out["fades"] = fades
    return out


def aggregate(episodes: list[Episode], span_weeks: float,
              market_weeks: float) -> dict[str, Any]:
    by_cfg: dict[tuple[int, int], list[Episode]] = {}
    for e in episodes:
        by_cfg.setdefault((e.x, e.t_h), []).append(e)
    out: dict[str, Any] = {
        "span_weeks": round(span_weeks, 1),
        "market_weeks_covered": round(market_weeks, 0),
        "configs": {},
    }
    for (x, t_h), eps in sorted(by_cfg.items()):
        key = f"X{x}_T{t_h}"
        cfg: dict[str, Any] = _cell_stats(eps)
        cfg["per_calendar_week"] = round(len(eps) / span_weeks, 2)
        cfg["per_1000_market_weeks"] = round(len(eps) / market_weeks * 1000, 2)
        for dim, keyfn in (
            ("by_direction", lambda e: e.direction),
            ("by_category", lambda e: e.category or "?"),
            ("by_price_bin", lambda e: e.price_bin),
            ("by_dtc_bin", lambda e: e.dtc_bin),
            ("by_liq_tier", lambda e: e.liq_tier),
            ("by_near_close", lambda e: "near_close<=48h" if e.near_close else "far_from_close"),
        ):
            groups: dict[str, list[Episode]] = {}
            for e in eps:
                groups.setdefault(keyfn(e), []).append(e)
            cfg[dim] = {
                k: _cell_stats(v) for k, v in sorted(
                    groups.items(), key=lambda kv: -len(kv[1]))
            }
        out["configs"][key] = cfg
    return out


# ---------------------------------------------------------------------------
# shortlist rule (pre-registered BEFORE the strategy existed)
# ---------------------------------------------------------------------------

SHORTLIST_RULE = (
    "Rank cells (config x direction x category x price_bin) by "
    "score = revert50@U24 * episodes_per_week * median capturable $ at d=5; "
    "require n >= 30, settled_with_move <= 0.35, median spread at trigger "
    "<= 10c. Top cells parameterize SwingFade; frozen from census structure, "
    "never from strategy P&L."
)


def shortlist(episodes: list[Episode], span_weeks: float,
              min_n: int = 30) -> list[dict[str, Any]]:
    cells: dict[tuple, list[Episode]] = {}
    for e in episodes:
        cells.setdefault((e.x, e.t_h, e.direction, e.category, e.price_bin), []).append(e)
    rows = []
    for key, eps in cells.items():
        n = len(eps)
        if n < min_n:
            continue
        stats = _cell_stats(eps)
        r50 = stats["revert50"]["U24"] or 0.0
        swm = stats["settled_with_move"]
        if swm is None:  # no settled episodes: treat as maximally adverse
            swm = 1.0
        spread = stats["median_spread0_c"]
        d5 = stats["fades"]["d5"]
        cap_pnl = (d5["capturable_dollar_pnl"] or {}).get("median", 0.0) or 0.0
        per_week = n / span_weeks
        if swm > 0.35 or (spread is not None and spread > 10):
            continue
        rows.append({
            "cell": {"x": key[0], "t_h": key[1], "direction": key[2],
                     "category": key[3], "price_bin": key[4]},
            "n": n, "per_week": round(per_week, 3),
            "revert50_U24": r50, "settled_with_move": swm,
            "median_spread0_c": spread,
            "d5_fill_rate": d5["fill_rate"],
            "d5_median_pnl_c": (d5["pnl_c"] or {}).get("median"),
            "d5_median_capturable_dollar_pnl": cap_pnl,
            "score": round(r50 * per_week * max(cap_pnl, 0.0), 4),
        })
    rows.sort(key=lambda r: -r["score"])
    return rows


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def run_census(db_path: str, out_dir: str, sample: int | None = None,
               configs: list[tuple[int, int]] | None = None) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    meta = load_market_meta(conn)
    cfgs = configs or [(x, t) for x in X_GRID for t in T_GRID]

    span = conn.execute(
        "SELECT MIN(ts), MAX(ts), COUNT(*) FROM candlesticks WHERE period_interval=60"
    ).fetchone()
    span_weeks = (span[1] - span[0]) / WEEK
    market_weeks = span[2] / (24 * 7)

    episodes: list[Episode] = []
    n_tickers = 0
    for ticker, hours in iter_hour_series(conn):
        m = meta.get(ticker)
        if m is None:
            continue                      # unsettled: not scoreable
        n_tickers += 1
        if sample is not None and n_tickers > sample:
            break
        for (x, t_h) in cfgs:
            for direction in ("down", "up"):
                for i0 in detect_swings(hours, x, t_h, direction):
                    ep = measure_episode(hours, i0, x, t_h, direction)
                    annotate(ep, m)
                    episodes.append(ep)
        if n_tickers % 5000 == 0:
            print(f"...{n_tickers} tickers, {len(episodes)} episodes")

    agg = aggregate(episodes, span_weeks, market_weeks)
    agg["n_tickers_scanned"] = n_tickers
    agg["n_episodes_total"] = len(episodes)
    agg["shortlist_rule"] = SHORTLIST_RULE
    agg["shortlist"] = shortlist(episodes, span_weeks)

    import os
    os.makedirs(out_dir, exist_ok=True)
    with open(f"{out_dir}/census.json", "w") as fh:
        json.dump(agg, fh, indent=1)
    with gzip.open(f"{out_dir}/episodes.jsonl.gz", "wt") as fh:
        for e in episodes:
            d = asdict(e)
            fh.write(json.dumps(d) + "\n")
    print(f"wrote {out_dir}/census.json ({len(episodes)} episodes, "
          f"{n_tickers} tickers)")
    return agg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/kalshi.db")
    ap.add_argument("--out-dir", default="reports/swings")
    ap.add_argument("--sample", type=int, default=None)
    ap.add_argument("--configs", default=None,
                    help="comma list like 20:6,30:1 (X cents:T hours)")
    args = ap.parse_args()
    cfgs = None
    if args.configs:
        cfgs = [tuple(int(v) for v in c.split(":")) for c in args.configs.split(",")]
    run_census(args.db, args.out_dir, sample=args.sample, configs=cfgs)


if __name__ == "__main__":
    main()

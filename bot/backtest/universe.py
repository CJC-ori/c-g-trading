"""Reusable market-universe filters over data/kalshi.db.

A "universe" is just a deterministic, documented list of tickers that gets
fed to the engine via ``iter_markets(tickers=...)``. Keeping selection out
of the engine keeps runs comparable: two strategies backtested on the same
universe saw exactly the same markets.

The default **research universe** (`research_universe()`) is:

- settled outcomes only (``result`` in yes/no/scalar; this includes
  status='determined', where the outcome is known but payout pending);
- non-sports, non-crypto (category filter), and none of the auto-generated
  parlay / high-frequency strike-ladder series
  (``bot.data.pull.DEFAULT_EXCLUDE_PREFIXES``, which starts with KXMVE);
- volume >= 1000 contracts. Kalshi volume is denominated in contracts, and
  a contract's max payout is $1, so 1000 contracts ~ >=$1k max notional
  ("volume > $1k" in the task's phrasing);
- markets that actually have hourly candles in the store. The pull sampled
  candles for ~300 markets (stratified + the pinned Michigan complex);
  a market without candles generates zero decision points, so including it
  would only pad the market count without adding information. This makes
  the universe a property of the *local dataset*, not of Kalshi — rerun the
  candle pull to widen it.

Results are sorted by ticker so every universe is reproducible.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Sequence

from bot.data.pull import DEFAULT_EXCLUDE_PREFIXES

DEFAULT_DB_PATH = "data/kalshi.db"

#: result values that mean "this market paid out" (see bot/data/NOTES.md:
#: '' means active or closed-without-settling, never a payout).
SETTLED_RESULTS = ("yes", "no", "scalar")

#: Categories excluded from the default research universe. Sports dominates
#: raw counts and has its own microstructure; crypto is the 15-minute ladder
#: spam tier even after prefix filters.
DEFAULT_EXCLUDE_CATEGORIES = ("Sports", "Crypto")


@dataclass(frozen=True)
class UniverseConfig:
    """Declarative market filter. All fields optional; defaults = research
    universe. Times are unix seconds (converted internally to the store's
    RFC3339 'Z' strings, which compare lexicographically)."""

    categories: tuple[str, ...] | None = None  # None = all
    exclude_categories: tuple[str, ...] = DEFAULT_EXCLUDE_CATEGORIES
    exclude_series_prefixes: tuple[str, ...] = DEFAULT_EXCLUDE_PREFIXES
    settled_only: bool = True
    close_after: int | None = None   # close_time >  this unix ts
    close_before: int | None = None  # close_time <  this unix ts
    min_volume: float = 1000.0       # contracts (~$ max notional)
    require_candles: bool = True     # must have hourly candles locally
    candle_period_s: int = 3600
    # Price band at a reference time: keep only markets whose bid/ask-mid at
    # the latest candle <= ref_ts is inside [lo_cents, hi_cents].
    price_band: tuple[int, int] | None = None
    price_band_ref_ts: int | None = None


def _iso_z(ts: int) -> str:
    import datetime as dt

    return (
        dt.datetime.fromtimestamp(ts, dt.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def select_universe(
    db_path: str = DEFAULT_DB_PATH, config: UniverseConfig | None = None
) -> list[str]:
    """Return the sorted ticker list matching ``config`` (default: research
    universe). Read-only; safe against a live store."""
    cfg = config or UniverseConfig()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        clauses: list[str] = []
        params: list = []
        if cfg.settled_only:
            clauses.append(
                f"result IN ({','.join('?' * len(SETTLED_RESULTS))})"
            )
            params.extend(SETTLED_RESULTS)
        if cfg.categories is not None:
            clauses.append(f"category IN ({','.join('?' * len(cfg.categories))})")
            params.extend(cfg.categories)
        for cat in cfg.exclude_categories:
            clauses.append("(category IS NULL OR category != ?)")
            params.append(cat)
        for prefix in cfg.exclude_series_prefixes:
            clauses.append("series_ticker NOT LIKE ?")
            params.append(prefix + "%")
        if cfg.close_after is not None:
            clauses.append("close_time > ?")
            params.append(_iso_z(cfg.close_after))
        if cfg.close_before is not None:
            clauses.append("close_time < ?")
            params.append(_iso_z(cfg.close_before))
        if cfg.min_volume:
            clauses.append("volume >= ?")
            params.append(cfg.min_volume)
        if cfg.require_candles:
            clauses.append(
                "ticker IN (SELECT DISTINCT ticker FROM candlesticks"
                " WHERE period_interval = ?)"
            )
            params.append(cfg.candle_period_s // 60)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        tickers = [
            r[0]
            for r in conn.execute(
                f"SELECT ticker FROM markets {where} ORDER BY ticker", params
            )
        ]
        if cfg.price_band is not None:
            if cfg.price_band_ref_ts is None:
                raise ValueError("price_band requires price_band_ref_ts")
            tickers = _filter_price_band(
                conn,
                tickers,
                cfg.price_band_ref_ts,
                cfg.price_band[0],
                cfg.price_band[1],
                cfg.candle_period_s // 60,
            )
        return tickers
    finally:
        conn.close()


def _filter_price_band(
    conn: sqlite3.Connection,
    tickers: Sequence[str],
    ref_ts: int,
    lo_cents: int,
    hi_cents: int,
    period_interval: int,
) -> list[str]:
    """Keep tickers whose bid/ask midpoint at the latest candle at or before
    ``ref_ts`` lies in [lo_cents, hi_cents]. Markets with no candle at or
    before ref_ts are dropped (their price then is unknowable)."""
    out = []
    for t in tickers:
        r = conn.execute(
            "SELECT yes_bid_close, yes_ask_close FROM candlesticks"
            " WHERE ticker = ? AND period_interval = ? AND ts <= ?"
            " ORDER BY ts DESC LIMIT 1",
            (t, period_interval, ref_ts),
        ).fetchone()
        if r is None or r[0] is None or r[1] is None:
            continue
        mid_cents = (r[0] + r[1]) / 2 * 100
        if lo_cents <= mid_cents <= hi_cents:
            out.append(t)
    return out


def research_universe(db_path: str = DEFAULT_DB_PATH) -> list[str]:
    """The documented default: settled, non-sports/non-crypto, no parlay or
    HF-ladder series, volume >= 1000 contracts, hourly candles present."""
    return select_universe(db_path, UniverseConfig())

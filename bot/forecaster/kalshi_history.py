"""Candle backfill + HistoryProvider for the rung-3 universe.

The shared ``data/kalshi.db`` store has hourly candles for only a sliver of
the Feb–May-2026 settled universe (it was pulled for the FLB endgame
studies), so rung 3 fetches its own hourly candles from the public Kalshi
API into a private sqlite file (``data/forecaster-cache/rung3/candles.db``)
with the SAME ``candlesticks`` schema/units as bot/data's store, and serves
them through a provider that inherits every row-adapter convention from
``bot.backtest.dataport.SqliteHistoryProvider`` (markets/settlements still
come from the main DB; trades are empty ⇒ the engine's conservative
candles-only maker fill rule applies).

The shared store is NOT written to (it is concurrently edited).
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Sequence

import certifi
import requests

from bot.backtest.dataport import SqliteHistoryProvider
from bot.backtest.types import Candle, Trade

REPO = Path(__file__).resolve().parents[2]
CANDLES_DB = REPO / "data" / "forecaster-cache" / "rung3" / "candles.db"
API = "https://api.elections.kalshi.com/trade-api/v2"
UA = {"User-Agent": "c-g-trading-research/0.1 (backtest backfill; polite)"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candlesticks (
    ticker TEXT NOT NULL,
    period_interval INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    price_open REAL, price_high REAL, price_low REAL, price_close REAL,
    price_mean REAL, price_previous REAL,
    yes_bid_open REAL, yes_bid_high REAL, yes_bid_low REAL, yes_bid_close REAL,
    yes_ask_open REAL, yes_ask_high REAL, yes_ask_low REAL, yes_ask_close REAL,
    volume REAL, open_interest REAL, source TEXT,
    PRIMARY KEY (ticker, period_interval, ts)
);
CREATE TABLE IF NOT EXISTS backfill_done (
    ticker TEXT NOT NULL, period_interval INTEGER NOT NULL,
    start_ts INTEGER, end_ts INTEGER, n INTEGER,
    PRIMARY KEY (ticker, period_interval)
);
"""


def _f(x) -> float | None:
    return None if x is None else float(x)


def backfill_candles(
    series_ticker: str, ticker: str, start_ts: int, end_ts: int,
    period_interval: int = 60, db_path: Path = CANDLES_DB,
    min_spacing_s: float = 2.5, max_attempts: int = 8,
) -> int:
    """Fetch hourly candles for one market into the private store.
    Idempotent (skips when already backfilled). Returns candle count."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=60)
    try:
        con.executescript(_SCHEMA)
        done = con.execute(
            "SELECT n FROM backfill_done WHERE ticker=? AND period_interval=?",
            (ticker, period_interval),
        ).fetchone()
        if done is not None:
            return int(done[0])
        rows: list[tuple] = []

        def g(d: dict, key: str):
            # live endpoint uses "<key>_dollars", historical uses "<key>"
            v = d.get(f"{key}_dollars")
            return v if v is not None else d.get(key)

        # the API caps ~5000 candles/request; hourly over <200d fits in one
        chunk = 4900 * period_interval * 60
        t0 = start_ts
        while t0 < end_ts:
            t1 = min(end_ts, t0 + chunk)
            # the /historical/ endpoint serves delisted markets too and
            # needs no series ticker (the live /series/ path 404s once a
            # market is pruned from the current API)
            url = (f"{API}/historical/markets/{ticker}/candlesticks"
                   f"?start_ts={t0}&end_ts={t1}&period_interval={period_interval}")
            backoff = 5.0
            for _ in range(max_attempts):
                time.sleep(min_spacing_s)
                try:
                    # api.elections.kalshi.com is tunneled (not MITM'd) by
                    # the egress proxy, so the proxy CA bundle in
                    # REQUESTS_CA_BUNDLE cannot verify it — use certifi.
                    r = requests.get(url, headers=UA, timeout=60,
                                     verify=certifi.where())
                except requests.RequestException:
                    time.sleep(backoff)
                    backoff *= 1.8
                    continue
                if r.status_code != 200:
                    print(f"[backfill] {ticker}: HTTP {r.status_code}, "
                          f"backing off {backoff:.0f}s", flush=True)
                if r.status_code == 404:
                    raise RuntimeError(f"404 for {ticker} candlesticks")
                if r.status_code == 200:
                    for c in r.json().get("candlesticks", []):
                        p = c.get("price") or {}
                        yb = c.get("yes_bid") or {}
                        ya = c.get("yes_ask") or {}
                        rows.append((
                            ticker, period_interval, int(c["end_period_ts"]),
                            _f(g(p, "open")), _f(g(p, "high")),
                            _f(g(p, "low")), _f(g(p, "close")),
                            _f(g(p, "mean")), _f(g(p, "previous")),
                            _f(g(yb, "open")), _f(g(yb, "high")),
                            _f(g(yb, "low")), _f(g(yb, "close")),
                            _f(g(ya, "open")), _f(g(ya, "high")),
                            _f(g(ya, "low")), _f(g(ya, "close")),
                            _f(c.get("volume_fp") or c.get("volume")),
                            _f(c.get("open_interest_fp") or c.get("open_interest")),
                            "rung3-backfill",
                        ))
                    break
                time.sleep(backoff)
                backoff *= 1.8
            else:
                raise RuntimeError(f"candlestick backfill failed for {ticker}")
            t0 = t1
        con.executemany(
            "INSERT OR REPLACE INTO candlesticks VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        con.execute(
            "INSERT OR REPLACE INTO backfill_done VALUES (?,?,?,?,?)",
            (ticker, period_interval, start_ts, end_ts, len(rows)))
        con.commit()
        return len(rows)
    finally:
        con.close()


class Rung3HistoryProvider(SqliteHistoryProvider):
    """Markets/settlements from the shared read-only store; candles from the
    private backfill store; no trades (candles-only maker fills)."""

    def __init__(self, db_path: str, candles_db: Path = CANDLES_DB):
        super().__init__(db_path)
        self._candles_db = candles_db
        self._cconn: sqlite3.Connection | None = None

    def _candles_conn(self) -> sqlite3.Connection:
        if self._cconn is None:
            self._cconn = sqlite3.connect(
                f"file:{self._candles_db}?mode=ro", uri=True)
            self._cconn.row_factory = sqlite3.Row
        return self._cconn

    def candles(
        self, ticker: str, period_s: int, until: int | None = None
    ) -> Sequence[Candle]:
        if period_s % 60 != 0:
            return []
        conn = self._candles_conn()
        q = (
            "SELECT * FROM candlesticks WHERE ticker = ? AND period_interval = ?"
            + (" AND ts <= ?" if until is not None else "")
            + " ORDER BY ts"
        )
        params = [ticker, period_s // 60] + ([until] if until is not None else [])
        return [self._candle_row(r) for r in conn.execute(q, params)]

    def trades(self, ticker: str, until: int | None = None) -> Sequence[Trade]:
        return []

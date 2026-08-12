"""Self-contained Kalshi hourly-candle puller for the structure scanner.

Why this exists: ``data/kalshi.db`` carries candles for only 302 tickers
(a stratified sample from the main pull), and the R3 overround scan needs
the *complete leg set* of a multi-outcome event quoted at the same instant
— a partial leg set makes Σ-quotes meaningless. This module pulls exactly
what the scan needs into its own store (``data/structure/structure.db``)
so it never writes to the shared DB.

Endpoints (research/kalshi-api.md §5):

    GET /markets/candlesticks?market_tickers=A,B,C&start_ts&end_ts&period_interval
        batch, <=100 tickers, and the *requested range* is capped at
        10,000 candles across all tickers (n_tickers x hours <= 10_000).
        Verified working for settled markets still in the live tier.
    GET /historical/markets/{ticker}/candlesticks
        single ticker, 5,000-candle range cap; the only path that serves
        archived (pre-cutover) markets, e.g. PRES-2024-*.

Row parsing rules that silently corrupt data if missed (§5):
  * ``price.*`` keys are ABSENT when volume == 0; ``yes_bid``/``yes_ask``
    are always present -> quotes are the tradeable mark, not the trade
    price.
  * dollar fields are named ``close_dollars`` on the live path and
    ``close`` on the historical path.
  * candles are sparse (only periods with a trade or a quote change).
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

API = "https://api.elections.kalshi.com/trade-api/v2"
DEFAULT_STORE = "data/structure/structure.db"
BATCH_MAX_TICKERS = 100
BATCH_MAX_CANDLES = 10_000
SINGLE_MAX_CANDLES = 5_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
  ticker TEXT NOT NULL,
  period_interval INTEGER NOT NULL,
  ts INTEGER NOT NULL,            -- end_period_ts (unix s)
  yes_bid_close REAL, yes_ask_close REAL,
  yes_bid_low REAL, yes_ask_high REAL,
  price_close REAL, volume REAL, open_interest REAL,
  PRIMARY KEY (ticker, period_interval, ts)
);
CREATE INDEX IF NOT EXISTS idx_sc_ticker ON candles(ticker, ts);
CREATE TABLE IF NOT EXISTS pull_log (
  scope TEXT PRIMARY KEY, start_ts INTEGER, end_ts INTEGER,
  period_interval INTEGER, n_rows INTEGER, fetched_at REAL
);
"""


def open_store(path: str = DEFAULT_STORE) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, timeout=60)
    conn.executescript(SCHEMA)
    return conn


def _get(url: str, retries: int = 4, timeout: int = 60):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()[:200]
            except Exception:
                pass
            last = f"HTTP {e.code}: {body}"
            if e.code == 400:
                raise ValueError(last) from None      # our bug, not transient
            time.sleep(0.5 * 2**attempt)
        except Exception as e:  # noqa: BLE001 - network flakiness
            last = repr(e)
            time.sleep(0.5 * 2**attempt)
    raise RuntimeError(f"GET failed after {retries}: {url} :: {last}")


def _pick(d: dict | None, *keys):
    """Live path uses ``*_dollars``, historical path uses the bare name."""
    if not d:
        return None
    for k in keys:
        if k in d and d[k] is not None:
            try:
                return float(d[k])
            except (TypeError, ValueError):
                return None
    return None


def parse_candle(ticker: str, period_interval: int, row: dict) -> tuple:
    """One API candle row -> a store tuple. Pure; unit-tested."""
    bid, ask, price = row.get("yes_bid"), row.get("yes_ask"), row.get("price")
    return (
        ticker,
        period_interval,
        int(row["end_period_ts"]),
        _pick(bid, "close_dollars", "close"),
        _pick(ask, "close_dollars", "close"),
        _pick(bid, "low_dollars", "low"),
        _pick(ask, "high_dollars", "high"),
        _pick(price, "close_dollars", "close"),
        _pick(row, "volume_fp", "volume") or 0.0,
        _pick(row, "open_interest_fp", "open_interest"),
    )


def plan_windows(
    tickers: list[str], start_ts: int, end_ts: int, period_s: int = 3600
) -> list[tuple[list[str], int, int]]:
    """Split (tickers x [start,end]) into requests inside the API caps.

    Cap is on the *requested range*: n_tickers x periods <= 10,000, and
    n_tickers <= 100. Returns [(chunk_tickers, s, e), ...] covering the
    whole rectangle exactly once.
    """
    if end_ts <= start_ts or not tickers:
        return []
    out: list[tuple[list[str], int, int]] = []
    for i in range(0, len(tickers), BATCH_MAX_TICKERS):
        chunk = tickers[i : i + BATCH_MAX_TICKERS]
        max_periods = max(1, BATCH_MAX_CANDLES // len(chunk))
        span = max_periods * period_s
        s = start_ts
        while s < end_ts:
            e = min(s + span, end_ts)
            out.append((chunk, s, e))
            s = e
    return out


def fetch_batch(
    tickers: list[str], start_ts: int, end_ts: int, period_interval: int = 60
) -> list[tuple]:
    url = (
        f"{API}/markets/candlesticks?market_tickers={','.join(tickers)}"
        f"&start_ts={start_ts}&end_ts={end_ts}&period_interval={period_interval}"
    )
    data = _get(url)
    rows: list[tuple] = []
    for m in data.get("markets", []):
        tk = m.get("market_ticker")
        for row in m.get("candlesticks") or []:
            rows.append(parse_candle(tk, period_interval, row))
    return rows


def fetch_historical(
    ticker: str, start_ts: int, end_ts: int, period_interval: int = 60
) -> list[tuple]:
    """Archived tier (single ticker). Used only when the batch path is empty."""
    rows: list[tuple] = []
    span = SINGLE_MAX_CANDLES * period_interval * 60
    s = start_ts
    while s < end_ts:
        e = min(s + span, end_ts)
        url = (
            f"{API}/historical/markets/{ticker}/candlesticks"
            f"?start_ts={s}&end_ts={e}&period_interval={period_interval}"
        )
        try:
            data = _get(url)
        except (ValueError, RuntimeError):
            data = {}
        for row in data.get("candlesticks") or []:
            rows.append(parse_candle(ticker, period_interval, row))
        s = e
    return rows


@dataclass
class PullStats:
    events: int = 0
    requests: int = 0
    rows: int = 0
    fallback_tickers: int = 0
    errors: int = 0


def pull_event(
    conn: sqlite3.Connection,
    event_ticker: str,
    tickers: list[str],
    start_ts: int,
    end_ts: int,
    period_interval: int = 60,
    stats: PullStats | None = None,
) -> int:
    """Pull every leg of one event over [start_ts, end_ts). Idempotent."""
    stats = stats or PullStats()
    plans = plan_windows(tickers, start_ts, end_ts, period_interval * 60)
    rows: list[tuple] = []
    for chunk, s, e in plans:
        try:
            rows += fetch_batch(chunk, s, e, period_interval)
        except Exception:  # noqa: BLE001
            stats.errors += 1
        stats.requests += 1
    seen = {r[0] for r in rows}
    missing = [t for t in tickers if t not in seen]
    for t in missing:  # archived tier fallback
        try:
            got = fetch_historical(t, start_ts, end_ts, period_interval)
        except Exception:  # noqa: BLE001
            got = []
            stats.errors += 1
        if got:
            stats.fallback_tickers += 1
        rows += got
    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?,?,?)", rows
        )
    conn.execute(
        "INSERT OR REPLACE INTO pull_log VALUES (?,?,?,?,?,?)",
        (f"event:{event_ticker}", start_ts, end_ts, period_interval, len(rows), time.time()),
    )
    conn.commit()
    stats.events += 1
    stats.rows += len(rows)
    return len(rows)


def already_pulled(conn: sqlite3.Connection, event_ticker: str) -> bool:
    r = conn.execute(
        "SELECT 1 FROM pull_log WHERE scope = ?", (f"event:{event_ticker}",)
    ).fetchone()
    return r is not None


def pull_events_parallel(
    store_path: str,
    jobs: list[tuple[str, list[str], int, int]],
    workers: int = 6,
    period_interval: int = 60,
    progress_every: int = 100,
) -> PullStats:
    """jobs = [(event_ticker, tickers, start_ts, end_ts)]. Network in
    threads, one writer connection (SQLite serializes writes anyway)."""
    conn = open_store(store_path)
    stats = PullStats()
    todo = [j for j in jobs if not already_pulled(conn, j[0])]

    def work(job):
        et, tickers, s, e = job
        plans = plan_windows(tickers, s, e, period_interval * 60)
        rows, errs = [], 0
        for chunk, ws, we in plans:
            try:
                rows += fetch_batch(chunk, ws, we, period_interval)
            except Exception:  # noqa: BLE001
                errs += 1
        seen = {r[0] for r in rows}
        fb = 0
        for t in [t for t in tickers if t not in seen]:
            try:
                got = fetch_historical(t, s, e, period_interval)
            except Exception:  # noqa: BLE001
                got, errs = [], errs + 1
            if got:
                fb += 1
            rows += got
        return et, s, e, rows, len(plans), errs, fb

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (et, s, e, rows, nreq, errs, fb) in enumerate(ex.map(work, todo), 1):
            if rows:
                conn.executemany(
                    "INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?,?,?)", rows
                )
            conn.execute(
                "INSERT OR REPLACE INTO pull_log VALUES (?,?,?,?,?,?)",
                (f"event:{et}", s, e, period_interval, len(rows), time.time()),
            )
            conn.commit()
            stats.events += 1
            stats.requests += nreq
            stats.rows += len(rows)
            stats.errors += errs
            stats.fallback_tickers += fb
            if progress_every and i % progress_every == 0:
                print(
                    f"[{i}/{len(todo)}] events={stats.events} rows={stats.rows:,} "
                    f"req={stats.requests} err={stats.errors}",
                    flush=True,
                )
    conn.close()
    return stats

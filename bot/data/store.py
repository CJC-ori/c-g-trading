"""SQLite store for Kalshi historical market data.

Default location is ``data/kalshi.db`` relative to the repo root (``data/`` is
gitignored). Everything is idempotent: re-running a pull upserts rather than
duplicating, so pulls can be interrupted and resumed freely.

Monetary values are stored as **floats in dollars** (Kalshi now quotes
decimal dollars, and sub-cent ticks exist), with the untouched API JSON kept in
a ``raw_json`` column so nothing is lost to schema drift.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

__all__ = ["Store", "DEFAULT_DB_PATH", "repo_root"]

log = logging.getLogger(__name__)


def repo_root() -> Path:
    """Repo root, derived from this file's location (``<root>/bot/data``)."""
    return Path(__file__).resolve().parents[2]


DEFAULT_DB_PATH = repo_root() / "data" / "kalshi.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    ticker              TEXT PRIMARY KEY,
    event_ticker        TEXT,
    series_ticker       TEXT,
    title               TEXT,
    subtitle            TEXT,
    yes_sub_title       TEXT,
    category            TEXT,
    market_type         TEXT,
    status              TEXT,
    open_time           TEXT,
    close_time          TEXT,
    expiration_time     TEXT,
    settlement_ts       TEXT,
    result              TEXT,
    settlement_value    REAL,
    can_close_early     INTEGER,
    liquidity           REAL,
    volume              REAL,
    volume_24h          REAL,
    open_interest       REAL,
    last_price          REAL,
    yes_bid             REAL,
    yes_ask             REAL,
    strike_type         TEXT,
    floor_strike        REAL,
    cap_strike          REAL,
    custom_strike       TEXT,
    rules_primary       TEXT,
    rules_secondary     TEXT,
    raw_json            TEXT,
    fetched_at          TEXT,
    -- added 2026-08-11 with the /historical/* tier
    price_level_structure TEXT,   -- 'linear_cent' | 'tapered_deci_cent' | ...
    price_ranges          TEXT,   -- JSON [{start,end,step}] — real tick grid
    expiration_value      TEXT,   -- settled observation (e.g. the '58' in HIGHNY)
    source                TEXT    -- 'live' | 'hist' (which API tier served it)
);
CREATE INDEX IF NOT EXISTS idx_markets_series   ON markets(series_ticker);
CREATE INDEX IF NOT EXISTS idx_markets_event    ON markets(event_ticker);
CREATE INDEX IF NOT EXISTS idx_markets_category ON markets(category);
CREATE INDEX IF NOT EXISTS idx_markets_close    ON markets(close_time);
CREATE INDEX IF NOT EXISTS idx_markets_result   ON markets(result);

CREATE TABLE IF NOT EXISTS series (
    series_ticker   TEXT PRIMARY KEY,
    title           TEXT,
    category        TEXT,
    frequency       TEXT,
    fee_type        TEXT,
    fee_multiplier  REAL,
    tags            TEXT,
    raw_json        TEXT,
    fetched_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_series_category ON series(category);

CREATE TABLE IF NOT EXISTS events (
    event_ticker    TEXT PRIMARY KEY,
    series_ticker   TEXT,
    title           TEXT,
    sub_title       TEXT,
    category        TEXT,
    mutually_exclusive INTEGER,
    raw_json        TEXT,
    fetched_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_series ON events(series_ticker);

-- One row per (market, interval, period). yes-side OHLC in dollars.
-- price_* is NULL when no trade occurred in the period (API omits it);
-- bid/ask OHLC are populated regardless, and are what a backtest should use.
CREATE TABLE IF NOT EXISTS candlesticks (
    ticker          TEXT NOT NULL,
    period_interval INTEGER NOT NULL,
    ts              INTEGER NOT NULL,   -- end_period_ts, unix seconds
    price_open      REAL,
    price_high      REAL,
    price_low       REAL,
    price_close     REAL,
    price_mean      REAL,
    price_previous  REAL,
    yes_bid_open    REAL,
    yes_bid_high    REAL,
    yes_bid_low     REAL,
    yes_bid_close   REAL,
    yes_ask_open    REAL,
    yes_ask_high    REAL,
    yes_ask_low     REAL,
    yes_ask_close   REAL,
    volume          REAL,
    open_interest   REAL,
    source          TEXT,               -- 'live' | 'hist'
    PRIMARY KEY (ticker, period_interval, ts)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_candles_ticker_ts ON candlesticks(ticker, ts);

CREATE TABLE IF NOT EXISTS trades (
    trade_id        TEXT PRIMARY KEY,
    ticker          TEXT NOT NULL,
    ts              INTEGER NOT NULL,   -- created_time, unix seconds
    created_time    TEXT,
    yes_price       REAL,
    no_price        REAL,
    count           REAL,
    taker_side      TEXT,
    is_block_trade  INTEGER,
    taker_book_side TEXT,               -- 'bid' | 'ask'; bid == yes, ask == no
    taker_outcome_side TEXT,            -- 'yes' | 'no'
    source          TEXT                -- 'live' | 'hist'
);
CREATE INDEX IF NOT EXISTS idx_trades_ticker_ts ON trades(ticker, ts);

CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts);

-- Bookkeeping so interrupted pulls can be resumed / audited.
CREATE TABLE IF NOT EXISTS pull_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT,
    target      TEXT,
    rows        INTEGER,
    started_at  TEXT,
    finished_at TEXT,
    note        TEXT
);
"""

#: Columns added after the first pull shipped. ``ALTER TABLE ... ADD COLUMN``
#: is a no-op on a fresh DB (the CREATE above already has them) and cheap on an
#: existing one, so each is attempted and its "duplicate column" error ignored.
MIGRATIONS: tuple[str, ...] = (
    "ALTER TABLE markets ADD COLUMN price_level_structure TEXT",
    "ALTER TABLE markets ADD COLUMN price_ranges TEXT",
    "ALTER TABLE markets ADD COLUMN expiration_value TEXT",
    "ALTER TABLE markets ADD COLUMN source TEXT",
    "ALTER TABLE candlesticks ADD COLUMN source TEXT",
    "ALTER TABLE trades ADD COLUMN taker_book_side TEXT",
    "ALTER TABLE trades ADD COLUMN taker_outcome_side TEXT",
    "ALTER TABLE trades ADD COLUMN source TEXT",
)

#: In compact mode ``raw_json`` keeps only these keys — everything else is
#: either already in its own column or not worth the disk. The full payload
#: averages 2.2 KB/market and the bulk historical pull is hundreds of thousands
#: of rows, so this is the difference between a 400 MB table and a 1.5 GB one.
#: The dropped fields are re-fetchable per ticker from
#: ``/historical/markets/{ticker}`` if a strategy ever needs them; the biggest
#: single item is ``early_close_condition`` (~300 B of boilerplate on every
#: weather strike).
_COMPACT_KEEP_KEYS = (
    "created_time",
    "updated_time",
    "expected_expiration_time",
    "occurrence_datetime",
    "no_sub_title",
    "notional_value_dollars",
    "settlement_timer_seconds",
    "previous_price_dollars",
    "series_ticker",
)


# --------------------------------------------------------------------------
# Parsing helpers for Kalshi's stringly-typed money/size fields.
# --------------------------------------------------------------------------
def _f(value: Any) -> float | None:
    """Parse a Kalshi numeric string (``"0.9700"``, ``"8.40"``) to float."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pick(row: dict[str, Any], *keys: str) -> Any:
    """First present, non-empty value among ``keys``."""
    for k in keys:
        v = row.get(k)
        if v is not None and v != "":
            return v
    return None


def _iso_to_ts(value: str | None) -> int | None:
    """Parse an RFC3339 timestamp to unix seconds."""
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        return int(dt.datetime.fromisoformat(text).timestamp())
    except ValueError:
        return None


def series_of(ticker: str) -> str:
    """Series ticker for a market ticker (the segment before the first dash)."""
    return ticker.split("-", 1)[0]


class Store:
    """Idempotent SQLite store. Use as a context manager."""

    def __init__(self, path: str | Path = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        for stmt in MIGRATIONS:
            try:
                self.conn.execute(stmt)
            except sqlite3.OperationalError as exc:  # already applied
                if "duplicate column" not in str(exc):
                    raise
        self.conn.commit()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    # -- writers -----------------------------------------------------------
    def upsert_markets(
        self,
        markets: Iterable[dict[str, Any]],
        category_by_series: dict[str, str] | None = None,
        *,
        series_ticker: str | None = None,
        source: str = "live",
        compact: bool = False,
    ) -> int:
        """Upsert market rows. Returns the number written.

        Args:
            markets: Raw ``/markets`` or ``/historical/markets`` payload dicts.
            category_by_series: Optional series -> category map, since markets
                themselves carry no category field.
            series_ticker: Series these markets belong to. **Required for
                archived pulls**: ``/historical/markets`` rows have no
                ``series_ticker`` field and their ticker prefix predates the
                2023 ``KX`` rename (``HIGHNY-22NOV08-B57.5`` belongs to series
                ``KXHIGHNY``), so the prefix heuristic silently mis-files them.
            source: ``'live'`` or ``'hist'`` — which API tier served the row.
            compact: Reduce ``raw_json`` to :data:`_COMPACT_KEEP_KEYS`. Cuts the
                stored payload from ~2.2 KB to ~250 B per market; use it for
                bulk historical pulls, where the row count is the constraint.
        """
        cats = category_by_series or {}
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        rows = []
        for m in markets:
            ticker = m.get("ticker")
            if not ticker:
                continue
            st = series_ticker or m.get("series_ticker") or series_of(ticker)
            strike = m.get("custom_strike")
            ranges = m.get("price_ranges")
            blob = (
                {k: m[k] for k in _COMPACT_KEEP_KEYS if m.get(k) not in (None, "")}
                if compact
                else m
            )
            rows.append(
                (
                    ticker,
                    m.get("event_ticker"),
                    st,
                    m.get("title"),
                    m.get("subtitle") or m.get("sub_title"),
                    m.get("yes_sub_title"),
                    cats.get(st),
                    m.get("market_type"),
                    m.get("status"),
                    m.get("open_time"),
                    m.get("close_time"),
                    m.get("expiration_time"),
                    m.get("settlement_ts"),
                    m.get("result"),
                    _f(_pick(m, "settlement_value_dollars", "settlement_value")),
                    1 if m.get("can_close_early") else 0,
                    _f(_pick(m, "liquidity_dollars", "liquidity")),
                    _f(_pick(m, "volume_fp", "volume")),
                    _f(_pick(m, "volume_24h_fp", "volume_24h")),
                    _f(_pick(m, "open_interest_fp", "open_interest")),
                    _f(_pick(m, "last_price_dollars", "last_price")),
                    _f(_pick(m, "yes_bid_dollars", "yes_bid")),
                    _f(_pick(m, "yes_ask_dollars", "yes_ask")),
                    m.get("strike_type"),
                    _f(_pick(m, "floor_strike")),
                    _f(_pick(m, "cap_strike")),
                    json.dumps(strike) if isinstance(strike, dict) else strike,
                    m.get("rules_primary"),
                    m.get("rules_secondary"),
                    json.dumps(blob, separators=(",", ":")),
                    now,
                    m.get("price_level_structure"),
                    json.dumps(ranges, separators=(",", ":")) if ranges else None,
                    m.get("expiration_value") or None,
                    source,
                )
            )
        if not rows:
            return 0
        self.conn.executemany(
            """INSERT INTO markets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(ticker) DO UPDATE SET
                 event_ticker=excluded.event_ticker, series_ticker=excluded.series_ticker,
                 title=excluded.title, subtitle=excluded.subtitle,
                 yes_sub_title=excluded.yes_sub_title,
                 category=COALESCE(excluded.category, markets.category),
                 market_type=excluded.market_type, status=excluded.status,
                 open_time=excluded.open_time, close_time=excluded.close_time,
                 expiration_time=excluded.expiration_time, settlement_ts=excluded.settlement_ts,
                 result=excluded.result, settlement_value=excluded.settlement_value,
                 can_close_early=excluded.can_close_early, liquidity=excluded.liquidity,
                 volume=excluded.volume, volume_24h=excluded.volume_24h,
                 open_interest=excluded.open_interest, last_price=excluded.last_price,
                 yes_bid=excluded.yes_bid, yes_ask=excluded.yes_ask,
                 strike_type=excluded.strike_type, floor_strike=excluded.floor_strike,
                 cap_strike=excluded.cap_strike, custom_strike=excluded.custom_strike,
                 rules_primary=excluded.rules_primary, rules_secondary=excluded.rules_secondary,
                 raw_json=excluded.raw_json, fetched_at=excluded.fetched_at,
                 price_level_structure=COALESCE(excluded.price_level_structure,
                                               markets.price_level_structure),
                 price_ranges=COALESCE(excluded.price_ranges, markets.price_ranges),
                 expiration_value=COALESCE(excluded.expiration_value, markets.expiration_value),
                 source=excluded.source""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def upsert_series(self, series: Iterable[dict[str, Any]]) -> int:
        """Upsert series metadata rows (the source of ``category``)."""
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        rows = [
            (
                s["ticker"],
                s.get("title"),
                s.get("category"),
                s.get("frequency"),
                s.get("fee_type"),
                _f(s.get("fee_multiplier")),
                json.dumps(s.get("tags") or []),
                json.dumps(s, separators=(",", ":")),
                now,
            )
            for s in series
            if s.get("ticker")
        ]
        if not rows:
            return 0
        self.conn.executemany(
            """INSERT INTO series VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(series_ticker) DO UPDATE SET
                 title=excluded.title, category=excluded.category,
                 frequency=excluded.frequency, fee_type=excluded.fee_type,
                 fee_multiplier=excluded.fee_multiplier, tags=excluded.tags,
                 raw_json=excluded.raw_json, fetched_at=excluded.fetched_at""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def upsert_events(self, events: Iterable[dict[str, Any]]) -> int:
        """Upsert event rows."""
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        rows = [
            (
                e["event_ticker"],
                e.get("series_ticker"),
                e.get("title"),
                e.get("sub_title"),
                e.get("category"),
                1 if e.get("mutually_exclusive") else 0,
                json.dumps(e, separators=(",", ":")),
                now,
            )
            for e in events
            if e.get("event_ticker")
        ]
        if not rows:
            return 0
        self.conn.executemany(
            """INSERT INTO events VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(event_ticker) DO UPDATE SET
                 series_ticker=excluded.series_ticker, title=excluded.title,
                 sub_title=excluded.sub_title, category=excluded.category,
                 mutually_exclusive=excluded.mutually_exclusive,
                 raw_json=excluded.raw_json, fetched_at=excluded.fetched_at""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def upsert_candlesticks(
        self,
        ticker: str,
        period_interval: int,
        candles: Sequence[dict[str, Any]],
        source: str = "live",
    ) -> int:
        """Upsert candlesticks for one market/interval.

        Accepts **both** JSON conventions: the live endpoint's
        ``open_dollars``/``volume_fp``/``open_interest_fp`` and the
        ``/historical/*`` endpoint's bare ``open``/``volume``/``open_interest``.
        The values are identical dollar/fixed-point strings either way.

        Handles the no-trade case where the API omits ``price`` OHLC keys:
        those columns land as NULL and only bid/ask OHLC are populated.
        """
        rows = []
        for c in candles:
            price = c.get("price") or {}
            bid = c.get("yes_bid") or {}
            ask = c.get("yes_ask") or {}
            rows.append(
                (
                    ticker,
                    period_interval,
                    int(c["end_period_ts"]),
                    _f(_pick(price, "open_dollars", "open")),
                    _f(_pick(price, "high_dollars", "high")),
                    _f(_pick(price, "low_dollars", "low")),
                    _f(_pick(price, "close_dollars", "close")),
                    _f(_pick(price, "mean_dollars", "mean")),
                    _f(_pick(price, "previous_dollars", "previous")),
                    _f(_pick(bid, "open_dollars", "open")),
                    _f(_pick(bid, "high_dollars", "high")),
                    _f(_pick(bid, "low_dollars", "low")),
                    _f(_pick(bid, "close_dollars", "close")),
                    _f(_pick(ask, "open_dollars", "open")),
                    _f(_pick(ask, "high_dollars", "high")),
                    _f(_pick(ask, "low_dollars", "low")),
                    _f(_pick(ask, "close_dollars", "close")),
                    _f(_pick(c, "volume_fp", "volume")),
                    _f(_pick(c, "open_interest_fp", "open_interest")),
                    source,
                )
            )
        if not rows:
            return 0
        self.conn.executemany(
            "INSERT OR REPLACE INTO candlesticks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def upsert_trades(self, trades: Iterable[dict[str, Any]], source: str = "live") -> int:
        """Upsert tape trades keyed on ``trade_id``.

        ``/historical/trades`` pages overlap by one row (the cursor is inclusive
        of the last ``created_time``), so idempotency here is load-bearing, not
        a nicety. ``taker_book_side`` is the field a maker-fill simulator wants:
        ``ask`` means the aggressor lifted NO / consumed a resting YES bid.
        """
        rows = []
        for t in trades:
            tid = t.get("trade_id")
            if not tid:
                continue
            created = t.get("created_time")
            rows.append(
                (
                    tid,
                    t.get("ticker"),
                    _iso_to_ts(created) or 0,
                    created,
                    _f(_pick(t, "yes_price_dollars", "yes_price")),
                    _f(_pick(t, "no_price_dollars", "no_price")),
                    _f(_pick(t, "count_fp", "count")),
                    t.get("taker_side"),
                    1 if t.get("is_block_trade") else 0,
                    t.get("taker_book_side"),
                    t.get("taker_outcome_side"),
                    source,
                )
            )
        if not rows:
            return 0
        self.conn.executemany(
            "INSERT OR REPLACE INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows
        )
        self.conn.commit()
        return len(rows)

    def log_pull(
        self, kind: str, target: str, rows: int, started_at: str, note: str = ""
    ) -> None:
        """Record a completed pull for auditability."""
        self.conn.execute(
            "INSERT INTO pull_log (kind,target,rows,started_at,finished_at,note)"
            " VALUES (?,?,?,?,?,?)",
            (
                kind,
                target,
                rows,
                started_at,
                dt.datetime.now(dt.timezone.utc).isoformat(),
                note,
            ),
        )
        self.conn.commit()

    def backfill_categories(self) -> int:
        """Fill ``markets.category`` from the ``series`` table. Returns rows set."""
        cur = self.conn.execute(
            """UPDATE markets SET category = (
                   SELECT s.category FROM series s WHERE s.series_ticker = markets.series_ticker
               )
               WHERE category IS NULL
                 AND series_ticker IN (SELECT series_ticker FROM series)"""
        )
        self.conn.commit()
        return cur.rowcount

    def backfill_from_raw(self) -> int:
        """Populate the columns added with the historical tier from ``raw_json``.

        Rows written before those columns existed still carry the full payload,
        so the tick structure can be recovered without re-fetching. Returns the
        number of market rows updated.
        """
        cur = self.conn.execute(
            """UPDATE markets SET
                 price_level_structure = COALESCE(price_level_structure,
                     json_extract(raw_json, '$.price_level_structure')),
                 price_ranges = COALESCE(price_ranges,
                     json_extract(raw_json, '$.price_ranges')),
                 expiration_value = COALESCE(expiration_value,
                     NULLIF(json_extract(raw_json, '$.expiration_value'), '')),
                 source = COALESCE(source, 'live')
               WHERE price_ranges IS NULL OR source IS NULL"""
        )
        n = cur.rowcount
        # Rows written before the tier split existed all came from the live API.
        self.conn.execute("UPDATE trades SET source='live' WHERE source IS NULL")
        self.conn.execute("UPDATE candlesticks SET source='live' WHERE source IS NULL")
        self.conn.commit()
        return n

    # -- readers -----------------------------------------------------------
    def counts(self) -> dict[str, int]:
        """Row counts for every data table."""
        out = {}
        for table in ("markets", "series", "events", "candlesticks", "trades"):
            out[table] = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return out

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        """Run a read query and return all rows."""
        return self.conn.execute(sql, params).fetchall()

    def size_bytes(self) -> int:
        """On-disk size of the database (including WAL, if present)."""
        total = self.path.stat().st_size if self.path.exists() else 0
        wal = self.path.with_suffix(self.path.suffix + "-wal")
        if wal.exists():
            total += wal.stat().st_size
        return total

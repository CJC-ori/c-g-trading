"""CLI for pulling Kalshi historical data into the local SQLite store.

Examples::

    # 1. Refresh the series catalog (needed for categories + frequency filters)
    python -m bot.data.pull series

    # 2. Settled markets, series-driven (bounded — the practical default)
    python -m bot.data.pull markets --by-series \\
        --categories Politics,Elections,Economics,World

    # 3. Settled markets by close-time window (faithful but expensive: the
    #    exchange finalises ~365k markets/day, ~80% auto-generated parlays)
    python -m bot.data.pull markets --settled-since 2026-05-01

    # 4. Open-market snapshot
    python -m bot.data.pull markets --status open

    # 5. Hourly candles for a stratified sample
    python -m bot.data.pull candles --sample 300 --strategy stratified --final-48h-minute

    # 6. Full trade tape for one market
    python -m bot.data.pull trades --ticker KXSENATEMID-26-AELS

    # 7. Coverage report
    python -m bot.data.pull stats

The ``hist-*`` subcommands drive the **/historical/\\* tier**, which is where
everything older than ``/historical/cutoff`` (2026-06-12 as of writing) lives —
settled markets back to 2021, tick trades back to 2021-06, candlesticks to
market inception::

    # archived market metadata for whole categories (series-driven)
    python -m bot.data.pull hist-markets --categories "Elections,Politics"

    # tick tape for a universe, newest-first, capped
    python -m bot.data.pull hist-trades --series PRES --max-pages 400

    # 1-minute candles over the final 72h of each market's life
    python -m bot.data.pull hist-candles --categories Elections --interval 1 \\
        --window final-72h --min-volume 5000

    # which markets traded during a window (exchange-wide firehose)
    python -m bot.data.pull discover --min-ts 2024-11-05 --max-ts 2024-11-07

Note:
    The **live** tier purges settled markets roughly 90 days after close, so
    ``--settled-since`` cannot reach further back than that. The historical tier
    has no such limit but also **no date filters** — enumeration must go through
    ``series_ticker``/``event_ticker``. See ``NOTES.md``.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import logging
import random
import sys
import time
from collections import Counter, defaultdict
from typing import Any, Callable, Iterable, Iterator, Sequence

from bot.data.kalshi_client import KalshiClient, known_categories
from bot.data.store import DEFAULT_DB_PATH, Store, series_of

log = logging.getLogger("pull")

#: Series prefixes excluded by default. Two families:
#:  * ``KXMVE*`` — auto-generated multivariate parlay combos. ~80% of all
#:    settled markets by count and essentially untradeable as a strategy.
#:  * high-frequency crypto / index / commodity strike ladders that settle
#:    every 15 minutes or hourly.
DEFAULT_EXCLUDE_PREFIXES: tuple[str, ...] = (
    "KXMVE",
    "KXBTC",
    "KXETH",
    "KXSOL",
    "KXXRP",
    "KXDOGE",
    "KXBNB",
    "KXHYPE",
    "KXLTC",
    "KXADA",
    "KXLINK",
    "KXINXU",
    "KXNASDAQ100U",
    "NASDAQ100I",
    "KXEURUSDH",
    "KXGOLDH",
    "KXGOLDD",
    "KXSILVERH",
    "KXPLATINUMH",
    "KXPALLADIUMH",
    "KXWTIH",
    "KXNGASH",
)

#: Categories a prediction-market research pull actually wants, and their
#: relative weight when drawing a stratified candlestick sample.
CATEGORY_WEIGHTS: dict[str, float] = {
    "Politics": 3.0,
    "Elections": 3.0,
    "Economics": 2.0,
    "World": 2.0,
    "Science and Technology": 1.0,
    "Health": 1.0,
    "Companies": 1.0,
    "Climate and Weather": 0.75,
    "Transportation": 0.5,
    "Financials": 0.5,
    "Crypto": 0.25,
    "Sports": 0.25,
    "Entertainment": 0.25,
}

#: Markets always included in a stratified sample (the Michigan Senate primary
#: complex plus its congressional-district siblings and the general-election
#: markets that reference the same candidates).
PINNED_SERIES: tuple[str, ...] = (
    "KXSENATEMID",   # MI Senate Democratic primary
    "KXSENATEMIR",   # MI Senate Republican primary
    "KXMIPRIMARY",   # MI congressional-district primaries
    "KXMISENATE",    # MI Senate general (open)
    "SENATEMI",      # MI Senate general, party framing (open)
    "KXMISENGOVCOMBO",
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _utc(day: str) -> int:
    """Parse ``YYYY-MM-DD`` or ``YYYY-MM-DDTHH:MM`` to unix seconds (UTC).

    Bare unix-second integers pass through, which keeps event-window scripting
    readable when a timestamp was copied straight out of a candle.
    """
    if day.isdigit():
        return int(day)
    fmt = "%Y-%m-%dT%H:%M" if "T" in day else "%Y-%m-%d"
    return int(dt.datetime.strptime(day, fmt).replace(tzinfo=dt.timezone.utc).timestamp())


def _iso(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")


def _excluded(ticker: str, prefixes: Sequence[str]) -> bool:
    """True if a market's series matches any excluded prefix."""
    s = series_of(ticker)
    return any(s.startswith(p) for p in prefixes)


def _category_map(store: Store) -> dict[str, str]:
    """series_ticker -> category, from the local series catalog."""
    return {
        r["series_ticker"]: r["category"]
        for r in store.query("SELECT series_ticker, category FROM series")
        if r["category"]
    }


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


# --------------------------------------------------------------------------
# subcommand: series
# --------------------------------------------------------------------------
def cmd_series(args: argparse.Namespace, client: KalshiClient, store: Store) -> None:
    """Refresh the full series catalog across every known category."""
    started = _now()
    total = 0
    for cat in known_categories():
        rows = client.list_series(cat)
        n = store.upsert_series(rows)
        total += n
        log.info("series %-24s %5d", cat, n)
    store.backfill_categories()
    store.log_pull("series", "all-categories", total, started)
    print(f"series catalog: {total} rows across {len(known_categories())} categories")


def cmd_events(args: argparse.Namespace, client: KalshiClient, store: Store) -> None:
    """Pull events — the level that carries ``category`` and, crucially,
    ``mutually_exclusive``.

    A race is one event holding many binary markets of which exactly one
    settles ``yes``; the backtest needs that grouping to avoid treating
    sibling contracts as independent bets.
    """
    started = _now()
    prefixes = (
        tuple(p for p in args.exclude_series_prefix.split(",") if p)
        if args.exclude_series_prefix is not None
        else DEFAULT_EXCLUDE_PREFIXES
    )
    total = skipped = 0
    batch: list[dict[str, Any]] = []
    for e in client.get_events(status=args.status, page_limit=200, max_pages=args.max_pages):
        st = e.get("series_ticker") or ""
        # The settled-event feed is dominated by 15-minute crypto series;
        # filter with the same prefix list the market pulls use.
        if any(st.startswith(p) for p in prefixes):
            skipped += 1
            continue
        batch.append(e)
        if len(batch) >= 1000:
            total += store.upsert_events(batch)
            batch.clear()
            log.info("events %d kept / %d skipped (reqs=%d)", total, skipped, client.request_count)
    total += store.upsert_events(batch)
    store.log_pull("events", args.status or "all", total, started, f"skipped={skipped}")
    print(f"events: {total} rows, {skipped} skipped (excluded series)")


# --------------------------------------------------------------------------
# subcommand: markets
# --------------------------------------------------------------------------
def _store_batch(
    store: Store,
    batch: list[dict[str, Any]],
    cats: dict[str, str],
    min_volume: float,
) -> int:
    keep = [m for m in batch if float(m.get("volume_fp") or m.get("volume") or 0) >= min_volume]
    return store.upsert_markets(keep, cats)


def _pull_window(
    args: argparse.Namespace,
    client: KalshiClient,
    store: Store,
    cats: dict[str, str],
    prefixes: Sequence[str],
) -> tuple[int, int]:
    """Enumerate settled markets day by day over the requested close-time window.

    Day-chunking keeps each pagination walk short enough to resume after an
    interruption and gives useful progress output. Returns (kept, skipped).
    """
    start = _utc(args.settled_since)
    end = _utc(args.settled_until) if args.settled_until else int(time.time())
    kept = skipped = 0

    day = start
    while day < end:
        nxt = min(day + 86400, end)
        batch: list[dict[str, Any]] = []
        d_keep = d_skip = 0
        for m in client.get_markets(
            status="settled", min_close_ts=day, max_close_ts=nxt, page_limit=1000
        ):
            if _excluded(m.get("ticker", ""), prefixes):
                d_skip += 1
                continue
            batch.append(m)
            if len(batch) >= 2000:
                d_keep += _store_batch(store, batch, cats, args.min_volume)
                batch.clear()
            if args.max_markets_per_day and d_skip + d_keep + len(batch) >= args.max_markets_per_day:
                break
        d_keep += _store_batch(store, batch, cats, args.min_volume)
        kept += d_keep
        skipped += d_skip
        log.info("%s: kept %6d  skipped %7d  (reqs=%d)", _iso(day), d_keep, d_skip, client.request_count)
        day = nxt
    return kept, skipped


def _pull_by_series(
    args: argparse.Namespace,
    client: KalshiClient,
    store: Store,
    cats: dict[str, str],
    prefixes: Sequence[str],
) -> tuple[int, int]:
    """Enumerate markets one series at a time.

    Far cheaper than the close-time window (one request per series instead of
    ~370 per day) and it attaches categories for free. This is the bounded path
    that actually finishes; the trade-off is that it only sees series present
    in the local catalog, so run ``pull series`` first.
    """
    wanted = [c.strip() for c in args.categories.split(",")] if args.categories else None
    q = "SELECT series_ticker, category, frequency FROM series"
    rows = store.query(q)
    excl_freq = {f.strip() for f in (args.exclude_frequency or "").split(",") if f.strip()}

    targets = []
    for r in rows:
        st = r["series_ticker"]
        if wanted and r["category"] not in wanted:
            continue
        if r["frequency"] in excl_freq:
            continue
        if any(st.startswith(p) for p in prefixes):
            continue
        targets.append(st)
    targets.extend(t for t in PINNED_SERIES if t not in targets)

    kept = skipped = 0
    for i, st in enumerate(targets, 1):
        batch = [m for m in client.get_markets(series_ticker=st, page_limit=1000)]
        pre = len(batch)
        batch = [m for m in batch if not _excluded(m.get("ticker", ""), prefixes)]
        skipped += pre - len(batch)
        if args.settled_only:
            batch = [m for m in batch if m.get("status") == "finalized"]
        kept += _store_batch(store, batch, cats, args.min_volume)
        if i % 200 == 0:
            log.info("series %d/%d  kept=%d  reqs=%d", i, len(targets), kept, client.request_count)
    return kept, skipped


def cmd_markets(args: argparse.Namespace, client: KalshiClient, store: Store) -> None:
    """Pull markets by close-time window, by series, or an open snapshot."""
    started = _now()
    prefixes = (
        tuple(p for p in args.exclude_series_prefix.split(",") if p)
        if args.exclude_series_prefix is not None
        else DEFAULT_EXCLUDE_PREFIXES
    )
    cats = _category_map(store)
    log.info("excluding %d series prefixes: %s", len(prefixes), ",".join(prefixes[:6]) + "...")

    if args.status:
        kept = skipped = 0
        batch: list[dict[str, Any]] = []
        for m in client.get_markets(status=args.status, page_limit=1000):
            if _excluded(m.get("ticker", ""), prefixes):
                skipped += 1
                continue
            batch.append(m)
            if len(batch) >= 2000:
                kept += _store_batch(store, batch, cats, args.min_volume)
                batch.clear()
        kept += _store_batch(store, batch, cats, args.min_volume)
        target = f"status={args.status}"
    elif args.by_series:
        kept, skipped = _pull_by_series(args, client, store, cats, prefixes)
        target = f"by-series categories={args.categories or 'all'}"
    else:
        if not args.settled_since:
            raise SystemExit("need --settled-since, --by-series, or --status")
        kept, skipped = _pull_window(args, client, store, cats, prefixes)
        target = f"settled {args.settled_since}..{args.settled_until or 'now'}"

    store.backfill_categories()
    store.log_pull("markets", target, kept, started, f"skipped={skipped}")
    print(f"markets [{target}]: stored {kept}, skipped {skipped} (excluded series)")


# --------------------------------------------------------------------------
# subcommand: candles
# --------------------------------------------------------------------------
def _stratified_sample(store: Store, n: int, min_volume: float) -> list[str]:
    """Draw ~``n`` settled markets, stratified by category and volume-weighted.

    Pinned series (the Michigan Senate primary complex) are always included and
    do not count against the per-category budget.
    """
    rows = store.query(
        """SELECT ticker, category, series_ticker, volume FROM markets
           WHERE status IN ('finalized','settled') AND volume >= ?""",
        (min_volume,),
    )
    by_cat: dict[str, list[Any]] = defaultdict(list)
    for r in rows:
        by_cat[r["category"] or "Unknown"].append(r)

    pinned = [
        r["ticker"]
        for r in store.query(
            "SELECT ticker FROM markets WHERE series_ticker IN (%s)"
            % ",".join("?" * len(PINNED_SERIES)),
            PINNED_SERIES,
        )
    ]

    weights = {c: CATEGORY_WEIGHTS.get(c, 0.25) for c in by_cat}
    total_w = sum(w for c, w in weights.items() if by_cat[c]) or 1.0
    rng = random.Random(20260811)

    picked: list[str] = []
    for cat, pool in sorted(by_cat.items()):
        budget = int(round(n * weights[cat] / total_w))
        if budget <= 0 or not pool:
            continue
        # Prefer liquid markets: take the top half by volume, then sample.
        pool = sorted(pool, key=lambda r: -(r["volume"] or 0))
        head = pool[: max(budget, len(pool) // 2)]
        picked.extend(r["ticker"] for r in rng.sample(head, min(budget, len(head))))

    out = list(dict.fromkeys(pinned + picked))
    log.info("sample: %d pinned + %d stratified = %d", len(pinned), len(picked), len(out))
    return out


def cmd_candles(args: argparse.Namespace, client: KalshiClient, store: Store) -> None:
    """Pull candlesticks for explicit tickers or a stratified settled sample."""
    started = _now()
    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    elif args.strategy == "stratified":
        tickers = _stratified_sample(store, args.sample, args.min_volume)
    else:
        rows = store.query(
            "SELECT ticker FROM markets WHERE status IN ('finalized','settled')"
            " ORDER BY volume DESC LIMIT ?",
            (args.sample,),
        )
        tickers = [r["ticker"] for r in rows]

    meta = {
        r["ticker"]: r
        for r in store.query(
            "SELECT ticker, series_ticker, open_time, close_time FROM markets"
        )
    }

    total = 0
    minute_total = 0
    failures = 0
    for i, tk in enumerate(tickers, 1):
        m = meta.get(tk)
        if m is None:
            failures += 1
            continue
        st = m["series_ticker"] or series_of(tk)
        now_ts = int(time.time())
        open_ts = _parse_iso(m["open_time"])
        # Open markets have close_time far in the future; never request past now.
        close_ts = min(_parse_iso(m["close_time"]) or now_ts, now_ts)
        # Candles start at market open; fall back to a generous lookback.
        start = open_ts or (close_ts - 86400 * 400)
        try:
            candles = client.get_candlesticks(st, tk, start, close_ts + 3600, args.interval)
        except Exception as exc:  # noqa: BLE001 - keep the pull going
            log.warning("candles failed %s: %s", tk, exc)
            failures += 1
            continue
        total += store.upsert_candlesticks(tk, args.interval, candles)

        if args.final_48h_minute:
            fine_start = max(start, close_ts - 48 * 3600)
            try:
                fine = client.get_candlesticks(st, tk, fine_start, close_ts + 600, 1)
                minute_total += store.upsert_candlesticks(tk, 1, fine)
            except Exception as exc:  # noqa: BLE001
                log.warning("1-min candles failed %s: %s", tk, exc)

        if i % 25 == 0:
            log.info(
                "candles %d/%d  hourly=%d  1min=%d  reqs=%d  db=%.0fMB",
                i, len(tickers), total, minute_total, client.request_count,
                store.size_bytes() / 1e6,
            )

    store.log_pull(
        "candles", f"n={len(tickers)} interval={args.interval}", total, started,
        f"minute={minute_total} failures={failures}",
    )
    print(
        f"candles: {total} rows @ {args.interval}min, {minute_total} rows @ 1min,"
        f" {len(tickers)} markets, {failures} failures"
    )


def _parse_iso(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


# --------------------------------------------------------------------------
# subcommand: trades
# --------------------------------------------------------------------------
def cmd_trades(args: argparse.Namespace, client: KalshiClient, store: Store) -> None:
    """Pull the full public trade tape for one or more markets."""
    started = _now()
    tickers = [t.strip() for t in args.ticker.split(",") if t.strip()]
    total = 0
    for tk in tickers:
        n = 0
        batch: list[dict[str, Any]] = []
        for t in client.get_trades(ticker=tk, page_limit=1000, max_pages=args.max_pages):
            batch.append(t)
            if len(batch) >= 5000:
                n += store.upsert_trades(batch)
                batch.clear()
        n += store.upsert_trades(batch)
        total += n
        log.info("trades %-32s %7d", tk, n)
    store.log_pull("trades", ",".join(tickers)[:200], total, started)
    print(f"trades: {total} rows across {len(tickers)} markets")


# --------------------------------------------------------------------------
# /historical/* tier
# --------------------------------------------------------------------------
#: The 12 daily-high city series the weather strategy needs
#: (``research/ground-truth.md`` §2). Each resolves ~10 strikes/day and the
#: series ticker also resolves its pre-2023 ``HIGH*`` markets.
WEATHER_HIGH_SERIES: tuple[str, ...] = (
    "KXHIGHNY", "KXHIGHCHI", "KXHIGHAUS", "KXHIGHMIA", "KXHIGHLAX", "KXHIGHPHIL",
    "KXHIGHDEN", "KXHIGHTPHX", "KXHIGHTATL", "KXHIGHTSEA", "KXHIGHTDC", "KXHIGHTDAL",
)


def _pmap(
    fn: Callable[[Any], Any], items: Sequence[Any], workers: int
) -> Iterator[tuple[Any, Any, BaseException | None]]:
    """Run ``fn`` over ``items`` in a thread pool, yielding as results land.

    Yields ``(item, result, exception)``. Network I/O parallelises well here —
    the client's rate limiter is shared and thread-safe, and measured throughput
    goes from ~3 rps single-threaded (latency-bound through the proxy) to ~15
    rps at 8 workers. Database writes stay on the calling thread, because the
    sqlite connection is not shared across threads.
    """
    if workers <= 1:
        for it in items:
            try:
                yield it, fn(it), None
            except BaseException as exc:  # noqa: BLE001 - reported, not raised
                yield it, None, exc
        return
    pool = cf.ThreadPoolExecutor(max_workers=workers)
    futures = {pool.submit(fn, it): it for it in items}
    try:
        for fut in cf.as_completed(futures):
            it = futures[fut]
            try:
                yield it, fut.result(), None
            except BaseException as exc:  # noqa: BLE001
                yield it, None, exc
    finally:
        # Callers stop early on row/size caps; drop the queued work instead of
        # blocking on it (and instead of tearing down mid-flight generators,
        # which surfaces as a spurious "generator ignored GeneratorExit").
        pool.shutdown(wait=False, cancel_futures=True)


def _over_budget(store: Store, limit_mb: float) -> bool:
    return limit_mb > 0 and store.size_bytes() / 1e6 >= limit_mb


def _window(row: Any, spec: str) -> tuple[int, int]:
    """Resolve a ``--window`` spec to (start_ts, end_ts) for one market.

    ``full`` spans market open to close; ``final-72h`` / ``final-15d`` take that
    much time off the end. The end is padded an hour past close so the settling
    candle is included, and clamped to now for still-open markets.
    """
    now = int(time.time())
    open_ts = _parse_iso(row["open_time"])
    close_ts = min(_parse_iso(row["close_time"]) or now, now)
    start = open_ts or (close_ts - 86400 * 400)
    if spec != "full":
        body = spec.split("-", 1)[1]
        n = float(body[:-1])
        unit = body[-1]
        secs = n * (3600 if unit == "h" else 86400)
        start = max(start, close_ts - int(secs))
    return start, min(close_ts + 3600, now + 3600)


def _select_tickers(store: Store, args: argparse.Namespace) -> list[Any]:
    """Resolve the universe selector flags shared by the ``hist-*`` commands.

    Returns ``markets`` rows (ticker, series_ticker, category, open/close_time,
    volume, source). The selectors compose: category + volume + settled + limit
    is the FLB universe; ``--series`` is how the election and weather pulls are
    targeted.
    """
    where: list[str] = ["1=1"]
    params: list[Any] = []
    if args.tickers:
        tk = [t.strip() for t in args.tickers.split(",") if t.strip()]
        where.append("ticker IN (%s)" % ",".join("?" * len(tk)))
        params += tk
    if getattr(args, "series", None):
        st = [s.strip() for s in args.series.split(",") if s.strip()]
        where.append("series_ticker IN (%s)" % ",".join("?" * len(st)))
        params += st
    if getattr(args, "series_like", None):
        where.append("series_ticker LIKE ?")
        params.append(args.series_like)
    if getattr(args, "categories", None):
        cats = [c.strip() for c in args.categories.split(",") if c.strip()]
        where.append("category IN (%s)" % ",".join("?" * len(cats)))
        params += cats
    if getattr(args, "exclude_categories", None):
        cats = [c.strip() for c in args.exclude_categories.split(",") if c.strip()]
        where.append("COALESCE(category,'') NOT IN (%s)" % ",".join("?" * len(cats)))
        params += cats
    if getattr(args, "min_volume", 0):
        where.append("volume >= ?")
        params.append(args.min_volume)
    if getattr(args, "settled_only", False):
        where.append("status IN ('finalized','settled')")
    if getattr(args, "source", None):
        where.append("source = ?")
        params.append(args.source)

    sql = (
        "SELECT ticker, series_ticker, category, open_time, close_time, volume, source"
        " FROM markets WHERE " + " AND ".join(where) + " ORDER BY volume DESC"
    )
    if getattr(args, "limit", 0):
        sql += f" LIMIT {int(args.limit)}"
    rows = store.query(sql, params)

    if getattr(args, "skip_existing", False):
        have = _already_have(store, args)
        rows = [r for r in rows if r["ticker"] not in have]
    return rows


def _already_have(store: Store, args: argparse.Namespace) -> set[str]:
    """Tickers that already carry data for the table this command writes."""
    if getattr(args, "interval", None):
        return {
            r["ticker"]
            for r in store.query(
                "SELECT ticker FROM candlesticks WHERE period_interval = ?"
                " GROUP BY ticker HAVING COUNT(*) > 0",
                (args.interval,),
            )
        }
    return {r["ticker"] for r in store.query("SELECT ticker FROM trades GROUP BY ticker")}


def cmd_hist_markets(args: argparse.Namespace, client: KalshiClient, store: Store) -> None:
    """Enumerate archived markets via ``/historical/markets?series_ticker=``.

    This is the whole-history counterpart to ``markets --by-series``. The
    historical endpoint has **no date filters** (they are accepted and silently
    ignored), so the series catalog is the only usable index. One series ticker
    also resolves its legacy pre-``KX`` markets, so this reaches back to 2021
    in a single sweep.
    """
    started = _now()
    cats = _category_map(store)
    if args.series:
        targets = [s.strip() for s in args.series.split(",") if s.strip()]
    else:
        wanted = [c.strip() for c in (args.categories or "").split(",") if c.strip()]
        q = "SELECT series_ticker FROM series"
        p: list[Any] = []
        if wanted:
            q += " WHERE category IN (%s)" % ",".join("?" * len(wanted))
            p = wanted
        targets = [r["series_ticker"] for r in store.query(q, p)]
    prefixes = DEFAULT_EXCLUDE_PREFIXES if args.exclude_default_prefixes else ()
    targets = [t for t in targets if not any(t.startswith(x) for x in prefixes)]
    log.info("hist-markets: %d series", len(targets))

    def fetch(series_ticker: str) -> list[dict[str, Any]]:
        return list(
            client.get_historical_markets(
                series_ticker=series_ticker, page_limit=1000, max_pages=args.max_pages
            )
        )

    kept = empty = failed = 0
    done = 0
    for st, rows, exc in _pmap(fetch, targets, args.workers):
        done += 1
        if exc is not None:
            failed += 1
            log.warning("hist-markets %s: %s", st, exc)
            continue
        if not rows:
            empty += 1
            continue
        if args.min_volume:
            rows = [
                r for r in rows
                if float(r.get("volume_fp") or r.get("volume") or 0) >= args.min_volume
            ]
        kept += store.upsert_markets(
            rows, cats, series_ticker=st, source="hist", compact=not args.full_json
        )
        if done % 200 == 0:
            log.info(
                "hist-markets %d/%d series  kept=%d empty=%d  reqs=%d  db=%.0fMB",
                done, len(targets), kept, empty, client.request_count,
                store.size_bytes() / 1e6,
            )
        if _over_budget(store, args.max_db_mb):
            log.warning("db budget %.0fMB reached — stopping", args.max_db_mb)
            break

    store.backfill_categories()
    store.log_pull(
        "hist-markets", args.categories or (args.series or "all"), kept, started,
        f"series={len(targets)} empty={empty} failed={failed}",
    )
    print(
        f"hist-markets: {kept} archived market rows from {len(targets)} series"
        f" ({empty} series had none, {failed} failed)"
    )


def cmd_hist_trades(args: argparse.Namespace, client: KalshiClient, store: Store) -> None:
    """Pull the tick tape for a selected universe, routed across the cutoff.

    Markets live in exactly one tier, so each ticker is tried on the tier its
    ``source`` column names and falls back to the other. Pages are capped by
    ``--max-pages`` (1,000 trades each) and the whole run by ``--max-rows``;
    both matter because the 2024 presidential markets alone run to hundreds of
    thousands of prints.

    Each worker buffers one market's whole tape in memory before the main
    thread writes it, so peak RSS is roughly
    ``workers x max_pages x 1000 x ~1 KB``. Keep ``--max-pages`` around 100 for
    wide sweeps and run the handful of giant markets separately with fewer
    workers.
    """
    started = _now()
    rows = _select_tickers(store, args)
    log.info("hist-trades: %d markets selected", len(rows))
    min_ts = _utc(args.min_ts) if args.min_ts else None
    max_ts = _utc(args.max_ts) if args.max_ts else None

    def fetch(row: Any) -> tuple[list[dict[str, Any]], str]:
        return client.trades_any(
            row["ticker"],
            min_ts=min_ts,
            max_ts=max_ts,
            max_pages=args.max_pages,
            prefer="live" if row["source"] == "live" else "hist",
        )

    total = 0
    per_tier: Counter[str] = Counter()
    empty = failed = done = 0
    for row, res, exc in _pmap(fetch, rows, args.workers):
        done += 1
        if exc is not None:
            failed += 1
            log.warning("trades %s: %s", row["ticker"], exc)
            continue
        trades, tier = res
        if not trades:
            empty += 1
            continue
        per_tier[tier] += len(trades)
        total += store.upsert_trades(trades, source=tier or "hist")
        if done % 25 == 0:
            log.info(
                "trades %d/%d  rows=%d  reqs=%d  db=%.0fMB",
                done, len(rows), total, client.request_count, store.size_bytes() / 1e6,
            )
        if args.max_rows and total >= args.max_rows:
            log.warning("row cap %d reached — stopping", args.max_rows)
            break
        if _over_budget(store, args.max_db_mb):
            log.warning("db budget %.0fMB reached — stopping", args.max_db_mb)
            break

    store.log_pull(
        "hist-trades", (args.series or args.categories or args.tickers or "selection")[:200],
        total, started, f"markets={done} empty={empty} failed={failed} tiers={dict(per_tier)}",
    )
    print(
        f"hist-trades: {total} rows over {done - empty - failed} markets"
        f" ({dict(per_tier)}), {empty} empty, {failed} failed"
    )


def cmd_hist_candles(args: argparse.Namespace, client: KalshiClient, store: Store) -> None:
    """Candlesticks for a selected universe over a per-market window.

    Handles both tiers and both JSON shapes. ``--window final-72h --interval 1``
    is the event-night pull; ``--window final-15d --interval 60`` is the
    favorite-longshot universe; ``--window full --interval 1440`` is one request
    per market for its whole life.
    """
    started = _now()
    rows = _select_tickers(store, args)
    log.info("hist-candles: %d markets, interval=%d window=%s",
             len(rows), args.interval, args.window)

    def fetch(row: Any) -> tuple[list[dict[str, Any]], str]:
        start, end = _window(row, args.window)
        return client.candlesticks_any(
            row["ticker"], start, end, args.interval,
            series_ticker=row["series_ticker"],
            prefer="live" if row["source"] == "live" else "hist",
        )

    total = empty = failed = done = 0
    per_tier: Counter[str] = Counter()
    for row, res, exc in _pmap(fetch, rows, args.workers):
        done += 1
        if exc is not None:
            failed += 1
            log.warning("candles %s: %s", row["ticker"], exc)
            continue
        candles, tier = res
        if not candles:
            empty += 1
            continue
        per_tier[tier] += len(candles)
        total += store.upsert_candlesticks(
            row["ticker"], args.interval, candles, source=tier or "hist"
        )
        if done % 100 == 0:
            log.info(
                "candles %d/%d  rows=%d  reqs=%d  db=%.0fMB",
                done, len(rows), total, client.request_count, store.size_bytes() / 1e6,
            )
        if args.max_rows and total >= args.max_rows:
            log.warning("row cap %d reached — stopping", args.max_rows)
            break
        if _over_budget(store, args.max_db_mb):
            log.warning("db budget %.0fMB reached — stopping", args.max_db_mb)
            break

    store.log_pull(
        "hist-candles",
        f"interval={args.interval} window={args.window} "
        + (args.series or args.categories or "selection")[:150],
        total, started, f"markets={done} empty={empty} failed={failed} tiers={dict(per_tier)}",
    )
    print(
        f"hist-candles: {total} rows @ {args.interval}min over"
        f" {done - empty - failed} markets ({dict(per_tier)}), {empty} empty, {failed} failed"
    )


def cmd_discover(args: argparse.Namespace, client: KalshiClient, store: Store) -> None:
    """Find (and optionally store) every market that traded in a time window.

    Walks the exchange-wide ``/historical/trades`` firehose, which is the only
    way to enumerate archived markets whose series is no longer in the live
    catalog — and the cheapest way to build an event-night universe, since it
    surfaces exactly the tickers that actually printed that night. Metadata for
    unknown tickers is then fetched in batches of 100 via
    ``/historical/markets?tickers=``.
    """
    started = _now()
    lo, hi = _utc(args.min_ts), _utc(args.max_ts)
    known = {r["ticker"] for r in store.query("SELECT ticker FROM markets")}
    seen: Counter[str] = Counter()
    trades_seen = 0
    stored_trades = 0

    # Walk backwards in 1-hour slices so a long window still makes progress.
    step = args.slice_hours * 3600
    for wstart in range(lo, hi, step):
        wend = min(wstart + step, hi)
        batch: list[dict[str, Any]] = []
        for t in client.get_historical_trades(
            min_ts=wstart, max_ts=wend, page_limit=1000, max_pages=args.max_pages
        ):
            trades_seen += 1
            seen[t["ticker"]] += 1
            if args.store_trades:
                batch.append(t)
                if len(batch) >= 5000:
                    stored_trades += store.upsert_trades(batch, source="hist")
                    batch.clear()
        if args.store_trades:
            stored_trades += store.upsert_trades(batch, source="hist")
        log.info(
            "discover %s: %d trades, %d distinct tickers so far (db=%.0fMB)",
            _iso(wstart), trades_seen, len(seen), store.size_bytes() / 1e6,
        )
        if _over_budget(store, args.max_db_mb):
            log.warning("db budget %.0fMB reached — stopping", args.max_db_mb)
            break

    new = [t for t, n in seen.most_common() if t not in known and n >= args.min_trades]
    log.info("discover: %d distinct tickers, %d unknown to the store", len(seen), len(new))

    cats = _category_map(store)
    stored = 0
    for i in range(0, len(new), 100):
        chunk = new[i : i + 100]
        rows = list(client.get_historical_markets(tickers=chunk, page_limit=1000))
        stored += store.upsert_markets(rows, cats, source="hist", compact=True)
    store.backfill_categories()
    store.log_pull(
        "discover", f"{args.min_ts}..{args.max_ts}", stored, started,
        f"trades={trades_seen} tickers={len(seen)} new={len(new)} stored_trades={stored_trades}",
    )
    print(
        f"discover {args.min_ts}..{args.max_ts}: {trades_seen:,} trades,"
        f" {len(seen):,} distinct tickers, {len(new):,} new -> {stored} market rows"
        + (f", {stored_trades:,} trades stored" if args.store_trades else "")
    )


def cmd_cutoff(args: argparse.Namespace, client: KalshiClient, store: Store) -> None:
    """Print the live/historical tier boundary."""
    for k, v in sorted(client.get_historical_cutoff().items()):
        print(f"  {k:34s} {v}")


# --------------------------------------------------------------------------
# subcommand: stats
# --------------------------------------------------------------------------
def cmd_stats(args: argparse.Namespace, client: KalshiClient, store: Store) -> None:
    """Print row counts, category breakdown and date coverage."""
    counts = store.counts()
    print("=== row counts ===")
    for k, v in counts.items():
        print(f"  {k:14s} {v:>12,}")
    print(f"  {'db size':14s} {store.size_bytes()/1e6:>11,.1f} MB")

    print("\n=== markets by category ===")
    for r in store.query(
        """SELECT COALESCE(category,'(none)') c, COUNT(*) n,
                  SUM(status IN ('finalized','settled')) settled,
                  ROUND(SUM(volume)) vol
           FROM markets GROUP BY c ORDER BY n DESC"""
    ):
        print(f"  {r['c']:26s} {r['n']:>7,}  settled={r['settled']:>7,}  vol={r['vol'] or 0:>14,.0f}")

    print("\n=== settlement results ===")
    for r in store.query(
        "SELECT COALESCE(NULLIF(result,''),'(empty)') r, COUNT(*) n"
        " FROM markets GROUP BY r ORDER BY n DESC"
    ):
        print(f"  {r['r']:14s} {r['n']:>8,}")

    print("\n=== date coverage ===")
    r = store.query(
        "SELECT MIN(close_time) a, MAX(close_time) b FROM markets"
        " WHERE status IN ('finalized','settled')"
    )[0]
    print(f"  settled close_time: {r['a']} .. {r['b']}")
    for r in store.query(
        "SELECT period_interval p, COUNT(*) n, COUNT(DISTINCT ticker) m,"
        " MIN(ts) a, MAX(ts) b FROM candlesticks GROUP BY p"
    ):
        print(
            f"  candles p={r['p']:<5} {r['n']:>10,} rows over {r['m']:>5,} markets"
            f"  {_iso(r['a'])} .. {_iso(r['b'])}"
        )
    r = store.query("SELECT COUNT(*) n, COUNT(DISTINCT ticker) m, MIN(ts) a, MAX(ts) b FROM trades")[0]
    if r["n"]:
        print(f"  trades      {r['n']:>10,} rows over {r['m']:>5,} markets  {_iso(r['a'])} .. {_iso(r['b'])}")

    print("\n=== top series by settled markets ===")
    for r in store.query(
        """SELECT series_ticker s, COUNT(*) n FROM markets
           WHERE status IN ('finalized','settled') GROUP BY s ORDER BY n DESC LIMIT 15"""
    ):
        print(f"  {r['s']:32s} {r['n']:>7,}")

    print("\n=== candle price-coverage (no-trade periods have NULL price) ===")
    r = store.query(
        "SELECT COUNT(*) n, SUM(price_close IS NOT NULL) withprice,"
        " SUM(yes_bid_close IS NOT NULL) withbid FROM candlesticks"
    )[0]
    if r["n"]:
        print(
            f"  {r['n']:,} candles: {100*r['withprice']/r['n']:.1f}% have last-trade price,"
            f" {100*r['withbid']/r['n']:.1f}% have a yes_bid quote"
        )


# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pull", description=__doc__.split("\n")[0])
    p.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite path")
    p.add_argument("--rate", type=float, default=5.0, help="requests/sec (default 5)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("series", help="refresh the series catalog")
    sp.set_defaults(func=cmd_series)

    sp = sub.add_parser("events", help="pull events (category + mutually_exclusive)")
    sp.add_argument("--status", choices=["unopened", "open", "closed", "settled"])
    sp.add_argument("--max-pages", type=int, default=None)
    sp.add_argument("--exclude-series-prefix", help="comma-separated; empty string disables")
    sp.set_defaults(func=cmd_events)

    sp = sub.add_parser("markets", help="pull markets")
    sp.add_argument("--settled-since", metavar="YYYY-MM-DD")
    sp.add_argument("--settled-until", metavar="YYYY-MM-DD")
    sp.add_argument("--status", choices=["unopened", "open", "closed", "settled"])
    sp.add_argument("--by-series", action="store_true", help="enumerate per series (bounded)")
    sp.add_argument("--categories", help="comma-separated, for --by-series")
    sp.add_argument("--settled-only", action="store_true", help="with --by-series, keep finalized only")
    sp.add_argument(
        "--exclude-series-prefix",
        help="comma-separated prefixes to skip; empty string disables. "
        f"Default: {','.join(DEFAULT_EXCLUDE_PREFIXES)}",
    )
    sp.add_argument("--exclude-frequency", default="fifteen_min,hourly")
    sp.add_argument("--min-volume", type=float, default=0.0)
    sp.add_argument("--max-markets-per-day", type=int, default=0)
    sp.set_defaults(func=cmd_markets)

    sp = sub.add_parser("candles", help="pull candlesticks")
    sp.add_argument("--tickers", help="comma-separated market tickers")
    sp.add_argument("--sample", type=int, default=300)
    sp.add_argument("--strategy", choices=["stratified", "top-volume"], default="stratified")
    sp.add_argument("--interval", type=int, choices=[1, 60, 1440], default=60)
    sp.add_argument("--final-48h-minute", action="store_true", help="also pull 1-min for final 48h")
    sp.add_argument("--min-volume", type=float, default=100.0)
    sp.set_defaults(func=cmd_candles)

    sp = sub.add_parser("trades", help="pull full trade history")
    sp.add_argument("--ticker", required=True, help="comma-separated market tickers")
    sp.add_argument("--max-pages", type=int, default=None)
    sp.set_defaults(func=cmd_trades)

    sp = sub.add_parser("stats", help="report DB coverage")
    sp.set_defaults(func=cmd_stats)

    # -- /historical/* -----------------------------------------------------
    def add_selector(sp: argparse.ArgumentParser) -> None:
        """Universe-selection flags shared by hist-trades / hist-candles."""
        sp.add_argument("--tickers", help="comma-separated market tickers")
        sp.add_argument("--series", help="comma-separated series tickers")
        sp.add_argument("--series-like", help="SQL LIKE pattern on series_ticker")
        sp.add_argument("--categories", help="comma-separated categories")
        sp.add_argument("--exclude-categories", help="comma-separated categories to drop")
        sp.add_argument("--min-volume", type=float, default=0.0)
        sp.add_argument("--settled-only", action="store_true")
        sp.add_argument("--source", choices=["live", "hist"], help="restrict by API tier")
        sp.add_argument("--limit", type=int, default=0, help="cap markets (highest volume first)")
        sp.add_argument("--skip-existing", action="store_true",
                        help="skip tickers that already have rows in the target table")
        sp.add_argument("--workers", type=int, default=8)
        sp.add_argument("--max-rows", type=int, default=0, help="stop after N stored rows")
        sp.add_argument("--max-db-mb", type=float, default=0.0,
                        help="stop when the DB reaches this size")

    sp = sub.add_parser("hist-markets", help="archived market metadata (all history)")
    sp.add_argument("--categories", help="comma-separated series categories")
    sp.add_argument("--series", help="comma-separated series tickers")
    sp.add_argument("--min-volume", type=float, default=0.0)
    sp.add_argument("--max-pages", type=int, default=None)
    sp.add_argument("--workers", type=int, default=8)
    sp.add_argument("--full-json", action="store_true",
                    help="keep the untrimmed payload in raw_json (~2x the disk)")
    sp.add_argument("--exclude-default-prefixes", action="store_true", default=True)
    sp.add_argument("--no-exclude-default-prefixes", dest="exclude_default_prefixes",
                    action="store_false")
    sp.add_argument("--max-db-mb", type=float, default=0.0)
    sp.set_defaults(func=cmd_hist_markets)

    sp = sub.add_parser("hist-trades", help="tick tape across both API tiers")
    add_selector(sp)
    sp.add_argument("--max-pages", type=int, default=None, help="pages (1000 trades) per market")
    sp.add_argument("--min-ts", metavar="YYYY-MM-DD[THH:MM]")
    sp.add_argument("--max-ts", metavar="YYYY-MM-DD[THH:MM]")
    sp.set_defaults(func=cmd_hist_trades)

    sp = sub.add_parser("hist-candles", help="candlesticks across both API tiers")
    add_selector(sp)
    sp.add_argument("--interval", type=int, choices=[1, 60, 1440], default=60)
    sp.add_argument("--window", default="full",
                    help="'full', or 'final-<N>h' / 'final-<N>d' (e.g. final-72h)")
    sp.set_defaults(func=cmd_hist_candles)

    sp = sub.add_parser("discover", help="find markets that traded in a window (firehose)")
    sp.add_argument("--min-ts", required=True, metavar="YYYY-MM-DD[THH:MM]")
    sp.add_argument("--max-ts", required=True, metavar="YYYY-MM-DD[THH:MM]")
    sp.add_argument("--slice-hours", type=int, default=6)
    sp.add_argument("--max-pages", type=int, default=None)
    sp.add_argument("--min-trades", type=int, default=1,
                    help="only resolve tickers with at least this many prints")
    sp.add_argument("--store-trades", action="store_true", help="also store the tape it walks")
    sp.add_argument("--max-db-mb", type=float, default=0.0)
    sp.set_defaults(func=cmd_discover)

    sp = sub.add_parser("cutoff", help="show the live/historical tier boundary")
    sp.set_defaults(func=cmd_cutoff)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    with KalshiClient(rate_per_sec=args.rate) as client, Store(args.db) as store:
        args.func(args, client, store)
    return 0


if __name__ == "__main__":
    sys.exit(main())

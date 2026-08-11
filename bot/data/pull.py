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

Note:
    Kalshi purges settled markets from the public API roughly 90 days after
    close, so ``--settled-since`` cannot reach further back than that no matter
    what date is passed. See ``NOTES.md``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import random
import sys
import time
from collections import Counter, defaultdict
from typing import Any, Iterable, Sequence

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
    """Parse ``YYYY-MM-DD`` to a unix second timestamp at UTC midnight."""
    return int(
        dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
    )


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

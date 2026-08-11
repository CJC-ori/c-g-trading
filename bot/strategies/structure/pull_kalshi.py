"""Runner: pull hourly candles for every settled multi-outcome event leg.

    python -m bot.strategies.structure.pull_kalshi [--window-days 90]
                                                   [--workers 6] [--limit N]

Writes to ``data/structure/structure.db`` (this package's own store — the
shared ``data/kalshi.db`` is never written). Idempotent: events already in
``pull_log`` are skipped, so the pull can be resumed.
"""
from __future__ import annotations

import argparse
import time

from bot.strategies.structure import kalshi_candles as kc
from bot.strategies.structure import kalshi_events as ke


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=ke.DEFAULT_DB)
    ap.add_argument("--store", default=kc.DEFAULT_STORE)
    ap.add_argument("--window-days", type=int, default=90)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ladders", action="store_true",
                    help="pull monotone (mutually_exclusive=0) ladder events instead")
    args = ap.parse_args()

    evs = ke.load_events(args.db, mutually_exclusive=not args.ladders)
    if args.ladders:
        evs = [e for e in evs if e.n >= 3 and e.total_volume >= 5000]
        evs.sort(key=lambda e: -e.total_volume)
    jobs = ke.pull_jobs(evs, args.window_days)
    if args.limit:
        jobs = jobs[: args.limit]
    print(f"events={len(jobs)} legs={sum(len(j[1]) for j in jobs)}", flush=True)
    t0 = time.time()
    stats = kc.pull_events_parallel(args.store, jobs, workers=args.workers)
    print(
        f"done in {time.time() - t0:.0f}s: events={stats.events} rows={stats.rows:,} "
        f"requests={stats.requests} fallback_tickers={stats.fallback_tickers} "
        f"errors={stats.errors}"
    )


if __name__ == "__main__":
    main()

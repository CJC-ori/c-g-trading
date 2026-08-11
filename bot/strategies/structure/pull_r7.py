"""R7 data pull: audit the curated pairs, then fetch both venues' history.

    python -m bot.strategies.structure.pull_r7 [--window-days 30] [--audit-only]

Steps:
 1. resolve every ``PairSpec`` to a live Polymarket market (Gamma event ->
    leg by ``groupItemTitle``) and a Kalshi market row;
 2. run ``pairs.audit_pair`` — a pair that fails ANY structural check is
    dropped and reported, never traded;
 3. pull 1-minute history for the surviving pairs over the last
    ``--window-days`` of the Kalshi market's life:
      * Kalshi: 1-minute candles (bid/ask/volume) into
        ``data/structure/structure.db`` via this package's own puller;
      * Polymarket: 1-minute CLOB midpoints into
        ``data/polymarket/pm_prices.db`` (chained 15-day windows anchored on
        ``closedTime``).
Both stores sit under the repo-root ``/data/`` directory, which .gitignore
already excludes.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import urllib.parse

from bot.strategies.structure import kalshi_candles as kc
from bot.strategies.structure import pairs as P
from bot.strategies.structure import polymarket as pmx

PM_STORE = "data/polymarket/pm_prices.db"
PM_META = "data/polymarket/pm_markets.json"
AUDIT_OUT = "reports/structure/r7_pair_audit.json"

PM_SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
  token_id TEXT NOT NULL, ts INTEGER NOT NULL, p REAL NOT NULL,
  PRIMARY KEY (token_id, ts)
);
CREATE TABLE IF NOT EXISTS markets (
  slug TEXT PRIMARY KEY, pair_id TEXT, condition_id TEXT, yes_token TEXT,
  question TEXT, start_ts INTEGER, closed_ts INTEGER, fees_enabled INTEGER,
  fee_rate REAL, tick_size REAL, resolved_yes INTEGER, clob_winner TEXT,
  description TEXT
);
"""


def open_pm_store(path: str = PM_STORE) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, timeout=60)
    conn.executescript(PM_SCHEMA)
    return conn


def kalshi_market_row(db_path: str, ticker: str) -> dict | None:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        r = conn.execute(
            "SELECT ticker, rules_primary, rules_secondary, close_time, result,"
            " settlement_ts, volume, series_ticker, title, yes_sub_title"
            " FROM markets WHERE ticker = ?",
            (ticker,),
        ).fetchone()
    finally:
        conn.close()
    if r is None:
        return None
    from bot.strategies.structure.kalshi_events import iso_to_ts

    return {
        "ticker": r[0],
        "rules_primary": r[1] or "",
        "rules_secondary": r[2] or "",
        "close_time_ts": iso_to_ts(r[3]),
        "result": r[4] or "",
        "settlement_ts": iso_to_ts(r[5]),
        "volume": r[6] or 0.0,
        "series_ticker": r[7] or "",
        "title": r[8] or "",
        "yes_sub_title": r[9] or "",
    }


def resolve_pm_market(
    client: pmx.PolymarketClient, spec: P.PairSpec
) -> pmx.PmMarket | None:
    """Gamma event -> the leg whose ``groupItemTitle`` matches the spec.

    Matching is on the venue's own structured leg label, not on free text.
    """
    url = (
        f"{pmx.GAMMA}/events/slug/"
        f"{urllib.parse.quote(spec.pm_event_slug)}"
    )
    try:
        data = pmx._request(url)
    except (ValueError, RuntimeError):
        return None
    ev = data[0] if isinstance(data, list) else data
    want = P.normalize_text(spec.pm_leg_title)
    for row in ev.get("markets", []):
        if P.normalize_text(row.get("groupItemTitle")) == want:
            return pmx.market_from_row(row)
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/kalshi.db")
    ap.add_argument("--kalshi-store", default=kc.DEFAULT_STORE)
    ap.add_argument("--pm-store", default=PM_STORE)
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--audit-only", action="store_true")
    args = ap.parse_args()

    client = pmx.PolymarketClient()
    specs = P.curated_pairs()
    audits, good = [], []
    pm_by_pair: dict[str, pmx.PmMarket] = {}
    k_by_pair: dict[str, dict] = {}

    for spec in specs:
        k = kalshi_market_row(args.db, spec.kalshi_ticker)
        m = resolve_pm_market(client, spec)
        pm_view = None
        if m is not None:
            pm_view = {
                "slug": m.slug,
                "description": m.description,
                "closed_ts": m.closed_ts,
                "resolved_yes": m.resolved_yes,
                "clob_winner": client.clob_winner(m.condition_id),
            }
        a = P.audit_pair(spec, k, pm_view)
        audits.append(
            {
                "pair_id": spec.pair_id,
                "family": spec.family,
                "kalshi_ticker": spec.kalshi_ticker,
                "pm_slug": m.slug if m else None,
                "subject": spec.subject,
                "strike": spec.strike,
                "resolution_date": spec.resolution_date,
                "kalshi_source": spec.kalshi_source,
                "pm_source": spec.pm_source,
                "audit": spec.audit,
                "checks": a.checks,
                "failures": a.failures,
                "ok": a.ok,
                "kalshi_result": (k or {}).get("result"),
                "pm_resolved_yes": (pm_view or {}).get("resolved_yes"),
                "kalshi_volume": (k or {}).get("volume"),
            }
        )
        print(f"{spec.pair_id:<26} ok={a.ok} failures={a.failures}", flush=True)
        if a.ok and m is not None and k is not None:
            good.append(spec)
            pm_by_pair[spec.pair_id] = m
            k_by_pair[spec.pair_id] = k

    os.makedirs(os.path.dirname(AUDIT_OUT), exist_ok=True)
    with open(AUDIT_OUT, "w") as fh:
        json.dump(
            {
                "n_specs": len(specs),
                "n_verified": len(good),
                "pairs": audits,
                "rejected_candidates_documented_in": "bot/strategies/structure/pairs.py",
            },
            fh,
            indent=2,
        )
    print(f"\nverified {len(good)}/{len(specs)} pairs -> {AUDIT_OUT}")
    if args.audit_only:
        return

    # ---- Kalshi 1-minute candles -----------------------------------------
    conn = kc.open_store(args.kalshi_store)
    tickers = [s.kalshi_ticker for s in good]
    jobs: list[tuple[str, list[str], int, int]] = []
    for s in good:
        k = k_by_pair[s.pair_id]
        end = k["close_time_ts"]
        jobs.append((f"r7:{s.pair_id}", [s.kalshi_ticker], end - args.window_days * 86400, end))
    stats = kc.PullStats()
    for et, tks, s_ts, e_ts in jobs:
        if kc.already_pulled(conn, et):
            continue
        n = kc.pull_event(conn, et, tks, s_ts, e_ts, period_interval=1, stats=stats)
        print(f"kalshi 1m {et}: {n:,} candles", flush=True)
    conn.close()

    # ---- Polymarket 1-minute midpoints -----------------------------------
    pconn = open_pm_store(args.pm_store)
    meta = []
    for s in good:
        m = pm_by_pair[s.pair_id]
        k = k_by_pair[s.pair_id]
        end = m.closed_ts or k["close_time_ts"]
        start = max(m.start_ts or 0, end - args.window_days * 86400)
        have = pconn.execute(
            "SELECT COUNT(*) FROM prices WHERE token_id = ? AND ts >= ?",
            (m.yes_token, start),
        ).fetchone()[0]
        if have < 100:
            pts = client.full_history(m.yes_token, start, end, fidelity=1)
            pconn.executemany(
                "INSERT OR REPLACE INTO prices VALUES (?,?,?)",
                [(m.yes_token, t, p) for t, p in pts],
            )
        else:
            pts = []
        pconn.execute(
            "INSERT OR REPLACE INTO markets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                m.slug, s.pair_id, m.condition_id, m.yes_token, m.question,
                m.start_ts, m.closed_ts, int(m.fees_enabled), m.fee_rate,
                m.tick_size,
                None if m.resolved_yes is None else int(m.resolved_yes),
                client.clob_winner(m.condition_id), m.description,
            ),
        )
        pconn.commit()
        meta.append(
            {
                "pair_id": s.pair_id, "slug": m.slug, "yes_token": m.yes_token,
                "closed_ts": m.closed_ts, "fee_rate": m.fee_rate,
                "fees_enabled": m.fees_enabled, "tick_size": m.tick_size,
                "new_points": len(pts),
            }
        )
        print(
            f"pm 1m {s.pair_id}: +{len(pts):,} pts (had {have:,}) "
            f"{dt.datetime.utcfromtimestamp(start):%Y-%m-%d}..",
            flush=True,
        )
    pconn.close()
    with open(PM_META, "w") as fh:
        json.dump(meta, fh, indent=2)
    print(f"kalshi 1m pull: {stats.rows:,} rows / {stats.requests} requests")


if __name__ == "__main__":
    main()

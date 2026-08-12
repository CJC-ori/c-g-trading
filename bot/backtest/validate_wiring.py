"""Validation gauntlet for the real-data wiring (run: python -m bot.backtest.validate_wiring).

Checks, against the real data/kalshi.db:

  A. Michigan Senate primary replay: the documented election-night panic dip
     must be visible in our candle data for KXSENATEMID-26-AELS (winner) and
     KXSENATEMID-26-HSTE (settled no, $20.5M).
  B. HoldFavorite baseline over the default research universe: P&L, trade
     count, fill rate + plausibility.
  C. Conservation: for every traded market, an independent replay of the
     recorded fills + settlement must reproduce the engine's cash exactly.
  D. Spread reality: bid/ask spread distribution by category; how often the
     harness's +/-1c fallback could fire and where it would mislead.
  E. Units: prevalence and magnitude of sub-cent rounding at the adapter.

Read-only. Prints a report to stdout; exits non-zero on hard failures.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import sys
import time

from bot.backtest import fills
from bot.backtest.dataport import SqliteHistoryProvider
from bot.backtest.engine import run_backtest
from bot.backtest.universe import research_universe
from bot.strategies.baseline import HoldFavorite

DB = "data/kalshi.db"
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def utc(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%m-%d %H:%M")


# ---------------------------------------------------------------------------
# A. Michigan replay
# ---------------------------------------------------------------------------

def michigan_replay(provider: SqliteHistoryProvider) -> None:
    print("\n=== A. Michigan Senate primary replay (election night 2026-08-04/05) ===")
    # Election-night window: Aug 4 18:00 ET .. Aug 5 12:00 ET (UTC-4)
    w0 = int(dt.datetime(2026, 8, 4, 22, tzinfo=dt.timezone.utc).timestamp())
    w1 = int(dt.datetime(2026, 8, 5, 16, tzinfo=dt.timezone.utc).timestamp())
    for ticker, expect in (("KXSENATEMID-26-AELS", "yes"), ("KXSENATEMID-26-HSTE", "no")):
        info = next(iter(provider.iter_markets(tickers=[ticker])))
        s = provider.settlement(ticker)
        # Clip at market close: post-close candles carry an empty book
        # (bid 0 / ask 100 -> mid 50), which is real but not a price.
        end = min(w1, info.close_ts)
        candles = [c for c in provider.candles(ticker, 3600) if w0 <= c.end_ts <= end]
        mids = [
            (c.end_ts, (c.yes_bid_close + c.yes_ask_close) / 2)
            for c in candles
            if c.yes_bid_close is not None and c.yes_ask_close is not None
        ]
        path = "  ".join(f"{utc(t)}={m:.1f}" for t, m in mids)
        print(f"\n{ticker} (settled {s.result} @ {utc(s.settled_ts)}):")
        print(f"  mid path (c): {path}")
        check(f"{ticker} settlement is '{expect}'", s is not None and s.result == expect)
        if ticker == "KXSENATEMID-26-AELS" and mids:
            first, last = mids[0][1], mids[-1][1]
            low = min(c.low for c in candles)  # candle lows catch the intra-hour dip
            print(f"  shape: start {first:.1f} -> candle-low min {low} -> end {last:.1f}")
            check(
                "AELS panic-dip shape (start >=90 -> dip <=80 -> recover >=95)",
                first >= 90 and low <= 80 and last >= 95,
                f"{first:.0f} -> {low} -> {last:.0f}",
            )
        if ticker == "KXSENATEMID-26-HSTE" and mids:
            hi = max(c.high for c in candles)
            last = mids[-1][1]
            print(f"  shape: candle-high max {hi} -> end {last:.1f}")
            check(
                "HSTE mirror spike (panic bid >=20 -> collapse <=5)",
                hi >= 20 and last <= 5,
                f"max {hi} -> end {last:.0f}",
            )


# ---------------------------------------------------------------------------
# B + C. Baseline run + conservation
# ---------------------------------------------------------------------------

def conservation(result) -> None:
    print("\n=== C. Conservation check (independent replay of all fills) ===")
    bankroll = result.config.risk.bankroll_cents
    cash = bankroll
    fees = 0
    by_market: dict[str, tuple[int, int]] = {}  # qty, basis
    for f in result.fills:
        yes_price = f.price_cents if f.side == "yes" else 100 - f.price_cents
        sign = 1 if (f.side == "yes") == (f.action == "buy") else -1
        qty, basis = by_market.get(f.ticker, (0, 0))
        qty, basis, cash_delta, _ = fills.apply_fill_to_position(
            qty, basis, sign * f.count, yes_price
        )
        by_market[f.ticker] = (qty, basis)
        cash += cash_delta - f.fee_cents
        fees += f.fee_cents
    for ticker, (qty, basis) in by_market.items():
        s = result.settlements.get(ticker)
        if s is not None:
            cash += fills.settlement_payout_cents(
                qty, basis, s.result, s.settlement_value_cents
            )
        else:
            check(f"unsettled traded market {ticker}", False, "position stranded")
    check(
        "sum(fill cash deltas) - fees + settlement payouts == final cash",
        cash == result.final_cash_cents,
        f"recomputed {cash} vs engine {result.final_cash_cents} (exact integer match required)",
    )
    check(
        "net P&L identity: realized - fees == final - bankroll",
        result.realized_pnl_cents - result.fees_cents
        == result.final_cash_cents - bankroll,
        f"{result.realized_pnl_cents} - {result.fees_cents} vs {result.final_cash_cents - bankroll}",
    )
    check("fee recomputation matches", fees == result.fees_cents)


def baseline_run(provider: SqliteHistoryProvider) -> None:
    print("\n=== B. HoldFavorite baseline over the research universe ===")
    tickers = research_universe(DB)
    print(f"universe: {len(tickers)} markets (settled, non-sports/crypto, vol>=1000, candles)")
    t0 = time.monotonic()
    result = run_backtest(
        provider, HoldFavorite(), market_filters={"tickers": tickers}
    )
    wall = time.monotonic() - t0
    n_int = len(result.intents)
    n_placed = sum(1 for i in result.intents if i["clamped"] > 0)
    req = sum(i["requested"] for i in result.intents)
    clamped = sum(i["clamped"] for i in result.intents)
    filled = sum(f.count for f in result.fills)
    pnl = result.net_pnl_cents
    print(f"wall time: {wall:.1f}s | decision calls: {result.n_decision_calls:,}")
    print(f"markets loaded: {len(result.markets)} | settled: {len(result.settlements)}")
    print(
        f"intents: {n_int} ({req} contracts) -> placed {n_placed} ({clamped}) "
        f"-> filled {filled} contracts"
    )
    print(
        f"fill rate: {filled / clamped * 100 if clamped else 0:.0f}% of placed contracts, "
        f"{sum(1 for f in result.fills)} fills"
    )
    print(
        f"gross P&L: ${result.realized_pnl_cents / 100:+,.2f} | fees ${result.fees_cents / 100:,.2f}"
        f" | net ${pnl / 100:+,.2f} on $%.0f bankroll" % (result.bankroll_cents / 100)
    )
    traded = {f.ticker for f in result.fills}
    per = sorted(
        ((tk, d["net_pnl_cents"]) for tk, d in result.per_market.items() if tk in traded),
        key=lambda x: x[1],
    )
    wins = sum(1 for _, p in per if p > 0)
    print(f"markets traded: {len(per)} | winners {wins} | losers {sum(1 for _, p in per if p < 0)}")
    if per:
        print("  worst:", ", ".join(f"{t} ${p/100:+,.0f}" for t, p in per[:3]))
        print("  best: ", ", ".join(f"{t} ${p/100:+,.0f}" for t, p in per[-3:]))
    check("baseline traded a plausible number of markets (>=20)", len(per) >= 20)
    check(
        "P&L magnitude sane (|net| < 30% of bankroll)",
        abs(pnl) < 0.30 * result.bankroll_cents,
        f"net {pnl}c",
    )
    check("performance target: full-universe baseline < 5 min", wall < 300, f"{wall:.1f}s")
    conservation(result)


# ---------------------------------------------------------------------------
# D. Spread reality
# ---------------------------------------------------------------------------

def spread_reality() -> None:
    print("\n=== D. Spread reality check (hourly candles) ===")
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = conn.execute(
        """SELECT COALESCE(m.category,'?') cat,
                  COUNT(*) n,
                  SUM(c.yes_bid_close IS NULL OR c.yes_ask_close IS NULL) missing,
                  AVG((c.yes_ask_close - c.yes_bid_close) * 100) mean_c,
                  SUM((c.yes_ask_close - c.yes_bid_close) * 100 <= 2.0) tight
           FROM candlesticks c JOIN markets m ON m.ticker = c.ticker
           WHERE c.period_interval = 60
           GROUP BY cat ORDER BY n DESC"""
    ).fetchall()
    total = sum(r[1] for r in rows)
    missing = sum(r[2] for r in rows)
    print(f"{'category':<24}{'candles':>9}{'mean spread':>13}{'<=2c share':>12}")
    for cat, n, _miss, mean_c, tight in rows:
        print(f"{cat:<24}{n:>9,}{mean_c:>11.1f}c{tight / n * 100:>11.0f}%")
    check(
        "bid/ask present on every candle (fallback structurally unused there)",
        missing == 0,
        f"{missing}/{total} missing",
    )
    # The +/-1c fallback can only fire when the *last trade is newer than the
    # newest candle* (or no candle exists). With contiguous candle series and
    # decision points on candle boundaries, a trade can never be newer.
    gaps = conn.execute(
        """SELECT COUNT(*) FROM (
             SELECT ticker, ts - LAG(ts) OVER (PARTITION BY ticker ORDER BY ts) d
             FROM candlesticks WHERE period_interval = 60) WHERE d > 3600"""
    ).fetchone()[0]
    print(f"hourly-candle gaps (missing hours across all series): {gaps}")
    wide = conn.execute(
        """SELECT SUM((yes_ask_close - yes_bid_close) * 100 > 2.0), COUNT(*)
           FROM candlesticks WHERE period_interval = 60"""
    ).fetchone()
    print(
        f"candles with spread wider than the 2c the fallback would assume: "
        f"{wide[0] / wide[1] * 100:.0f}% — the fallback flatters illiquid books; "
        "quotes (always present) are used instead."
    )
    conn.close()


# ---------------------------------------------------------------------------
# E. Units / rounding impact
# ---------------------------------------------------------------------------

def rounding_impact() -> None:
    print("\n=== E. Sub-cent rounding impact (integer-cent decision) ===")
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

    def sub(expr, table, where=""):
        n, tot = conn.execute(
            f"SELECT SUM(ABS({expr}*100 - ROUND({expr}*100)) > 1e-9), COUNT(*)"
            f" FROM {table} {where}"
        ).fetchone()
        return n or 0, tot

    for label, expr, table, where in (
        ("candle bid closes", "yes_bid_close", "candlesticks", ""),
        ("candle ask closes", "yes_ask_close", "candlesticks", ""),
        ("trade prints", "yes_price", "trades", ""),
    ):
        n, tot = sub(expr, table, where)
        print(f"  {label}: {n:,}/{tot:,} ({n / tot * 100:.1f}%) carry sub-cent parts")
    mean_d, worst = conn.execute(
        "SELECT AVG(ABS(yes_bid_close*100 - ROUND(yes_bid_close*100))),"
        " MAX(ABS(yes_bid_close*100 - ROUND(yes_bid_close*100)))"
        " FROM candlesticks"
        " WHERE ABS(yes_bid_close*100 - ROUND(yes_bid_close*100)) > 1e-9"
    ).fetchone()
    print(
        f"  on affected quotes: mean |rounding delta| {mean_d:.2f}c, max {worst:.1f}c"
        " (deci-cent ticks)"
    )
    print(
        "  decision: harness stays integer-cent; bid floors / ask ceils "
        "(book never tighter than reality), price low ceils / high floors "
        "(maker-through can only under-fill), sizes floor - the distortion "
        "is sub-cent and pessimistic wherever it touches execution."
    )
    conn.close()


def main() -> int:
    provider = SqliteHistoryProvider(DB)
    michigan_replay(provider)
    baseline_run(provider)
    spread_reality()
    rounding_impact()
    print("\n=== Summary ===")
    if FAILURES:
        print(f"{len(FAILURES)} FAILURES: {FAILURES}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

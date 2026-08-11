"""Smoke tests for the local Kalshi store.

These validate schema shape and a handful of known rows in ``data/kalshi.db``.
The whole module is skipped when the DB is absent (fresh clone / CI without a
pull), so it is safe to run anywhere::

    pytest bot/data/test_smoke.py -v
"""

from __future__ import annotations

import pytest

from bot.data.store import DEFAULT_DB_PATH, Store, series_of

pytestmark = pytest.mark.skipif(
    not DEFAULT_DB_PATH.exists(),
    reason=f"no local store at {DEFAULT_DB_PATH}; run `python -m bot.data.pull` first",
)

#: The Michigan Senate Democratic primary, settled 2026-08-05.
#: Abdul El-Sayed won; Haley Stevens was the high-volume loser.
MI_WINNER = "KXSENATEMID-26-AELS"
MI_LOSER = "KXSENATEMID-26-HSTE"


@pytest.fixture(scope="module")
def store() -> Store:
    s = Store(DEFAULT_DB_PATH)
    yield s
    s.close()


def test_schema_tables_present(store: Store) -> None:
    names = {
        r["name"]
        for r in store.query("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"markets", "series", "events", "candlesticks", "trades", "pull_log"} <= names


def test_tables_are_populated(store: Store) -> None:
    counts = store.counts()
    assert counts["series"] > 1000, counts
    assert counts["markets"] > 1000, counts
    assert counts["candlesticks"] > 10_000, counts
    assert counts["trades"] > 1000, counts


def test_michigan_primary_winner_row(store: Store) -> None:
    rows = store.query("SELECT * FROM markets WHERE ticker = ?", (MI_WINNER,))
    assert rows, f"{MI_WINNER} missing from store"
    m = rows[0]
    assert m["result"] == "yes"
    assert m["status"] in ("finalized", "settled")
    assert m["series_ticker"] == "KXSENATEMID"
    assert m["category"] == "Elections"
    assert m["close_time"].startswith("2026-08-05")
    assert m["volume"] and m["volume"] > 1_000_000
    assert "El-Sayed" in (m["yes_sub_title"] or "")


def test_michigan_primary_has_exactly_one_winner(store: Store) -> None:
    rows = store.query(
        "SELECT ticker, result FROM markets WHERE series_ticker = 'KXSENATEMID'"
    )
    assert len(rows) >= 10
    winners = [r["ticker"] for r in rows if r["result"] == "yes"]
    assert winners == [MI_WINNER]


def test_michigan_winner_has_candles(store: Store) -> None:
    rows = store.query(
        "SELECT COUNT(*) n, MIN(ts) a, MAX(ts) b FROM candlesticks"
        " WHERE ticker = ? AND period_interval = 60",
        (MI_WINNER,),
    )[0]
    assert rows["n"] > 100, "expected a long hourly history for the MI winner"
    # History must span at least a month before settlement.
    assert rows["b"] - rows["a"] > 30 * 86400


def test_michigan_winner_has_trades(store: Store) -> None:
    rows = store.query(
        "SELECT COUNT(*) n, MIN(yes_price) lo, MAX(yes_price) hi FROM trades"
        " WHERE ticker = ?",
        (MI_WINNER,),
    )[0]
    assert rows["n"] > 100
    assert 0.0 <= rows["lo"] <= rows["hi"] <= 1.0, "prices must be dollars in [0,1]"


def test_prices_are_dollars_not_cents(store: Store) -> None:
    """Kalshi now quotes decimal dollars; nothing should exceed $1."""
    r = store.query(
        "SELECT MAX(price_close) a, MAX(yes_ask_close) b FROM candlesticks"
    )[0]
    assert (r["a"] or 0) <= 1.0
    assert (r["b"] or 0) <= 1.0


def test_candles_may_lack_price_but_keep_quotes(store: Store) -> None:
    """No-trade periods drop the price OHLC yet still carry bid/ask."""
    r = store.query(
        "SELECT COUNT(*) n FROM candlesticks"
        " WHERE price_close IS NULL AND yes_ask_close IS NOT NULL"
    )[0]
    assert r["n"] > 0, "expected zero-volume candles with quotes but no trade price"


def test_series_catalog_has_categories(store: Store) -> None:
    cats = {
        r["category"]
        for r in store.query("SELECT DISTINCT category FROM series WHERE category IS NOT NULL")
    }
    assert {"Politics", "Elections", "Economics"} <= cats


def test_no_excluded_spam_series_stored(store: Store) -> None:
    r = store.query(
        "SELECT COUNT(*) n FROM markets WHERE series_ticker LIKE 'KXMVE%'"
    )[0]
    assert r["n"] == 0, "multivariate parlay spam should have been filtered out"


def test_series_of_helper() -> None:
    assert series_of("KXSENATEMID-26-AELS") == "KXSENATEMID"
    assert series_of("SENATEMI-26-R") == "SENATEMI"
    assert series_of("NOSUFFIX") == "NOSUFFIX"

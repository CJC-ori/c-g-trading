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


# --------------------------------------------------------------------------
# /historical/* tier (added 2026-08-11)
# --------------------------------------------------------------------------
#: Donald Trump's 2024 presidential market — archived, unreachable from the
#: live tier, and the reference case for every historical-tier code path.
PRES_WINNER = "PRES-2024-DJT"
PRES_LOSER = "PRES-2024-KH"


def test_historical_tier_columns_exist(store: Store) -> None:
    cols = {r["name"] for r in store.query("PRAGMA table_info(markets)")}
    assert {"source", "price_ranges", "price_level_structure", "expiration_value"} <= cols
    cols = {r["name"] for r in store.query("PRAGMA table_info(trades)")}
    assert {"source", "taker_book_side", "taker_outcome_side"} <= cols
    assert "source" in {r["name"] for r in store.query("PRAGMA table_info(candlesticks)")}


def test_archived_markets_present_and_tagged(store: Store) -> None:
    """The historical tier must have contributed rows, tagged `hist`."""
    n = store.query("SELECT COUNT(*) n FROM markets WHERE source = 'hist'")[0]["n"]
    assert n > 10_000, f"only {n} archived markets stored"


def test_2024_presidential_market(store: Store) -> None:
    rows = store.query("SELECT * FROM markets WHERE ticker = ?", (PRES_WINNER,))
    assert rows, f"{PRES_WINNER} missing — the historical tier was not pulled"
    m = rows[0]
    assert m["source"] == "hist"
    assert m["result"] == "yes"
    assert m["series_ticker"] == "PRES", "archived rows must not take the series from the ticker"
    assert m["close_time"].startswith("2025-01-20")
    assert m["volume"] and m["volume"] > 200_000_000
    loser = store.query("SELECT result FROM markets WHERE ticker = ?", (PRES_LOSER,))
    assert loser and loser[0]["result"] == "no"


def test_history_reaches_before_the_live_cutoff(store: Store) -> None:
    """The live tier bottoms out ~90 days back; the archive must beat that."""
    r = store.query(
        "SELECT MIN(close_time) a FROM markets WHERE status IN ('finalized','settled')"
    )[0]
    assert r["a"] < "2023-01-01", f"earliest settled close_time is {r['a']}"

    r = store.query("SELECT MIN(ts) a FROM trades")[0]
    assert r["a"] < 1700000000, "trades should reach back past 2023-11"

    r = store.query("SELECT MIN(ts) a FROM candlesticks")[0]
    assert r["a"] < 1700000000, "candles should reach back past 2023-11"


def test_legacy_ticker_prefixes_map_to_modern_series(store: Store) -> None:
    """`HIGHNY-22NOV08-B57.5` belongs to series `KXHIGHNY`, not `HIGHNY`.

    Deriving the series from the ticker prefix silently mis-files every market
    renamed in the 2023 `KX` migration, which is most of the pre-2023 archive.
    """
    rows = store.query(
        "SELECT series_ticker FROM markets WHERE ticker LIKE 'HIGHNY-2%' AND source='hist'"
    )
    if not rows:
        pytest.skip("legacy weather markets not pulled")
    assert {r["series_ticker"] for r in rows} == {"KXHIGHNY"}


def test_tick_structure_captured(store: Store) -> None:
    """`price_ranges` is what a fill simulator must quantize to."""
    r = store.query(
        "SELECT COUNT(*) n, SUM(price_level_structure='tapered_deci_cent') deci"
        " FROM markets WHERE price_ranges IS NOT NULL"
    )[0]
    assert r["n"] > 100_000, "price_ranges should be populated for nearly every market"
    assert r["deci"] > 100, "expected deci-cent-tick markets (0.1c below 10c)"
    # Archived markets carry it too — verified against PRES-2024.
    r = store.query(
        "SELECT price_ranges FROM markets WHERE ticker = ?", (PRES_WINNER,)
    )
    if r:
        assert r[0]["price_ranges"] and "step" in r[0]["price_ranges"]


def test_historical_candles_parsed_from_bare_keys(store: Store) -> None:
    """The archive uses `close`/`volume`, not `close_dollars`/`volume_fp`.

    If the bare-key variant were missed, these rows would exist with every
    price column NULL.
    """
    rows = store.query(
        "SELECT COUNT(*) n, SUM(price_close IS NOT NULL) p, SUM(volume IS NOT NULL) v"
        " FROM candlesticks WHERE source = 'hist'"
    )[0]
    if not rows["n"]:
        pytest.skip("no historical candles pulled")
    assert rows["v"] == rows["n"], "volume must parse from the bare `volume` key"
    assert rows["p"] > 0, "price OHLC must parse from bare `close` keys"


def test_2024_election_night_minute_candles(store: Store) -> None:
    """The P-3 panic-ladder backtest needs election night at 1-minute grain."""
    rows = store.query(
        "SELECT COUNT(*) n, MIN(ts) a, MAX(ts) b FROM candlesticks"
        " WHERE ticker = ? AND period_interval = 1",
        (PRES_WINNER,),
    )[0]
    if not rows["n"]:
        pytest.skip("PRES-2024-DJT minute candles not pulled")
    # 2024-11-05 23:00Z .. 2024-11-06 07:00Z is the decisive window.
    assert rows["a"] <= 1730847600 and rows["b"] >= 1730876400
    moves = store.query(
        "SELECT MIN(price_close) lo, MAX(price_close) hi FROM candlesticks"
        " WHERE ticker = ? AND period_interval = 1 AND ts BETWEEN 1730847600 AND 1730876400",
        (PRES_WINNER,),
    )[0]
    assert moves["lo"] < 0.60 < 0.90 < moves["hi"], "expected the 59c -> 97c election-night run"


def test_trade_tape_carries_maker_fill_fields(store: Store) -> None:
    r = store.query(
        "SELECT COUNT(*) n, SUM(taker_book_side IN ('bid','ask')) sided FROM trades"
        " WHERE source = 'hist'"
    )[0]
    if not r["n"]:
        pytest.skip("no historical trades pulled")
    assert r["sided"] == r["n"], "taker_book_side is required for maker-fill simulation"


def test_weather_universe_pulled(store: Store) -> None:
    """The weather strategy needs all 12 daily-high city series."""
    from bot.data.pull import WEATHER_HIGH_SERIES

    rows = store.query(
        "SELECT series_ticker s, COUNT(*) n FROM markets WHERE series_ticker IN (%s)"
        " GROUP BY s" % ",".join("?" * len(WEATHER_HIGH_SERIES)),
        WEATHER_HIGH_SERIES,
    )
    got = {r["s"]: r["n"] for r in rows}
    missing = [s for s in WEATHER_HIGH_SERIES if got.get(s, 0) < 500]
    assert not missing, f"thin/missing weather series: {missing}"


def test_expiration_value_recorded_for_weather(store: Store) -> None:
    """`expiration_value` is the settled observation — free ground truth."""
    r = store.query(
        "SELECT COUNT(*) n FROM markets WHERE series_ticker LIKE 'KXHIGH%'"
        " AND expiration_value IS NOT NULL"
    )[0]
    assert r["n"] > 1000, "expected settled temperature readings on weather markets"

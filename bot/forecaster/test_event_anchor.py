"""Tests for v2 event-date anchoring + the forecast-freshness gate.

Run: python -m pytest bot/forecaster/test_event_anchor.py -q

The Wisconsin regression (reports/case-wisconsin/ANALYSIS.md §3): the
KXGOVWINOMD-26 nominee markets resolved on the 2026-08-11 primary while
carrying close_time 2026-11-03 pre-event, so the frozen D = close − 10d
(2026-10-24) never landed before the event. v2 anchoring must put D
pre-primary from metadata alone.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from bot.forecaster import event_anchor as EA
from bot.forecaster import hits as H

DB = Path(__file__).resolve().parents[2] / "data" / "kalshi.db"
DAY = 86400

# Pre-event close_time of the KXGOVWINOMD-26 markets (general-election
# date), documented from the pre-event DB snapshot in
# reports/case-wisconsin/ANALYSIS.md §3. The live DB now carries the
# realized early close (2026-08-12T11:18:25Z), so the pre-event state is
# reconstructed explicitly here.
WI_PRE_EVENT_CLOSE = "2026-11-03T15:00:00Z"
WI_PRIMARY = date(2026, 8, 11)


def _ts(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


needs_db = pytest.mark.skipif(not DB.exists(), reason="data/kalshi.db missing")


# ---------------------------------------------------------------------------
# text parser
# ---------------------------------------------------------------------------

def test_parse_explicit_date_formats():
    got = {d for d, _ in EA.parse_dates_from_text(
        "on Jun 23, 2026 and before August 1st, 2026; also 23 June 2026, "
        "2026-07-04T14:00:00Z and 6/9/2026")}
    assert got == {date(2026, 6, 23), date(2026, 8, 1),
                   date(2026, 7, 4), date(2026, 6, 9)}


def test_parse_month_year_is_not_a_date():
    # "Jun 2026" (month-year, no day) must NOT parse as Jun 20
    assert EA.parse_dates_from_text(
        "the CPI for Jun 2026, as published in July 2026") == []


def test_parse_yearless_date_uses_default_year():
    assert EA.parse_dates_from_text("checked on July 20", 2026) == \
        [(date(2026, 7, 20), "July 20")]
    # no default year -> skipped rather than guessed
    assert EA.parse_dates_from_text("checked on July 20", None) == []


def _anchor(title="", rules="", subtitle="", open_iso="2026-01-01T00:00:00Z",
            close_iso="2026-12-01T00:00:00Z", raw_json=None):
    return EA.derive_event_anchor(
        title=title, subtitle=subtitle, rules=rules,
        open_ts=_ts(open_iso), close_ts=_ts(close_iso), raw_json=raw_json)


def test_anchor_picks_latest_in_life_date():
    # background date (Jan 12) + operative deadline (Feb 15): take the latest
    a = _anchor(
        rules="Will the strike that began on January 12, 2026 end "
              "before Feb 15, 2026?",
        open_iso="2026-01-13T00:00:00Z", close_iso="2026-02-15T05:00:00Z")
    assert a.source == "explicit_text"
    assert a.event_date == date(2026, 2, 15)


def test_anchor_ignores_dates_beyond_close():
    # a backstop mention after close is not the decision-relevant event
    a = _anchor(rules="the vote scheduled for Mar 3, 2026; if unresolved "
                      "by December 31, 2026 the market resolves No",
                close_iso="2026-04-01T00:00:00Z")
    assert a.event_date == date(2026, 3, 3)


def test_anchor_ignores_dates_before_open():
    a = _anchor(rules="following the January 5, 2026 summit",
                open_iso="2026-02-01T00:00:00Z",
                close_iso="2026-06-01T00:00:00Z")
    assert a.source == "none" and a.event_ts is None


# ---------------------------------------------------------------------------
# election calendar
# ---------------------------------------------------------------------------

def test_calendar_nominee_market_maps_to_state_primary():
    a = _anchor(title="Will Jane Doe be the Democratic nominee for "
                      "Governor in Wisconsin?",
                rules="If Jane Doe wins the nomination for the Democratic "
                      "Party to contest the 2026 Wisconsin Governorship, "
                      "then the market resolves to Yes.")
    assert a.source == "election_calendar"
    assert a.provenance == "primary_2026:wisconsin"
    assert a.event_date == WI_PRIMARY


def test_calendar_district_code_detection():
    a = _anchor(title="Will John Roe be the Democratic nominee for AZ-01?",
                rules="If John Roe wins the nomination for the Democratic "
                      "Party to contest the 2026 AZ-01 House seat, then the "
                      "market resolves to Yes.")
    assert a.source == "election_calendar"
    assert a.event_date == date(2026, 8, 4)   # AZ: first Tuesday of August


def test_calendar_excludes_runoffs_and_specials():
    for word in ("runoff", "special"):
        a = _anchor(title=f"Will X win the 2026 South Carolina Republican "
                          f"gubernatorial primary {word}?")
        assert a.source == "none"


def test_calendar_explicit_text_takes_priority():
    a = _anchor(title="Will X win the Wisconsin primary?",
                rules="the primary election held on August 11, 2026")
    assert a.source == "explicit_text" and a.event_date == WI_PRIMARY


# ---------------------------------------------------------------------------
# expected_expiration + activity fallback
# ---------------------------------------------------------------------------

def test_expected_expiration_fallback():
    a = _anchor(title="Will the thing happen?",   # no parseable date
                raw_json={"expected_expiration_time": "2026-06-15T14:00:00Z"},
                close_iso="2026-12-01T00:00:00Z")
    assert a.source == "expected_expiration"
    assert a.event_date == date(2026, 6, 15)
    d, used = EA.anchored_decision_ts(a, _ts("2026-12-01T00:00:00Z"), 10)
    assert used == "expected_expiration"


def test_activity_fallback_is_flagged_and_never_anchors_backtests():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE candlesticks (ticker TEXT, period_interval INT,"
                " ts INT, volume REAL, price_close REAL,"
                " yes_bid_close REAL, yes_ask_close REAL)")
    base = _ts("2026-08-11T00:00:00Z")
    rows = [("T", 60, base + h * 3600, 10_000.0, None, None, None)
            for h in range(24)]                       # spike day
    rows += [("T", 60, base - d * DAY, 10.0, None, None, None)
             for d in range(1, 60)]                   # quiet life before
    con.executemany("INSERT INTO candlesticks VALUES (?,?,?,?,?,?,?)", rows)
    a = EA.derive_event_anchor(
        title="no dates here", open_ts=base - 90 * DAY,
        close_ts=base + 80 * DAY, con=con, ticker="T", allow_activity=True)
    assert a.source == "activity" and a.outcome_adjacent
    assert a.event_date == date(2026, 8, 11)
    # outcome-adjacent anchors are refused for decision anchoring
    d, used = EA.anchored_decision_ts(a, base + 80 * DAY, 10)
    assert used == "close_time" and d == base + 70 * DAY
    # and the screen never enables it: default derive path returns none
    a2 = EA.derive_event_anchor(title="no dates here", open_ts=base - 90 * DAY,
                                close_ts=base + 80 * DAY, con=con, ticker="T")
    assert a2.source == "none"


# ---------------------------------------------------------------------------
# anchored decision date
# ---------------------------------------------------------------------------

def test_anchor_rule_event_must_precede_close_by_more_than_k():
    close = _ts("2026-11-03T15:00:00Z")
    near = EA.EventAnchor(event_ts=close - 5 * DAY, source="explicit_text",
                          provenance="x")
    d, used = EA.anchored_decision_ts(near, close, 10)
    assert used == "close_time" and d == close - 10 * DAY
    far = EA.EventAnchor(event_ts=close - 60 * DAY, source="explicit_text",
                         provenance="x")
    d, used = EA.anchored_decision_ts(far, close, 10)
    assert used == "explicit_text" and d == close - 70 * DAY


# ---------------------------------------------------------------------------
# the Wisconsin regression, on the real DB rows
# ---------------------------------------------------------------------------

@needs_db
def test_wisconsin_markets_anchor_pre_primary():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT ticker, title, yes_sub_title, rules_primary, open_time,
                  raw_json FROM markets
           WHERE ticker LIKE 'KXGOVWINOMD-26-%' ORDER BY ticker""").fetchall()
    con.close()
    assert len(rows) >= 7, "expected the KXGOVWINOMD-26 complex in the DB"
    pre_close_ts = _ts(WI_PRE_EVENT_CLOSE)
    for r in rows:
        a = EA.derive_event_anchor(
            title=r["title"] or "", subtitle=r["yes_sub_title"] or "",
            rules=r["rules_primary"] or "", open_ts=_ts(r["open_time"]),
            close_ts=pre_close_ts, raw_json=r["raw_json"])
        # metadata alone recovers the statutory primary date
        assert a.source == "election_calendar", r["ticker"]
        assert a.event_date == WI_PRIMARY, r["ticker"]
        d, used = EA.anchored_decision_ts(a, pre_close_ts, H.DECISION_DAYS)
        d_date = datetime.fromtimestamp(d, tz=timezone.utc).date()
        # v2: D = Aug 1, ten days BEFORE the primary
        assert used == "election_calendar"
        assert d_date == date(2026, 8, 1)
        assert d_date < WI_PRIMARY
        # frozen behaviour was blind: close-anchored D lands post-primary
        frozen_d = datetime.fromtimestamp(
            pre_close_ts - H.DECISION_DAYS * DAY, tz=timezone.utc).date()
        assert frozen_d == date(2026, 10, 24) > WI_PRIMARY


@needs_db
def test_wisconsin_screen_end_to_end_on_pre_event_snapshot(tmp_path):
    """Reconstruct the pre-event state in a fixture DB (real metadata, the
    documented pre-event close_time, real hourly candles) and prove the v2
    screen sees the market where the frozen screen is structurally blind."""
    src = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    m = src.execute("SELECT * FROM markets WHERE ticker='KXGOVWINOMD-26-FHON'"
                    ).fetchone()
    candles = src.execute(
        """SELECT ticker, period_interval, ts, price_close, yes_bid_close,
                  yes_ask_close, volume FROM candlesticks
           WHERE ticker='KXGOVWINOMD-26-FHON' AND period_interval=60
             AND ts <= ?""", (_ts("2026-08-02T00:00:00Z"),)).fetchall()
    src.close()
    assert m is not None and len(candles) > 0

    fix = tmp_path / "fixture.db"
    con = sqlite3.connect(fix)
    con.execute("""CREATE TABLE markets (ticker TEXT, event_ticker TEXT,
        series_ticker TEXT, title TEXT, yes_sub_title TEXT, category TEXT,
        open_time TEXT, close_time TEXT, volume REAL, result TEXT,
        rules_primary TEXT, raw_json TEXT)""")
    con.execute("""CREATE TABLE candlesticks (ticker TEXT,
        period_interval INT, ts INT, price_close REAL, yes_bid_close REAL,
        yes_ask_close REAL, volume REAL)""")
    con.execute("INSERT INTO markets VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
        m["ticker"], m["event_ticker"], m["series_ticker"], m["title"],
        m["yes_sub_title"], m["category"], m["open_time"],
        WI_PRE_EVENT_CLOSE,                       # the pre-event close
        m["volume"], m["result"], m["rules_primary"], m["raw_json"]))
    con.executemany("INSERT INTO candlesticks VALUES (?,?,?,?,?,?,?)",
                    [tuple(c) for c in candles])
    con.commit()
    con.close()

    win = {"WI": {"close_min": "2026-10-01", "close_max": "2026-11-30",
                  "judge_model": "opus"}}
    # frozen close-anchoring: D = Oct 24, no candle within 48h -> BLIND
    frozen, _ = H.enumerate_candidates("WI", db_path=fix, anchoring="close",
                                       windows=win)
    assert frozen == []
    # v2 event anchoring: D = Aug 1, pre-primary, extreme price -> candidate
    v2, funnel = H.enumerate_candidates("WI", db_path=fix, anchoring="event",
                                        windows=win)
    assert funnel["event_anchored"] == 1
    assert [c.ticker for c in v2] == ["KXGOVWINOMD-26-FHON"]
    c = v2[0]
    assert c.anchor_source == "election_calendar"
    assert c.anchor_provenance == "primary_2026:wisconsin"
    d_date = datetime.fromtimestamp(c.decision_ts, tz=timezone.utc).date()
    assert d_date == date(2026, 8, 1) < WI_PRIMARY
    assert c.price_at_d >= H.EXTREME_HI          # ~95c favorite
    assert c.cheap_side == "no"                  # the ~20x pre-event fade


# ---------------------------------------------------------------------------
# parse rate over the politics/econ universe (reported, floor-asserted)
# ---------------------------------------------------------------------------

@needs_db
def test_parse_rate_on_db_sample(capsys):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT ticker, title, yes_sub_title, rules_primary, open_time,
                  close_time, raw_json FROM markets
           WHERE result IN ('yes','no') AND volume >= 2000
             AND category IN ('Politics','Elections','World','Economics',
                              'Companies')
             AND close_time >= '2026-02-01'
             AND close_time <= '2026-08-05T23:59:59Z'
           ORDER BY ticker""").fetchall()
    con.close()
    sample = rows[::8]                      # deterministic 1-in-8 sample
    assert len(sample) >= 300
    by_source: dict[str, int] = {}
    moved = 0
    for r in sample:
        o, c = _ts(r["open_time"]), _ts(r["close_time"])
        a = EA.derive_event_anchor(
            title=r["title"] or "", subtitle=r["yes_sub_title"] or "",
            rules=(r["rules_primary"] or "")[:2000], open_ts=o, close_ts=c,
            raw_json=r["raw_json"])
        by_source[a.source] = by_source.get(a.source, 0) + 1
        _, used = EA.anchored_decision_ts(a, c, H.DECISION_DAYS)
        moved += used != "close_time"
    n = len(sample)
    rate = 1.0 - by_source.get("none", 0) / n
    with capsys.disabled():
        print(f"\n[event_anchor] parse rate {rate:.1%} on n={n} "
              f"(sources: {by_source}; anchors moved: {moved})")
    assert rate >= 0.5      # floor, not a tuned target
    # no outcome-adjacent source may ever appear in screen derivation
    assert "activity" not in by_source


# ---------------------------------------------------------------------------
# freshness gate primitives
# ---------------------------------------------------------------------------

def test_is_stale_window():
    d = _ts("2026-06-11T03:59:00Z")
    f = EA.FRESHNESS_HOURS
    assert not EA.is_stale(d, d)
    assert not EA.is_stale(d + int(f * 3600), d)
    assert EA.is_stale(d + int(f * 3600) + 1, d)
    assert EA.is_stale(d - int(f * 3600) - 1, d)      # symmetric: |now − D|
    assert not EA.is_stale(d + 8 * 3600, d, f_hours=12.0)
    assert EA.is_stale(d + 13 * 3600, d, f_hours=12.0)


def test_gap_revalidation_blocks_price_through_forecast():
    # the Clayton failure: forecast 18%, price 93.5c at D, 0.5c at execution
    assert EA.gap_still_valid(0.18, 93.5, 93.5, gate_pts=15)      # at D: fires
    assert not EA.gap_still_valid(0.18, 0.5, 93.5, gate_pts=15)   # crossed
    # gap shrunk below the gate: no longer our trade
    assert not EA.gap_still_valid(0.18, 25.0, 93.5, gate_pts=15)
    # same side, still wide: fine
    assert EA.gap_still_valid(0.18, 80.0, 93.5, gate_pts=15)

"""Tests for rung 2a — the FutureSearch 153-question replication.

Outcome coverage depends on live/stored market data, so the scoring path is
tested on synthetic questions; only the parsing and the shape of the real load
are tested against the staged CSV.
"""
from __future__ import annotations

from datetime import date

import pytest

from bot.evals import fs_replication as fsr


# ---------------------------------------------------------------------------
# The loose date parser (the CSV's date column is half free text)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,want",
    [
        ("2026-03-08", date(2026, 3, 8)),
        ("February 28, 2026", date(2026, 2, 28)),
        ("January 1, 2027", date(2027, 1, 1)),
        ("November 07, 2028", date(2028, 11, 7)),
        ("2026-04-01T04:00:00Z", date(2026, 4, 1)),
        ("2026-07-01 03:59:00 UTC", date(2026, 7, 1)),
        (
            "2028-11-07 (Market End Date) / August 2028 (Estimated Convention Date)",
            date(2028, 11, 7),
        ),
        ("", None),
        ("   ", None),
        ("sometime next year", None),
    ],
)
def test_parse_loose_date(raw, want):
    assert fsr.parse_loose_date(raw) == want


def test_parse_loose_date_takes_the_latest_of_several():
    """A runoff row can only settle on the later date."""
    raw = "March 3, 2026 (Primary Election) or May 26, 2026 (Runoff)"
    assert fsr.parse_loose_date(raw) == date(2026, 5, 26)
    raw2 = "March 10, 2026 (Special General); April 7, 2026 (Potential Runoff)"
    assert fsr.parse_loose_date(raw2) == date(2026, 4, 7)


def test_parse_loose_date_ignores_impossible_dates():
    assert fsr.parse_loose_date("February 31, 2026") is None


# ---------------------------------------------------------------------------
# Loading the real CSV
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qs():
    if not fsr.CSV_PATH.exists():
        pytest.skip("staged FutureSearch CSV missing")
    return fsr.load_questions()


def test_loads_all_153_questions(qs):
    assert len(qs) == 153
    assert len({q.ticker for q in qs}) == 153


def test_probabilities_are_normalised_to_unit_interval(qs):
    for q in qs:
        assert 0.0 <= q.market_price <= 1.0
        assert 0.0 <= q.fs_median <= 1.0
        for p in q.fs_models.values():
            assert 0.0 <= p <= 1.0


def test_market_prices_respect_their_3_to_97_filter(qs):
    """research/futuresearch.md §2.3: hard filter of 3-97c on the YES price."""
    assert min(q.market_price for q in qs) >= 0.03
    assert max(q.market_price for q in qs) <= 0.97


def test_only_one_row_has_an_unparseable_resolution_date(qs):
    """Documents the state of the staged file; a change here is a data change."""
    assert sum(1 for q in qs if q.resolution_date is None) == 1


def test_half_the_set_resolves_after_today_contradicting_futuresearch_md_7(qs):
    """§7 claims they "all resolved months ago". They did not."""
    later = [q for q in qs if q.resolution_date and q.resolution_date > date(2026, 8, 11)]
    assert len(later) > 80
    assert max(q.resolution_date for q in qs if q.resolution_date).year >= 2029


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _q(ticker, price, median, outcome, res_date):
    return fsr.Question(
        ticker=ticker,
        question=ticker,
        market_price=price,
        volume=10_000,
        resolution_date=res_date,
        resolution_date_raw=str(res_date),
        days_remaining=1,
        fs_median=median,
        outcome=outcome,
        outcome_source="test",
    )


def test_evaluate_splits_on_the_judge_cutoff():
    pre = date(2026, 3, 1)
    post = date(2026, 7, 1)
    qs = [
        _q("A", 0.6, 0.7, 1.0, pre),
        _q("B", 0.4, 0.3, 0.0, pre),
        _q("C", 0.6, 0.7, 1.0, post),
        _q("D", 0.4, 0.3, 0.0, post),
        _q("E", 0.4, 0.3, 0.0, None),
    ]
    r = fsr.evaluate(qs, {})
    assert r["scores"]["all_resolved"]["n"] == 5
    assert r["scores"]["pre_cutoff_2026_05_01"]["n"] == 2
    assert r["scores"]["post_cutoff_2026_05_01"]["n"] == 2
    assert r["scores"]["unknown_resolution_date"]["n"] == 1


def test_three_way_brier_columns_and_orientation():
    post = date(2026, 7, 1)
    qs = [_q("A", 0.5, 0.9, 1.0, post), _q("B", 0.5, 0.1, 0.0, post)]
    r = fsr.evaluate(qs, {"A": 0.99, "B": 0.01})
    s = r["scores"]["all_resolved"]
    assert s["brier_market"] == pytest.approx(0.25)
    assert s["brier_fs_median"] == pytest.approx(0.01)
    assert s["brier_ours"] == pytest.approx(0.0001)
    # Positive delta = forecaster beat the price.
    assert s["fs_vs_market"]["delta"] > 0
    assert s["ours_vs_market"]["delta"] > s["fs_vs_market"]["delta"]
    assert s["ours_vs_fs"]["delta"] > 0
    assert s["three_number_block"]["complete"] is False  # no P&L supplied


def test_our_forecast_column_is_absent_until_wired():
    r = fsr.evaluate([_q("A", 0.5, 0.6, 1.0, date(2026, 7, 1))] * 2, {})
    assert r["scores"]["all_resolved"]["brier_ours"] is None


def test_coverage_records_why_each_question_is_unscorable():
    q = _q("A", 0.5, 0.6, None, date(2026, 7, 1))
    q.outcome = None
    q.outcome_source = None
    q.unresolvable_reason = "still active on Kalshi"
    r = fsr.evaluate([q], {})
    assert r["coverage"]["resolved"] == 0
    assert r["coverage"]["unresolved_by_reason"] == {"still active on Kalshi": 1}


def test_load_our_forecasts_accepts_both_scales(tmp_path):
    p = tmp_path / "f.json"
    p.write_text('{"A": 0.42, "B": 42, "C": {"p_yes": 0.5}}')
    got = fsr.load_our_forecasts(p)
    assert got == {"A": 0.42, "B": 0.42, "C": 0.5}


def test_load_our_forecasts_rejects_out_of_range(tmp_path):
    p = tmp_path / "f.json"
    p.write_text('{"A": 420}')
    with pytest.raises(ValueError):
        fsr.load_our_forecasts(p)


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------

def test_overrides_require_a_source_citation(tmp_path):
    p = tmp_path / "o.json"
    p.write_text('{"A": {"outcome": 1}}')
    qs = [_q("A", 0.5, 0.6, None, date(2026, 7, 1))]
    qs[0].outcome = None
    with pytest.raises(ValueError, match="source"):
        fsr.resolve_from_overrides(qs, p)


def test_overrides_apply_and_ignore_doc_keys(tmp_path):
    p = tmp_path / "o.json"
    p.write_text('{"_README": {"x": 1}, "A": {"outcome": 1, "source": "https://example.test"}}')
    qs = [_q("A", 0.5, 0.6, None, date(2026, 7, 1))]
    qs[0].outcome = None
    assert fsr.resolve_from_overrides(qs, p) == 1
    assert qs[0].outcome == 1.0
    assert qs[0].outcome_source.startswith("override:")


def test_shipped_overrides_file_is_valid():
    """The checked-in overrides file must load without raising."""
    qs = fsr.load_questions()
    for q in qs:
        q.outcome = None
    fsr.resolve_from_overrides(qs, fsr.OVERRIDES_PATH)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_main_offline_writes_reports(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_REPORTS_DIR", str(tmp_path))
    assert fsr.main(["--quiet"]) == 0
    assert (tmp_path / "fs_replication.json").exists()
    assert (tmp_path / "fs_replication.md").exists()

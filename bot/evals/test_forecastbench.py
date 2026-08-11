"""Tests for rung 2 — the ForecastBench loader, baseline and scoring API.

The dataset-dependent tests skip cleanly when ``data/forecastbench-datasets`` is
absent (it is gitignored, 139 MB). The API tests run on synthetic rows and
always execute.
"""
from __future__ import annotations

import json

import pytest

from bot.evals import forecastbench as fb

needs_data = pytest.mark.skipif(
    not fb.data_available(),
    reason="ForecastBench clone missing; run `python -m bot.evals.forecastbench --ensure-data`",
)


def _row(key, source="polymarket", market_p=0.5, outcome=1.0, due="2026-03-01"):
    return fb.Row(
        key=key, due=due, source=source, qid=key.split("|")[-1], question="q?",
        background="", resolution_criteria="", url="", freeze="2026-02-19T00:00:00+00:00",
        close="", resolution_date="2026-03-08", market_p=market_p, outcome=outcome,
    )


# ---------------------------------------------------------------------------
# The self-test — the whole point of rung 2 (benchmarks §5 rung 0)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rows():
    if not fb.data_available():
        pytest.skip("no dataset")
    return fb.questions()


@needs_data
def test_reproduces_published_pooled_baseline(rows):
    """research/benchmarks.md §1.6: n=1,216, Brier 0.1172, sd 0.1785."""
    base = fb.market_baseline(rows)
    assert base["n"] == pytest.approx(1216, rel=0.05)
    assert base["brier"] == pytest.approx(0.1172, abs=0.005)
    assert base["sd"] == pytest.approx(0.1785, abs=0.01)


@needs_data
def test_reproduces_per_source_baseline_table(rows):
    """INFER 0.065, Manifold 0.096, Polymarket 0.125, Metaculus 0.155."""
    by_source = fb.market_baseline(rows)["by_source"]
    for src, exp in fb.EXPECTED["by_source"].items():
        assert by_source[src]["brier"] == pytest.approx(exp["brier"], abs=0.005), src
        assert by_source[src]["n"] == pytest.approx(exp["n"], rel=0.10), src


@needs_data
def test_self_test_passes(rows):
    st = fb.self_test(rows)
    assert st["passed"], [c for c in st["checks"] if not c["pass"]]


@needs_data
def test_only_market_sources_and_only_resolved(rows):
    assert {r.source for r in rows} <= fb.MARKET_SOURCES
    # resolved market questions resolve to exactly 0.0 or 1.0 (§1.3)
    assert {r.outcome for r in rows} == {0.0, 1.0}


@needs_data
def test_unresolved_rows_would_carry_prices_not_outcomes(rows):
    """The single easiest way to corrupt this eval (§1.3) — prove the filter bites."""
    loose = fb.questions(resolved_only=False)
    assert len(loose) > len(rows)
    assert any(0.0 < r.outcome < 1.0 for r in loose), (
        "expected unresolved rows to carry live market values in resolved_to"
    )


@needs_data
def test_min_due_window_is_respected(rows):
    assert all(r.due >= fb.DEFAULT_MIN_DUE for r in rows)
    tighter = fb.questions(min_due="2026-03-01")
    assert len(tighter) < len(rows)
    assert all(r.due >= "2026-03-01" for r in tighter)


@needs_data
def test_rows_carry_everything_a_forecaster_needs(rows):
    r = rows[0]
    assert r.question and r.url and r.freeze
    assert r.key.count("|") == 2
    assert len({x.key for x in rows}) == len(rows)  # keys are unique


@needs_data
def test_always_half_scores_a_quarter(rows):
    assert fb.market_baseline(rows)["always_half_brier"] == pytest.approx(0.25, abs=0.001)


def test_questions_raises_when_data_missing(tmp_path):
    with pytest.raises(fb.DataMissing):
        fb.questions(path=tmp_path)


# ---------------------------------------------------------------------------
# The scoring API (synthetic — always runs)
# ---------------------------------------------------------------------------

def test_score_matches_on_full_key():
    rows = [
        _row("2026-03-01|polymarket|a", market_p=0.5, outcome=1.0),
        _row("2026-03-01|polymarket|b", market_p=0.5, outcome=0.0),
    ]
    out = fb.score({"2026-03-01|polymarket|a": 0.9, "2026-03-01|polymarket|b": 0.1}, rows=rows)
    assert out["n_scored"] == 2
    assert out["n_unmatched"] == 0
    assert out["brier_ours"] == pytest.approx(0.01)
    assert out["brier_market"] == pytest.approx(0.25)
    assert out["three_number_block"]["pooled_delta_brier"]["delta"] == pytest.approx(0.24)


def test_score_falls_back_to_bare_question_id_when_unambiguous():
    rows = [_row("2026-03-01|polymarket|a"), _row("2026-03-01|polymarket|b", outcome=0.0)]
    out = fb.score({"a": 0.9, "b": 0.1}, rows=rows)
    assert out["n_scored"] == 2


def test_score_reports_unmatched_keys():
    rows = [_row("2026-03-01|polymarket|a"), _row("2026-03-01|polymarket|b", outcome=0.0)]
    out = fb.score({"a": 0.9, "b": 0.1, "zzz": 0.5}, rows=rows)
    assert out["n_unmatched"] == 1
    assert out["unmatched_keys"] == ["zzz"]


def test_score_accepts_percent_scale():
    rows = [_row("k|polymarket|a"), _row("k|polymarket|b", outcome=0.0)]
    a = fb.score({"a": 90, "b": 10}, rows=rows)
    b = fb.score({"a": 0.9, "b": 0.1}, rows=rows)
    assert a["brier_ours"] == pytest.approx(b["brier_ours"])


def test_score_rejects_out_of_range_probability():
    rows = [_row("k|polymarket|a"), _row("k|polymarket|b")]
    with pytest.raises(ValueError):
        fb.score({"a": 150.0, "b": 0.5}, rows=rows)


def test_score_reports_coverage_and_by_source():
    rows = [
        _row("k|polymarket|a", source="polymarket", outcome=1.0),
        _row("k|polymarket|b", source="polymarket", outcome=0.0),
        _row("k|metaculus|c", source="metaculus", outcome=1.0),
        _row("k|metaculus|d", source="metaculus", outcome=0.0),
    ]
    out = fb.score({"a": 0.9, "b": 0.1, "c": 0.9, "d": 0.1}, rows=rows)
    assert out["coverage_of_question_set"] == 1.0
    assert set(out["by_source"]) == {"polymarket", "metaculus"}


def test_score_block_is_incomplete_without_pnl():
    rows = [_row("k|polymarket|a"), _row("k|polymarket|b", outcome=0.0)]
    out = fb.score({"a": 0.9, "b": 0.1}, rows=rows)
    assert out["three_number_block"]["complete"] is False
    out2 = fb.score({"a": 0.9, "b": 0.1}, rows=rows, simulated_pnl_usd=10.0)
    assert out2["three_number_block"]["complete"] is True


def test_score_gate_is_underpowered_at_small_n():
    rows = [_row(f"k|polymarket|{i}", outcome=float(i % 2)) for i in range(10)]
    out = fb.score({str(i): 0.5 for i in range(10)}, rows=rows)
    assert out["gate"]["verdict"] == "UNDERPOWERED"


def test_score_errors_cleanly_on_no_matches():
    rows = [_row("k|polymarket|a")]
    out = fb.score({"nope": 0.5}, rows=rows)
    assert "error" in out and out["n_scored"] == 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@needs_data
def test_main_writes_reports(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_REPORTS_DIR", str(tmp_path))
    assert fb.main(["--quiet"]) == 0
    payload = json.loads((tmp_path / "forecastbench.json").read_text())
    assert payload["self_test"]["passed"]
    assert payload["baseline"]["brier"] == pytest.approx(0.1172, abs=0.005)
    assert (tmp_path / "forecastbench.md").exists()

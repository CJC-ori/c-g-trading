"""Unit tests for tools/evals/forecast_quality.py (pure checks, no DB).

The RED tests this eval ships with encode the historical/literature defect
classes: an unfalsifiable forecast (no resolution criteria — the
rationale-shaped hole), an out-of-bounds probability, a single-pass number
masquerading as an ensemble, and an ungrounded forecast citing nothing.
The exception path scoring 0 (never vanishing) is
``test_exception_path_scores_zero``.
"""

from tools.evals.forecast_quality import (
    _safe,
    check_independent_passes,
    check_probability_bounds,
    check_resolution_criteria,
    check_sources_cited,
)

GOOD_CRITERIA = (
    "Resolves YES if the EU Parliament adopts, by 2027-12-31, a regulation "
    "restricting cultivated-meat labeling, verifiable in the Official Journal. "
    "Single-country bans do not count."
)


# ---------------------------------------------------------------------------
# RED: the defect classes
# ---------------------------------------------------------------------------

def test_red_missing_criteria_scores_zero():
    r = check_resolution_criteria(None)
    assert r["score"] == 0.0
    assert "unfalsifiable" in r["notes"]


def test_red_vacuous_criteria_scores_zero():
    assert check_resolution_criteria("TBD")["score"] == 0.0


def test_red_out_of_bounds_probability_scores_zero():
    r = check_probability_bounds({"probability_or_range": "140%"})
    assert r["score"] == 0.0
    assert "out-of-bounds" in r["notes"]


def test_red_certainty_claim_scores_zero():
    assert check_probability_bounds({"probability_or_range": "100%"})["score"] == 0.0
    assert check_probability_bounds({"probability_or_range": "0%"})["score"] == 0.0


def test_red_no_probabilities_at_all_scores_zero():
    r = check_probability_bounds({})
    assert r["score"] == 0.0
    assert "vacuous" in r["notes"]


def test_red_single_pass_scores_zero():
    r = check_independent_passes([{"stance": "solo", "probability": "30%"}])
    assert r["score"] == 0.0
    assert "single-pass" in r["notes"]


def test_red_uncited_forecast_scores_zero():
    r = check_sources_cited([], "We reckon 30% based on general knowledge.")
    assert r["score"] == 0.0
    assert "NO source" in r["notes"]


def test_exception_path_scores_zero():
    def boom():
        raise RuntimeError("db fell over")

    r = _safe(boom, "forecast_probability_bounds", "reliability")
    assert r["score"] == 0.0
    assert "CHECK CRASHED" in r["notes"]
    assert "db fell over" in r["details"]["exception"]


# ---------------------------------------------------------------------------
# Partial credit
# ---------------------------------------------------------------------------

def test_deadline_less_criteria_score_half():
    r = check_resolution_criteria(
        "Resolves YES if the Parliament adopts a labeling restriction, "
        "verifiable in the Official Journal; bans elsewhere do not count."
    )
    assert r["score"] == 0.5
    assert not r["details"]["has_deadline_year"]


def test_malformed_pass_scores_half():
    r = check_independent_passes(
        [
            {"stance": "outside-view", "probability": "25%"},
            {"probability": "35%"},  # stance missing
        ]
    )
    assert r["score"] == 0.5
    assert r["details"]["malformed"] == [1]


# ---------------------------------------------------------------------------
# GREEN
# ---------------------------------------------------------------------------

def test_green_full_payload_passes_everything():
    details = {
        "resolution_criteria": GOOD_CRITERIA,
        "probability_or_range": "28% (range 15-45%)",
        "status_quo": "20% (range 10-35%)",
        "with_intervention": "32% (range 18-50%)",
        "per_pass": [
            {"stance": "outside-view-first", "probability": "25%"},
            {"stance": "causal-chain-first", "probability": "32%"},
            {"stance": "adversarial-NO", "probability": "22%"},
        ],
    }
    assert check_resolution_criteria(details["resolution_criteria"])["score"] == 1.0
    bounds = check_probability_bounds(details)
    assert bounds["score"] == 1.0
    assert bounds["details"]["checked"] == 6  # 3 headline fields + 3 passes
    assert check_independent_passes(details["per_pass"])["score"] == 1.0
    assert check_sources_cited(["https://europarl.europa.eu/x"], None)["score"] == 1.0


def test_green_db_uuid_in_answer_counts_as_grounding():
    r = check_sources_cited(
        [], "Per meeting 8f3a1b2c-3d4e-4f56-8a9b-0c1d2e3f4a5b the CEO stated X."
    )
    assert r["score"] == 1.0
    assert r["details"]["db_id_refs"] == 1


# ---------------------------------------------------------------------------
# RED: inverted / malformed canonical 3-value ranges (gen5 wave-1 review)
# ---------------------------------------------------------------------------

def test_red_inverted_3_value_range_scores_zero():
    """Parity with store_forecast: 'CENTRAL% (range LO-HI%)' with LO > HI must
    fail bounds — the old check only order-checked the 2-value form."""
    r = check_probability_bounds({"probability_or_range": "28% (range 45-15%)"})
    assert r["score"] == 0.0
    assert "inverted" in r["notes"]


def test_red_central_outside_3_value_range_scores_zero():
    r = check_probability_bounds({"probability_or_range": "60% (range 15-45%)"})
    assert r["score"] == 0.0
    assert "outside its own range" in r["notes"]


# ---------------------------------------------------------------------------
# Gate-level: the new deterministic floors actually reach gate_summary
# (mirrors test_me_quality.test_red_silent_lane_breaches_eval_gate)
# ---------------------------------------------------------------------------

from tools.evals import eval_gate  # noqa: E402


def _gate(monkeypatch, results):
    monkeypatch.setattr(eval_gate, "_send_slack", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(eval_gate, "_write_flag", lambda *a, **k: True)
    return eval_gate.gate_summary(
        {"run_id": "forecast-x", "run_type": "forecast", "quality_error": None,
         "results": results}
    )


def test_red_uncited_forecast_breaches_gate(monkeypatch):
    assert _gate(monkeypatch, [check_sources_cited([], "no refs here")]) == eval_gate.EXIT_BREACH


def test_red_out_of_bounds_breaches_gate(monkeypatch):
    assert _gate(
        monkeypatch, [check_probability_bounds({"probability_or_range": "140%"})]
    ) == eval_gate.EXIT_BREACH


def test_boundary_half_score_passes_half_floor_gate(monkeypatch):
    """forecast_resolution_criteria floor is 0.5 and the comparison is strict
    (<): a 0.5 partial-credit score must PASS."""
    r = check_resolution_criteria("Substantive but deadline-less criteria " * 3)
    assert r["score"] == 0.5
    assert _gate(monkeypatch, [r]) == eval_gate.EXIT_PASS


def test_green_forecast_summary_passes_gate(monkeypatch):
    results = [
        check_resolution_criteria(GOOD_CRITERIA),
        check_probability_bounds({"probability_or_range": "28% (range 15-45%)"}),
        check_independent_passes([
            {"stance": "outside-view-first", "probability": "25%"},
            {"stance": "causal-chain-first", "probability": "32%"},
        ]),
        check_sources_cited(["https://example.org/x"], None),
    ]
    assert _gate(monkeypatch, results) == eval_gate.EXIT_PASS

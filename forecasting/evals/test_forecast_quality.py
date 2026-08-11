"""Unit tests for tools/evals/forecast_quality.py (pure checks, no DB).

The RED tests this eval ships with encode the historical/literature defect
classes: an unfalsifiable forecast (no resolution criteria — the
rationale-shaped hole), an out-of-bounds probability, a single-pass number
masquerading as an ensemble, and an ungrounded forecast citing nothing.
The exception path scoring 0 (never vanishing) is
``test_exception_path_scores_zero``.
"""

from forecast_quality import (
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
# NVF-ONLY (removed in this repo): the original file ended with four
# gate-level tests wiring these checks into NVF's eval_gate (Slack alerts +
# deterministic score floors). That plumbing wasn't ported — see the
# NVF-ONLY BOUNDARY banner in forecast_quality.py. If this project grows an
# alerting gate, resurrect them from NVF's tools/evals/test_forecast_quality.py.
# ---------------------------------------------------------------------------

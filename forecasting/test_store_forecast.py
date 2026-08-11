"""Unit tests for tools/forecast/store_forecast.py (pure validation, no DB).

RED tests encode the defect classes this wrapper exists to refuse:
  * a vibes forecast with no real resolution criteria ("TBD");
  * out-of-bounds or certainty-claiming probabilities (0%, 100%, 140%);
  * a single-pass "ensemble" (independence structure not logged);
  * a half-missing counterfactual pair (delta would be meaningless).
"""

import pytest

from store_forecast import (
    ForecastValidationError,
    parse_probabilities,
    validate_forecast,
)

CRITERIA = (
    "Resolves YES if the EU Parliament adopts, by 2027-12-31, a regulation or "
    "directive restricting the labeling of cultivated meat, verifiable in the "
    "Official Journal. Vague resolutions or single-country bans do not count."
)


def good_payload(**overrides):
    details = {
        "resolution_criteria": CRITERIA,
        "probability_or_range": "28% (range 15-45%)",
        "base_rate": "3/17 EU food-labeling restriction proposals adopted 2015-2025",
        "key_drivers": [{"driver": "Italy precedent", "direction": "up"}],
        "per_pass": [
            {"stance": "outside-view-first", "probability": "25%",
             "rationale_summary": "base rate anchor"},
            {"stance": "causal-chain-first", "probability": "32%",
             "rationale_summary": "chain of conditionals"},
        ],
        "review_by_date": "2027-12-31",
    }
    details.update(overrides.pop("details", {}))
    payload = {
        "question": "Will the EU Parliament restrict cultivated-meat labeling by end 2027?",
        "answer_md": "Reasoning narrative.",
        "source_urls": ["https://www.europarl.europa.eu/x"],
        "details": details,
        "source_tool": "forecast",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# parse_probabilities — parity contract with the web renderer's parseProbRange
# ---------------------------------------------------------------------------

def test_parse_percent_and_range_forms():
    assert parse_probabilities("28%") == [28.0]
    assert parse_probabilities("15-45%") == [15.0, 45.0]
    assert parse_probabilities("28% (range 15-45%)") == [28.0, 15.0, 45.0]


def test_parse_fraction_form_scales():
    assert parse_probabilities(0.28) == pytest.approx([28.0])
    assert parse_probabilities("0.28") == pytest.approx([28.0])


def test_parse_garbage_is_empty():
    assert parse_probabilities("unknown") == []
    assert parse_probabilities(None) == []


# ---------------------------------------------------------------------------
# RED: the defect classes
# ---------------------------------------------------------------------------

def test_red_vacuous_resolution_criteria_refused():
    with pytest.raises(ForecastValidationError, match="resolution_criteria"):
        validate_forecast(good_payload(details={"resolution_criteria": "TBD"}))


def test_red_probability_over_100_refused():
    with pytest.raises(ForecastValidationError, match="out of bounds"):
        validate_forecast(good_payload(details={"probability_or_range": "140%"}))


def test_red_certainty_claims_refused():
    for degenerate in ("0%", "100%"):
        with pytest.raises(ForecastValidationError, match="out of bounds"):
            validate_forecast(good_payload(details={"probability_or_range": degenerate}))


def test_red_single_pass_refused():
    with pytest.raises(ForecastValidationError, match="per_pass"):
        validate_forecast(
            good_payload(details={"per_pass": [{"stance": "solo", "probability": "30%"}]})
        )


def test_red_half_missing_pair_refused():
    with pytest.raises(ForecastValidationError, match="half-missing"):
        validate_forecast(good_payload(details={"status_quo": "20% (range 10-35%)"}))


def test_red_inverted_range_refused():
    with pytest.raises(ForecastValidationError, match="inverted"):
        validate_forecast(good_payload(details={"probability_or_range": "45-15%"}))


def test_red_central_outside_range_refused():
    with pytest.raises(ForecastValidationError, match="outside its own range"):
        validate_forecast(good_payload(details={"probability_or_range": "60% (range 15-45%)"}))


def test_red_inverted_range_in_canonical_3_value_form_refused():
    """The wave-1 gap: the compound condition silently ACCEPTED an inverted
    range in the canonical 'CENTRAL% (range LO-HI%)' form — both when the
    central sat between the swapped bounds and when it didn't."""
    with pytest.raises(ForecastValidationError, match="inverted"):
        validate_forecast(good_payload(details={"probability_or_range": "28% (range 45-15%)"}))
    with pytest.raises(ForecastValidationError, match="inverted"):
        validate_forecast(good_payload(details={"probability_or_range": "50% (range 45-15%)"}))


def test_red_odds_phrasing_refused_not_misparsed():
    """RED TEST for the defect this replaced: digit-scraping turned "1 in 4"
    into the two numbers [1, 4], i.e. a 1-4% range for a 25% forecast — an
    order of magnitude wrong, accepted by validate_forecast, scored 1.0 by the
    eval, and drawn as a 1-4% bar. A confidently wrong parse is worse than a
    refusal, so odds/frequency phrasing now parses to nothing."""
    assert parse_probabilities("1 in 4") == []
    assert parse_probabilities("3/17") == []
    assert parse_probabilities("1:4") == []
    # the canonical forms are untouched
    assert parse_probabilities("25%") == [25.0]
    assert parse_probabilities("28% (range 15-45%)") == [28.0, 15.0, 45.0]
    with pytest.raises(ForecastValidationError, match="no parseable probability"):
        validate_forecast(good_payload(details={"probability_or_range": "1 in 4"}))


def test_red_four_number_string_still_range_checked():
    """RED TEST: the ordering checks branched on len(vals) == 2 / == 3, so ANY
    trailing annotation re-opened the inverted-range hole —
    "28% (range 45-15%) vs prior 30%" parsed to 4 numbers and skipped every
    check."""
    with pytest.raises(ForecastValidationError, match="canonical forms"):
        validate_forecast(
            good_payload(
                details={"probability_or_range": "28% (range 45-15%) vs prior 30%"}
            )
        )


def test_red_headline_must_match_the_logged_ensemble():
    """RED TEST: nothing compared the headline to per_pass, so the ensemble
    could be decoration beside an invented number and every guard said green.
    Passes of 25%/32% aggregate to ~28% by geomean of odds; 90% is not that."""
    with pytest.raises(ForecastValidationError, match="geometric mean of odds"):
        validate_forecast(good_payload(details={"probability_or_range": "90% (range 85-95%)"}))
    # the fixture's own honest headline (28% on 25/32) still passes
    validate_forecast(good_payload())


def test_geomean_of_odds_matches_the_documented_rule():
    from store_forecast import geomean_of_odds

    # 93/88/90 -> 90.5% (the worked example in the /forecast skill)
    assert round(geomean_of_odds([93.0, 88.0, 90.0]), 1) == 90.5
    assert round(geomean_of_odds([25.0, 32.0]), 1) == 28.4


def test_red_delta_contradicting_its_branches_refused():
    """RED TEST: delta was stored and rendered entirely unvalidated. The delta
    is what /botec consumes as the additionality parameter, so a wrong sign
    propagates straight into cost-effectiveness."""
    pair = {
        "status_quo": "20% (range 10-35%)",
        "with_intervention": "32% (range 18-50%)",
        "probability_or_range": "28% (range 15-45%)",
    }
    with pytest.raises(ForecastValidationError, match="contradicts its own branches"):
        validate_forecast(good_payload(details={**pair, "delta": "-40pp"}))
    with pytest.raises(ForecastValidationError, match="carries no number"):
        validate_forecast(good_payload(details={**pair, "delta": "lots"}))
    # the truthful delta passes
    validate_forecast(good_payload(details={**pair, "delta": "+12pp"}))


def test_red_resolution_on_a_fresh_note_refused():
    """RED TEST: `details.resolution` renders a green "Resolution:" box, so a
    brand-new forecast carrying one displayed as already settled. Resolution
    belongs on the SUPERSEDING note."""
    with pytest.raises(ForecastValidationError, match="supersedes nothing"):
        validate_forecast(good_payload(details={"resolution": "RESOLVED YES"}))
    # ...and is allowed on a note that actually supersedes one
    validate_forecast(
        good_payload(
            details={"resolution": "RESOLVED YES"},
            supersedes_note_id="11111111-2222-3333-4444-555555555555",
        )
    )


def test_red_pass_missing_stance_refused():
    with pytest.raises(ForecastValidationError, match="stance"):
        validate_forecast(
            good_payload(
                details={"per_pass": [{"probability": "30%"}, {"probability": "20%"}]}
            )
        )


def test_red_wrong_kind_refused():
    with pytest.raises(ForecastValidationError, match="kind='forecast'"):
        validate_forecast(good_payload(kind="observation"))


# ---------------------------------------------------------------------------
# GREEN
# ---------------------------------------------------------------------------

def test_green_valid_forecast_passes():
    out = validate_forecast(good_payload())
    assert out["kind"] == "forecast"


def test_green_counterfactual_pair_passes():
    out = validate_forecast(
        good_payload(
            details={
                "status_quo": "20% (range 10-35%)",
                "with_intervention": "32% (range 18-50%)",
                "delta": "+12pp (range 0-25pp)",
            }
        )
    )
    assert out["details"]["delta"] == "+12pp (range 0-25pp)"


def test_green_unpaired_single_probability_passes():
    assert validate_forecast(good_payload())["details"]["probability_or_range"]

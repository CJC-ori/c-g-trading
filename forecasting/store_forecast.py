"""Store a forecast on the append surface — thin, strict wrapper over
``tools/notes/store_note.py`` for ``kind='forecast'`` rows.

Why this wrapper exists (vs. calling store_note directly)
---------------------------------------------------------
``store_note`` enforces only presence of the forecast detail keys. A forecast
is the one note kind where a *malformed payload silently poisons a future
process*: the calibration sweep, the review-due sweep, and the org-page
probability bars all parse ``details``. So forecasts go through here, which
additionally enforces:

  * ``resolution_criteria`` is a substantive string (not "TBD");
  * every probability anywhere in the payload (``probability_or_range``,
    ``status_quo`` / ``with_intervention``, ``per_pass[].probability``) parses
    and sits strictly inside (0, 100) — a 0% or 100% "forecast" is a claim of
    certainty, not a forecast (methodology 01 §6: tails need structural
    arguments; store those in prose, not as degenerate numbers);
  * ranges are ordered (lo <= hi);
  * ``per_pass`` records >= 2 independent passes, each with a stance and a
    probability — the independence structure is the active ingredient
    (methodology 03 §1) and MUST be logged for later calibration;
  * counterfactual pairs are all-or-nothing: ``status_quo`` and
    ``with_intervention`` come together or not at all (a lone branch makes the
    delta meaningless — methodology 02 §3).

Everything else (status derivation, supersession, events emission, review_by
mirroring, anchors) is store_note's job — this module never touches SQL.

CLI (JSON on stdin, same shape as store_note but kind is implied)::

    echo '{"question": "...", "answer_md": "...", "source_urls": [...],
           "organization_id": "...", "source_tool": "forecast",
           "created_by": "fable-5",
           "details": {"resolution_criteria": "...",
                        "probability_or_range": "28% (range 15-45%)",
                        "base_rate": "...", "key_drivers": [...],
                        "per_pass": [{"stance": "outside-view", "probability": "25%",
                                       "rationale_summary": "..."}, ...],
                        "review_by_date": "2027-12-31"}}' \\
        | uv run python tools/forecast/store_forecast.py

Public API:
  - ``parse_probabilities(raw) -> list[float]``  (pure; also used by the eval)
  - ``validate_forecast(payload) -> dict``        (pure — no DB; unit-tested)
  - ``store_forecast(payload, conn=None, commit=True) -> dict``
"""

from __future__ import annotations

import json
import math as _math
import os
import re
import sys
from typing import Any

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
from tools.notes.store_note import StoreNoteError, store_note  # noqa: E402

MIN_PASSES = 2  # the skill dispatches 2-3+; fewer than 2 is not an ensemble
MIN_CRITERIA_CHARS = 40  # "TBD" / "passes" are not resolution criteria
# How far the stored headline may sit from the geometric mean of odds of the
# per_pass ensemble, in percentage points. The aggregation rule IS the method
# (methodology 03 §2), and nothing used to compare the two — a headline of 90%
# on passes of 25%/32% (true geomean 28%) validated clean and scored 4/4. The
# band is generous: it exists to catch an invented headline, not to police
# legitimate judgement (Samotsvety-style trims, extremising) on top of the mean.
MAX_HEADLINE_DRIFT_PP = 12.0

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
# A probability string must be an explicit percentage or a 0-1 fraction. Digit
# scraping alone silently mis-parsed "1 in 4" as the range 1-4% (an order of
# magnitude wrong, and then scored 1.0) and "3/17" as 3-17%.
_ODDS_PHRASE_RE = re.compile(r"\d\s*(?:in|/|:)\s*\d")


class ForecastValidationError(StoreNoteError):
    """A forecast-specific validation failure. Message is the whole story."""


def parse_probabilities(raw: Any) -> list[float]:
    """Extract probabilities (as percent, 0-100 scale) from a value.

    Accepts the formats the renderer's parseProbRange understands (keep in
    parity with web/src/lib/probRange.ts): "28%",
    "28% (range 15-45%)", "15-45%", 0.28, "0.28". Fraction form (all values
    <= 1, no % sign) is scaled x100. Returns [] when nothing parses.
    """
    if raw is None:
        return []
    if isinstance(raw, (int, float)):
        v = float(raw)
        return [v * 100 if 0 < v <= 1 else v]
    s = str(raw)
    # Odds/frequency phrasing ("1 in 4", "3/17", "1:4") is NOT the canonical
    # form and digit-scraping it produces a confidently wrong number, which is
    # worse than failing to parse. Refuse by returning nothing parseable.
    if _ODDS_PHRASE_RE.search(s):
        return []
    vals = [float(m) for m in _NUM_RE.findall(s)]
    if not vals:
        return []
    if "%" not in s and all(v <= 1 for v in vals):
        vals = [v * 100 for v in vals]
    return vals


def _check_bounds(raw: Any, field: str) -> list[float]:
    vals = parse_probabilities(raw)
    if not vals:
        raise ForecastValidationError(
            f"{field} carries no parseable probability: {raw!r} "
            "(expected e.g. '28%', '15-45%', '28% (range 15-45%)')"
        )
    for v in vals:
        if not (0 < v < 100):
            raise ForecastValidationError(
                f"{field} probability {v}pp is out of bounds — must be strictly "
                "between 0 and 100. A 0%/100% claim is certainty, not a "
                f"forecast (got {raw!r})."
            )
    # Canonical formats: "28%" · "15-45%" · "28% (range 15-45%)". More than
    # three numbers means the string carries prose the parser cannot interpret
    # ("28% (range 45-15%) vs prior 30%" used to skip EVERY ordering check
    # because the length branches only handled 2 and 3).
    if len(vals) > 3:
        raise ForecastValidationError(
            f"{field} carries {len(vals)} numbers: {raw!r}. Use exactly one of "
            "the canonical forms — 'P%', 'LO-HI%', or 'CENTRAL% (range LO-HI%)' "
            "— and move any commentary into key_drivers/crux."
        )
    if len(vals) == 2 and vals[0] > vals[1]:
        raise ForecastValidationError(f"{field} range is inverted (lo > hi): {raw!r}")
    if len(vals) == 3:
        # 3-value canonical form is [central, lo, hi]: refuse an inverted
        # range FIRST (the old compound condition silently accepted
        # "28% (range 45-15%)" — gen5 wave-1 review), then a central
        # estimate outside its own range.
        if vals[1] > vals[2]:
            raise ForecastValidationError(
                f"{field} range is inverted (lo > hi): {raw!r} "
                "(canonical form is 'CENTRAL% (range LO-HI%)')"
            )
        if not (vals[1] <= vals[0] <= vals[2]):
            raise ForecastValidationError(
                f"{field} central estimate sits outside its own range: {raw!r} "
                "(canonical form is 'CENTRAL% (range LO-HI%)')"
            )
    return vals


def central_of(raw: Any) -> float | None:
    """The single best point estimate implied by a probability string. Pure.

    "28% (range 15-45%)" -> 28 (the canonical 3-value form is [central, lo, hi])
    "15-45%"             -> 30 (midpoint; no central was stated)
    "28%"                -> 28
    """
    vals = parse_probabilities(raw)
    if not vals:
        return None
    if len(vals) >= 3:
        return vals[0]
    if len(vals) == 2:
        return (vals[0] + vals[1]) / 2
    return vals[0]


def geomean_of_odds(probs: list[float]) -> float | None:
    """Aggregate percent probabilities by the geometric mean of their ODDS.

    THE aggregation rule for the ensemble (methodology 03 §2) — the reason the
    module asks for independent passes at all. Values are clamped away from the
    0/100 asymptotes so one extreme pass cannot annihilate the mean. Pure.
    """
    usable = [min(max(p, 0.01), 99.99) for p in probs if p is not None]
    if not usable:
        return None
    log_odds = sum(_math.log(p / (100 - p)) for p in usable) / len(usable)
    odds = _math.exp(log_odds)
    return 100 * odds / (1 + odds)


def check_headline_matches_passes(details: dict) -> None:
    """The stored headline must be near the ensemble it claims to summarise.

    Nothing used to compare them, so a per_pass list could be pure decoration
    beside an invented headline and every guard still said green. Raises
    :class:`ForecastValidationError` when the drift exceeds
    ``MAX_HEADLINE_DRIFT_PP``. Pure.
    """
    per_pass = details.get("per_pass")
    if not isinstance(per_pass, list):
        return
    pass_centrals = [
        c
        for c in (
            central_of(p.get("probability"))
            for p in per_pass
            if isinstance(p, dict)
        )
        if c is not None
    ]
    if len(pass_centrals) < MIN_PASSES:
        return
    aggregate = geomean_of_odds(pass_centrals)
    headline = central_of(details.get("probability_or_range"))
    if aggregate is None or headline is None:
        return
    drift = abs(headline - aggregate)
    if drift > MAX_HEADLINE_DRIFT_PP:
        raise ForecastValidationError(
            f"details.probability_or_range ({headline:.1f}%) is {drift:.1f}pp "
            f"from the geometric mean of odds of the {len(pass_centrals)} "
            f"logged passes ({aggregate:.1f}%). The ensemble aggregation IS the "
            "method (methodology 03 §2) — either the headline was not derived "
            "from these passes, or a pass is missing. Max drift: "
            f"{MAX_HEADLINE_DRIFT_PP}pp."
        )


def check_delta(details: dict) -> None:
    """``delta`` must agree in sign and magnitude with its own two branches.

    The delta is what /botec consumes as the additionality parameter, so a
    wrong sign propagates into cost-effectiveness — and it used to be stored
    and rendered entirely unvalidated ("Δ -40pp" beside bars implying +12pp,
    and the literal string "lots", both accepted). Pure.
    """
    raw = details.get("delta")
    if raw is None:
        return
    sq = central_of(details.get("status_quo"))
    wi = central_of(details.get("with_intervention"))
    if sq is None or wi is None:
        raise ForecastValidationError(
            "details.delta is set but the counterfactual pair is not — a delta "
            "with nothing to difference is uncheckable; drop it or supply both "
            "status_quo and with_intervention."
        )
    expected = wi - sq
    text = str(raw)
    nums = _NUM_RE.findall(text)
    if not nums:
        raise ForecastValidationError(
            f"details.delta carries no number: {raw!r} (expected e.g. '+14pp')"
        )
    stated = float(nums[0])
    if "-" in text.split(nums[0])[0] or "\u2212" in text.split(nums[0])[0]:
        stated = -stated
    if abs(stated - expected) > 1.0:
        raise ForecastValidationError(
            f"details.delta ({raw!r}) contradicts its own branches: "
            f"with_intervention {wi:.1f}% - status_quo {sq:.1f}% = "
            f"{expected:+.1f}pp."
        )


def validate_forecast(payload: dict) -> dict:
    """Forecast-specific validation. Pure — no DB. Returns the payload with
    ``kind`` pinned to 'forecast'. Raises :class:`ForecastValidationError`
    on the first problem. store_note re-validates the generic contract."""
    if not isinstance(payload, dict):
        raise ForecastValidationError("payload must be a JSON object")
    kind = payload.get("kind", "forecast")
    if kind != "forecast":
        raise ForecastValidationError(
            f"store_forecast only stores kind='forecast' (got {kind!r}) — "
            "use tools/notes/store_note.py for other kinds"
        )
    details = payload.get("details")
    if not isinstance(details, dict):
        raise ForecastValidationError("details must be a JSON object")

    criteria = details.get("resolution_criteria")
    if not isinstance(criteria, str) or len(criteria.strip()) < MIN_CRITERIA_CHARS:
        raise ForecastValidationError(
            "details.resolution_criteria must be a substantive string "
            f"(>= {MIN_CRITERIA_CHARS} chars): entity, event, threshold, "
            "deadline, evidence source + what does NOT count "
            "(tools/forecast/methodology/02-fuzzy-questions.md §1). "
            f"Got: {criteria!r}"
        )

    _check_bounds(details.get("probability_or_range"), "details.probability_or_range")

    # Counterfactual pair: all-or-nothing.
    sq, wi = details.get("status_quo"), details.get("with_intervention")
    if (sq is None) != (wi is None):
        raise ForecastValidationError(
            "counterfactual pair is half-missing: status_quo and "
            "with_intervention must both be present (same resolution criteria, "
            "two conditionings) or both absent — a lone branch has no delta."
        )
    if sq is not None:
        _check_bounds(sq, "details.status_quo")
        _check_bounds(wi, "details.with_intervention")
    check_delta(details)

    # A brand-new forecast cannot already be resolved: `details.resolution`
    # belongs on the SUPERSEDING note (store_note module docstring, migration
    # 106). Without this a first-time forecast could ship with a green
    # "Resolution: RESOLVED YES" banner on the org page.
    if details.get("resolution") is not None and not payload.get("supersedes_note_id"):
        raise ForecastValidationError(
            "details.resolution is set on a note that supersedes nothing — a "
            "forecast resolves by being SUPERSEDED by a note carrying the "
            "outcome, never by being born resolved."
        )

    per_pass = details.get("per_pass")
    if not isinstance(per_pass, list) or len(per_pass) < MIN_PASSES:
        raise ForecastValidationError(
            f"details.per_pass must log >= {MIN_PASSES} independent passes "
            "(the ensemble is the method — methodology 03 §1); got "
            f"{len(per_pass) if isinstance(per_pass, list) else per_pass!r}"
        )
    for i, p in enumerate(per_pass):
        if not isinstance(p, dict) or not str(p.get("stance") or "").strip():
            raise ForecastValidationError(
                f"details.per_pass[{i}] needs a non-empty 'stance' "
                "(e.g. outside-view-first, causal-chain-first, adversarial-NO)"
            )
        _check_bounds(p.get("probability"), f"details.per_pass[{i}].probability")
    check_headline_matches_passes(details)

    out = dict(payload)
    out["kind"] = "forecast"
    return out


def store_forecast(payload: dict, conn=None, commit: bool = True) -> dict:
    """Validate forecast-specifically, then delegate to store_note (which
    validates the generic contract, inserts, supersedes, emits the event)."""
    return store_note(validate_forecast(payload), conn=conn, commit=commit)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"store_forecast: stdin is not valid JSON: {exc}", file=sys.stderr)
        return 1
    try:
        result = store_forecast(payload)
    except StoreNoteError as exc:  # includes ForecastValidationError
        print(f"store_forecast: REFUSED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

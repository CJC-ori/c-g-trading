"""Deterministic quality scorecard for stored forecasts (gen5 wave 1).

Scores ONE ``research_notes`` row with ``kind='forecast'`` — the row the
``/forecast`` skill persists via ``tools/forecast/store_forecast.py``. Wired
as ``run_evals.py --run-type forecast`` (the single wiring site); called
non-blocking by the skill after storing, but a breach fires the generic eval
gate like every other arm.

What it defends against (the defect classes from the forecasting literature,
tools/forecast/methodology/): a bare confident number with no falsifiable
resolution criteria; out-of-bounds or certainty-claiming probabilities; a
"forecast" that skipped the independent-ensemble structure (single pass); and
an ungrounded forecast citing nothing (pretraining-memory rule). Four
deterministic checks, no LLM judge — v1 deliberately does NOT judge forecast
*quality* (calibration needs resolved questions; revisit at >=30 resolutions).

  forecast_resolution_criteria  substantive criteria present (>=40 chars) AND
                                carrying a deadline year; "TBD" scores 0,
                                deadline-less criteria score 0.5.
  forecast_probability_bounds   every probability in the payload (headline,
                                pair branches, per-pass) parses via the SAME
                                parser the store wrapper uses and sits
                                strictly inside (0, 100); ranges ordered.
  forecast_independent_passes   >=2 logged passes each with stance +
                                in-bounds probability (the calibration
                                flywheel input); 1 pass or malformed = 0.
  forecast_sources_cited        >=1 concrete grounding ref: a source_urls
                                entry, an http(s) URL in answer_md, or a DB
                                row UUID in answer_md.

Exception path scores 0, never vanishes (verifier-scope rule) — see _safe.
Results land in eval_results with both run-FKs NULL and the note id in
details (brief/writeup/correction pattern; no migration needed).

CLI: ``uv run python tools/evals/forecast_quality.py --id <research_notes.id>``
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

# The pure checks below need only the sibling store_forecast module.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Same parser as the store wrapper — two computations that must agree read the
# same object (never retype the parsing rules here).
from store_forecast import (  # noqa: E402
    MIN_CRITERIA_CHARS,
    MIN_PASSES,
    parse_probabilities,
)

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)
_URL_RE = re.compile(r"https?://\S+")
_YEAR_RE = re.compile(r"\b20\d{2}\b")


def _conn():
    # ── NVF-ONLY ─────────────────────────────────────────────────────────
    # Connects to NVF's Postgres via $NVF_DATABASE_URL (env was loaded by
    # NVF's tools/shared/env.py, dropped in this repo). Lazy import so the
    # pure checks above/below stay importable without psycopg2 installed.
    # TO PORT: repoint this (and fetch/_store below) at the new DB.
    import psycopg2

    return psycopg2.connect(os.environ["NVF_DATABASE_URL"], connect_timeout=10)


def _safe(check_fn, eval_name: str, eval_category: str, *args, **kwargs) -> dict:
    """Run one check; a crash yields a 0-score row naming the error — the
    exception path must score 0, never vanish (verifier-scope rule)."""
    try:
        return check_fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001 — eval is non-blocking; degrade loudly
        return {
            "eval_name": eval_name,
            "eval_category": eval_category,
            "score": 0.0, "max_score": 1.0,
            "details": {"exception": f"{type(e).__name__}: {e}"},
            "notes": f"CHECK CRASHED — scored 0, not skipped: {e}",
        }


# ---------------------------------------------------------------------------
# Pure checks (unit-tested without a DB)
# ---------------------------------------------------------------------------

def check_resolution_criteria(criteria) -> dict:
    """Substantive + deadline-bearing resolution criteria (methodology 02 §1)."""
    text = str(criteria or "").strip()
    if len(text) < MIN_CRITERIA_CHARS:
        score, note = 0.0, (
            f"resolution criteria missing or vacuous ({text!r}) — an "
            "unfalsifiable forecast can never be scored; this is the "
            "rationale-shaped-hole defect the eval exists to catch"
        )
    elif not _YEAR_RE.search(text):
        score, note = 0.5, (
            "resolution criteria carry no deadline year — criteria must pin "
            "entity/event/threshold/DEADLINE/evidence source"
        )
    else:
        score, note = 1.0, None
    return {
        "eval_name": "forecast_resolution_criteria",
        "eval_category": "quality",
        "score": score, "max_score": 1.0,
        "details": {"length": len(text), "has_deadline_year": bool(_YEAR_RE.search(text))},
        "notes": note,
    }


def _bounds_ok(raw) -> tuple[bool, str | None]:
    vals = parse_probabilities(raw)
    if not vals:
        return False, f"unparseable probability: {raw!r}"
    bad = [v for v in vals if not (0 < v < 100)]
    if bad:
        return False, f"out-of-bounds probability {bad} in {raw!r}"
    if len(vals) == 2 and vals[0] > vals[1]:
        return False, f"inverted range: {raw!r}"
    if len(vals) == 3:
        # canonical [central, lo, hi] — mirror store_forecast._check_bounds
        if vals[1] > vals[2]:
            return False, f"inverted range: {raw!r}"
        if not (vals[1] <= vals[0] <= vals[2]):
            return False, f"central estimate outside its own range: {raw!r}"
    return True, None


def check_probability_bounds(details: dict) -> dict:
    """Every probability anywhere in the payload parses and is in (0, 100)."""
    problems: list[str] = []
    checked = 0
    for field in ("probability_or_range", "status_quo", "with_intervention"):
        if details.get(field) is not None:
            checked += 1
            ok, why = _bounds_ok(details[field])
            if not ok:
                problems.append(f"{field}: {why}")
    for i, p in enumerate(details.get("per_pass") or []):
        prob = p.get("probability") if isinstance(p, dict) else None
        checked += 1
        ok, why = _bounds_ok(prob)
        if not ok:
            problems.append(f"per_pass[{i}]: {why}")
    if checked == 0:
        problems.append("no probabilities found anywhere in details — vacuous forecast")
    return {
        "eval_name": "forecast_probability_bounds",
        "eval_category": "reliability",
        "score": 0.0 if problems else 1.0,
        "max_score": 1.0,
        "details": {"checked": checked, "problems": problems},
        "notes": "; ".join(problems) or None,
    }


def check_independent_passes(per_pass) -> dict:
    """The independence structure must be logged: >=2 passes, stance + prob."""
    passes = per_pass if isinstance(per_pass, list) else []
    n = len(passes)
    malformed = [
        i for i, p in enumerate(passes)
        if not isinstance(p, dict)
        or not str(p.get("stance") or "").strip()
        or not _bounds_ok(p.get("probability"))[0]
    ]
    if n < MIN_PASSES:
        score, note = 0.0, (
            f"only {n} pass(es) logged (need >= {MIN_PASSES}) — the "
            "independent-ensemble structure is the method; a single-pass "
            "number is the badly-calibrated defect (methodology 03 §1)"
        )
    elif malformed:
        score, note = 0.5, f"passes {malformed} missing stance or valid probability"
    else:
        score, note = 1.0, None
    return {
        "eval_name": "forecast_independent_passes",
        "eval_category": "coverage",
        "score": score, "max_score": 1.0,
        "details": {"n_passes": n, "malformed": malformed},
        "notes": note,
    }


def check_sources_cited(source_urls, answer_md) -> dict:
    """>=1 concrete grounding ref: URL in source_urls/answer, or DB row UUID."""
    urls = [u for u in (source_urls or []) if u] + _URL_RE.findall(answer_md or "")
    uuids = _UUID_RE.findall(answer_md or "")
    ok = bool(urls or uuids)
    return {
        "eval_name": "forecast_sources_cited",
        "eval_category": "quality",
        "score": 1.0 if ok else 0.0,
        "max_score": 1.0,
        "details": {"url_refs": len(urls), "db_id_refs": len(uuids)},
        "notes": None if ok else (
            "forecast cites NO source (no URL, no DB row ref) — an ungrounded "
            "forecast is pretraining memory wearing a number (source-grounding "
            "house rule)"
        ),
    }


# ---------------------------------------------------------------------------
# Fetch + orchestrate
# ---------------------------------------------------------------------------

# =========================================================================
# ── NVF-ONLY BOUNDARY ────────────────────────────────────────────────────
# Everything ABOVE (the check_* functions and _safe) is pure and portable —
# score any forecast payload, no DB needed. Everything BELOW reads/writes
# NVF's Postgres (research_notes + eval_results tables) and must be
# reimplemented on this project's own storage.
# =========================================================================


def fetch_forecast_note(note_id: str) -> dict | None:
    import psycopg2.extras  # NVF-ONLY, see boundary banner

    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT id, kind, question, answer_md, source_urls, details, status "
            "FROM research_notes WHERE id::text = %s",
            (note_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def _store(note_id: str, results: list[dict]) -> int:
    """eval_results rows, both run-FKs NULL, note id in details."""
    with _conn() as conn, conn.cursor() as cur:
        for r in results:
            details = dict(r.get("details") or {})
            details["forecast_note_id"] = note_id
            cur.execute(
                """INSERT INTO eval_results (
                     research_run_id, person_research_run_id, eval_name, eval_category,
                     score, max_score, details, evaluator_type, evaluator_model, notes
                   ) VALUES (NULL, NULL, %s, %s, %s, %s, %s::jsonb, 'sql', NULL, %s)""",
                (r["eval_name"], r["eval_category"], r["score"], r["max_score"],
                 json.dumps(details, default=str), r.get("notes")),
            )
        conn.commit()
    return len(results)


def evaluate_forecast(note_id: str, store: bool = True) -> dict:
    """Score one stored forecast. Returns {forecast_note_id, results, summary,
    quality_error}."""
    row = fetch_forecast_note(note_id)
    if not row:
        msg = f"no research_notes row found for id {note_id!r}"
        return {"summary": msg, "results": [], "quality_error": msg}
    if row["kind"] != "forecast":
        msg = f"note {note_id} has kind={row['kind']!r}, not 'forecast'"
        return {"summary": msg, "results": [], "quality_error": msg}

    details = row.get("details") or {}
    results = [
        _safe(check_resolution_criteria, "forecast_resolution_criteria", "quality",
              details.get("resolution_criteria")),
        _safe(check_probability_bounds, "forecast_probability_bounds", "reliability",
              details),
        _safe(check_independent_passes, "forecast_independent_passes", "coverage",
              details.get("per_pass")),
        _safe(check_sources_cited, "forecast_sources_cited", "quality",
              row.get("source_urls"), row.get("answer_md")),
    ]
    if store:
        _store(note_id, results)
    crashed = [r["eval_name"] for r in results if (r.get("details") or {}).get("exception")]
    failed = [r["eval_name"] for r in results if r["score"] < r["max_score"]]
    return {
        "forecast_note_id": note_id,
        "question": row["question"],
        "results": results,
        "summary": " · ".join(
            f"{r['eval_name'].removeprefix('forecast_')}={r['score']}" for r in results),
        "quality_error": ("; ".join(f"{n} crashed" for n in crashed) or None),
        "failed": failed,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Forecast quality evals (deterministic)")
    ap.add_argument("--id", required=True, help="research_notes.id (kind='forecast')")
    ap.add_argument("--no-store", action="store_true")
    args = ap.parse_args()
    out = evaluate_forecast(args.id, store=not args.no_store)
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

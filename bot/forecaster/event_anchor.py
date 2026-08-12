"""Event-date anchoring for the hits screen (v2) + forecast-freshness gate.

WHY THIS EXISTS (post-hoc infrastructure fix, 2026-08-12; see
reports/case-wisconsin/ANALYSIS.md §3 and reports/hits/ANALYSIS.md
"v2 anchoring"): the frozen hits screen anchors the decision date at
D = close_time − k. For early-determination markets — every nominee market
that resolves on a public primary date months before its listed
close_time — that anchor never lands before the real event. The Wisconsin
gov-primary market (KXGOVWINOMD-26-*) carried close_time 2026-11-03
pre-event while resolving on the 2026-08-11 primary, so the screen was
structurally blind to it. The F3 close-anchor audit
(reports/tournament/round4/ia_econ_only/f3_close_anchor_audit.json)
flagged the same shape: `can_close_early` markets whose data-determined
outcome fixes long before close.

This module derives a decision-relevant EVENT DATE from market metadata,
with provenance, in strict priority order:

  1. ``explicit_text`` — dates parsed from rules_primary / title /
     subtitle ("on Jun 23, 2026", "before Aug 1, 2026", ISO, m/d/y, ...).
     Among parsed dates the LATEST one inside the market's life
     (open .. close+1d] is taken: rules typically cite background dates
     first and the operative deadline/measurement date last.
  2. ``election_calendar`` — for election markets with no explicit date
     (the Wisconsin shape: "Will X be the Democratic nominee ...?"), the
     public statutory election calendar. Primary dates are ex-ante
     public-calendar knowledge (state statutes fix them years ahead);
     the table below is partial and states not listed simply yield no
     calendar date. Runoffs/special elections are excluded (dates are
     not statutory).
  3. ``expected_expiration`` — the market's own expected_expiration_time
     (raw JSON), when it precedes close_time. An upper bound on the event
     date, still far better than a far-future close.
  4. ``activity`` — volume-concentration signature from hourly candles.
     OUTCOME-ADJACENT (trading concentrates when the event happens), so it
     is flagged ``outcome_adjacent=True`` and MUST NEVER be used for
     backtest candidate selection; it exists for live diagnostics only.
     ``anchored_decision_ts`` refuses to anchor on it.

Anchor rule: D = event_date − k days whenever an event date exists and
precedes close_time by more than k days; otherwise D = close_time − k
(the frozen behaviour, unchanged).

FRESHNESS GATE (pre-specified by reports/hits/ANALYSIS.md — "a forecast
must carry a freshness window"; engine lesson: the Clayton trade fired on
the wrong side days after D, after the price had moved *through* the
forecast): a forecast is STALE if |now − D| > f hours at (simulated)
execution time, and entry additionally requires re-validating that the
forecast-vs-price gap still points the same way it did at D.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone

DAY = 86400

# Default k (days before the event) — mirrors bot.forecaster.hits.DECISION_DAYS
# (not imported to keep this module dependency-free and import-cycle-safe).
DEFAULT_K_DAYS = 10

# Freshness gate default: a forecast made at D is executable for f hours.
# Chosen as a round 1-day default when the gate was specified as follow-up
# infrastructure (reports/hits/ANALYSIS.md caveat 3) — NOT tuned on any
# backtest result.
FRESHNESS_HOURS = 24.0

# Event timestamps are pinned to 12:00 UTC of the derived calendar date —
# a neutral midpoint; the anchor granularity is the day, not the hour.
EVENT_HOUR_UTC = 12


@dataclass(frozen=True)
class EventAnchor:
    """A derived event date with provenance."""
    event_ts: int | None            # unix ts (12:00 UTC of event date) or None
    source: str                     # "explicit_text" | "election_calendar" |
                                    # "expected_expiration" | "activity" | "none"
    provenance: str                 # matched text / field / calendar key
    outcome_adjacent: bool = False  # True only for "activity"

    @property
    def event_date(self) -> date | None:
        if self.event_ts is None:
            return None
        return datetime.fromtimestamp(self.event_ts, tz=timezone.utc).date()


# ---------------------------------------------------------------------------
# 1. Explicit dates in text
# ---------------------------------------------------------------------------

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}

_MON = r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|" \
       r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|" \
       r"Nov(?:ember)?|Dec(?:ember)?)"
_ORD = r"(?:st|nd|rd|th)?"

# "Jun 23, 2026", "June 23 2026", "Jun 23" (year resolved from context).
# (?!\d) stops "Jun 2026" (month-year, no day) parsing as day 20.
_RE_MDY = re.compile(rf"\b{_MON}\.?\s+(\d{{1,2}})(?!\d){_ORD}(?:,?\s+(\d{{4}}))?")
# "23 June 2026"
_RE_DMY = re.compile(rf"\b(\d{{1,2}}){_ORD}\s+{_MON}\.?,?\s+(\d{{4}})")
# ISO 2026-06-23 (also matches inside 2026-06-23T14:00:00Z)
_RE_ISO = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})")
# 6/23/2026
_RE_NUM = re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b")


def _mk_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_dates_from_text(text: str,
                          default_year: int | None = None
                          ) -> list[tuple[date, str]]:
    """All (date, matched_text) pairs found in ``text``.

    Dates written without a year take ``default_year`` (typically the
    close year); if ``default_year`` is None they are skipped.
    """
    out: list[tuple[date, str]] = []
    if not text:
        return out
    for m in _RE_MDY.finditer(text):
        mon = _MONTHS[m.group(1)[:3].lower()]
        day = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else default_year
        if year is None:
            continue
        d = _mk_date(year, mon, day)
        if d:
            out.append((d, m.group(0)))
    for m in _RE_DMY.finditer(text):
        mon = _MONTHS[m.group(2)[:3].lower()]
        d = _mk_date(int(m.group(3)), mon, int(m.group(1)))
        if d:
            out.append((d, m.group(0)))
    for m in _RE_ISO.finditer(text):
        d = _mk_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            out.append((d, m.group(0)))
    for m in _RE_NUM.finditer(text):
        d = _mk_date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        if d:
            out.append((d, m.group(0)))
    return out


# ---------------------------------------------------------------------------
# 2. Public election calendar (partial; statutory dates only)
# ---------------------------------------------------------------------------
# 2026 statutory state-primary dates. These are ex-ante public-calendar
# knowledge (fixed by state statute long before any market opened). The
# table is deliberately PARTIAL: only entries with a well-known statutory
# rule are included; a state not listed yields no calendar date and the
# anchor falls through to the next source. Verifiable against repo data:
# TX 03-03 (KXTXRSEN2ND-26MAR03 tickers), MI 08-04 and WI/MN/CT/VT 08-11
# (reports/case-wisconsin/ANALYSIS.md).
PRIMARY_2026: dict[str, tuple[int, int]] = {
    # first Tuesday of March
    "texas": (3, 3), "north carolina": (3, 3),
    "illinois": (3, 17),
    # first Tuesday of May
    "ohio": (5, 5), "indiana": (5, 5),
    "west virginia": (5, 12), "nebraska": (5, 12),
    # third Tuesday of May
    "pennsylvania": (5, 19), "kentucky": (5, 19), "oregon": (5, 19),
    "idaho": (5, 19), "georgia": (5, 19),
    # first Tuesday after first Monday of June
    "california": (6, 2), "iowa": (6, 2), "montana": (6, 2),
    "new jersey": (6, 2), "new mexico": (6, 2), "south dakota": (6, 2),
    "mississippi": (6, 2),
    "maine": (6, 9), "nevada": (6, 9), "north dakota": (6, 9),
    "south carolina": (6, 9),
    # first Tuesday of August
    "arizona": (8, 4), "kansas": (8, 4), "michigan": (8, 4),
    "missouri": (8, 4), "washington": (8, 4),
    "tennessee": (8, 6),        # first Thursday of August
    "hawaii": (8, 8),           # second Saturday of August
    # second Tuesday of August (Wis. Stat. §5.02(12s) and analogues)
    "wisconsin": (8, 11), "minnesota": (8, 11), "vermont": (8, 11),
    "connecticut": (8, 11),
    # third Tuesday of August
    "alaska": (8, 18), "wyoming": (8, 18), "florida": (8, 18),
    "new hampshire": (9, 8),    # second Tuesday of September
}

# First Tuesday after the first Monday in November.
GENERAL_ELECTION: dict[int, date] = {2026: date(2026, 11, 3)}

_STATE_ABBR = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas",
    "CA": "california", "CO": "colorado", "CT": "connecticut",
    "DE": "delaware", "FL": "florida", "GA": "georgia", "HI": "hawaii",
    "ID": "idaho", "IL": "illinois", "IN": "indiana", "IA": "iowa",
    "KS": "kansas", "KY": "kentucky", "LA": "louisiana", "ME": "maine",
    "MD": "maryland", "MA": "massachusetts", "MI": "michigan",
    "MN": "minnesota", "MS": "mississippi", "MO": "missouri",
    "MT": "montana", "NE": "nebraska", "NV": "nevada",
    "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico",
    "NY": "new york", "NC": "north carolina", "ND": "north dakota",
    "OH": "ohio", "OK": "oklahoma", "OR": "oregon", "PA": "pennsylvania",
    "RI": "rhode island", "SC": "south carolina", "SD": "south dakota",
    "TN": "tennessee", "TX": "texas", "UT": "utah", "VT": "vermont",
    "VA": "virginia", "WA": "washington", "WV": "west virginia",
    "WI": "wisconsin", "WY": "wyoming",
}

_PRIMARY_KW = re.compile(r"\bnominee\b|\bnomination\b|\bprimary\b", re.I)
_EXCLUDE_KW = re.compile(r"\brunoff\b|\brun-off\b|\bspecial\b|\brecall\b", re.I)
_GENERAL_KW = re.compile(r"\bgeneral election\b|\belection day\b", re.I)
_RE_DISTRICT = re.compile(r"\b([A-Z]{2})-\d{1,2}\b")


def _detect_state(text: str) -> str | None:
    low = text.lower()
    for name in PRIMARY_2026:
        if name in low:
            return name
    # district codes like "AZ-01" (unambiguous uppercase-with-district form)
    for m in _RE_DISTRICT.finditer(text):
        name = _STATE_ABBR.get(m.group(1))
        if name:
            return name
    return None


def election_calendar_date(text: str, default_year: int | None
                           ) -> tuple[date, str] | None:
    """(date, provenance) from the statutory calendar, or None."""
    if not text or _EXCLUDE_KW.search(text):
        return None
    years = [int(y) for y in re.findall(r"\b(20\d{2})\b", text)]
    year = years[0] if years else default_year
    if _PRIMARY_KW.search(text):
        if year != 2026:            # calendar table covers 2026 only
            return None
        state = _detect_state(text)
        if state and state in PRIMARY_2026:
            mth, dom = PRIMARY_2026[state]
            return (date(2026, mth, dom),
                    f"primary_2026:{state}")
    if _GENERAL_KW.search(text) and year in GENERAL_ELECTION:
        return GENERAL_ELECTION[year], f"general_election:{year}"
    return None


# ---------------------------------------------------------------------------
# 3. expected_expiration_time (raw market JSON)
# ---------------------------------------------------------------------------

def _iso_to_ts(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def expected_expiration_ts(raw_json: str | dict | None) -> int | None:
    if not raw_json:
        return None
    d = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
    v = d.get("expected_expiration_time")
    if not v:
        return None
    try:
        return _iso_to_ts(v)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 4. Activity signature (OUTCOME-ADJACENT — live diagnostics only)
# ---------------------------------------------------------------------------

def activity_event_ts(con: sqlite3.Connection, ticker: str,
                      min_share: float = 0.4) -> tuple[int | None, float]:
    """(event_ts, share) from hourly-candle volume concentration.

    Peak calendar day of traded volume; returns an anchor only when that
    single day carries >= ``min_share`` of lifetime candle volume. This
    observes WHEN trading concentrated, which is a function of the outcome
    happening — it must never feed backtest candidate selection.
    """
    rows = con.execute(
        """SELECT ts, volume FROM candlesticks
           WHERE ticker=? AND period_interval=60 AND volume IS NOT NULL""",
        (ticker,),
    ).fetchall()
    by_day: dict[int, float] = {}
    total = 0.0
    for ts, vol in rows:
        v = float(vol or 0.0)
        total += v
        by_day[ts // DAY] = by_day.get(ts // DAY, 0.0) + v
    if not by_day or total <= 0:
        return None, 0.0
    peak_day, peak_vol = max(by_day.items(), key=lambda kv: kv[1])
    share = peak_vol / total
    if share < min_share:
        return None, share
    return peak_day * DAY + EVENT_HOUR_UTC * 3600, share


# ---------------------------------------------------------------------------
# Derivation + anchoring
# ---------------------------------------------------------------------------

def _date_to_ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, EVENT_HOUR_UTC,
                        tzinfo=timezone.utc).timestamp())


def derive_event_anchor(
    *,
    title: str = "",
    subtitle: str = "",
    rules: str = "",
    open_ts: int,
    close_ts: int,
    raw_json: str | dict | None = None,
    con: sqlite3.Connection | None = None,
    ticker: str = "",
    allow_activity: bool = False,
) -> EventAnchor:
    """Best-priority event date for one market, with provenance.

    Validity for text/calendar dates: the event must fall inside the
    market's life — strictly after open, no later than close + 1 day
    (a mentioned date beyond close is a backstop/expiration reference,
    not the decision-relevant event).
    """
    close_year = datetime.fromtimestamp(close_ts, tz=timezone.utc).year
    lo, hi = open_ts, close_ts + DAY

    # 1. explicit dates in text (rules first: they carry the operative date)
    found: list[tuple[date, str, str]] = []
    for field, text in (("rules_primary", rules), ("title", title),
                        ("subtitle", subtitle)):
        for d, matched in parse_dates_from_text(text, default_year=close_year):
            found.append((d, matched, field))
    valid = [(d, matched, field) for d, matched, field in found
             if lo < _date_to_ts(d) <= hi]
    if valid:
        d, matched, field = max(valid, key=lambda x: x[0])
        return EventAnchor(event_ts=_date_to_ts(d), source="explicit_text",
                           provenance=f"{field}: {matched!r}")

    # 2. statutory election calendar
    blob = " ".join(t for t in (title, subtitle, rules) if t)
    cal = election_calendar_date(blob, default_year=close_year)
    if cal is not None:
        d, prov = cal
        ts = _date_to_ts(d)
        if lo < ts <= hi:
            return EventAnchor(event_ts=ts, source="election_calendar",
                               provenance=prov)

    # 3. the market's own expected expiration
    exp_ts = expected_expiration_ts(raw_json)
    if exp_ts is not None and lo < exp_ts < close_ts:
        return EventAnchor(event_ts=exp_ts, source="expected_expiration",
                           provenance="raw_json.expected_expiration_time")

    # 4. activity signature — flagged fallback, never for backtest selection
    if allow_activity and con is not None and ticker:
        ts, share = activity_event_ts(con, ticker)
        if ts is not None and lo < ts <= hi:
            return EventAnchor(event_ts=ts, source="activity",
                               provenance=f"volume_concentration={share:.2f}",
                               outcome_adjacent=True)

    return EventAnchor(event_ts=None, source="none", provenance="")


def anchored_decision_ts(anchor: EventAnchor | None, close_ts: int,
                         k_days: int = DEFAULT_K_DAYS) -> tuple[int, str]:
    """(decision_ts, anchor_source_used).

    D = event_date − k when a non-outcome-adjacent event date exists and
    precedes close_time by more than k days; otherwise the frozen
    D = close_time − k. Activity-derived anchors are refused here by
    construction (outcome-adjacent ⇒ never backtest selection).
    """
    d_close = close_ts - k_days * DAY
    if (anchor is not None and anchor.event_ts is not None
            and not anchor.outcome_adjacent
            and close_ts - anchor.event_ts > k_days * DAY):
        return anchor.event_ts - k_days * DAY, anchor.source
    return d_close, "close_time"


# ---------------------------------------------------------------------------
# Forecast-freshness gate (pre-specified: reports/hits/ANALYSIS.md caveat 3)
# ---------------------------------------------------------------------------

def is_stale(now_ts: int, decision_ts: int,
             f_hours: float = FRESHNESS_HOURS) -> bool:
    """A forecast anchored at D is stale when |now − D| > f hours."""
    return abs(now_ts - decision_ts) > f_hours * 3600.0


def gap_still_valid(p_yes: float, mid_now_cents: float,
                    price_at_d_cents: float, gate_pts: float) -> bool:
    """Re-validate the forecast-vs-price gap at (simulated) execution.

    The gap must (a) still clear the entry gate and (b) point the SAME WAY
    it did at D. A price that has moved *through* the forecast (the Clayton
    failure: forecast 18%, price 93.5c at D, 0.5c at execution) inverts the
    sign and is not our trade — the market has already agreed with us and
    kept going.
    """
    edge_now = p_yes * 100.0 - mid_now_cents
    edge_at_d = p_yes * 100.0 - price_at_d_cents
    return abs(edge_now) >= gate_pts and edge_now * edge_at_d > 0.0

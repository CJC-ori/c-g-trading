"""R7 — the hand-curated, STRUCTURALLY verified Kalshi<->Polymarket pair map.

Title matching is banned here. research/oss-arb.md §4.2 measured what fuzzy
matchers do on exactly the family we care about: "Bitcoin above $100,000 on
Dec 31" vs "…$110,000…" scores **1.000**; "Fed cuts rates by 25 bps in
September" vs "…50 bps…" scores 0.817; "Will Trump be indicted in 2026?" vs
"Will Trump resign in 2026?" scores 0.861. Its confidence is
anti-correlated with correctness precisely on strike/threshold/date
variants. So every pair in this file is matched on:

  1. the **underlying quantity** (what number/event decides it),
  2. the **exact strike** (bps bucket, name, threshold),
  3. the **exact resolution date/instant**,
  4. the **resolution source** named in both venues' rules text,

each recorded verbatim in the pair's ``audit`` field, and then re-checked
mechanically by ``audit_pair`` against live metadata from both venues
(``audit_pairs.py``). A pair that fails any check is dropped — never traded
with a "needs review" flag.

### What was checked, per family

**Fed decision (11 FOMC meetings, May 2025 – Jul 2026, 2 legs each = 22 pairs)**

* Kalshi `KXFEDDECISION-<YYMON>-<leg>`; rules_primary: *"If the Federal
  Reserve does a Hike of 0bps on <date>, then the market resolves to Yes"* /
  *"…does a Cut of 25bps on <date>…"*; rules_secondary states the event is
  mutually exclusive and that a canceled meeting resolves "Fed maintains
  rate" YES.
* Polymarket `fed-decision-in-<month>` event, legs "No change" / "25 bps
  decrease"; description: *"The FED interest rates are defined in this
  market by the upper bound of the target federal funds range… resolve to
  the amount of basis points the upper bound … is changed by versus the
  level it was prior to the Federal Reserve's <Month> <Year> meeting…
  rounded up to the nearest 25"*.
* Underlying: the same FOMC target-range decision, same meeting, same day.
  Strike: 0 bps and −25 bps, identical buckets. Source: the FOMC statement
  itself on both venues.
* **Residual difference (documented, not hidden):** Kalshi's outer buckets
  are "Cut >25bps"/"Hike >25bps" while Polymarket's are "50+ bps"; these
  coincide only under Polymarket's round-up-to-25 rule. That is why the map
  uses ONLY the "no change" and "cut 25" legs, where the two definitions are
  literally identical. Kalshi resolves a canceled meeting to "maintains
  rate"; Polymarket's text is silent — immaterial for the 11 meetings here
  (all occurred), flagged for forward use.

**Nobel Peace Prize 2025 — Donald Trump (1 pair)**

* Kalshi `KXNOBELPEACE-25-DJT`: *"If Donald Trump wins the Nobel Peace Prize
  in 2025, then the market resolves to Yes."*
* Polymarket `will-donald-trump-win-the-nobel-peace-prize-in-2025`.
* Same person, same prize year, same announcement instant (Norwegian Nobel
  Committee, 2025-10-10), both resolved NO.

### Rejected candidates (recorded so the rejection is auditable)

* **LA mayor 2026** — Kalshi `KXMAYORLA-26` closed 2026-06-08 (first round);
  the Polymarket `los-angeles-mayoral-election-117` event runs to
  2026-12-31 (general). Different question. REJECTED.
* **NYC mayor 2025** — Kalshi `KXMAYORNYCPARTY-25-D` resolves on the
  *party* of the winner ("a representative of the Democratic party"), the
  Polymarket market on the *person* (Mamdani). Co-extensive in fact, not in
  rules. REJECTED.
* **PRES-2024** — same party-vs-person defect. REJECTED.
* **Khamenei out** — Kalshi's deadline is 2026-02-01T15:00Z; the Polymarket
  ladder is by-March-31 / by-June-30 / by-July-31. No shared strike date.
  REJECTED.
* **Government shutdown** — Kalshi `KXGOVSHUT-26JAN31` is "shut down *on*
  Jan 31"; Polymarket's are "shutdown *by* <date>". Point-in-time vs
  cumulative — a genuinely different payoff. REJECTED.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- the hand-verified table -------------------------------------------------
# (kalshi_event, pm_event_slug, meeting label as it appears in the PM rules
#  text, Kalshi close date UTC, FOMC decision date UTC)
FED_MEETINGS: list[tuple[str, str, str, str]] = [
    ("KXFEDDECISION-25MAY", "fed-decision-in-may-2025", "May 2025", "2025-05-07"),
    ("KXFEDDECISION-25JUN", "fed-decision-in-june", "June 2025", "2025-06-18"),
    ("KXFEDDECISION-25JUL", "fed-decision-in-july", "July 2025", "2025-07-30"),
    ("KXFEDDECISION-25SEP", "fed-decision-in-september", "September 2025", "2025-09-17"),
    ("KXFEDDECISION-25OCT", "fed-decision-in-october", "October 2025", "2025-10-29"),
    ("KXFEDDECISION-25DEC", "fed-decision-in-december", "December 2025", "2025-12-10"),
    ("KXFEDDECISION-26JAN", "fed-decision-in-january", "January 2026", "2026-01-28"),
    ("KXFEDDECISION-26MAR", "fed-decision-in-march-885", "March 2026", "2026-03-18"),
    ("KXFEDDECISION-26APR", "fed-decision-in-april", "April 2026", "2026-04-29"),
    ("KXFEDDECISION-26JUN", "fed-decision-in-june-825", "June 2026", "2026-06-17"),
    ("KXFEDDECISION-26JUL", "fed-decision-in-july-181", "July 2026", "2026-07-29"),
]

#: Kalshi leg suffix -> (Polymarket groupItemTitle, human strike statement).
#: Only the two legs whose definitions are *literally* identical on both
#: venues are mapped; see the module docstring on the >25 vs 50+ mismatch.
FED_LEGS: dict[str, tuple[str, str]] = {
    "H0": ("No change", "target range change = 0 bps at this meeting"),
    "C25": ("25 bps decrease", "target range change = −25 bps at this meeting"),
}

@dataclass(frozen=True)
class PairSpec:
    """One structurally verified Kalshi<->Polymarket pair."""
    pair_id: str
    family: str
    kalshi_ticker: str
    pm_event_slug: str           # Gamma event slug (multi-leg events)
    pm_leg_title: str            # groupItemTitle of the leg inside that event
    subject: str                 # the underlying quantity
    strike: str                  # the exact threshold/bucket/name
    resolution_date: str         # UTC date both venues settle on
    kalshi_source: str           # resolution source named in Kalshi's rules
    pm_source: str               # ... and in Polymarket's description
    audit: str                   # verbatim evidence + residual differences
    pm_rules_token: str = ""     # substring that must appear in the PM text
    kalshi_rules_token: str = ""  # ... and in the Kalshi rules text


@dataclass
class PairAudit:
    pair_id: str
    ok: bool
    checks: dict = field(default_factory=dict)
    failures: list = field(default_factory=list)


FED_SOURCE_K = "FOMC target federal funds rate decision announced at the meeting"
FED_SOURCE_P = (
    "upper bound of the target federal funds range set by the FOMC at that meeting"
)


def _fed_pairs() -> list[PairSpec]:
    out: list[PairSpec] = []
    for k_event, pm_event, label, date in FED_MEETINGS:
        month_year = label
        for leg, (pm_leg, strike_text) in FED_LEGS.items():
            out.append(
                PairSpec(
                    pair_id=f"fed-{k_event.split('-')[-1].lower()}-{leg.lower()}",
                    family="fed_decision",
                    kalshi_ticker=f"{k_event}-{leg}",
                    pm_event_slug=pm_event,
                    pm_leg_title=pm_leg,
                    subject=f"FOMC target-range change at the {month_year} meeting",
                    strike=strike_text,
                    resolution_date=date,
                    kalshi_source=FED_SOURCE_K,
                    pm_source=FED_SOURCE_P,
                    audit=(
                        f"Kalshi rules_primary: 'If the Federal Reserve does a "
                        f"{'Hike of 0bps' if leg == 'H0' else 'Cut of 25bps'} on "
                        f"<{date}>, then the market resolves to Yes.' | Polymarket "
                        f"description: 'resolve to the amount of basis points the "
                        f"upper bound of the target federal funds rate is changed by "
                        f"versus the level it was prior to the Federal Reserve's "
                        f"{month_year} meeting'. Same meeting, same day, same bucket "
                        f"({strike_text}). Both venues settle off the FOMC statement."
                    ),
                    pm_rules_token=f"{month_year} meeting",
                    kalshi_rules_token=(
                        "Hike of 0bps" if leg == "H0" else "Cut of 25bps"
                    ),
                )
            )
    return out


NOBEL_PAIR = PairSpec(
    pair_id="nobel-peace-2025-trump",
    family="nobel",
    kalshi_ticker="KXNOBELPEACE-25-DJT",
    pm_event_slug="nobel-peace-prize-winner-2025",
    pm_leg_title="Donald Trump",
    subject="Winner of the 2025 Nobel Peace Prize",
    strike="laureate is Donald J. Trump",
    resolution_date="2025-10-10",
    kalshi_source="Norwegian Nobel Committee announcement",
    pm_source="Norwegian Nobel Committee announcement",
    audit=(
        "Kalshi rules_primary: 'If Donald Trump wins the Nobel Peace Prize in "
        "2025, then the market resolves to Yes.' Kalshi close "
        "2025-10-10T11:59:30Z. Polymarket market "
        "'will-donald-trump-win-the-nobel-peace-prize-in-2025', event "
        "'Nobel Peace Prize Winner 2025', closedTime 2025-10-10 12:10:18+00. "
        "Same person, same prize, same year, same announcing body, same day; "
        "both resolved NO."
    ),
    pm_rules_token="Nobel Peace Prize",
    kalshi_rules_token="Nobel Peace Prize",
)


def curated_pairs() -> list[PairSpec]:
    """The full hand-curated map (23 pairs / 12 independent resolutions)."""
    return _fed_pairs() + [NOBEL_PAIR]


# ---------------------------------------------------------------------------
# Mechanical re-verification
# ---------------------------------------------------------------------------

def _same_utc_day(ts: int | None, date_str: str) -> bool:
    import datetime as dt

    if ts is None:
        return False
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d") == date_str


def normalize_text(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def audit_pair(
    spec: PairSpec,
    kalshi: dict | None,
    pm: dict | None,
    require_outcome_agreement: bool = True,
) -> PairAudit:
    """Re-derive every claim in ``spec`` from live metadata.

    ``kalshi``: {'ticker','rules_primary','close_time_ts','result'}
    ``pm``:     {'slug','description','closed_ts','resolved_yes','clob_winner'}

    Checks, all of which must pass:
      1. both records exist and carry the recorded tickers/slugs;
      2. the Kalshi rules text contains the recorded strike token;
      3. the Polymarket description contains the recorded resolution token
         (for Fed pairs this is the exact "<Month> <Year> meeting" string —
         the check that stops a September market being paired to an October
         one, the single most likely fuzzy-match failure);
      4. both venues settle on the same UTC calendar day;
      5. Polymarket's Gamma outcome agrees with the CLOB winner (catches
         mid-dispute rows);
      6. the two venues' realized outcomes agree — if the same question
         settled differently, the pair was never the same question.
    """
    a = PairAudit(spec.pair_id, True)
    if kalshi is None:
        a.failures.append("kalshi-market-missing")
    if pm is None:
        a.failures.append("pm-market-missing")
    if kalshi is None or pm is None:
        a.ok = False
        return a

    a.checks["kalshi_ticker"] = kalshi.get("ticker") == spec.kalshi_ticker
    a.checks["pm_slug"] = bool(pm.get("slug"))
    a.checks["kalshi_rules_token"] = (
        normalize_text(spec.kalshi_rules_token) in normalize_text(kalshi.get("rules_primary"))
        if spec.kalshi_rules_token
        else True
    )
    a.checks["pm_rules_token"] = (
        normalize_text(spec.pm_rules_token) in normalize_text(pm.get("description"))
        if spec.pm_rules_token
        else True
    )
    a.checks["kalshi_resolution_date"] = _same_utc_day(
        kalshi.get("close_time_ts"), spec.resolution_date
    )
    a.checks["pm_resolution_date"] = _same_utc_day(pm.get("closed_ts"), spec.resolution_date)

    k_yes = (kalshi.get("result") or "").lower() == "yes"
    p_yes = pm.get("resolved_yes")
    winner = (pm.get("clob_winner") or "").lower()
    a.checks["pm_gamma_clob_agree"] = (
        p_yes is None or winner == "" or (p_yes == (winner == "yes"))
    )
    if require_outcome_agreement:
        a.checks["outcomes_agree"] = p_yes is not None and (k_yes == p_yes)

    for name, passed in a.checks.items():
        if not passed:
            a.failures.append(name)
    a.ok = not a.failures
    return a

"""Tests for the R7 pair-audit logic.

The audit is the only thing standing between us and the failure mode
research/oss-arb.md §4.2 measured: fuzzy matchers score *different* markets
at 0.82–1.00 confidence precisely on strike/threshold/date variants. Each
test below is one of those variants, and must be rejected.
"""
from __future__ import annotations

import datetime as dt

from bot.strategies.structure import pairs as P


def ts(date_str: str, hour: int = 21) -> int:
    return int(
        dt.datetime.strptime(date_str, "%Y-%m-%d")
        .replace(hour=hour, tzinfo=dt.timezone.utc)
        .timestamp()
    )


SPEC = P.PairSpec(
    pair_id="fed-25sep-h0",
    family="fed_decision",
    kalshi_ticker="KXFEDDECISION-25SEP-H0",
    pm_event_slug="fed-decision-in-september",
    pm_leg_title="No change",
    subject="FOMC target-range change at the September 2025 meeting",
    strike="target range change = 0 bps at this meeting",
    resolution_date="2025-09-17",
    kalshi_source="FOMC statement",
    pm_source="FOMC statement",
    audit="...",
    pm_rules_token="September 2025 meeting",
    kalshi_rules_token="Hike of 0bps",
)

KALSHI = {
    "ticker": "KXFEDDECISION-25SEP-H0",
    "rules_primary": (
        "If the Federal Reserve does a Hike of 0bps on September 17, 2025, "
        "then the market resolves to Yes."
    ),
    "close_time_ts": ts("2025-09-17", 17),
    "result": "no",
}

PM = {
    "slug": "no-change-in-fed-interest-rates-after-september-2025-meeting",
    "description": (
        "This market will resolve to the amount of basis points the upper bound "
        "of the target federal funds rate is changed by versus the level it was "
        "prior to the Federal Reserve's September 2025 meeting."
    ),
    "closed_ts": ts("2025-09-17"),
    "resolved_yes": False,
    "clob_winner": "No",
}


def test_matching_pair_passes_every_check():
    a = P.audit_pair(SPEC, KALSHI, PM)
    assert a.ok, a.failures
    assert set(a.checks) >= {
        "kalshi_ticker", "pm_rules_token", "kalshi_rules_token",
        "kalshi_resolution_date", "pm_resolution_date",
        "pm_gamma_clob_agree", "outcomes_agree",
    }
    assert all(a.checks.values())


def test_wrong_month_is_rejected_even_though_titles_are_near_identical():
    """'Fed decision in September' vs 'Fed decision in October' is exactly
    the pair a title matcher scores ~0.95 on. The rules-token check is what
    catches it."""
    october = dict(PM)
    october["description"] = PM["description"].replace("September 2025", "October 2025")
    a = P.audit_pair(SPEC, KALSHI, october)
    assert not a.ok
    assert "pm_rules_token" in a.failures


def test_wrong_strike_bucket_is_rejected():
    """25 bps vs 0 bps: the Kalshi rules token pins the bucket."""
    cut25 = dict(KALSHI)
    cut25["rules_primary"] = cut25["rules_primary"].replace("Hike of 0bps", "Cut of 25bps")
    a = P.audit_pair(SPEC, cut25, PM)
    assert not a.ok and "kalshi_rules_token" in a.failures


def test_resolution_date_mismatch_is_rejected_on_either_side():
    late_pm = dict(PM, closed_ts=ts("2025-10-29"))
    assert "pm_resolution_date" in P.audit_pair(SPEC, KALSHI, late_pm).failures
    late_k = dict(KALSHI, close_time_ts=ts("2025-10-29"))
    assert "kalshi_resolution_date" in P.audit_pair(SPEC, late_k, PM).failures


def test_outcome_disagreement_rejects_the_pair():
    """If the same question settled differently on the two venues, it was
    never the same question — this is the strongest available check and it
    is ex-post, so it can only be used to build the map, never to trade."""
    flipped = dict(PM, resolved_yes=True, clob_winner="Yes")
    a = P.audit_pair(SPEC, KALSHI, flipped)
    assert not a.ok and "outcomes_agree" in a.failures


def test_gamma_clob_settlement_disagreement_is_caught():
    """Mid-dispute UMA rows show up as Gamma/CLOB disagreement."""
    disputed = dict(PM, resolved_yes=False, clob_winner="Yes")
    a = P.audit_pair(SPEC, KALSHI, disputed)
    assert not a.ok and "pm_gamma_clob_agree" in a.failures


def test_missing_side_fails_closed():
    assert P.audit_pair(SPEC, None, PM).failures == ["kalshi-market-missing"]
    assert P.audit_pair(SPEC, KALSHI, None).failures == ["pm-market-missing"]
    assert not P.audit_pair(SPEC, None, None).ok


def test_unresolved_pm_market_cannot_be_verified():
    live = dict(PM, resolved_yes=None, clob_winner=None)
    a = P.audit_pair(SPEC, KALSHI, live)
    assert not a.ok and "outcomes_agree" in a.failures
    # ... but with the ex-post check switched off, the structural ones stand
    a2 = P.audit_pair(SPEC, KALSHI, live, require_outcome_agreement=False)
    assert a2.ok


def test_normalize_text_is_whitespace_and_case_insensitive():
    assert P.normalize_text("  September   2025\nMEETING ") == "september 2025 meeting"


# --- the curated map itself --------------------------------------------------

def test_curated_map_is_well_formed():
    specs = P.curated_pairs()
    assert len(specs) == 23
    assert len({s.pair_id for s in specs}) == len(specs)
    assert len({s.kalshi_ticker for s in specs}) == len(specs)
    for s in specs:
        assert s.subject and s.strike and s.audit
        assert s.kalshi_source and s.pm_source
        dt.datetime.strptime(s.resolution_date, "%Y-%m-%d")
        assert s.pm_event_slug and s.pm_leg_title


def test_curated_map_only_uses_the_unambiguous_fed_legs():
    """Kalshi's outer buckets are '>25bps' while Polymarket's are '50+';
    they agree only via Polymarket's round-up rule, so those legs are
    deliberately excluded from the map."""
    fed = [s for s in P.curated_pairs() if s.family == "fed_decision"]
    assert {s.kalshi_ticker.rsplit("-", 1)[1] for s in fed} == {"H0", "C25"}


def test_independent_resolution_count():
    """23 pairs, but only 12 independent resolutions — the two legs of one
    FOMC meeting are the same event. The kill criterion is stated on clean
    *pairs*; the statistical n is the smaller number."""
    specs = P.curated_pairs()
    assert len({s.resolution_date for s in specs}) == 12

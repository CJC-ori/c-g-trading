"""Tests for the R3 scan math (the numbers ANALYSIS.md reports)."""
from __future__ import annotations

import pytest

from bot.backtest import fees as harness_fees
from bot.strategies.structure import overround as ov
from bot.strategies.structure.scan_r3 import forward_filled_grid

CC = ov.CONTRACT_CC


def q(ticker, bid_c, ask_c, vol=0.0, stale=0):
    """LegQuote from whole-cent prices."""
    return ov.LegQuote(ticker, int(bid_c * 100), int(ask_c * 100), vol, stale)


# --- fee model ---------------------------------------------------------------

@pytest.mark.parametrize("price_cents", [1, 5, 17, 33, 50, 66, 90, 99])
def test_fee_matches_harness_on_whole_cents(price_cents):
    """Our centicent-precision fee is the harness formula, extended — not a
    different model. Pinning them together stops the two drifting apart."""
    assert ov.taker_fee_cc(price_cents * 100, 7) == harness_fees.trade_fee_centicents(
        price_cents, 7, is_taker=True
    )


def test_fee_is_symmetric_and_ceiled_to_a_centicent():
    assert ov.taker_fee_cc(3000) == ov.taker_fee_cc(7000)     # P(1-P) symmetry
    # 0.07 * 0.5 * 0.5 = $0.0175 exactly -> 175 centicents, no rounding up.
    assert ov.taker_fee_cc(5000) == 175
    # 0.07 * 0.1 * 0.9 = $0.0063 -> 63 cc exactly.
    assert ov.taker_fee_cc(1000) == 63
    # a sub-cent price the harness's integer-cent API cannot express
    assert ov.taker_fee_cc(50) > 0


def test_fee_multiplier_zero_series_pays_nothing():
    assert ov.taker_fee_cc(5000, 10, fee_multiplier=0.0) == 0


def test_fee_price_out_of_band_rejected():
    with pytest.raises(ValueError):
        ov.taker_fee_cc(0)
    with pytest.raises(ValueError):
        ov.taker_fee_cc(CC)


# --- the executable-vs-literal distinction -----------------------------------

def test_no_set_uses_bids_not_asks():
    """A perfectly coherent book (mids 50/30/20, sum exactly 100c) with a
    4c spread has Σask = 106c and is NOT an arb — but the literal ask-sum
    rule fires on it, because it is measuring the spread."""
    legs = [q("a", 48, 52), q("b", 28, 32), q("c", 18, 22)]   # mids 50/30/20
    assert ov.no_set_signal(legs).fires is False
    lit = ov.literal_ask_sum_signal(legs)
    assert lit.quote_sum_cc == 10600 and lit.gross_edge_cc == 600
    assert lit.fires is True          # <- the spec's literal rule, wrongly


def test_no_set_fires_when_bid_sum_exceeds_par():
    legs = [q("a", 60, 62), q("b", 30, 32), q("c", 20, 22)]   # Σbid = 110c
    sig = ov.no_set_signal(legs)
    assert sig.quote_sum_cc == 11000
    assert sig.gross_edge_cc == 1000                      # 10c gross
    assert sig.fires
    # cost = 3*100 - 110 = 190c; payoff floor = 200c
    assert sig.cost_cc == 19000
    assert sig.net_edge_cc == sig.gross_edge_cc - sig.fee_cc - 100


def test_no_set_payoff_invariant_holds_for_every_winner():
    """Buying the full NO set pays (n-1)*100 whichever single leg wins, and
    n*100 if none does — the property that makes the trade riskless."""
    legs = [q("a", 60, 62), q("b", 30, 32), q("c", 20, 22)]
    sig = ov.no_set_signal(legs)
    n = len(legs)
    for winner in list(range(n)) + [None]:
        payoff = sum(0 if i == winner else CC for i in range(n))
        assert payoff >= (n - 1) * CC
        assert payoff - sig.cost_cc >= sig.gross_edge_cc


def test_leg_with_no_yes_bid_makes_the_set_unbuyable():
    legs = [q("a", 0, 62), q("b", 60, 62), q("c", 55, 57)]    # Σbid = 115c
    assert ov.no_set_signal(legs).fires is False


def test_yes_set_fires_on_underround_and_prices_the_cost():
    legs = [q("a", 40, 42), q("b", 25, 27), q("c", 20, 22)]   # Σask = 91c
    sig = ov.yes_set_signal(legs)
    assert sig.quote_sum_cc == 9100 and sig.cost_cc == 9100
    assert sig.gross_edge_cc == 900
    assert sig.fires


def test_fillable_sets_bind_on_the_thinnest_leg():
    legs = [q("a", 60, 62, vol=1000), q("b", 30, 32, vol=8), q("c", 20, 22, vol=4000)]
    sig = ov.no_set_signal(legs, depth_frac=0.25)
    assert sig.fillable_sets == 2                     # 25% of 8, not of 1000
    assert sig.fillable_notional_cc == 2 * sig.cost_cc


def test_fee_stress_can_extinguish_a_marginal_signal():
    legs = [q("a", 50, 52), q("b", 30, 32), q("c", 21, 23)]   # Σbid = 101c
    assert ov.no_set_signal(legs, buffer_cc=0).fires is False  # fees eat 1c
    assert ov.no_set_signal(legs, buffer_cc=0, fee_stress=1.5).net_edge_cc < ov.no_set_signal(
        legs, buffer_cc=0
    ).net_edge_cc


# --- single-bracket fade -----------------------------------------------------

def test_dominant_cheap_bracket_identifies_the_offending_leg():
    # coherent level for 'c' given the others is 100-95 = 5c; it bids 9c.
    legs = [q("a", 60, 62), q("b", 35, 37), q("c", 9, 11)]
    fade = ov.dominant_cheap_bracket(legs)
    assert fade is not None and fade.ticker == "c"
    assert fade.contribution_cc == 400 and fade.share == pytest.approx(1.0)


def test_dominant_cheap_bracket_ignores_expensive_legs():
    legs = [q("a", 80, 82), q("b", 25, 27)]           # 'a' is the culprit but 80c
    assert ov.dominant_cheap_bracket(legs, max_price_cc=1000) is None


def test_dominant_cheap_bracket_none_without_overround():
    legs = [q("a", 50, 52), q("b", 30, 32), q("c", 9, 11)]
    assert ov.dominant_cheap_bracket(legs) is None


# --- structural exhaustiveness ----------------------------------------------

def test_contiguous_open_ended_bracket_ladder_is_exhaustive():
    strikes = [
        ("less", None, 2.5),
        ("between", 2.5, 2.6),
        ("between", 2.6, 2.7),
        ("greater", 2.7, None),
    ]
    assert ov.is_exhaustive_by_structure(strikes) is True


def test_bracket_ladder_with_a_gap_is_not_exhaustive():
    strikes = [
        ("less", None, 2.5),
        ("between", 2.5, 2.6),
        ("between", 5.0, 5.1),      # gap
        ("greater", 5.1, None),
    ]
    assert ov.is_exhaustive_by_structure(strikes) is False


def test_closed_ended_ladder_is_not_exhaustive():
    assert ov.is_exhaustive_by_structure(
        [("between", 2.5, 2.6), ("between", 2.6, 2.7)]
    ) is False


def test_candidate_field_is_never_provably_exhaustive():
    """'structured'/'custom' legs are candidate fields: a write-in outcome
    is always possible, and 80 of 2,896 settled exclusive events did in
    fact resolve zero YES."""
    assert ov.is_exhaustive_by_structure(
        [("structured", None, None), ("custom", None, None)]
    ) is False


# --- grid construction -------------------------------------------------------

def _series(points):
    return {tk: [(t, b * 100, a * 100, v) for t, b, a, v in rows] for tk, rows in points.items()}


def test_grid_requires_every_leg_to_be_quoted():
    legs = _series({"a": [(3600, 50, 52, 1)], "b": [(7200, 30, 32, 1)]})
    grid = forward_filled_grid(legs, staleness_s=24 * 3600)
    assert [ts for ts, _ in grid] == [7200]        # 3600 has no quote for 'b'


def test_grid_forward_fills_but_respects_the_staleness_bound():
    legs = _series({"a": [(3600, 50, 52, 1)], "b": [(3600, 30, 32, 1), (100000, 31, 33, 1)]})
    fresh = forward_filled_grid(legs, staleness_s=3600)
    stale_ok = forward_filled_grid(legs, staleness_s=10**6)
    assert [ts for ts, _ in fresh] == [3600]
    assert [ts for ts, _ in stale_ok] == [3600, 100000]
    assert stale_ok[1][1]["a"][3] == 100000 - 3600     # reported staleness


def test_grid_reports_this_hours_volume_not_the_carried_one():
    legs = _series({"a": [(3600, 50, 52, 9), (7200, 50, 52, 0)], "b": [(3600, 30, 32, 5), (7200, 30, 32, 4)]})
    grid = dict(forward_filled_grid(legs, staleness_s=24 * 3600))
    assert grid[3600]["a"][2] == 9
    assert grid[7200]["a"][2] == 0
    assert grid[7200]["a"][4] == 9                    # trailing-24h volume

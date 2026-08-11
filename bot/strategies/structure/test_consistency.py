"""Tests for cross-instrument coherence math and the divergence strategy."""
from __future__ import annotations

import pytest

from bot.strategies.structure import consistency as C
from bot.strategies.structure import overround as ov
from bot.strategies.structure.strategy import ReferenceDivergence
from bot.backtest.types import Candle, MarketInfo, MarketView, Portfolio, Position


def q(ticker, bid_c, ask_c, vol=100.0, stale=0):
    return ov.LegQuote(ticker, int(bid_c * 100), int(ask_c * 100), vol, stale)


# --- (A) monotone ladders ----------------------------------------------------

def test_coherent_ladder_has_no_breaks():
    legs = [(2.5, q("a", 70, 72)), (2.6, q("b", 50, 52)), (2.7, q("c", 30, 32))]
    assert C.ladder_breaks(legs) == []


def test_monotonicity_break_is_priced_as_bid_high_minus_ask_low():
    # 'b' (the stricter threshold) is bid ABOVE 'a''s ask: impossible.
    legs = [(2.5, q("a", 40, 42)), (2.6, q("b", 55, 57))]
    breaks = C.ladder_breaks(legs)
    assert len(breaks) == 1
    b = breaks[0]
    assert (b.low_ticker, b.high_ticker) == ("a", "b")
    assert b.gross_cc == 1300                      # 55c - 42c
    assert b.net_cc == b.gross_cc - b.fee_cc
    assert b.fires


def test_breaks_are_checked_across_all_strike_pairs_not_just_adjacent():
    legs = [(1.0, q("a", 40, 42)), (2.0, q("b", 20, 25)), (3.0, q("c", 50, 52))]
    pairs = {(b.low_ticker, b.high_ticker) for b in C.ladder_breaks(legs)}
    assert ("a", "c") in pairs and ("b", "c") in pairs


def test_equal_strikes_are_not_compared():
    legs = [(2.0, q("a", 40, 42)), (2.0, q("b", 60, 62))]
    assert C.ladder_breaks(legs) == []


# --- (B) binary vs ladder ----------------------------------------------------

SENATE = C.CONTROL_PAIRS[0]          # senate-2024-R, tie goes to the VP's party


def _ladder(prices: dict[str, tuple[float, float]]):
    return {k: q(f"L-{k}", b, a) for k, (b, a) in prices.items()}


def test_control_mapping_includes_the_tie_seat():
    """The 50-seat leg counts as control because the Vice-President breaks
    the organising tie — the exact resolution-rule subtlety that produced
    the phantom 'Silver Bulletin 9-point Senate edge'."""
    assert "50" in SENATE.control_legs
    assert "49" not in SENATE.control_legs


def test_coherent_pair_shows_no_arb_and_no_divergence():
    ladder = _ladder({s: (9, 11) for s in SENATE.control_legs})     # 7 legs
    binary = q("CONTROLS-2024-R", 68, 72)                           # inside [63,77]
    r = C.coherence_reading(0, SENATE, binary, ladder)
    assert r.ladder_bid_sum_cc == 6300 and r.ladder_ask_sum_cc == 7700
    assert r.divergence_cc == 0
    assert r.arb_buy_ladder_net_cc < 0 and r.arb_buy_binary_net_cc < 0


def test_binary_too_dear_versus_ladder_is_a_riskless_sell():
    ladder = _ladder({s: (5, 6) for s in SENATE.control_legs})      # Σask = 42c
    binary = q("CONTROLS-2024-R", 60, 62)
    r = C.coherence_reading(0, SENATE, binary, ladder)
    assert r.arb_buy_ladder_net_cc > 0                              # 60 - 42 - fees
    assert r.divergence_cc > 0                                      # mid above band


def test_binary_too_cheap_versus_ladder_is_a_riskless_buy():
    ladder = _ladder({s: (12, 13) for s in SENATE.control_legs})    # Σbid = 84c
    binary = q("CONTROLS-2024-R", 60, 62)
    r = C.coherence_reading(0, SENATE, binary, ladder)
    assert r.arb_buy_binary_net_cc > 0                              # 84 - 62 - fees
    assert r.divergence_cc < 0


def test_ambiguous_bucket_widens_the_band_and_blocks_the_unsafe_leg():
    """The House ladder's '218 or less' bucket contains the winning case
    218, so the ladder brackets the binary instead of pinning it, and the
    'buy binary, sell ladder' arb is not riskless."""
    house = C.CONTROL_PAIRS[1]
    ladder = _ladder({s: (5, 6) for s in house.control_legs + house.ambiguous_legs})
    binary = q("CONTROLH-2024-R", 60, 62)
    r = C.coherence_reading(0, house, binary, ladder)
    assert r.ladder_ask_sum_upper_cc > r.ladder_ask_sum_cc
    assert r.arb_buy_binary_net_cc < 0            # blocked by the ambiguity


def test_missing_ladder_leg_yields_no_reading():
    ladder = _ladder({s: (9, 11) for s in SENATE.control_legs[:-1]})
    assert C.coherence_reading(0, SENATE, q("b", 60, 62), ladder) is None


def test_fillable_sets_include_the_binary_leg():
    ladder = {s: q(f"L-{s}", 9, 11, vol=1000) for s in SENATE.control_legs}
    r = C.coherence_reading(0, SENATE, q("bin", 60, 62, vol=8), ladder)
    assert r.fillable_sets == 2                   # 25% of the binary's 8


def test_every_control_pair_is_documented():
    for p in C.CONTROL_PAIRS:
        assert p.rule and p.control_legs and p.binary_ticker and p.ladder_prefix
        assert not set(p.control_legs) & set(p.ambiguous_legs)


# --- ReferenceDivergence trigger discipline ----------------------------------

def _view(ticker: str, t: int, bid: int, ask: int) -> MarketView:
    c = Candle(ticker, t - 60, 60, bid, ask, bid, (bid + ask) // 2, 10, bid, ask)
    return MarketView(
        market=MarketInfo(ticker=ticker, event_ticker="E"),
        t=t,
        candles_by_period={60: (c,)},
    )


EMPTY = Portfolio(cash_cents=1_000_000)


def test_divergence_requires_the_gap_to_persist():
    s = ReferenceDivergence(lambda tk, t: 60.0, gate_cents=4, persist_s=600)
    v0 = _view("M", 1000, 49, 51)          # mid 50, ref 60 -> gap +10c
    assert s.on_decision_point(v0, EMPTY) == []          # first sighting only
    assert s.on_decision_point(_view("M", 1300, 49, 51), EMPTY) == []   # 300s
    out = s.on_decision_point(_view("M", 1700, 49, 51), EMPTY)          # 700s
    assert len(out) == 1 and out[0].side == "yes" and out[0].action == "buy"
    assert out[0].limit_price_cents == 49                # maker at the near touch


def test_gap_below_gate_resets_the_persistence_clock():
    s = ReferenceDivergence(lambda tk, t: 60.0, gate_cents=4, persist_s=600)
    s.on_decision_point(_view("M", 1000, 49, 51), EMPTY)
    s.on_decision_point(_view("M", 1300, 58, 62), EMPTY)   # gap ~0 -> reset
    assert s.on_decision_point(_view("M", 1700, 49, 51), EMPTY) == []


def test_direction_flip_restarts_the_clock():
    ref = {"v": 60.0}
    s = ReferenceDivergence(lambda tk, t: ref["v"], gate_cents=4, persist_s=600)
    s.on_decision_point(_view("M", 1000, 49, 51), EMPTY)
    ref["v"] = 40.0                                        # now Kalshi is dear
    s.on_decision_point(_view("M", 1300, 49, 51), EMPTY)
    assert s.on_decision_point(_view("M", 1500, 49, 51), EMPTY) == []
    out = s.on_decision_point(_view("M", 1950, 49, 51), EMPTY)
    assert len(out) == 1 and out[0].side == "no"


def test_position_is_closed_when_the_gap_closes():
    s = ReferenceDivergence(lambda tk, t: 50.5, gate_cents=4, exit_gap_cents=1.0)
    held = Portfolio(cash_cents=0, positions=(Position("M", 100, 4900),))
    out = s.on_decision_point(_view("M", 1000, 49, 51), held)
    assert len(out) == 1 and out[0].action == "sell" and out[0].size == 100


def test_no_reference_means_no_trade():
    s = ReferenceDivergence(lambda tk, t: None, gate_cents=4, persist_s=0)
    assert s.on_decision_point(_view("M", 1000, 49, 51), EMPTY) == []


def test_stated_edge_is_haircut_toward_the_market():
    """The reference is the other venue's MIDPOINT, never an executable
    price (research/polymarket-data.md §2.5), so p_hat is shaded back."""
    s = ReferenceDivergence(lambda tk, t: 60.0, gate_cents=4, persist_s=0,
                            edge_haircut_cents=1.0)
    out = s.on_decision_point(_view("M", 1000, 49, 51), EMPTY)
    assert out[0].p_hat == pytest.approx(0.59)

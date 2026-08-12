"""Cross-instrument consistency monitor (SYNTHESIS §1 P-5, second bullet).

Two mechanical coherence relations, both derived from Kalshi's own strike
metadata — no title matching, no judgement calls:

**(A) Monotone threshold ladders.** In a ``greater``/``greater_or_equal``
ladder, ``YES(s₂) ⊆ YES(s₁)`` for ``s₁ < s₂``. Buying YES on the lower
strike and NO on the higher one pays ≥100¢ in every state, at a cost of
``ask(s₁) + 100 − bid(s₂)``, so:

    riskless iff   bid(s₂) − ask(s₁) > fees          (a monotonicity break)

**(B) Binary control market vs its own seat-count ladder.** The ladder's
legs partition the seat count; the binary is the indicator of a *subset* of
those legs, and which subset is a **resolution-rule question, not a
guess**: Kalshi's own rules_secondary says Senate control is "the party
identification of the President pro tempore", i.e. the majority party, and
a 50-50 Senate is organised by the Vice-President's party. This is exactly
the trap `research/ground-truth.md` §3.5 flags — the Silver-Bulletin
"9-point Senate edge" was a resolution-rule mismatch (50-50 resolves R).
So each pair carries an explicit ``control_legs`` set plus, where the
ladder's buckets straddle the majority threshold, an ``ambiguous_legs`` set
that turns the implied price into an **interval** rather than a point.

    lower(binary) = Σ bid over control legs
    upper(binary) = Σ ask over control legs ∪ ambiguous legs

Two riskless combinations follow (both all-or-nothing, depth-capped):

    binary_bid > Σ ask(control legs) + fees
        -> buy the ladder legs + buy NO on the binary; pays exactly 100¢.
    Σ bid(control legs) > binary_ask + fees            [exact pairs only]
        -> buy YES on the binary + NO on every control leg; pays k·100¢.

Anything short of those thresholds is a *divergence*, not an arb, and is
traded directionally by ``strategy.ConsistencyDivergence``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from bot.strategies.structure.overround import CONTRACT_CC, LegQuote, taker_fee_cc, _fee_price


# ---------------------------------------------------------------------------
# (A) monotone ladders
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class MonotonicityBreak:
    low_ticker: str
    high_ticker: str
    low_strike: float
    high_strike: float
    gross_cc: int          # bid(high) − ask(low)
    fee_cc: int
    net_cc: int

    @property
    def fires(self) -> bool:
        return self.net_cc > 0


def ladder_breaks(
    legs: list[tuple[float, LegQuote]],
    fee_multiplier: float = 1.0,
    buffer_cc: int = 0,
) -> list[MonotonicityBreak]:
    """All adjacent-or-not strike pairs violating monotonicity, priced.

    ``legs`` = [(strike, quote)] of a *threshold* ladder (each leg is
    "value > strike"). Only pairs with a strictly higher strike are
    compared, so the relation is the subset relation, not an assumption
    about adjacency.
    """
    out: list[MonotonicityBreak] = []
    ordered = sorted(legs, key=lambda x: x[0])
    for i, (s_lo, q_lo) in enumerate(ordered):
        for s_hi, q_hi in ordered[i + 1 :]:
            if s_hi <= s_lo:
                continue
            gross = q_hi.yes_bid_cc - q_lo.yes_ask_cc
            if gross <= 0:
                continue
            fee = taker_fee_cc(_fee_price(q_lo.yes_ask_cc), 1, fee_multiplier) + taker_fee_cc(
                _fee_price(CONTRACT_CC - q_hi.yes_bid_cc), 1, fee_multiplier
            )
            out.append(
                MonotonicityBreak(
                    q_lo.ticker, q_hi.ticker, s_lo, s_hi, gross, fee,
                    gross - fee - buffer_cc,
                )
            )
    return out


# ---------------------------------------------------------------------------
# (B) binary vs ladder
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ControlPair:
    """One hand-specified binary-vs-ladder pair.

    ``control_legs``/``ambiguous_legs`` are ticker suffixes; the mapping
    from seat count to "controls the chamber" is written out per pair with
    the rule that justifies it.
    """
    pair_id: str
    binary_ticker: str
    ladder_event: str
    ladder_prefix: str
    control_legs: tuple[str, ...]
    ambiguous_legs: tuple[str, ...] = ()
    rule: str = ""
    settled: bool = True


#: The control mapping is the load-bearing part; each entry cites the rule.
CONTROL_PAIRS: tuple[ControlPair, ...] = (
    ControlPair(
        pair_id="senate-2024-R",
        binary_ticker="CONTROLS-2024-R",
        ladder_event="RSENATESEATS-25",
        ladder_prefix="RSENATESEATS-25-",
        # R controls with >=51 outright, and with exactly 50 because the
        # Vice-President (Vance, R) breaks organising ties — Kalshi settles
        # on "party identification of the President pro tempore on
        # 2025-02-01", which follows the organising majority.
        control_legs=("50", "51", "52", "53", "54", "55", "56"),
        rule="R seats >= 50 (VP Vance breaks a 50-50 tie); leg '56' is "
             "'56 or more', leg '49' is '49 or fewer'.",
    ),
    ControlPair(
        pair_id="house-2024-R",
        binary_ticker="CONTROLH-2024-R",
        ladder_event="RHOUSESEATSSMALL-25",
        ladder_prefix="RHOUSESEATSSMALL-25-",
        control_legs=("219", "220", "221", "222", "223", "224", "225", "226",
                      "227", "228", "229", "230"),
        # The '218 or less' bucket CONTAINS the winning case R = 218
        # (218/435 is a majority), so the ladder cannot pin the binary
        # exactly — it brackets it.
        ambiguous_legs=("218",),
        rule="R seats >= 218 wins the Speakership; ladder bucket '218 or "
             "less' straddles the threshold -> interval, not a point.",
    ),
    ControlPair(
        pair_id="senate-2026-D",
        binary_ticker="CONTROLS-2026-D",
        ladder_event="KXDSENATESEATS-27",
        ladder_prefix="KXDSENATESEATS-27-",
        control_legs=("51", "52", "ABOVE52"),
        rule="D needs >= 51 (VP through Jan 2029 is Republican, so 50-50 "
             "organises R); ladder is exhaustive over BELOW45..ABOVE52.",
        settled=False,
    ),
    ControlPair(
        pair_id="house-2026-D",
        binary_ticker="CONTROLH-2026-D",
        ladder_event="KXDHOUSESEATS-27",
        ladder_prefix="KXDHOUSESEATS-27-",
        control_legs=("220", "224", "228", "232", "236", "240", "244", "248", "249"),
        rule="D seats >= 218; bracket '218-221' (ticker suffix 220) lies "
             "wholly inside the control region, so the mapping is exact.",
        settled=False,
    ),
)


@dataclass
class CoherenceReading:
    ts: int
    pair_id: str
    binary_bid_cc: int
    binary_ask_cc: int
    ladder_bid_sum_cc: int          # Σ bid over control legs  (lower bound)
    ladder_ask_sum_cc: int          # Σ ask over control legs
    ladder_ask_sum_upper_cc: int    # ... incl. ambiguous legs (upper bound)
    n_legs: int
    max_stale_s: int = 0
    arb_buy_ladder_net_cc: int = 0  # binary_bid − Σ ask(control) − fees
    arb_buy_binary_net_cc: int = 0  # Σ bid(control) − binary_ask − fees
    divergence_cc: int = 0          # signed distance of binary mid outside band
    fillable_sets: int = 0

    @property
    def binary_mid_cc(self) -> float:
        return (self.binary_bid_cc + self.binary_ask_cc) / 2

    @property
    def band(self) -> tuple[float, float]:
        return (self.ladder_bid_sum_cc, self.ladder_ask_sum_upper_cc)


def coherence_reading(
    ts: int,
    pair: ControlPair,
    binary: LegQuote,
    ladder: dict[str, LegQuote],
    fee_multiplier: float = 1.0,
    depth_frac: float = 0.25,
) -> CoherenceReading | None:
    """Price one instant of one binary-vs-ladder pair. None if legs missing."""
    ctrl = [ladder[s] for s in pair.control_legs if s in ladder]
    if len(ctrl) != len(pair.control_legs):
        return None
    amb = [ladder[s] for s in pair.ambiguous_legs if s in ladder]
    ask_sum = sum(q.yes_ask_cc for q in ctrl)
    bid_sum = sum(q.yes_bid_cc for q in ctrl)
    ask_upper = ask_sum + sum(q.yes_ask_cc for q in amb)

    fee_ladder_yes = sum(
        taker_fee_cc(_fee_price(q.yes_ask_cc), 1, fee_multiplier) for q in ctrl
    )
    fee_binary_no = taker_fee_cc(_fee_price(CONTRACT_CC - binary.yes_bid_cc), 1, fee_multiplier)
    arb_a = binary.yes_bid_cc - ask_sum - fee_ladder_yes - fee_binary_no

    fee_ladder_no = sum(
        taker_fee_cc(_fee_price(CONTRACT_CC - q.yes_bid_cc), 1, fee_multiplier) for q in ctrl
    )
    fee_binary_yes = taker_fee_cc(_fee_price(binary.yes_ask_cc), 1, fee_multiplier)
    arb_b = bid_sum - binary.yes_ask_cc - fee_ladder_no - fee_binary_yes
    if pair.ambiguous_legs:
        arb_b = min(arb_b, -1)   # unsafe when a bucket straddles the threshold

    mid = (binary.yes_bid_cc + binary.yes_ask_cc) / 2
    if mid < bid_sum:
        div = int(mid - bid_sum)          # binary too cheap vs ladder
    elif mid > ask_upper:
        div = int(mid - ask_upper)        # binary too dear vs ladder
    else:
        div = 0
    quotes = ctrl + [binary]
    sets = int(min(int(depth_frac * (q.volume or 0.0)) for q in quotes))
    return CoherenceReading(
        ts=ts,
        pair_id=pair.pair_id,
        binary_bid_cc=binary.yes_bid_cc,
        binary_ask_cc=binary.yes_ask_cc,
        ladder_bid_sum_cc=bid_sum,
        ladder_ask_sum_cc=ask_sum,
        ladder_ask_sum_upper_cc=ask_upper,
        n_legs=len(ctrl),
        max_stale_s=max(q.stale_s for q in quotes),
        arb_buy_ladder_net_cc=arb_a,
        arb_buy_binary_net_cc=arb_b,
        divergence_cc=div,
        fillable_sets=sets,
    )

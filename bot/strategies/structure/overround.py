"""R3 — multi-outcome overround math (SYNTHESIS §1 P-5).

## The microstructure correction (read this before trusting any number)

SYNTHESIS states R3 as "when Σ best-ask YES > 100¢ + fees, buy the full NO
set". Taken literally that rule is **not executable**, and it fires almost
always. Kalshi's book exposes YES bids and NO bids only; the API's own
words (research/kalshi-api.md §7):

    "a bid for yes at price X is equivalent to an ask for no at price
     (100−X)"

so the reported ``yes_ask`` is *itself* 100 − best NO bid, and:

    price to BUY NO on leg i  =  100 − yes_bid_i        (the NO ask)
    price to BUY YES on leg i =  yes_ask_i

Therefore, for an n-leg mutually exclusive event:

    NO-set  cost      = Σ (100 − yes_bid_i) = 100n − Σ yes_bid_i
    NO-set  payoff    ≥ (n−1)·100           (=n·100 if zero legs resolve YES)
    NO-set  gross P&L ≥ Σ yes_bid_i − 100        <-- **BID sum, not ask sum**

    YES-set cost      = Σ yes_ask_i
    YES-set payoff    = 100 iff the event is exhaustive (some leg must win)
    YES-set gross P&L = 100 − Σ yes_ask_i

Σ ask > 100 is the *normal* state of any book with a spread (it is
100 + Σ spreads when the mid-prices are coherent), so scanning for it
measures the spread, not an edge. Both quantities are computed here:
``ask_sum`` is reported as the literal-spec diagnostic and ``bid_sum`` as
the executable one. This distinction is the single most important finding
of the R3 scan and it is why the literal rule's "frequency" is ~100% while
the executable one is what the kill criterion must be judged on.

## Units

Everything is in **centicents** (cc): $0.0001, i.e. 1¢ = 100 cc, a full
contract = 10,000 cc. Kalshi quotes carry deci-cent (10 cc) ticks in
tapered election markets, and the fee itself is ceiling-rounded to exactly
one centicent, so cc is the natural integer grid. ``taker_fee_cc`` is the
same formula as ``bot.backtest.fees.trade_fee_centicents`` extended to
sub-cent prices (a unit test pins the two together on whole cents).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal

CONTRACT_CC = 10_000          # $1.00 in centicents
TAKER_COEF = Decimal("0.07")
MAKER_COEF = Decimal("0.0175")
CENTICENT = Decimal("0.0001")


def taker_fee_cc(
    price_cc: int,
    count: int = 1,
    fee_multiplier: float = 1.0,
    stress: float = 1.0,
) -> int:
    """Kalshi taker fee in centicents: ceil_cc(0.07 × mult × C × P(1−P)).

    Identical to ``bot.backtest.fees.trade_fee_centicents`` but accepts a
    centicent price so deci-cent ticks are not rounded away.
    """
    if count <= 0:
        return 0
    if not (0 < price_cc < CONTRACT_CC):
        raise ValueError(f"price_cc out of range: {price_cc}")
    p = Decimal(price_cc) / CONTRACT_CC
    raw = (
        TAKER_COEF
        * Decimal(str(fee_multiplier))
        * Decimal(str(stress))
        * Decimal(count)
        * p
        * (1 - p)
    )
    return int(raw.quantize(CENTICENT, rounding=ROUND_CEILING) * 10_000)


def _fee_price(price_cc: int) -> int:
    """Clamp a quote to the priceable band before charging a fee.

    A leg quoted at 0 or $1.00 is untradeable on that side; the signal
    functions zero such sets out separately, but the fee formula still needs
    a legal price (and P(1−P)→0 there anyway, so the clamp is immaterial)."""
    return min(CONTRACT_CC - 1, max(1, price_cc))


def dollars_to_cc(x: float | None) -> int | None:
    """Store dollars -> centicents (exact on the 0.0001 grid)."""
    if x is None:
        return None
    return int(round(float(x) * 10_000))


@dataclass(frozen=True, slots=True)
class LegQuote:
    """Top-of-book for one leg at one instant, in centicents."""
    ticker: str
    yes_bid_cc: int
    yes_ask_cc: int
    volume: float = 0.0          # contracts traded in this period
    stale_s: int = 0             # age of the quote (forward-fill distance)

    @property
    def no_ask_cc(self) -> int:
        """What it costs to BUY NO on this leg."""
        return CONTRACT_CC - self.yes_bid_cc


@dataclass(frozen=True, slots=True)
class SetSignal:
    """Result of evaluating one instant of one event."""
    kind: str                    # "no_set" | "yes_set"
    n_legs: int
    quote_sum_cc: int            # Σ yes_bid (no_set) or Σ yes_ask (yes_set)
    gross_edge_cc: int           # per set, before fees
    fee_cc: int                  # per set, all legs, taker
    net_edge_cc: int             # gross − fee (buffer applied by caller)
    cost_cc: int                 # cash outlay per set
    max_stale_s: int = 0
    fillable_sets: int = 0       # volume-capped, all-or-nothing across legs
    fillable_notional_cc: int = 0

    @property
    def fires(self) -> bool:
        return self.net_edge_cc > 0

    @property
    def net_edge_cents(self) -> float:
        return self.net_edge_cc / 100.0

    @property
    def fillable_dollars(self) -> float:
        return self.fillable_notional_cc / 1_000_000.0


def _fillable_sets(legs: list[LegQuote], depth_frac: float, mult: float = 1.0) -> int:
    """All-or-nothing depth cap: a set needs one contract on EVERY leg, so
    the binding constraint is the thinnest leg (SPEC §3: ≤25% of printed
    volume)."""
    if not legs:
        return 0
    return int(min(int(depth_frac * mult * (l.volume or 0.0)) for l in legs))


def no_set_signal(
    legs: list[LegQuote],
    fee_multiplier: float = 1.0,
    buffer_cc: int = 100,
    depth_frac: float = 0.25,
    depth_multiplier: float = 1.0,
    fee_stress: float = 1.0,
) -> SetSignal:
    """Buy NO on every leg. Riskless whenever ≤1 leg can resolve YES.

    Fires when Σ yes_bid − 100¢ > fees + buffer. ``buffer_cc`` defaults to
    the 1¢ buffer mandated by SYNTHESIS R3.
    """
    n = len(legs)
    quote_sum = sum(l.yes_bid_cc for l in legs)
    cost = n * CONTRACT_CC - quote_sum
    gross = quote_sum - CONTRACT_CC          # payoff (n−1)·100 minus cost
    fee = sum(
        taker_fee_cc(_fee_price(l.no_ask_cc), 1, fee_multiplier, fee_stress)
        for l in legs
    )
    net = gross - fee - buffer_cc
    # A leg with no YES bid cannot be bought as NO (its NO ask is $1.00).
    if any(l.yes_bid_cc <= 0 for l in legs):
        net = min(net, 0) - 1
    sets = _fillable_sets(legs, depth_frac, depth_multiplier)
    return SetSignal(
        kind="no_set",
        n_legs=n,
        quote_sum_cc=quote_sum,
        gross_edge_cc=gross,
        fee_cc=fee,
        net_edge_cc=net,
        cost_cc=cost,
        max_stale_s=max((l.stale_s for l in legs), default=0),
        fillable_sets=sets,
        fillable_notional_cc=sets * cost,
    )


def yes_set_signal(
    legs: list[LegQuote],
    fee_multiplier: float = 1.0,
    buffer_cc: int = 100,
    depth_frac: float = 0.25,
    depth_multiplier: float = 1.0,
    fee_stress: float = 1.0,
) -> SetSignal:
    """Buy YES on every leg. Riskless only if the event is EXHAUSTIVE
    (exactly one leg must resolve YES) — see ``is_exhaustive_by_structure``.
    Fires when 100¢ − Σ yes_ask > fees + buffer."""
    n = len(legs)
    quote_sum = sum(l.yes_ask_cc for l in legs)
    cost = quote_sum
    gross = CONTRACT_CC - quote_sum
    fee = sum(
        taker_fee_cc(_fee_price(l.yes_ask_cc), 1, fee_multiplier, fee_stress)
        for l in legs
    )
    net = gross - fee - buffer_cc
    if any(l.yes_ask_cc <= 0 or l.yes_ask_cc >= CONTRACT_CC for l in legs):
        net = min(net, 0) - 1
    sets = _fillable_sets(legs, depth_frac, depth_multiplier)
    return SetSignal(
        kind="yes_set",
        n_legs=n,
        quote_sum_cc=quote_sum,
        gross_edge_cc=gross,
        fee_cc=fee,
        net_edge_cc=net,
        cost_cc=cost,
        max_stale_s=max((l.stale_s for l in legs), default=0),
        fillable_sets=sets,
        fillable_notional_cc=sets * cost,
    )


def literal_ask_sum_signal(legs: list[LegQuote], fee_multiplier: float = 1.0,
                           buffer_cc: int = 100) -> SetSignal:
    """SYNTHESIS's rule as literally written — Σ best-ask YES > 100¢ + fees
    + 1¢ — kept as a DIAGNOSTIC only. It is not executable (you cannot buy
    NO at 100 − yes_ask); see the module docstring."""
    n = len(legs)
    quote_sum = sum(l.yes_ask_cc for l in legs)
    gross = quote_sum - CONTRACT_CC
    fee = sum(
        taker_fee_cc(_fee_price(CONTRACT_CC - l.yes_ask_cc), 1, fee_multiplier)
        for l in legs
    )
    return SetSignal(
        kind="literal_ask_sum",
        n_legs=n,
        quote_sum_cc=quote_sum,
        gross_edge_cc=gross,
        fee_cc=fee,
        net_edge_cc=gross - fee - buffer_cc,
        cost_cc=n * CONTRACT_CC - quote_sum,
        max_stale_s=max((l.stale_s for l in legs), default=0),
    )


# ---------------------------------------------------------------------------
# Single-bracket fade variant (R3's second clause)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class BracketFade:
    ticker: str
    yes_bid_cc: int
    contribution_cc: int         # this leg's share of the overround
    share: float                 # contribution / total overround


def dominant_cheap_bracket(
    legs: list[LegQuote], max_price_cc: int = 1000, min_share: float = 0.5
) -> BracketFade | None:
    """R3's higher-capacity variant: when a single ≤10¢ bracket supplies ≥
    half of the overround, fade just that bracket (buy its NO) instead of
    lifting the whole set.

    The overround is measured on the executable (bid) side; a leg's
    contribution is how far its own bid sits above the coherent level
    implied by the other legs, i.e. bid_i − max(0, 100 − Σ_{j≠i} bid_j).
    """
    total = sum(l.yes_bid_cc for l in legs) - CONTRACT_CC
    if total <= 0:
        return None
    best: BracketFade | None = None
    for l in legs:
        others = sum(x.yes_bid_cc for x in legs if x.ticker != l.ticker)
        coherent = max(0, CONTRACT_CC - others)
        contrib = l.yes_bid_cc - coherent
        if contrib <= 0 or l.yes_bid_cc > max_price_cc:
            continue
        share = contrib / total
        if share >= min_share and (best is None or contrib > best.contribution_cc):
            best = BracketFade(l.ticker, l.yes_bid_cc, contrib, share)
    return best


# ---------------------------------------------------------------------------
# Structural exhaustiveness (needed for the YES-set variant)
# ---------------------------------------------------------------------------

def is_exhaustive_by_structure(strikes: list[tuple[str, float | None, float | None]]) -> bool:
    """True iff the legs' strike structure provably covers the whole line.

    Input is [(strike_type, floor_strike, cap_strike)] for every leg. A
    ``between`` bracket ladder is exhaustive iff the brackets are
    contiguous AND the two ends are open (lowest bracket has no floor or is
    paired with a ``less`` leg, highest has no cap or a ``greater`` leg).
    Candidate-field events (``structured``/``custom``) can never be proven
    exhaustive from metadata — a write-in/"none of the above" outcome is
    always possible — so they return False. Ex-post, 80 of 2,896 settled
    exclusive events resolved zero YES, which is exactly this hazard.
    """
    if not strikes:
        return False
    kinds = {s[0] for s in strikes}
    if not kinds <= {"between", "less", "less_or_equal", "greater", "greater_or_equal"}:
        return False
    lows: list[tuple[float, float]] = []
    open_low = open_high = False
    for kind, floor_s, cap_s in strikes:
        if kind in ("less", "less_or_equal"):
            if cap_s is None:
                return False
            lows.append((float("-inf"), cap_s))
            open_low = True
        elif kind in ("greater", "greater_or_equal"):
            if floor_s is None:
                return False
            lows.append((floor_s, float("inf")))
            open_high = True
        else:
            if floor_s is None or cap_s is None:
                return False
            lows.append((floor_s, cap_s))
    lows.sort()
    if not (open_low and open_high):
        return False
    for (a_lo, a_hi), (b_lo, b_hi) in zip(lows, lows[1:]):
        # contiguous within one tick of the strike grid; brackets are
        # inclusive ranges so b_lo must not leave a gap above a_hi.
        if b_lo > a_hi + 1e-9 and not _adjacent(a_hi, b_lo):
            return False
    return True


def _adjacent(a_hi: float, b_lo: float) -> bool:
    """Kalshi bracket ladders are quoted on a fixed strike grid; adjacent
    brackets touch (cap == next floor) or sit one grid step apart."""
    gap = b_lo - a_hi
    return -1e-9 <= gap <= 1.0 + 1e-9

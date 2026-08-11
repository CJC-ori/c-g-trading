"""Honest fill simulation (SPEC.md §3) as pure functions.

Everything here is normalized to YES price space:

- "yes-buy" = any intent that INCREASES yes-equivalent exposure
  (buy YES, or sell NO). Its yes-space limit for side "no" is
  100 - limit (selling NO at p receives p, i.e. acquiring YES exposure
  at yes price 100 - p).
- "yes-sell" = any intent that DECREASES yes-equivalent exposure
  (sell YES, or buy NO). Buying NO at p == selling YES exposure at 100 - p.

Rules implemented (all volume caps default to 25%, scaled by a depth
multiplier for capacity-curve runs, and never exceed the observed volume):

- Taker: an intent that crosses the last known ask/bid fills in the
  *following* candle period only, capped at cap_frac of that window's
  traded volume. Fill price = the quote it crossed (never better than
  the limit by definition of crossing).
- Maker (trades available): a resting order fills only when a subsequent
  trade prints AT or THROUGH its limit, capped at cap_frac of each
  qualifying print. Fill price = the order's own limit.
- Maker (candles only): conservative OHLC rule - a resting yes-buy at L
  fills only if a candle's low is STRICTLY below L (a touch is not enough),
  capped at cap_frac of the candle's volume. Symmetric for sells vs high.
- Settlement: yes pays 100c per long YES contract, no pays 100c per long
  NO contract, void refunds the position's cost basis.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from bot.backtest.types import Candle, OrderIntent, Outcome, Side, Trade

DEFAULT_VOLUME_CAP_FRAC = 0.25


# ---------------------------------------------------------------------------
# Normalization: side/action -> yes-space direction + limit
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class NormalizedIntent:
    """An OrderIntent expressed in YES price space."""
    ticker: str
    yes_buy: bool     # True = increases yes-equivalent exposure
    yes_limit: int    # limit price in YES cents
    size: int

    @property
    def qty_sign(self) -> int:
        return 1 if self.yes_buy else -1


def normalize(intent: OrderIntent) -> NormalizedIntent:
    if not (1 <= intent.limit_price_cents <= 99):
        raise ValueError(f"limit price out of range: {intent.limit_price_cents}")
    if intent.size < 0:
        raise ValueError(f"negative size: {intent.size}")
    yes_buy = (intent.side == "yes") == (intent.action == "buy")
    yes_limit = (
        intent.limit_price_cents
        if intent.side == "yes"
        else 100 - intent.limit_price_cents
    )
    return NormalizedIntent(intent.ticker, yes_buy, yes_limit, intent.size)


def side_price(side: Side, yes_price: int) -> int:
    """Convert a yes-space execution price to the side's own price space."""
    return yes_price if side == "yes" else 100 - yes_price


# ---------------------------------------------------------------------------
# Volume caps
# ---------------------------------------------------------------------------

def volume_cap(
    observed_volume: int,
    cap_frac: float = DEFAULT_VOLUME_CAP_FRAC,
    depth_multiplier: float = 1.0,
) -> int:
    """Max contracts we allow ourselves against `observed_volume` prints.

    floor(cap_frac * depth_multiplier * volume), never above the volume
    itself (even a 10x capacity run can't trade more than the tape printed).
    """
    if observed_volume <= 0:
        return 0
    return min(observed_volume, math.floor(cap_frac * depth_multiplier * observed_volume))


# ---------------------------------------------------------------------------
# Taker fills
# ---------------------------------------------------------------------------

def crosses(norm: NormalizedIntent, best_bid: int | None, best_ask: int | None) -> bool:
    """Does this intent cross the last known book estimate (=> taker)?"""
    if norm.yes_buy:
        return best_ask is not None and norm.yes_limit >= best_ask
    return best_bid is not None and norm.yes_limit <= best_bid


def taker_price(norm: NormalizedIntent, best_bid: int | None, best_ask: int | None) -> int:
    """Yes-space execution price for a crossing intent: the quote it hit."""
    if norm.yes_buy:
        assert best_ask is not None and norm.yes_limit >= best_ask
        return best_ask
    assert best_bid is not None and norm.yes_limit <= best_bid
    return best_bid


def taker_fill_count(
    remaining: int,
    window_volume: int,
    cap_frac: float = DEFAULT_VOLUME_CAP_FRAC,
    depth_multiplier: float = 1.0,
) -> int:
    """Contracts filled by a taker order given the following window's volume."""
    return min(remaining, volume_cap(window_volume, cap_frac, depth_multiplier))


# ---------------------------------------------------------------------------
# Maker fills
# ---------------------------------------------------------------------------

def trade_qualifies(norm: NormalizedIntent, trade: Trade) -> bool:
    """Does a trade print AT or THROUGH the resting limit?"""
    if norm.yes_buy:
        return trade.yes_price <= norm.yes_limit
    return trade.yes_price >= norm.yes_limit


def maker_fill_from_trade(
    norm: NormalizedIntent,
    trade: Trade,
    remaining: int,
    cap_frac: float = DEFAULT_VOLUME_CAP_FRAC,
    depth_multiplier: float = 1.0,
) -> int:
    """Contracts a resting order takes from one subsequent trade print."""
    if remaining <= 0 or not trade_qualifies(norm, trade):
        return 0
    return min(remaining, volume_cap(trade.count, cap_frac, depth_multiplier))


def candle_qualifies(norm: NormalizedIntent, candle: Candle) -> bool:
    """Conservative OHLC maker rule: price must move STRICTLY through limit."""
    if candle.volume <= 0:
        return False
    if norm.yes_buy:
        return candle.low < norm.yes_limit
    return candle.high > norm.yes_limit


def maker_fill_from_candle(
    norm: NormalizedIntent,
    candle: Candle,
    remaining: int,
    cap_frac: float = DEFAULT_VOLUME_CAP_FRAC,
    depth_multiplier: float = 1.0,
) -> int:
    """Contracts a resting order fills against one candle (no trade data)."""
    if remaining <= 0 or not candle_qualifies(norm, candle):
        return 0
    return min(remaining, volume_cap(candle.volume, cap_frac, depth_multiplier))


# ---------------------------------------------------------------------------
# Position accounting (signed yes-equivalent qty + premium cost basis)
# ---------------------------------------------------------------------------

def apply_fill_to_position(
    qty: int, cost_basis_cents: int, delta_qty: int, yes_price: int
) -> tuple[int, int, int, int]:
    """Apply a fill of `delta_qty` yes-equivalent contracts at `yes_price`.

    Returns (new_qty, new_cost_basis_cents, cash_delta_cents,
    realized_pnl_delta_cents).

    Premium convention: opening YES exposure costs yes_price per contract;
    opening NO exposure costs (100 - yes_price). Reducing a position returns
    the same premium at the exit price (buying NO against a YES long is
    modeled as the equivalent pair-redemption: pay 100-p, redeem the matched
    pair for 100 => net +p, identical to selling YES at p).
    Cost basis released on partial exits is floor-prorated, keeping
    everything in integer cents.
    """
    cash = 0
    realized = 0
    # 1) Reduce the opposing position first.
    if qty != 0 and delta_qty != 0 and (delta_qty > 0) != (qty > 0):
        n = min(abs(delta_qty), abs(qty))
        released = cost_basis_cents * n // abs(qty)
        proceeds = n * yes_price if qty > 0 else n * (100 - yes_price)
        cash += proceeds
        realized += proceeds - released
        cost_basis_cents -= released
        qty += n if delta_qty > 0 else -n
        delta_qty -= n if delta_qty > 0 else -n
    # 2) Any remainder opens/extends in the fill's direction.
    if delta_qty != 0:
        premium = delta_qty * yes_price if delta_qty > 0 else -delta_qty * (100 - yes_price)
        cash -= premium
        cost_basis_cents += premium
        qty += delta_qty
    return qty, cost_basis_cents, cash, realized


# ---------------------------------------------------------------------------
# Settlement
# ---------------------------------------------------------------------------

def settlement_payout_cents(
    qty: int,
    cost_basis_cents: int,
    result: Outcome,
    settlement_value_cents: int | None = None,
) -> int:
    """Cash paid out at settlement for an open position.

    yes    -> 100c per long YES contract (long NO gets nothing).
    no     -> 100c per long NO contract (long YES gets nothing).
    void   -> refund at cost (the premium actually paid).
    scalar -> settlement_value_cents per long YES contract,
              (100 - settlement_value_cents) per long NO contract.
              This is Kalshi's fractional-payout outcome (ties/pushes,
              settlement_value_dollars in the API); "yes" is the v=100
              special case and "no" the v=0 one.
    """
    if result == "void":
        return cost_basis_cents
    if result == "yes":
        return 100 * qty if qty > 0 else 0
    if result == "no":
        return -100 * qty if qty < 0 else 0
    if result == "scalar":
        if settlement_value_cents is None:
            raise ValueError("scalar settlement requires settlement_value_cents")
        v = settlement_value_cents
        if not (0 <= v <= 100):
            raise ValueError(f"settlement value out of range: {v}")
        return qty * v if qty > 0 else -qty * (100 - v)
    raise ValueError(f"unknown settlement result: {result}")

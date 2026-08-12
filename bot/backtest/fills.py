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
- Maker queue position (2026-08-12, MakerQueueConfig): a resting order does
  NOT participate in every qualifying print. It joins BEHIND an estimate of
  the displayed depth already at its level, and only qualifying volume in
  excess of that queue reaches it. The store carries no book sizes
  (candlesticks have bid/ask price OHLC only), so the queue-ahead estimate
  is `depth_windows x recent per-window traded volume` - a configurable
  pessimism factor whose default is calibrated so the simulated maker
  CONTRACT fill rate on the frozen FLB R2 train universe lands in SPEC
  §3's 40-50% sanity band (calibrated on FILL RATE against the
  FutureSearch live-fill anchor, never on strategy P&L; see
  reports/flb/QUEUE_IMPACT.md for the sweep and metric discussion).
  Where even a volume proxy is unavailable (no candles yet), each
  qualifying event instead reaches the order with a fixed probability
  (`unknown_depth_fill_prob`, default 0.45 = the middle of the sanity
  band), decided by a deterministic per-(order, event) hash.
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
# Maker queue model (SPEC §3, amended 2026-08-12)
# ---------------------------------------------------------------------------

#: Pessimism factor: assumed displayed depth ahead of a newly joined resting
#: order, as a multiple of the recent per-window traded volume. The default
#: is CALIBRATED ON FILL RATE (never on P&L): swept on the frozen FLB R2
#: train universe (reports/flb/universe.json, stale-ladder lifecycle - the
#: exact configuration whose 69% simulated fill rate tripped SPEC §3's
#: >60% flag) until the maker CONTRACT fill rate landed in SPEC §3's
#: 40-50% sanity band, whose anchor is FutureSearch's measured live maker
#: fill rate (positions table ~50.1%, headline 43%). At 1.0 the calibrated
#: rate is 48.9% (any value in [0.75, 3] lands 48-50%; 1.0 is chosen as
#: the natural unit - you join behind one window's worth of recent traded
#: volume, the same per-window estimator the risk layer's taker depth
#: clamp uses - and because it distorts the R5 variants, whose fill rates
#: were already anchor-consistent, the least). Sweep table and the
#: order-vs-contract metric discussion: reports/flb/QUEUE_IMPACT.md.
DEFAULT_QUEUE_DEPTH_WINDOWS = 1.0

#: Fallback when no volume proxy exists at placement: probability that a
#: qualifying print/candle reaches the order at all. 0.45 = middle of the
#: 40-50% sanity band - the same external anchor, applied directly.
DEFAULT_UNKNOWN_DEPTH_FILL_PROB = 0.45


@dataclass(frozen=True, slots=True)
class MakerQueueConfig:
    """Queue-position model for resting (maker) orders.

    enabled=False restores the legacy every-qualifying-print fill model
    (the one all three strategy reports flagged as too generous: 69-100%
    simulated maker fill rates vs SPEC §3's 40-50% band).
    """
    enabled: bool = True
    depth_windows: float = DEFAULT_QUEUE_DEPTH_WINDOWS
    min_depth_contracts: int = 0
    unknown_depth_fill_prob: float = DEFAULT_UNKNOWN_DEPTH_FILL_PROB
    seed: int = 7


def initial_queue_ahead(
    window_volume: int | None, config: MakerQueueConfig
) -> int | None:
    """Estimated contracts ahead of an order joining a level right now.

    window_volume is the recent per-window traded volume (the engine uses
    the mean of the last 3 candles, the same estimator the risk layer's
    taker depth clamp uses). None = no proxy available -> returns None,
    which switches the order to the probabilistic-discount path.
    """
    if window_volume is None:
        return None
    return max(
        config.min_depth_contracts,
        math.ceil(config.depth_windows * window_volume),
    )


def queued_maker_fill(
    remaining: int,
    event_volume: int,
    queue_ahead: int,
    cap_frac: float = DEFAULT_VOLUME_CAP_FRAC,
    depth_multiplier: float = 1.0,
) -> tuple[int, int]:
    """One qualifying event (trade print or strictly-through candle) hits a
    level where we rest behind `queue_ahead` contracts.

    The event's volume first consumes the queue ahead; only the excess can
    reach us, still capped at cap_frac of that excess (we can't be most of
    even the marginal tape). Returns (fill_count, new_queue_ahead).
    """
    if event_volume <= 0:
        return 0, queue_ahead
    consumed = min(queue_ahead, event_volume)
    excess = event_volume - consumed
    fill = min(remaining, volume_cap(excess, cap_frac, depth_multiplier))
    return fill, queue_ahead - consumed


def unknown_depth_event_reaches(
    config: MakerQueueConfig, order_id: int, event_ts: int
) -> bool:
    """Deterministic pseudo-random gate for the no-volume-proxy fallback.

    Pure integer hash of (seed, order_id, event_ts) -> uniform [0,1);
    reproducible across runs and interpreters (no PYTHONHASHSEED
    dependence)."""
    x = (
        config.seed * 1_000_003
        ^ order_id * 2_654_435_761
        ^ event_ts * 97_531
    ) & 0xFFFFFFFF
    x ^= (x << 13) & 0xFFFFFFFF
    x ^= x >> 17
    x ^= (x << 5) & 0xFFFFFFFF
    return x / 2**32 < config.unknown_depth_fill_prob


# ---------------------------------------------------------------------------
# Opportunity lifetime (SYNTHESIS §2.2; research/oss-arb.md §7.3)
# ---------------------------------------------------------------------------

def opportunity_lifetime_s(
    norm: NormalizedIntent,
    candles: list[Candle],
    trades: list[Trade],
    placed_ts: int,
    end_ts: int,
) -> int:
    """How long the order's trigger condition persisted in the tape, seconds.

    Diagnostic only (uses future data; never visible to strategies). The
    condition for a yes-buy at limit L is "the market still offered a price
    <= L": it dies at the first data point after placement showing price
    strictly beyond the limit (trade print > L, or candle low > L; symmetric
    for sells vs high). Returns end_ts - placed_ts if it never dies. A
    strategy whose median lifetime is < ~5s is not ours to trade — the
    opportunity is gone before any non-colocated bot can act.
    """
    death_ts: int | None = None
    if trades:
        for tr in trades:
            if tr.ts <= placed_ts:
                continue
            gone = tr.yes_price > norm.yes_limit if norm.yes_buy else (
                tr.yes_price < norm.yes_limit
            )
            if gone:
                death_ts = tr.ts
                break
    else:
        for c in candles:
            if c.start_ts < placed_ts:
                continue
            gone = c.low > norm.yes_limit if norm.yes_buy else (
                c.high < norm.yes_limit
            )
            if gone:
                death_ts = c.end_ts
                break
    if death_ts is None:
        death_ts = end_ts
    return max(0, death_ts - placed_ts)


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

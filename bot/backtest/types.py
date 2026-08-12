"""Core frozen datatypes for the backtest harness (SPEC.md §2-3).

Conventions used across the harness:

- All timestamps are unix epoch seconds (int).
- All prices are integer cents. Canonical price space is the YES price
  (1..99). NO prices are the complement: no_price = 100 - yes_price.
- All money amounts (cash, fees, P&L) are integer cents.
- `OrderIntent.limit_price_cents` and `Fill.price_cents` are in the
  *side's own* price space (i.e. the price of the NO contract if
  side == "no"). Internal fill math normalizes to YES space; see fills.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping, Protocol, runtime_checkable

Side = Literal["yes", "no"]
Action = Literal["buy", "sell"]
# "rest": rest until canceled (canceled automatically at market close).
# "ioc":  this tick only - fills in the immediate taker window, remainder dies.
Tif = Literal["rest", "ioc"]
Outcome = Literal["yes", "no", "void", "scalar"]

FAR_FUTURE = 2**62


# ---------------------------------------------------------------------------
# Market data primitives (what a HistoryProvider serves)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Candle:
    """One OHLCV candle in YES-price cents. Fully observable at end_ts."""
    ticker: str
    start_ts: int
    period_s: int
    open: int
    high: int
    low: int
    close: int
    volume: int  # contracts traded during the period
    yes_bid_close: int | None = None
    yes_ask_close: int | None = None

    @property
    def end_ts(self) -> int:
        return self.start_ts + self.period_s


@dataclass(frozen=True, slots=True)
class Trade:
    """A single public trade print, price in YES cents."""
    ticker: str
    ts: int
    yes_price: int
    count: int
    taker_side: Side | None = None


@dataclass(frozen=True, slots=True)
class PriceRange:
    """One tick-size band of a market's price structure, in CENTICENTS
    ($0.0001 units; 1 cent = 100 centicents, a 0.1c tick = 10)."""
    start_cc: int
    end_cc: int
    step_cc: int


@dataclass(frozen=True, slots=True)
class PriceStructure:
    """Per-market tick structure (Kalshi ``price_level_structure`` +
    ``price_ranges``; research/kalshi-api.md §2).

    Most markets are ``linear_cent`` (1c ticks everywhere); election-style
    markets are ``tapered_deci_cent`` (0.1c ticks below 10c and above 90c).
    Every observed structure admits all whole-cent prices, so an
    integer-cent limit is always on-grid — the quantizer exists for
    sub-cent (centicent) prices in the tapered tails.
    """
    kind: str = "linear_cent"
    ranges: tuple[PriceRange, ...] = (PriceRange(0, 10_000, 100),)

    @classmethod
    def from_raw(cls, kind: str | None, price_ranges) -> "PriceStructure":
        """Build from the market record's raw fields (dollar strings)."""
        ranges = tuple(
            PriceRange(
                start_cc=round(float(r["start"]) * 10_000),
                end_cc=round(float(r["end"]) * 10_000),
                step_cc=round(float(r["step"]) * 10_000),
            )
            for r in (price_ranges or [])
        )
        if not ranges:
            return LINEAR_CENT
        return cls(kind or "linear_cent", ranges)

    def is_valid_tick(self, price_cc: int) -> bool:
        for r in self.ranges:
            if r.start_cc <= price_cc <= r.end_cc and (
                (price_cc - r.start_cc) % r.step_cc == 0
            ):
                return True
        return False

    def quantize_cc(self, price_cc: int, round_down: bool) -> int:
        """Snap a centicent price to the nearest valid tick.

        round_down=True snaps toward 0 (conservative for buy limits — never
        pay more than intended); False snaps up (conservative for sells).
        Out-of-range prices clamp to the structure's bounds.
        """
        lo = min(r.start_cc for r in self.ranges)
        hi = max(r.end_cc for r in self.ranges)
        price_cc = max(lo, min(hi, price_cc))
        if self.is_valid_tick(price_cc):
            return price_cc
        for r in self.ranges:
            if r.start_cc <= price_cc <= r.end_cc:
                offset = price_cc - r.start_cc
                snapped = r.start_cc + (
                    (offset // r.step_cc) * r.step_cc
                    if round_down
                    else -((-offset) // r.step_cc) * r.step_cc
                )
                return max(lo, min(hi, snapped))
        # Between ranges (shouldn't happen with contiguous ranges): clamp.
        return lo if round_down else hi


LINEAR_CENT = PriceStructure()


@dataclass(frozen=True, slots=True)
class MarketInfo:
    """Static market data known at listing time (visible at any t >= open_ts).

    series_ticker and price_structure are optional (additive, 2026-08-11):
    when absent the harness derives the series from the ticker prefix and
    assumes linear 1c ticks.
    """
    ticker: str
    event_ticker: str = ""
    category: str = ""
    title: str = ""
    open_ts: int = 0
    close_ts: int = FAR_FUTURE
    rules_summary: str = ""
    series_ticker: str = ""
    price_structure: PriceStructure | None = None


@dataclass(frozen=True, slots=True)
class SettlementResult:
    """Terminal outcome of a market.

    result "scalar" is a real Kalshi outcome (~0.3% of settled markets,
    mostly sports pushes/ties): the contract pays a *fractional* value per
    YES contract, carried here as settlement_value_cents (0..100, integer
    cents; NO contracts pay the 100-complement). For "yes"/"no" the field
    is implied (100/0) and may be left None.
    """
    ticker: str
    result: Outcome
    settled_ts: int
    settlement_value_cents: int | None = None


# ---------------------------------------------------------------------------
# Strategy-facing types
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class OrderIntent:
    """A priced limit-order intent (no market orders; crossing = taker).

    limit_price_cents is in the side's own price space.
    p_hat is the strategy's probability that the market resolves YES
    (required for Kelly sizing; intents without p_hat get size 0 from the
    risk layer unless they reduce an existing position).

    replace (additive, 2026-08-12): cancel-and-replace. When True, the
    engine first cancels ALL of this strategy's still-RESTING orders in the
    same market on the same yes-space direction (buy-YES==sell-NO side),
    then processes this intent as a fresh order with a fresh maker-queue
    position. A replace intent with size=0 is a pure cancel. Orders
    currently in their taker window are in flight and are not touched.
    """
    ticker: str
    side: Side
    action: Action
    limit_price_cents: int
    size: int
    tif: Tif = "rest"
    p_hat: float | None = None
    rationale: str = ""
    replace: bool = False


@dataclass(frozen=True, slots=True)
class Fill:
    order_id: int
    ticker: str
    side: Side
    action: Action
    price_cents: int  # side-space execution price
    count: int
    ts: int
    is_taker: bool
    fee_cents: int


@dataclass(frozen=True, slots=True)
class Position:
    """Open position in one market.

    qty is signed YES-equivalent exposure: qty > 0 means long qty YES
    contracts; qty < 0 means long |qty| NO contracts.
    cost_basis_cents is the total premium paid for the open position
    (this is exactly what a voided market refunds).
    """
    ticker: str
    qty: int
    cost_basis_cents: int
    realized_pnl_cents: int = 0

    @property
    def avg_premium_cents(self) -> float:
        """Average premium paid per open contract (side space)."""
        return self.cost_basis_cents / abs(self.qty) if self.qty else 0.0


@dataclass(frozen=True, slots=True)
class Portfolio:
    """Snapshot handed to strategies at each decision point."""
    cash_cents: int
    positions: tuple[Position, ...] = ()
    committed_cents: int = 0  # premium reserved by resting/unfilled orders
    fees_paid_cents: int = 0
    inference_cost_cents: int = 0

    def position(self, ticker: str) -> Position | None:
        for p in self.positions:
            if p.ticker == ticker:
                return p
        return None

    @property
    def deployed_cost_cents(self) -> int:
        return sum(p.cost_basis_cents for p in self.positions)


# ---------------------------------------------------------------------------
# MarketView: the only window a strategy has onto history
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MarketView:
    """Point-in-time view of one market, sliced strictly before t.

    The engine constructs this with pre-sliced immutable data; a strategy
    holding a MarketView cannot reach anything at or after t:

    - candles(period) returns only candles with end_ts <= t (a candle whose
      period *ends exactly at* t is fully in the past and thus visible).
    - last_trade is the latest trade with ts < t.
    - best_bid/best_ask are the harness's estimate of the book at t built
      only from data < t (bid/ask candle closes when available, otherwise
      last traded price +/- 1c as a conservative spread guess).
    """
    market: MarketInfo
    t: int
    candles_by_period: Mapping[int, tuple[Candle, ...]] = field(default_factory=dict)
    trades_before_t: tuple[Trade, ...] = ()

    def candles(self, period_s: int) -> tuple[Candle, ...]:
        return self.candles_by_period.get(period_s, ())

    @property
    def last_trade(self) -> Trade | None:
        return self.trades_before_t[-1] if self.trades_before_t else None

    @property
    def _latest_candle(self) -> Candle | None:
        latest: Candle | None = None
        for series in self.candles_by_period.values():
            if series and (latest is None or series[-1].end_ts > latest.end_ts):
                latest = series[-1]
        return latest

    @property
    def last_price(self) -> int | None:
        """Most recent traded/close YES price known strictly before t."""
        cand = self._latest_candle
        tr = self.last_trade
        if tr is not None and (cand is None or tr.ts >= cand.end_ts):
            return tr.yes_price
        return cand.close if cand is not None else None

    @property
    def best_bid(self) -> int | None:
        cand = self._latest_candle
        if cand is not None and cand.yes_bid_close is not None:
            tr = self.last_trade
            if tr is None or tr.ts < cand.end_ts:
                return cand.yes_bid_close
        p = self.last_price
        return max(1, p - 1) if p is not None else None

    @property
    def best_ask(self) -> int | None:
        cand = self._latest_candle
        if cand is not None and cand.yes_ask_close is not None:
            tr = self.last_trade
            if tr is None or tr.ts < cand.end_ts:
                return cand.yes_ask_close
        p = self.last_price
        return min(99, p + 1) if p is not None else None

    @property
    def mid_cents(self) -> float | None:
        b, a = self.best_bid, self.best_ask
        if b is None or a is None:
            return None
        return (b + a) / 2


# ---------------------------------------------------------------------------
# Strategy protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class Strategy(Protocol):
    """A strategy sees exactly one MarketView + its portfolio per decision.

    Cadence is strategy-declared but the harness generates the clock
    (SPEC §2). Optional attributes read by the engine (defaults in
    parentheses):

    - decision_interval_s: int | None (3600) - fixed cadence per market.
    - price_move_trigger_cents: int | None (None) - extra decision point
      whenever |close - close at last decision| >= this many cents.
    - candle_period_s: int (engine config default) - candle granularity
      requested from the data layer.
    - last_inference_cost_cents: int (0) - read by the engine after every
      on_decision_point call and charged to P&L as inference cost.
    """

    def on_decision_point(
        self, view: MarketView, portfolio: Portfolio
    ) -> list[OrderIntent]: ...

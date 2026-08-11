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
Outcome = Literal["yes", "no", "void"]

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
class MarketInfo:
    """Static market data known at listing time (visible at any t >= open_ts)."""
    ticker: str
    event_ticker: str = ""
    category: str = ""
    title: str = ""
    open_ts: int = 0
    close_ts: int = FAR_FUTURE
    rules_summary: str = ""


@dataclass(frozen=True, slots=True)
class SettlementResult:
    ticker: str
    result: Outcome
    settled_ts: int


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
    """
    ticker: str
    side: Side
    action: Action
    limit_price_cents: int
    size: int
    tif: Tif = "rest"
    p_hat: float | None = None
    rationale: str = ""


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

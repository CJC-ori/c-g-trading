"""Point-in-time replay engine (SPEC.md §1-5).

Flow per run:

1. Load markets + full history from a HistoryProvider (the only component
   that touches data; strategies never do).
2. Build the decision-point clock per market from the strategy's declared
   cadence (fixed interval and/or price-move trigger), aligned to candle
   boundaries so every decision uses only fully-observable data.
3. At each decision point t: construct a MarketView sliced strictly < t,
   snapshot the portfolio, collect the strategy's OrderIntents, clamp them
   through the risk layer, and register orders (crossing intents become
   takers filling in the following candle window; the rest rest as makers).
4. Between events, replay trades/candles in time order against live orders
   (fills.py rules), track cash/positions, and mark to market.
5. At market close (or early settlement) cancel that market's resting
   orders; at settlement, pay out positions (voids refund at cost).
6. Charge taker/maker fees per fill and per-decision inference cost
   (strategies report via `last_inference_cost_cents`, default 0).
   Inference cost is tracked as its own P&L line item (equity curve is
   gross of inference; metrics report both gross and net).
7. Emit a full JSONL-able event log for auditability.
"""
from __future__ import annotations

import json
from bisect import bisect_right
from dataclasses import dataclass, field, replace
from typing import Any, Iterable

from bot.backtest import fees as fees_mod
from bot.backtest import fills
from bot.backtest.dataport import HistoryProvider
from bot.backtest.risk import ClampResult, ExposureState, RiskConfig, clamp_intent_size
from bot.backtest.types import (
    Candle,
    Fill,
    MarketInfo,
    MarketView,
    OrderIntent,
    Portfolio,
    Position,
    SettlementResult,
    Strategy,
    Trade,
)

DEFAULT_DECISION_INTERVAL_S = 3600
DEFAULT_CANDLE_PERIOD_S = 3600


@dataclass(frozen=True, slots=True)
class EngineConfig:
    risk: RiskConfig = field(default_factory=RiskConfig)
    candle_period_s: int = DEFAULT_CANDLE_PERIOD_S
    fee_stress: float = 1.0
    event_log_path: str | None = None
    keep_events_in_memory: bool = True


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """One (t, market) instant where the strategy stated a probability.

    Used for Brier-vs-market-baseline scoring at identical instants.
    """
    ts: int
    ticker: str
    p_hat: float
    market_mid_cents: float | None


@dataclass
class BacktestResult:
    config: EngineConfig
    markets: dict[str, MarketInfo]
    settlements: dict[str, SettlementResult]
    fills: list[Fill]
    orders: dict[int, dict[str, Any]]           # order_id -> intent/clamp info
    intents: list[dict[str, Any]]               # every intent incl. fully-clamped
    decisions: list[DecisionRecord]
    equity_curve: list[tuple[int, int]]         # (ts, equity cents), gross of inference
    deployed_curve: list[tuple[int, int]]       # (ts, premium at risk cents)
    per_market: dict[str, dict[str, Any]]
    final_cash_cents: int
    fees_cents: int
    inference_cost_cents: int
    realized_pnl_cents: int
    n_decision_calls: int
    events: list[dict[str, Any]]

    @property
    def bankroll_cents(self) -> int:
        return self.config.risk.bankroll_cents

    @property
    def net_pnl_cents(self) -> int:
        """Realized P&L after fees, gross of inference cost."""
        return self.final_cash_cents - self.bankroll_cents

    @property
    def net_pnl_after_inference_cents(self) -> int:
        return self.net_pnl_cents - self.inference_cost_cents


# ---------------------------------------------------------------------------
# Internal mutable state
# ---------------------------------------------------------------------------

_TAKER, _RESTING, _DONE = "taker_window", "resting", "done"


class _Order:
    __slots__ = (
        "order_id", "intent", "norm", "remaining", "placed_ts", "state",
        "taker_yes_price", "window_end", "maker_from_ts", "p_hat",
    )

    def __init__(self, order_id: int, intent: OrderIntent, norm, placed_ts: int):
        self.order_id = order_id
        self.intent = intent
        self.norm = norm
        self.remaining = norm.size
        self.placed_ts = placed_ts
        self.state = _RESTING
        self.taker_yes_price: int | None = None
        self.window_end: int | None = None
        self.maker_from_ts = placed_ts
        self.p_hat = intent.p_hat

    @property
    def live(self) -> bool:
        return self.state != _DONE and self.remaining > 0

    @property
    def premium_per_contract(self) -> int:
        return self.norm.yes_limit if self.norm.yes_buy else 100 - self.norm.yes_limit

    @property
    def committed_cents(self) -> int:
        return self.remaining * self.premium_per_contract if self.live else 0


class _MarketState:
    __slots__ = (
        "info", "candles", "candle_ends", "trades", "trade_ts", "settlement",
        "has_trades", "ci", "ti", "orders", "qty", "basis", "realized",
        "fees", "mark", "period_s", "first_entry_ts", "exit_ts",
        "contracts_traded", "decision_times",
    )

    def __init__(
        self,
        info: MarketInfo,
        candles: list[Candle],
        trades: list[Trade],
        settlement: SettlementResult | None,
        period_s: int,
    ):
        self.info = info
        self.candles = candles
        self.candle_ends = [c.end_ts for c in candles]
        self.trades = trades
        self.trade_ts = [t.ts for t in trades]
        self.settlement = settlement
        self.has_trades = bool(trades)
        self.ci = 0
        self.ti = 0
        self.orders: list[_Order] = []
        self.qty = 0
        self.basis = 0
        self.realized = 0
        self.fees = 0
        self.mark: int | None = None
        self.period_s = period_s
        self.first_entry_ts: int | None = None
        self.exit_ts: int | None = None
        self.contracts_traded = 0
        self.decision_times: list[int] = []

    @property
    def effective_end_ts(self) -> int:
        end = self.info.close_ts
        if self.settlement is not None:
            end = min(end, self.settlement.settled_ts)
        return end

    def live_orders(self) -> list["_Order"]:
        return [o for o in self.orders if o.live]

    def mark_value_cents(self) -> int:
        if self.qty == 0:
            return 0
        if self.mark is None:
            return self.basis  # no price seen yet: carry at cost
        return self.qty * self.mark if self.qty > 0 else -self.qty * (100 - self.mark)


# ---------------------------------------------------------------------------
# Decision clock
# ---------------------------------------------------------------------------

def build_decision_times(
    candles: list[Candle],
    interval_s: int | None,
    move_trigger_cents: int | None,
    end_ts: int,
) -> list[int]:
    """Candidate decision points are candle end times (data fully observable).

    The first candidate always fires (a strategy gets at least one look);
    afterwards a candidate fires if the fixed interval elapsed or the close
    moved >= the trigger since the last decision. Strategies cannot peek
    between their own ticks - this list IS their clock.
    """
    times: list[int] = []
    last_t: int | None = None
    last_close: int | None = None
    for c in candles:
        t = c.end_ts
        if t >= end_ts:
            break
        fire = last_t is None
        if not fire and interval_s is not None and t - last_t >= interval_s:
            fire = True
        if (
            not fire
            and move_trigger_cents is not None
            and last_close is not None
            and abs(c.close - last_close) >= move_trigger_cents
        ):
            fire = True
        if fire:
            times.append(t)
            last_t = t
            last_close = c.close
    return times


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class _Engine:
    def __init__(self, provider: HistoryProvider, strategy: Strategy, config: EngineConfig):
        self.provider = provider
        self.strategy = strategy
        self.config = config
        self.risk = config.risk
        self.cash = self.risk.bankroll_cents
        self.fees = 0
        self.inference = 0
        self.next_order_id = 1
        self.markets: dict[str, _MarketState] = {}
        self.result_fills: list[Fill] = []
        self.orders_info: dict[int, dict[str, Any]] = {}
        self.intents_log: list[dict[str, Any]] = []
        self.decisions: list[DecisionRecord] = []
        self.equity_curve: list[tuple[int, int]] = []
        self.deployed_curve: list[tuple[int, int]] = []
        self.events: list[dict[str, Any]] = []
        self.n_decision_calls = 0
        self._log_fh = open(config.event_log_path, "w") if config.event_log_path else None

    # -- event log -----------------------------------------------------------

    def _log(self, ts: int, type_: str, **kw: Any) -> None:
        ev = {"ts": ts, "type": type_, **kw}
        if self.config.keep_events_in_memory:
            self.events.append(ev)
        if self._log_fh is not None:
            self._log_fh.write(json.dumps(ev) + "\n")

    # -- portfolio helpers ----------------------------------------------------

    def _committed_cents(self) -> int:
        return sum(
            o.committed_cents for ms in self.markets.values() for o in ms.orders
        )

    def _total_basis_cents(self) -> int:
        return sum(ms.basis for ms in self.markets.values())

    def _equity_cents(self) -> int:
        return self.cash + sum(ms.mark_value_cents() for ms in self.markets.values())

    def _snapshot_portfolio(self) -> Portfolio:
        positions = tuple(
            Position(ms.info.ticker, ms.qty, ms.basis, ms.realized)
            for ms in self.markets.values()
            if ms.qty != 0
        )
        return Portfolio(
            cash_cents=self.cash,
            positions=positions,
            committed_cents=self._committed_cents(),
            fees_paid_cents=self.fees,
            inference_cost_cents=self.inference,
        )

    def _record_curves(self, ts: int) -> None:
        self.equity_curve.append((ts, self._equity_cents()))
        self.deployed_curve.append(
            (ts, self._total_basis_cents() + self._committed_cents())
        )

    # -- fills ----------------------------------------------------------------

    def _fee_for(self, is_taker: bool, exec_side_price: int, count: int, category: str) -> int:
        fn = fees_mod.taker_fee_cents if is_taker else fees_mod.maker_fee_cents
        return fn(
            exec_side_price, count, category=category,
            stress_multiplier=self.config.fee_stress,
        )

    def _apply_fill(
        self, ms: _MarketState, order: _Order, count: int, yes_price: int,
        ts: int, is_taker: bool,
    ) -> None:
        if count <= 0:
            return
        exec_side_price = fills.side_price(order.intent.side, yes_price)
        fee = self._fee_for(is_taker, exec_side_price, count, ms.info.category)
        delta = count * order.norm.qty_sign
        ms.qty, ms.basis, cash_delta, realized = fills.apply_fill_to_position(
            ms.qty, ms.basis, delta, yes_price
        )
        self.cash += cash_delta - fee
        ms.fees += fee
        self.fees += fee
        ms.realized += realized
        ms.contracts_traded += count
        if ms.first_entry_ts is None:
            ms.first_entry_ts = ts
        order.remaining -= count
        if order.remaining <= 0:
            order.state = _DONE
        f = Fill(
            order_id=order.order_id,
            ticker=ms.info.ticker,
            side=order.intent.side,
            action=order.intent.action,
            price_cents=exec_side_price,
            count=count,
            ts=ts,
            is_taker=is_taker,
            fee_cents=fee,
        )
        self.result_fills.append(f)
        self._log(
            ts, "fill", order_id=order.order_id, ticker=ms.info.ticker,
            side=order.intent.side, action=order.intent.action,
            price_cents=exec_side_price, yes_price_cents=yes_price, count=count,
            is_taker=is_taker, fee_cents=fee, cash_cents=self.cash,
            position_qty=ms.qty,
        )

    def _finalize_taker_window(self, ms: _MarketState, order: _Order, ts: int) -> None:
        """Taker window elapsed: remainder rests (tif=rest) or dies (ioc)."""
        if order.state != _TAKER:
            return
        if order.remaining <= 0:
            order.state = _DONE
            return
        if order.intent.tif == "ioc":
            order.state = _DONE
            self._log(
                ts, "cancel", order_id=order.order_id, ticker=ms.info.ticker,
                reason="ioc-expired", unfilled=order.remaining,
            )
        else:
            order.state = _RESTING
            order.maker_from_ts = order.window_end or ts
            self._log(
                ts, "order_resting", order_id=order.order_id,
                ticker=ms.info.ticker, remaining=order.remaining,
            )

    def _advance_market(self, ms: _MarketState, t: int) -> None:
        """Replay this market's data with timestamps <= t against live orders."""
        vf = self.risk.depth_cap_frac
        vm = self.risk.depth_multiplier
        while True:
            next_trade_ts = ms.trade_ts[ms.ti] if ms.ti < len(ms.trades) else None
            next_candle_ts = ms.candle_ends[ms.ci] if ms.ci < len(ms.candles) else None
            pick_trade = (
                next_trade_ts is not None
                and next_trade_ts <= t
                and (next_candle_ts is None or next_trade_ts <= next_candle_ts)
            )
            pick_candle = (
                not pick_trade and next_candle_ts is not None and next_candle_ts <= t
            )
            if not pick_trade and not pick_candle:
                break
            if pick_trade:
                trade = ms.trades[ms.ti]
                ms.ti += 1
                ms.mark = trade.yes_price
                if ms.has_trades:  # trades are the fill stream when present
                    for o in ms.live_orders():
                        if (
                            o.state == _TAKER
                            and o.placed_ts < trade.ts <= (o.window_end or 0)
                        ):
                            n = min(o.remaining, fills.volume_cap(trade.count, vf, vm))
                            self._apply_fill(
                                ms, o, n, o.taker_yes_price, trade.ts, is_taker=True
                            )
                        elif o.state == _RESTING and trade.ts > o.maker_from_ts:
                            n = fills.maker_fill_from_trade(
                                o.norm, trade, o.remaining, vf, vm
                            )
                            self._apply_fill(
                                ms, o, n, o.norm.yes_limit, trade.ts, is_taker=False
                            )
            else:
                candle = ms.candles[ms.ci]
                ms.ci += 1
                ms.mark = candle.close
                if not ms.has_trades:  # candles are the (conservative) fill stream
                    for o in ms.live_orders():
                        if (
                            o.state == _TAKER
                            and candle.start_ts >= o.placed_ts
                            and candle.start_ts < (o.window_end or 0)
                        ):
                            n = min(
                                o.remaining, fills.volume_cap(candle.volume, vf, vm)
                            )
                            self._apply_fill(
                                ms, o, n, o.taker_yes_price, candle.end_ts, is_taker=True
                            )
                        elif o.state == _RESTING and candle.start_ts >= o.maker_from_ts:
                            n = fills.maker_fill_from_candle(
                                o.norm, candle, o.remaining, vf, vm
                            )
                            self._apply_fill(
                                ms, o, n, o.norm.yes_limit, candle.end_ts, is_taker=False
                            )
                # A completed candle also closes any taker window it covers.
                for o in ms.orders:
                    if o.state == _TAKER and candle.end_ts >= (o.window_end or 0):
                        self._finalize_taker_window(ms, o, candle.end_ts)
        # Time-based taker-window expiry (no data needed).
        for o in ms.orders:
            if o.state == _TAKER and (o.window_end or 0) <= t:
                self._finalize_taker_window(ms, o, t)

    def _advance_all(self, t: int) -> None:
        for ms in self.markets.values():
            self._advance_market(ms, t)

    # -- decision handling ----------------------------------------------------

    def _build_view(self, ms: _MarketState, t: int) -> MarketView:
        n_candles = bisect_right(ms.candle_ends, t)
        n_trades = bisect_right(ms.trade_ts, t - 1)  # strictly < t
        return MarketView(
            market=ms.info,
            t=t,
            candles_by_period={ms.period_s: tuple(ms.candles[:n_candles])},
            trades_before_t=tuple(ms.trades[:n_trades]),
        )

    def _exposure_for(self, ms: _MarketState) -> ExposureState:
        committed = self._committed_cents()
        market_cost = ms.basis + sum(o.committed_cents for o in ms.orders)
        event_cost = sum(
            m2.basis + sum(o.committed_cents for o in m2.orders)
            for m2 in self.markets.values()
            if m2.info.event_ticker == ms.info.event_ticker
        )
        return ExposureState(
            market_cost_cents=market_cost,
            event_cost_cents=event_cost,
            total_cost_cents=self._total_basis_cents() + committed,
            available_cash_cents=self.cash - committed,
            position_qty=ms.qty,
        )

    def _recent_window_volume(self, view: MarketView, period_s: int) -> int | None:
        series = view.candles(period_s)
        if not series:
            return None
        tail = series[-3:]
        return sum(c.volume for c in tail) // len(tail)

    def _handle_decision(self, ms: _MarketState, t: int) -> None:
        view = self._build_view(ms, t)
        portfolio = self._snapshot_portfolio()
        self.n_decision_calls += 1
        intents = self.strategy.on_decision_point(view, portfolio) or []
        cost = int(getattr(self.strategy, "last_inference_cost_cents", 0) or 0)
        if cost:
            self.inference += cost
            self._log(t, "inference_cost", ticker=ms.info.ticker, cost_cents=cost)
        self._log(
            t, "decision", ticker=ms.info.ticker, n_intents=len(intents),
            mid_cents=view.mid_cents,
        )
        for intent in intents:
            self._handle_intent(ms, view, intent, t)

    def _handle_intent(
        self, ms: _MarketState, view: MarketView, intent: OrderIntent, t: int
    ) -> None:
        if intent.ticker != ms.info.ticker:
            self._log(t, "intent_rejected", ticker=intent.ticker, reason="wrong-ticker")
            return
        try:
            norm = fills.normalize(intent)
        except ValueError as e:
            self._log(t, "intent_rejected", ticker=intent.ticker, reason=str(e))
            return
        if intent.p_hat is not None:
            self.decisions.append(
                DecisionRecord(t, intent.ticker, intent.p_hat, view.mid_cents)
            )
        bid, ask = view.best_bid, view.best_ask
        is_taker = fills.crosses(norm, bid, ask)
        est_yes_price = fills.taker_price(norm, bid, ask) if is_taker else norm.yes_limit
        cat = ms.info.category.lower()
        est_rate = (
            fees_mod.CATEGORY_TAKER_RATES.get(cat, fees_mod.DEFAULT_TAKER_RATE)
            if is_taker
            else fees_mod.CATEGORY_MAKER_RATES.get(cat, fees_mod.DEFAULT_MAKER_RATE)
        )
        fee_pc = (
            fees_mod.per_contract_fee_cents(
                fills.side_price(intent.side, est_yes_price),
                rate=est_rate,
                stress_multiplier=self.config.fee_stress,
            )
            if est_rate > 0
            else 0
        )
        clamp: ClampResult = clamp_intent_size(
            norm,
            intent.p_hat,
            est_yes_price,
            fee_pc,
            self._exposure_for(ms),
            self.risk,
            observed_window_volume=self._recent_window_volume(view, ms.period_s),
        )
        self.intents_log.append(
            {
                "ts": t, "ticker": intent.ticker, "side": intent.side,
                "action": intent.action, "limit_price_cents": intent.limit_price_cents,
                "tif": intent.tif, "p_hat": intent.p_hat,
                "requested": intent.size, "clamped": clamp.size,
                "clamp_reasons": list(clamp.reasons), "rationale": intent.rationale,
            }
        )
        self._log(
            t, "intent", ticker=intent.ticker, side=intent.side,
            action=intent.action, limit_price_cents=intent.limit_price_cents,
            requested=intent.size, clamped=clamp.size,
            clamp_reasons=list(clamp.reasons), p_hat=intent.p_hat,
            rationale=intent.rationale,
        )
        if clamp.size <= 0:
            return
        if not is_taker and intent.tif == "ioc":
            # This-tick-only order that doesn't cross: nothing to do.
            self._log(t, "cancel", ticker=intent.ticker, reason="ioc-no-cross")
            return
        order = _Order(
            self.next_order_id,
            replace(intent, size=clamp.size),
            fills.NormalizedIntent(norm.ticker, norm.yes_buy, norm.yes_limit, clamp.size),
            t,
        )
        self.next_order_id += 1
        if is_taker:
            order.state = _TAKER
            order.taker_yes_price = est_yes_price
            order.window_end = t + ms.period_s
            order.maker_from_ts = order.window_end
        ms.orders.append(order)
        self.orders_info[order.order_id] = {
            "ts": t, "ticker": intent.ticker, "side": intent.side,
            "action": intent.action, "limit_price_cents": intent.limit_price_cents,
            "size": clamp.size, "tif": intent.tif, "p_hat": intent.p_hat,
            "is_taker_at_entry": is_taker,
        }
        self._log(
            t, "order_placed", order_id=order.order_id, ticker=intent.ticker,
            is_taker=is_taker, size=clamp.size,
            taker_yes_price=order.taker_yes_price, yes_limit=norm.yes_limit,
        )

    # -- close / settlement ---------------------------------------------------

    def _handle_close(self, ms: _MarketState, t: int) -> None:
        for o in ms.live_orders():
            o.state = _DONE
            self._log(
                t, "cancel", order_id=o.order_id, ticker=ms.info.ticker,
                reason="market-closed", unfilled=o.remaining,
            )

    def _handle_settlement(self, ms: _MarketState, t: int) -> None:
        s = ms.settlement
        assert s is not None
        payout = fills.settlement_payout_cents(ms.qty, ms.basis, s.result)
        realized = payout - ms.basis
        self.cash += payout
        ms.realized += realized
        ms.exit_ts = t
        self._log(
            t, "settlement", ticker=ms.info.ticker, result=s.result,
            qty=ms.qty, payout_cents=payout, realized_cents=realized,
            cash_cents=self.cash,
        )
        ms.qty = 0
        ms.basis = 0
        ms.mark = None

    # -- main loop -------------------------------------------------------------

    def run(self, market_filters: dict | None = None) -> BacktestResult:
        cfg = self.config
        interval = getattr(self.strategy, "decision_interval_s", DEFAULT_DECISION_INTERVAL_S)
        trigger = getattr(self.strategy, "price_move_trigger_cents", None)
        period = getattr(self.strategy, "candle_period_s", None) or cfg.candle_period_s

        events: list[tuple[int, int, int, str, str]] = []  # (ts, prio, seq, kind, ticker)
        seq = 0
        for info in self.provider.iter_markets(**(market_filters or {})):
            candles = list(self.provider.candles(info.ticker, period))
            trades = list(self.provider.trades(info.ticker))
            settlement = self.provider.settlement(info.ticker)
            ms = _MarketState(info, candles, trades, settlement, period)
            self.markets[info.ticker] = ms
            ms.decision_times = build_decision_times(
                candles, interval, trigger, ms.effective_end_ts
            )
            for t in ms.decision_times:
                events.append((t, 0, seq, "decision", info.ticker))
                seq += 1
            events.append((ms.effective_end_ts, 1, seq, "close", info.ticker))
            seq += 1
            if settlement is not None:
                events.append((settlement.settled_ts, 2, seq, "settle", info.ticker))
                seq += 1
        events.sort()

        self._log(
            events[0][0] if events else 0, "run_start",
            n_markets=len(self.markets),
            bankroll_cents=self.risk.bankroll_cents,
            depth_multiplier=self.risk.depth_multiplier,
            fee_stress=cfg.fee_stress,
        )

        for ts, _prio, _seq, kind, ticker in events:
            ms = self.markets[ticker]
            self._advance_all(ts)
            if kind == "decision":
                self._handle_decision(ms, ts)
            elif kind == "close":
                self._handle_close(ms, ts)
            elif kind == "settle":
                self._handle_settlement(ms, ts)
            self._record_curves(ts)

        last_ts = events[-1][0] if events else 0
        self._log(
            last_ts, "run_end", cash_cents=self.cash, fees_cents=self.fees,
            inference_cost_cents=self.inference,
        )
        if self._log_fh is not None:
            self._log_fh.close()

        per_market = {
            tk: {
                "realized_pnl_cents": ms.realized,
                "fees_cents": ms.fees,
                "net_pnl_cents": ms.realized - ms.fees,
                "first_entry_ts": ms.first_entry_ts,
                "exit_ts": ms.exit_ts,
                "contracts_traded": ms.contracts_traded,
                "category": ms.info.category,
                "event_ticker": ms.info.event_ticker,
            }
            for tk, ms in self.markets.items()
        }
        return BacktestResult(
            config=cfg,
            markets={tk: ms.info for tk, ms in self.markets.items()},
            settlements={
                tk: ms.settlement
                for tk, ms in self.markets.items()
                if ms.settlement is not None
            },
            fills=self.result_fills,
            orders=self.orders_info,
            intents=self.intents_log,
            decisions=self.decisions,
            equity_curve=self.equity_curve,
            deployed_curve=self.deployed_curve,
            per_market=per_market,
            final_cash_cents=self.cash,
            fees_cents=self.fees,
            inference_cost_cents=self.inference,
            realized_pnl_cents=sum(ms.realized for ms in self.markets.values()),
            n_decision_calls=self.n_decision_calls,
            events=self.events,
        )


def run_backtest(
    provider: HistoryProvider,
    strategy: Strategy,
    config: EngineConfig | None = None,
    market_filters: dict | None = None,
) -> BacktestResult:
    """Run one deterministic backtest. See module docstring for semantics."""
    return _Engine(provider, strategy, config or EngineConfig()).run(market_filters)

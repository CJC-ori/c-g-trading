"""ForecasterStrategy — runs the P-4 forecaster through the backtest engine
(rung 3). Maker-first, ≥4-point trade gate, inference cost charged to P&L.

Forecasts are PRE-COMPUTED (run_rung3.py phase A) at a fixed anchor
``asof = close - anchor_days`` per market, under full point-in-time
discipline, and handed to the strategy as a dict. At the first decision
point ``t >= asof`` the strategy charges the recorded inference cost via
``last_inference_cost_cents`` (the engine reads it after every
``on_decision_point``) and, if the forecast disagrees with the market mid by
at least ``gate_cents``, rests a maker order on the cheap side. One episode
per market, hold to resolution.

Using a pre-computed forecast made at ``asof < t`` is point-in-time-safe:
the strategy only ever uses information from before its decision time.
"""
from __future__ import annotations

from dataclasses import dataclass

from bot.backtest.types import MarketView, OrderIntent, Portfolio

DAY = 86400


@dataclass(frozen=True)
class PrecomputedForecast:
    ticker: str
    p_yes: float            # aggregated pipeline output, [0,1]
    asof_ts: int            # unix ts the forecast was built at (PIT anchor)
    cost_cents: int         # full pipeline inference cost for this market


class ForecasterStrategy:
    """SPEC Strategy protocol implementation over precomputed forecasts."""

    decision_interval_s = DAY
    price_move_trigger_cents: int | None = None
    candle_period_s = 3600
    last_inference_cost_cents = 0

    def __init__(
        self,
        forecasts: dict[str, PrecomputedForecast],
        gate_cents: int = 4,
        size: int = 1000,
    ):
        self.forecasts = forecasts
        self.gate_cents = gate_cents
        self.size = size
        self._charged: set[str] = set()
        self._quoted: set[str] = set()

    def on_decision_point(
        self, view: MarketView, portfolio: Portfolio
    ) -> list[OrderIntent]:
        self.last_inference_cost_cents = 0
        tkr = view.market.ticker
        fc = self.forecasts.get(tkr)
        if fc is None or view.t < fc.asof_ts:
            return []
        if tkr not in self._charged:
            # charge the pipeline cost once, at the first decision point
            # where the forecast becomes usable
            self.last_inference_cost_cents = fc.cost_cents
            self._charged.add(tkr)
        if tkr in self._quoted:
            return []  # one episode per market; resting order stays live
        pos = portfolio.position(tkr)
        if pos is not None and pos.qty != 0:
            return []
        mid = view.mid_cents
        yb, ya = view.best_bid, view.best_ask
        if mid is None or yb is None or ya is None:
            return []
        edge_cents = fc.p_yes * 100.0 - mid
        if abs(edge_cents) < self.gate_cents:
            return []
        if edge_cents > 0:
            # we think YES is underpriced: rest a maker bid at best bid
            side, price = "yes", yb
        else:
            # YES overpriced: rest a maker bid on NO at (100 - best ask)
            side, price = "no", 100 - ya
        if not (1 <= price <= 99):
            return []
        self._quoted.add(tkr)
        return [
            OrderIntent(
                ticker=tkr,
                side=side,  # type: ignore[arg-type]
                action="buy",
                limit_price_cents=price,
                size=self.size,
                tif="rest",  # maker-first
                p_hat=fc.p_yes,
                rationale=(
                    f"forecaster p={fc.p_yes:.2f} vs mid={mid:.0f}c "
                    f"edge={edge_cents:+.1f}c gate={self.gate_cents}c"
                ),
            )
        ]

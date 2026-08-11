"""Trivial reference strategies proving the backtest loop end-to-end.

These are NOT meant to make money; they exist to exercise the harness and
to provide null/sanity baselines for real strategies.

- HoldFavorite: buys YES on markets priced 90-97c at its first look and
  holds to settlement (classic favorite-bias probe).
- CoinFlip: emits a random p_hat per market (seeded), trades toward it with
  a tiny size; its P&L distribution should be centered ~0 minus fees.
"""
from __future__ import annotations

import random

from bot.backtest.types import MarketView, OrderIntent, Portfolio


class HoldFavorite:
    """Buy YES once when price is in [lo, hi] cents; hold to settlement."""

    decision_interval_s = 3600
    price_move_trigger_cents = None
    candle_period_s = 3600
    last_inference_cost_cents = 0  # price-only: free decisions

    def __init__(self, lo: int = 90, hi: int = 97, size: int = 100, edge_cents: int = 2):
        self.lo = lo
        self.hi = hi
        self.size = size
        self.edge_cents = edge_cents  # assumed favorite-bias edge over price

    def on_decision_point(
        self, view: MarketView, portfolio: Portfolio
    ) -> list[OrderIntent]:
        price = view.last_price
        if price is None or not (self.lo <= price <= self.hi):
            return []
        pos = portfolio.position(view.market.ticker)
        if pos is not None and pos.qty != 0:
            return []  # already holding
        ask = view.best_ask
        if ask is None or ask >= 100:
            return []
        p_hat = min(0.99, (price + self.edge_cents) / 100)
        return [
            OrderIntent(
                ticker=view.market.ticker,
                side="yes",
                action="buy",
                limit_price_cents=min(99, ask),  # cross => taker
                size=self.size,
                tif="ioc",
                p_hat=p_hat,
                rationale=f"favorite-bias: price {price}c in [{self.lo},{self.hi}]",
            )
        ]


class CoinFlip:
    """Null strategy: random (seeded) p_hat, trades toward it with tiny size."""

    decision_interval_s = 3600
    price_move_trigger_cents = None
    candle_period_s = 3600
    last_inference_cost_cents = 0

    def __init__(self, seed: int = 1234, size: int = 10):
        self.rng = random.Random(seed)
        self.size = size
        self._p_hat_by_market: dict[str, float] = {}

    def on_decision_point(
        self, view: MarketView, portfolio: Portfolio
    ) -> list[OrderIntent]:
        ticker = view.market.ticker
        pos = portfolio.position(ticker)
        if pos is not None and pos.qty != 0:
            return []
        mid = view.mid_cents
        if mid is None:
            return []
        # One sticky random belief per market (deterministic per seed).
        p_hat = self._p_hat_by_market.setdefault(
            ticker, self.rng.uniform(0.05, 0.95)
        )
        buy_yes = p_hat * 100 > mid
        ask, bid = view.best_ask, view.best_bid
        if buy_yes:
            if ask is None:
                return []
            side, limit = "yes", min(99, ask)
        else:
            if bid is None:
                return []
            side, limit = "no", min(99, 100 - bid)  # cross vs yes bid
        return [
            OrderIntent(
                ticker=ticker,
                side=side,
                action="buy",
                limit_price_cents=limit,
                size=self.size,
                tif="ioc",
                p_hat=p_hat,
                rationale=f"coinflip p_hat={p_hat:.2f} vs mid {mid:.1f}",
            )
        ]

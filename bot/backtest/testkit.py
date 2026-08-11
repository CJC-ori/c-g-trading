"""Builders + synthetic histories for tests and smoke runs (no real data)."""
from __future__ import annotations

import random
from typing import Callable

from bot.backtest.dataport import InMemoryHistoryProvider
from bot.backtest.types import (
    Candle,
    MarketInfo,
    MarketView,
    OrderIntent,
    Portfolio,
    SettlementResult,
    Trade,
)

HOUR = 3600


def mk_candle(
    ticker: str = "MKT",
    start: int = 0,
    period: int = HOUR,
    o: int = 50,
    h: int | None = None,
    l: int | None = None,
    c: int = 50,
    vol: int = 400,
    bid: int | None = "auto",
    ask: int | None = "auto",
) -> Candle:
    h = h if h is not None else max(o, c)
    l = l if l is not None else min(o, c)
    if bid == "auto":
        bid = max(1, c - 1)
    if ask == "auto":
        ask = min(99, c + 1)
    return Candle(ticker, start, period, o, h, l, c, vol, bid, ask)


def mk_trade(
    ticker: str = "MKT", ts: int = 0, price: int = 50, count: int = 100
) -> Trade:
    return Trade(ticker, ts, price, count)


def mk_market(
    ticker: str = "MKT",
    event: str = "EV",
    category: str = "test",
    open_ts: int = 0,
    close_ts: int = 100 * HOUR,
) -> MarketInfo:
    return MarketInfo(
        ticker=ticker, event_ticker=event, category=category,
        title=ticker, open_ts=open_ts, close_ts=close_ts,
    )


class ScriptedStrategy:
    """Emits pre-scripted intents keyed by (ts, ticker); records every view.

    script: {(ts, ticker): [OrderIntent, ...]} or a callable
    (view, portfolio) -> list[OrderIntent].
    """

    decision_interval_s = HOUR
    price_move_trigger_cents = None
    candle_period_s = HOUR
    last_inference_cost_cents = 0

    def __init__(
        self,
        script: dict[tuple[int, str], list[OrderIntent]] | Callable | None = None,
        inference_cost_cents: int = 0,
    ):
        self.script = script or {}
        self.last_inference_cost_cents = inference_cost_cents
        self.calls: list[tuple[int, str]] = []
        self.views: list[MarketView] = []

    def on_decision_point(
        self, view: MarketView, portfolio: Portfolio
    ) -> list[OrderIntent]:
        self.calls.append((view.t, view.market.ticker))
        self.views.append(view)
        if callable(self.script):
            return self.script(view, portfolio)
        return list(self.script.get((view.t, view.market.ticker), []))


def synthetic_provider(
    seed: int = 7,
    n_random: int = 5,
    n_favorites: int = 3,
    hours: int = 72,
    period: int = HOUR,
    t0: int = 1_000_000 * HOUR,  # aligned to the candle grid
) -> InMemoryHistoryProvider:
    """A small deterministic synthetic Kalshi-like history.

    n_favorites markets random-walk in the low 90s and settle YES;
    n_random markets random-walk around 35-65c and settle by final price.
    Hourly candles with bid/ask = close +/- 1 and volume 200-600.
    """
    rng = random.Random(seed)
    markets, candles, settlements = [], [], []
    for i in range(n_favorites + n_random):
        favorite = i < n_favorites
        ticker = f"{'FAV' if favorite else 'RND'}-{i}"
        close_ts = t0 + hours * period
        markets.append(
            MarketInfo(
                ticker=ticker,
                event_ticker=f"EV-{i}",
                category="favorites" if favorite else "randoms",
                title=ticker,
                open_ts=t0,
                close_ts=close_ts,
            )
        )
        p = rng.randint(91, 93) if favorite else rng.randint(35, 65)
        for hidx in range(hours):
            o = p
            step = rng.choice([-1, 0, 0, 1]) if favorite else rng.randint(-2, 2)
            p = max(5, min(96 if favorite else 95, p + step))
            candles.append(
                mk_candle(
                    ticker=ticker,
                    start=t0 + hidx * period,
                    period=period,
                    o=o,
                    h=max(o, p) + (0 if max(o, p) >= 96 else 1),
                    l=min(o, p) - (0 if min(o, p) <= 4 else 1),
                    c=p,
                    vol=rng.randint(200, 600),
                )
            )
        result = "yes" if (favorite or p >= 50) else "no"
        settlements.append(SettlementResult(ticker, result, close_ts))
    return InMemoryHistoryProvider(markets, candles, [], settlements)

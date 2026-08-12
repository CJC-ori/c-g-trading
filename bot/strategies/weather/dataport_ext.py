"""Provider wrapper: synthesize hourly candles from the trade tape.

The data agent pulled full trade history for the weather series well
before candles (900k+ prints, 550+ markets), and the engine only needs
candles for the decision clock / quote estimates — when trades exist
they are the fill stream (engine `has_trades` path). This wrapper serves
real candles when the store has them and otherwise builds hourly OHLCV
candles from the market's own prints:

- open/high/low/close/volume from the hour's trade prints;
- no bid/ask closes -> MarketView falls back to its documented
  conservative last-trade ±1c book estimate;
- hours with no prints get no candle (sparse markets get sparse decision
  points, which is honest — nobody was trading).

Fills stay honest: with trades present the engine fills maker orders
only on prints at/through the limit, volume-capped; synthesized candles
never fill anything themselves.
"""
from __future__ import annotations

from typing import Iterable, Sequence

from bot.backtest.dataport import SqliteHistoryProvider
from bot.backtest.types import Candle, MarketInfo, SettlementResult, Trade


class TradeSynthCandleProvider:
    """SqliteHistoryProvider + trade-synthesized candles where missing."""

    def __init__(self, db_path: str = "data/kalshi.db"):
        self._inner = SqliteHistoryProvider(db_path)

    def iter_markets(self, **filters) -> Iterable[MarketInfo]:
        return self._inner.iter_markets(**filters)

    def trades(self, ticker: str, until: int | None = None) -> Sequence[Trade]:
        return self._inner.trades(ticker, until)

    def settlement(self, ticker: str) -> SettlementResult | None:
        return self._inner.settlement(ticker)

    def candles(
        self, ticker: str, period_s: int, until: int | None = None
    ) -> Sequence[Candle]:
        real = self._inner.candles(ticker, period_s, until)
        if real:
            return real
        trades = self._inner.trades(ticker, until)
        if not trades:
            return []
        return synthesize_candles(trades, period_s)


def synthesize_candles(trades: Sequence[Trade], period_s: int) -> list[Candle]:
    """Hourly (or any period) OHLCV candles from a market's trade prints."""
    out: list[Candle] = []
    cur_start: int | None = None
    o = h = l = c = 0
    vol = 0
    for t in trades:
        start = (t.ts // period_s) * period_s
        if start != cur_start:
            if cur_start is not None:
                out.append(Candle(
                    ticker=t.ticker, start_ts=cur_start, period_s=period_s,
                    open=o, high=h, low=l, close=c, volume=vol,
                ))
            cur_start = start
            o = h = l = c = t.yes_price
            vol = 0
        h = max(h, t.yes_price)
        l = min(l, t.yes_price)
        c = t.yes_price
        vol += t.count
    if cur_start is not None and trades:
        out.append(Candle(
            ticker=trades[-1].ticker, start_ts=cur_start, period_s=period_s,
            open=o, high=h, low=l, close=c, volume=vol,
        ))
    return out

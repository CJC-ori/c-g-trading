"""Per-episode HistoryProvider for P-3 backtests.

One EpisodeProvider serves exactly one episode's market to the engine,
with honest per-episode data selection (mirroring episodes.py's coverage
logic, decided by data availability only — never by prices):

- period 60 runs when the event window has real 1-min candles OR tick
  trades that cover it (trade-synthesized 1-min candles fill minute gaps
  so the decision clock exists); otherwise the episode runs at period 3600
  on hourly candles with the engine's conservative candle fill rules.
- tick trades are served as the fill stream ONLY when they cover the
  window (episodes.py's trades_cover_window flag: trade volume >= 50% of
  the best candle volume). Serving partial tick data would silently
  suppress candle-based fills everywhere else (the engine treats trades
  as the exclusive fill stream when any exist).
- hourly candles are merged in only for hours containing no minute-level
  candle at all (no double-counted volume, no duplicate clock ticks).
- data starts DATA_START_LEAD_H before the window (R4b's entry phase
  needs a trailing-24h qualification average at window_start - 24h) and
  runs to the market's end so exits/settlement replay fully.

The MarketInfo (incl. the tapered_deci_cent price structure where the
market has one) and settlement come straight from the store adapter, so
fees and tick quantization behave exactly as in any other harness run.
"""
from __future__ import annotations

from bot.backtest.dataport import DEFAULT_DB_PATH, SqliteHistoryProvider
from bot.backtest.types import Candle, MarketInfo, SettlementResult, Trade

HOUR = 3600

#: Hours of context served before window_start: 24h entry lead (R4b) +
#: 24h qualification lookback + 2h slack.
DATA_START_LEAD_H = 50


def synth_minute_candles(trades: list[Trade], ticker: str) -> list[Candle]:
    """1-min OHLCV candles bucketed from tick trades (no bid/ask quotes).

    Used only to give the engine a decision clock / marks where trades are
    the fill stream but no real 1-min candles were pulled; the engine never
    fills against candle volume when trades exist."""
    by_min: dict[int, list[Trade]] = {}
    for t in trades:
        by_min.setdefault(t.ts // 60 * 60, []).append(t)
    out = []
    for start in sorted(by_min):
        ts = by_min[start]
        prices = [t.yes_price for t in ts]
        out.append(
            Candle(
                ticker=ticker,
                start_ts=start,
                period_s=60,
                open=prices[0],
                high=max(prices),
                low=min(prices),
                close=prices[-1],
                volume=sum(t.count for t in ts),
            )
        )
    return out


class EpisodeProvider:
    """HistoryProvider serving one episode's market (see module docstring)."""

    def __init__(
        self,
        ticker: str,
        window_start: int,
        window_end: int,
        trades_cover_window: bool,
        db_path: str = DEFAULT_DB_PATH,
        base: SqliteHistoryProvider | None = None,
    ):
        self.ticker = ticker
        self.window_start = window_start
        self.window_end = window_end
        self.trades_cover_window = trades_cover_window
        self.base = base or SqliteHistoryProvider(db_path)
        self.data_start = window_start - DATA_START_LEAD_H * HOUR
        self._built: dict[int, list[Candle]] = {}
        self._trades: list[Trade] | None = None

    # -- data assembly ------------------------------------------------------

    def _load_trades(self) -> list[Trade]:
        if self._trades is None:
            if self.trades_cover_window:
                self._trades = [
                    t
                    for t in self.base.trades(self.ticker)
                    if t.ts >= self.data_start
                ]
            else:
                self._trades = []
        return self._trades

    def _minute_series(self) -> list[Candle]:
        real = [
            c
            for c in self.base.candles(self.ticker, 60)
            if c.end_ts > self.data_start
        ]
        have_ends = {c.end_ts for c in real}
        synth = [
            c
            for c in synth_minute_candles(self._load_trades(), self.ticker)
            if c.end_ts not in have_ends
        ]
        merged = real + synth
        # Hourly gap-fill: only hours containing no minute candle at all.
        minute_hours = {(c.end_ts - 1) // HOUR for c in merged}
        hourly = [
            c
            for c in self.base.candles(self.ticker, 3600)
            if c.end_ts > self.data_start
            and not any(
                h in minute_hours
                for h in range((c.start_ts) // HOUR, (c.end_ts - 1) // HOUR + 1)
            )
        ]
        merged += hourly
        merged.sort(key=lambda c: c.end_ts)
        return merged

    @property
    def chosen_period_s(self) -> int:
        """60 when minute-level data covers the event window, else 3600."""
        for c in self._minute_candles_cached():
            if c.period_s == 60 and self.window_start < c.end_ts <= self.window_end:
                return 60
        return 3600

    def _minute_candles_cached(self) -> list[Candle]:
        if 60 not in self._built:
            self._built[60] = self._minute_series()
        return self._built[60]

    # -- HistoryProvider ----------------------------------------------------

    def iter_markets(self, **filters):
        for m in self.base.iter_markets(tickers=[self.ticker]):
            yield m

    def candles(self, ticker: str, period_s: int, until: int | None = None):
        if ticker != self.ticker:
            return []
        if period_s == 60:
            series = self._minute_candles_cached()
        elif period_s == 3600:
            if 3600 not in self._built:
                self._built[3600] = [
                    c
                    for c in self.base.candles(self.ticker, 3600)
                    if c.end_ts > self.data_start
                ]
            series = self._built[3600]
        else:
            return []
        if until is None:
            return list(series)
        return [c for c in series if c.end_ts <= until]

    def trades(self, ticker: str, until: int | None = None):
        if ticker != self.ticker:
            return []
        series = self._load_trades()
        if until is None:
            return list(series)
        return [t for t in series if t.ts < until]

    def settlement(self, ticker: str) -> SettlementResult | None:
        if ticker != self.ticker:
            return None
        return self.base.settlement(ticker)

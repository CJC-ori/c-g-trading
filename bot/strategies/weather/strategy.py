"""P-2 weather strategies (SYNTHESIS §1 P-2).

Two variants, both pure lookup at decision time (no network, no LLM —
everything point-in-time is precomputed by the runner from caches):

**WeatherDayAheadStrategy** — bias-corrected point-in-time forecast →
fair probability per strike → maker-first limit orders where
|fair − obtainable price| > fee + buffer.

Phases (decided from the decision timestamp in the city's timezone,
conservative about model availability — previous-runs lead-N values for
target date D come from runs initialized on day D−N):

- "d1" (day before target): uses the **lead-2** forecast (runs from D−2;
  every contributing run is >24h old at decision time — no lookahead).
- "d0am" (target-day morning, local hour 6–11): uses the **lead-1**
  forecast (runs from D−1, all initialized before local midnight and
  published hours before the decision).
- target-day afternoon+: no new entries (the deterministic forecast is
  stale once the max is forming; the intraday variant takes over).

**WeatherIntradayStrategy** — the 4 PM variant (gated on the measured
max-by-4PM stats): at the first decision with local wall time >= 16:30
it knows the station's running max through ~16:00 local (ACIS hourly obs;
live it would come from the ~4:30 PM CLI intermediate report, same
number) and prices every strike from the empirical rise-after-4PM PMF
fit on pre-window history.

Order placement (both variants, maker-first; weather series are plain
`quadratic`, maker fee = 0 — research/ground-truth.md §6.2):

- fair above the market: rest a YES buy at best_bid+1 when
  fair − (bid+1) > buffer (maker fee 0, so threshold = fee + 2c = 2c).
- fair below the market: rest a NO buy at ask−1 (in YES space) when
  (ask−1) − fair > buffer.
- p_hat = fair probability (feeds the harness's Kelly sizing; SPEC §5).
- one order per (market, phase); holders keep to settlement.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from bot.backtest.types import MarketView, OrderIntent, Portfolio
from bot.groundtruth.weather import (
    BiasFit,
    strike_prob,
    strike_prob_from_rise_pmf,
)

_MON = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}

_RE_EVENT_DATE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})(?:-|$)")


def target_date_from_ticker(ticker: str) -> str | None:
    """KXHIGHNY-26AUG10-T89 -> '2026-08-10' (None if no date code)."""
    m = _RE_EVENT_DATE.search(ticker)
    if not m:
        return None
    yy, mon, dd = m.groups()
    mm = _MON.get(mon)
    if mm is None:
        return None
    return f"20{yy}-{mm:02d}-{int(dd):02d}"


@dataclass(frozen=True, slots=True)
class Strike:
    """Static listing data for one weather market (known at listing)."""
    ticker: str
    series_ticker: str
    target_date: str          # YYYY-MM-DD local
    strike_type: str          # greater | less | between
    floor_strike: float | None
    cap_strike: float | None


def load_strikes_from_db(
    db_path: str = "data/kalshi.db",
    series_tickers: tuple[str, ...] | None = None,
) -> dict[str, Strike]:
    """Strike ladder for every KXHIGH* daily market in the store.

    All of this (strike type/levels, target date) is static listing data,
    visible from t = open_ts, so handing it to a strategy is
    point-in-time safe.
    """
    import sqlite3

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    out: dict[str, Strike] = {}
    try:
        for r in conn.execute(
            "SELECT ticker, series_ticker, strike_type, floor_strike, cap_strike"
            " FROM markets WHERE ticker LIKE 'KXHIGH%'"
            " AND strike_type IN ('greater','less','between')"
        ):
            if series_tickers is not None and r["series_ticker"] not in series_tickers:
                continue
            date = target_date_from_ticker(r["ticker"])
            if date is None:
                continue
            out[r["ticker"]] = Strike(
                ticker=r["ticker"],
                series_ticker=r["series_ticker"],
                target_date=date,
                strike_type=r["strike_type"],
                floor_strike=r["floor_strike"],
                cap_strike=r["cap_strike"],
            )
    finally:
        conn.close()
    return out


@dataclass(frozen=True, slots=True)
class CityModel:
    """Per-city forecast model handed to the strategy by the runner."""
    tz: str
    # lead -> bias fit (offset possibly seasonal + residual sd vs station)
    bias: dict[int, BiasFit] = field(default_factory=dict)
    # lead -> {target_date: raw grid daily-max forecast °F}
    forecasts: dict[int, dict[str, float]] = field(default_factory=dict)
    # optional walk-forward offsets, lead -> {target_date: offset °F}.
    # When a date is present here it overrides bias.offset_for(date)
    # (the runner computes these point-in-time: pre-window prior +
    # settled residuals through D-2 only).
    offset_by_date: dict[int, dict[str, float]] = field(default_factory=dict)


_PHASE_LEAD = {"d1": 2, "d0am": 1}


class WeatherDayAheadStrategy:
    """Day-ahead / morning-of bias-corrected forecast strategy."""

    decision_interval_s = 3600
    price_move_trigger_cents = None
    candle_period_s = 3600
    last_inference_cost_cents = 0  # pure math: free decisions

    def __init__(
        self,
        strikes: dict[str, Strike],
        models: dict[str, CityModel],
        buffer_cents: int = 2,
        min_sd: float = 1.0,
        request_size: int = 1000,
        morning_end_hour: int = 12,
    ):
        self.strikes = strikes
        self.models = models
        self.buffer_cents = buffer_cents
        self.min_sd = min_sd
        self.request_size = request_size
        self.morning_end_hour = morning_end_hour
        self.placed: set[tuple[str, str]] = set()
        # every priced look: dicts for post-run Brier/calibration scoring
        self.p_hat_log: list[dict] = []

    # -- phase / fair-value ---------------------------------------------------

    def _phase(self, t: int, tz: str, target_date: str) -> str | None:
        local = dt.datetime.fromtimestamp(t, tz=ZoneInfo(tz))
        d = local.date().isoformat()
        if d < target_date:
            return "d1"
        if d == target_date and local.hour < self.morning_end_hour:
            # lead-1 runs all initialized on D-1; give publication ~6h of
            # slack by not trading before 06:00 local.
            return "d0am" if local.hour >= 6 else None
        return None

    def fair_prob(self, strike: Strike, phase: str) -> float | None:
        model = self.models.get(strike.series_ticker)
        if model is None:
            return None
        lead = _PHASE_LEAD[phase]
        fc = model.forecasts.get(lead, {}).get(strike.target_date)
        bias = model.bias.get(lead)
        if fc is None or bias is None:
            return None
        wf_off = model.offset_by_date.get(lead, {}).get(strike.target_date)
        mu = fc + wf_off if wf_off is not None else bias.correct(fc, strike.target_date)
        sd = max(bias.sd, self.min_sd)
        return strike_prob(
            mu, sd, strike.strike_type, strike.floor_strike, strike.cap_strike
        )

    # -- decision -------------------------------------------------------------

    def on_decision_point(
        self, view: MarketView, portfolio: Portfolio
    ) -> list[OrderIntent]:
        ticker = view.market.ticker
        strike = self.strikes.get(ticker)
        if strike is None:
            return []
        model = self.models.get(strike.series_ticker)
        if model is None:
            return []
        phase = self._phase(view.t, model.tz, strike.target_date)
        if phase is None:
            return []
        fair = self.fair_prob(strike, phase)
        if fair is None:
            return []
        bid, ask = view.best_bid, view.best_ask
        mid = view.mid_cents
        self.p_hat_log.append(
            {"t": view.t, "ticker": ticker, "phase": phase,
             "p_hat": fair, "mid_cents": mid, "bid": bid, "ask": ask}
        )
        if (ticker, phase) in self.placed:
            return []
        pos = portfolio.position(ticker)
        if pos is not None and pos.qty != 0:
            return []
        # Kelly (SPEC §5) requires 0 < p_hat < 1; a degenerate 0/1 fair
        # (e.g. a strike already decided at 4 PM) would size to zero.
        fair_clamped = min(0.99, max(0.01, fair))
        intent = self._make_intent(ticker, fair_clamped, bid, ask, phase)
        if intent is None:
            return []
        self.placed.add((ticker, phase))
        return [intent]

    def _make_intent(
        self, ticker: str, fair: float, bid: int | None, ask: int | None,
        phase: str,
    ) -> OrderIntent | None:
        """Maker-first order construction (shared by both variants)."""
        fair_c = fair * 100.0
        thr = float(self.buffer_cents)  # maker fee 0 on weather series
        # YES side: rest at bid+1 (price-improving, still below the ask)
        if bid is not None and ask is not None:
            buy_yes_at = bid + 1
            if buy_yes_at < ask and 1 <= buy_yes_at <= 99 and (
                fair_c - buy_yes_at > thr
            ):
                return OrderIntent(
                    ticker=ticker,
                    side="yes",
                    action="buy",
                    limit_price_cents=buy_yes_at,
                    size=self.request_size,
                    tif="rest",
                    p_hat=fair,
                    rationale=f"{phase}: fair {fair_c:.1f}c > rest {buy_yes_at}c + {thr:.0f}c",
                )
            # NO side: rest sell-YES at ask-1 == buy NO at 100-(ask-1)
            sell_yes_at = ask - 1
            no_price = 100 - sell_yes_at
            if sell_yes_at > bid and 1 <= no_price <= 99 and (
                sell_yes_at - fair_c > thr
            ):
                return OrderIntent(
                    ticker=ticker,
                    side="no",
                    action="buy",
                    limit_price_cents=no_price,
                    size=self.request_size,
                    tif="rest",
                    p_hat=fair,
                    rationale=f"{phase}: fair {fair_c:.1f}c < rest yes {sell_yes_at}c - {thr:.0f}c",
                )
        return None


class WeatherIntradayStrategy(WeatherDayAheadStrategy):
    """4 PM variant: price the remaining session off max-so-far + rise PMF.

    max16: {(series_ticker, target_date): running max °F through ~16:00
    local} — in backtest reconstructed from ACIS hourly obs (public in
    real time); live it is the CLI intermediate report's max-so-far.
    rise_pmf: per-series PMFs fit on pre-window history.
    """

    def __init__(
        self,
        strikes: dict[str, Strike],
        models: dict[str, CityModel],
        max16: dict[tuple[str, str], float],
        rise_pmf: dict[str, dict[int, float]],
        entry_hour: float = 16.5,
        last_entry_hour: float = 22.0,
        **kw,
    ):
        super().__init__(strikes, models, **kw)
        self.max16 = max16
        self.rise_pmf = rise_pmf
        self.entry_hour = entry_hour
        self.last_entry_hour = last_entry_hour

    def _phase(self, t: int, tz: str, target_date: str) -> str | None:
        local = dt.datetime.fromtimestamp(t, tz=ZoneInfo(tz))
        if local.date().isoformat() != target_date:
            return None
        h = local.hour + local.minute / 60.0
        if self.entry_hour <= h < self.last_entry_hour:
            return "pm4"
        return None

    def fair_prob(self, strike: Strike, phase: str) -> float | None:
        if phase != "pm4":
            return None
        s = self.max16.get((strike.series_ticker, strike.target_date))
        pmf = self.rise_pmf.get(strike.series_ticker)
        if s is None or pmf is None:
            return None
        return strike_prob_from_rise_pmf(
            s, pmf, strike.strike_type, strike.floor_strike, strike.cap_strike
        )

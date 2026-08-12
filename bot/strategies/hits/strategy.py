"""The hits trade rule + its two nulls, as SPEC §2 strategies.

Trade rule (frozen up front, bot/forecaster/hits.py):
    enter (maker-first, rest until cancel, hold to resolution) iff
      |forecast − market mid| >= GATE_PTS (15 points)   AND
      payoff if the forecast is right >= MIN_PAYOFF (3x):
        (100 − entry_price) / entry_price >= 3  ⇔  entry <= 25c
    on the side the forecast favors. Because forecasts are made only on
    markets priced at an extreme, a firing trade is by construction a fade
    of the extreme: buying the cheap side.

Nulls (SPEC §7 hits-based clause + research/futuresearch.md §4.3):

- :class:`MarketAsForecastStrategy` — the SAME rule with the market's own
  contemporaneous mid as the forecast. |mid − mid| = 0 < 15, so it can
  never fire; it exists to prove the gate does the work, and its engine run
  must report 0 trades.
- :class:`AlwaysFadeStrategy` — the FLB null: fires on EVERY candidate,
  no forecast at all: buy the cheap side of every extreme with the same
  maker-first entry, payoff bound and sizing (p_hat = the minimum-edge
  claim, price ± GATE). If the selective forecaster's P&L profile matches
  this, the "forecasting skill" is longshot-bias harvesting wearing a hat.
"""
from __future__ import annotations

from dataclasses import dataclass

from bot.backtest.types import MarketView, OrderIntent, Portfolio
from bot.forecaster.hits import GATE_PTS, MIN_PAYOFF

DAY = 86400


@dataclass(frozen=True)
class HitsForecast:
    ticker: str
    p_yes: float                # aggregated pipeline output, [0,1]
    asof_ts: int                # D — the PIT anchor the forecast was made at
    cost_cents: int             # full pipeline + amortized screen cost


def payoff_multiple(entry_price_cents: float) -> float:
    """Gross payoff multiple of a binary bought at entry_price (cents)."""
    if entry_price_cents <= 0:
        return float("inf")
    return (100.0 - entry_price_cents) / entry_price_cents


class HitsStrategy:
    """Selective hits strategy over precomputed extreme-market forecasts.

    From the first decision point t >= asof (forecast usable, PIT-safe) it
    checks the rule each tick until it quotes once; one episode per market,
    resting maker order, hold to resolution. Inference cost is charged at
    the first usable decision point whether or not a trade fires — research
    spend on a no-trade is still spend.
    """

    decision_interval_s = DAY
    price_move_trigger_cents: int | None = None
    candle_period_s = 3600
    last_inference_cost_cents = 0

    def __init__(
        self,
        forecasts: dict[str, HitsForecast],
        gate_pts: float = GATE_PTS,
        min_payoff: float = MIN_PAYOFF,
        size: int = 1000,
    ):
        self.forecasts = forecasts
        self.gate_pts = gate_pts
        self.min_payoff = min_payoff
        self.size = size
        self._charged: set[str] = set()
        self._quoted: set[str] = set()

    def _p_hat(self, view: MarketView) -> float | None:
        fc = self.forecasts.get(view.market.ticker)
        return None if fc is None else fc.p_yes

    def on_decision_point(
        self, view: MarketView, portfolio: Portfolio
    ) -> list[OrderIntent]:
        self.last_inference_cost_cents = 0
        tkr = view.market.ticker
        fc = self.forecasts.get(tkr)
        if fc is None or view.t < fc.asof_ts:
            return []
        if tkr not in self._charged:
            self.last_inference_cost_cents = fc.cost_cents
            self._charged.add(tkr)
        if tkr in self._quoted:
            return []
        pos = portfolio.position(tkr)
        if pos is not None and pos.qty != 0:
            return []
        mid = view.mid_cents
        yb, ya = view.best_bid, view.best_ask
        if mid is None or yb is None or ya is None:
            return []
        p_hat = self._p_hat(view)
        if p_hat is None:
            return []
        edge_pts = p_hat * 100.0 - mid
        if abs(edge_pts) < self.gate_pts:
            return []
        if edge_pts > 0:
            side, price = "yes", max(1, yb)
        else:
            side, price = "no", max(1, 100 - ya)
        if price > 99:
            return []
        if payoff_multiple(price) < self.min_payoff:
            return []          # asymmetry gone — not our trade class
        self._quoted.add(tkr)
        return [
            OrderIntent(
                ticker=tkr,
                side=side,  # type: ignore[arg-type]
                action="buy",
                limit_price_cents=price,
                size=self.size,
                tif="rest",
                p_hat=p_hat,
                rationale=(
                    f"hits: p={p_hat:.2f} vs mid={mid:.1f}c "
                    f"edge={edge_pts:+.1f}pts payoff={payoff_multiple(price):.1f}x"
                ),
            )
        ]


class MarketAsForecastStrategy(HitsStrategy):
    """Null 1: forecast == the market's own contemporaneous mid.

    The rule's gate is |p_hat*100 − mid| >= 15; with p_hat = mid/100 the
    edge is identically zero, so this strategy NEVER fires. Its run is the
    proof that all trading below comes from forecast-vs-price disagreement,
    not from the entry mechanics. Charges no inference.
    """

    def __init__(self, tickers: dict[str, int], **kw):
        # tickers: ticker -> asof_ts (same anchors as the real strategy)
        forecasts = {
            t: HitsForecast(ticker=t, p_yes=0.0, asof_ts=ts, cost_cents=0)
            for t, ts in tickers.items()
        }
        super().__init__(forecasts, **kw)

    def _p_hat(self, view: MarketView) -> float | None:
        mid = view.mid_cents
        return None if mid is None else mid / 100.0


class AlwaysFadeStrategy(HitsStrategy):
    """Null 2 (the FLB null): buy the cheap side of EVERY candidate.

    No forecast: p_hat is the minimum claim that clears the gate
    (mid ± GATE_PTS, clamped), so sizing matches a minimum-conviction hits
    trade. Direction comes from the candidate screen's cheap side, decided
    from the live mid at the decision point (same information the real
    strategy sees). Charges no inference.
    """

    def __init__(self, candidates: dict[str, int], **kw):
        # candidates: ticker -> decision_ts (D)
        forecasts = {
            t: HitsForecast(ticker=t, p_yes=0.0, asof_ts=ts, cost_cents=0)
            for t, ts in candidates.items()
        }
        super().__init__(forecasts, **kw)

    def _p_hat(self, view: MarketView) -> float | None:
        mid = view.mid_cents
        if mid is None:
            return None
        if mid <= 50.0:
            # cheap side is YES: claim it is GATE_PTS underpriced
            return min(0.97, (mid + self.gate_pts) / 100.0)
        return max(0.03, (mid - self.gate_pts) / 100.0)

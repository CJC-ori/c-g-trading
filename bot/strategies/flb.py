"""P-1 favorite-longshot-bias (FLB) harvesting strategies (SYNTHESIS.md §1).

Three price-only variants, per the GWU working paper 2026-001 (Burgi-Deng-
Whelan, "Makers or Takers") and research/systematic-edges.md §1-§4:

- R1Taker   ("R1-taker"):   any side priced 70-97c with <=10 days to close
  -> buy at the ask (taker), hold to resolution.
- R2Maker   ("R2-maker"):   rest a bid on the >=50c side at best-bid (or
  last trade - 1c when no bid is quoted), size <= 20-25% of trailing 1h
  taker volume, reprice hourly, stand down in the final 6h.
- R5Endgame ("R5-endgame"): in the final 6/12/24h, maker-buy the side
  priced 90-98c; the win-rate-vs-price curve by category is produced by
  bot/strategies/flb_analysis.py from the engine's fill log.

Honest p_hat
============
Every intent carries p_hat = the price-implied probability adjusted by the
documented bias estimate, so Brier/calibration metrics are meaningful and
the harness's fee-aware fractional-Kelly sizing sees the same belief we
claim in the analysis. The adjustment is multiplicative on the *bought
side's* price:

    p_side = clip(side_price/100 * (1 + bias_frac), 0.005, 0.995)
    p_hat  = p_side              (buying YES)
    p_hat  = 1 - p_side          (buying NO; p_hat is always P(resolve YES))

Documented bias_frac choices (frozen 2026-08-11, before any test-set look;
see reports/flb/ANALYSIS.md "Frozen parameters"):

- R1 taker  bias_frac = 0.03. GWU: statistically significant *positive
  post-fee* taker returns above 70c, magnitude +1-3%/episode. We take the
  top of the documented range deliberately: the harness Kelly layer refuses
  any trade whose stated edge is below the (per-contract-ceiled) fee, and
  at bias 0.02 that would silently censor the 70-84c half of the band the
  rule is supposed to test. At 0.03 the whole band trades with size
  proportional to stated edge, and any miscalibration shows up honestly in
  the Brier/calibration output instead of via universe self-selection.
- R2 maker  bias_frac = 0.026. GWU headline: makers buying >=50c contracts
  earn +2.6% per episode after fees (SD 33%).
- R5 endgame bias_frac = 0.013. Half the maker figure: GWU shows maker
  returns *deteriorate* at closing prices, so we deliberately claim only
  half the mid-life maker edge in the endgame window. The variant exists
  to measure the actual win-rate-vs-price curve, not to assume it.

Cancel/replace (2026-08-12): the harness now supports replace intents
(OrderIntent.replace), so "reprice hourly" is implemented as designed: when
the computed quote moves, the maker variants emit a replace intent that
cancels the prior resting order and joins the book (and the simulated
maker queue) afresh at the new price. `use_replace=False` restores the old
stale-ladder behavior ("place a new resting order when the quote moves;
prior orders stay live until fill or market close") for before/after
comparisons — see reports/flb/QUEUE_IMPACT.md.
"""
from __future__ import annotations

from bot.backtest.types import MarketView, OrderIntent, Portfolio, Side

HOUR = 3600
DAY = 86400

# Frozen bias estimates (see module docstring).
R1_BIAS_FRAC = 0.03
R2_BIAS_FRAC = 0.026
R5_BIAS_FRAC = 0.013


def biased_p_hat(side: Side, side_price_cents: int, bias_frac: float) -> float:
    """Price-implied probability of the *bought side*, bias-adjusted,
    returned as P(market resolves YES) as the harness requires."""
    p_side = side_price_cents / 100.0 * (1.0 + bias_frac)
    p_side = min(0.995, max(0.005, p_side))
    return p_side if side == "yes" else 1.0 - p_side


def _holding(portfolio: Portfolio, ticker: str) -> bool:
    pos = portfolio.position(ticker)
    return pos is not None and pos.qty != 0


def _pure_cancel(ticker: str, side: Side, limit: int, tag: str) -> OrderIntent:
    """size=0 replace intent: cancels this strategy's resting orders in
    `ticker` on `side`'s buy direction without placing a new order (the
    engine treats replace+size=0 as a pure cancel; see OrderIntent docs)."""
    return OrderIntent(
        ticker=ticker,
        side=side,
        action="buy",
        limit_price_cents=limit,
        size=0,
        tif="rest",
        p_hat=None,
        rationale=f"{tag} cancel stale {side}@{limit}c quote",
        replace=True,
    )


class _FLBBase:
    """Shared cadence/attrs: hourly decisions on hourly candles, zero
    inference cost (price-only strategies)."""

    decision_interval_s = HOUR
    price_move_trigger_cents: int | None = None
    candle_period_s = HOUR
    last_inference_cost_cents = 0

    def __init__(self, categories: tuple[str, ...] | None = None):
        # None = all categories; otherwise exact-match filter on
        # MarketInfo.category (used for category ablations).
        self.categories = tuple(categories) if categories is not None else None

    def _category_ok(self, view: MarketView) -> bool:
        return self.categories is None or view.market.category in self.categories


class R1Taker(_FLBBase):
    """R1-taker: buy the 70-97c side at the ask, <=10 days to close, hold.

    Parameters
    ----------
    band : (lo, hi) side-price band in cents, inclusive (default 70-97).
    days_window : max days to close (default 10, per the GWU sample).
    categories : optional category filter for ablations.
    size : requested contracts per intent (the harness Kelly/cap layer
        clamps this down; requesting big and letting the clamp bite keeps
        sizing logic in one place, per SPEC §5).
    """

    def __init__(
        self,
        band: tuple[int, int] = (70, 97),
        days_window: float = 10.0,
        categories: tuple[str, ...] | None = None,
        size: int = 1000,
        bias_frac: float = R1_BIAS_FRAC,
    ):
        super().__init__(categories)
        self.band = band
        self.days_window = days_window
        self.size = size
        self.bias_frac = bias_frac

    def on_decision_point(
        self, view: MarketView, portfolio: Portfolio
    ) -> list[OrderIntent]:
        if not self._category_ok(view):
            return []
        ttc = view.market.close_ts - view.t
        if ttc <= 0 or ttc > self.days_window * DAY:
            return []
        if _holding(portfolio, view.market.ticker):
            return []  # one episode per market, hold to resolution
        lo, hi = self.band
        yb, ya = view.best_bid, view.best_ask
        candidates: list[tuple[int, Side]] = []
        # YES side costs the yes ask; NO side costs 100 - yes bid.
        if ya is not None and lo <= ya <= hi:
            candidates.append((ya, "yes"))
        if yb is not None and lo <= 100 - yb <= hi:
            candidates.append((100 - yb, "no"))
        if not candidates:
            return []
        # If both sides somehow qualify (crossed/degenerate book) take the
        # higher-priced side - the stronger favorite.
        price, side = max(candidates)
        if not (1 <= price <= 99):
            return []
        return [
            OrderIntent(
                ticker=view.market.ticker,
                side=side,
                action="buy",
                limit_price_cents=price,
                size=self.size,
                tif="ioc",  # cross now or die this tick; retry next hour
                p_hat=biased_p_hat(side, price, self.bias_frac),
                rationale=f"R1-taker {side}@{price}c ttc={ttc / DAY:.1f}d",
            )
        ]


class R2Maker(_FLBBase):
    """R2-maker: rest a bid on the >=50c side, sized off trailing volume.

    At each hourly decision (within `days_window` of close, but not in the
    final `standdown_s`): pick the side whose price is >=50c (by mid),
    quote at that side's best bid - falling back to (last trade - 1c) when
    no bid is quoted - and size at `vol_frac` of the trailing 1h volume.
    A new order is only emitted when the computed quote moved (harness has
    no cancel/replace; see module docstring) and while we hold no
    position (one episode per market, hold to resolution).
    """

    def __init__(
        self,
        min_side_price: int = 50,
        vol_frac: float = 0.25,
        standdown_s: int = 6 * HOUR,
        days_window: float = 10.0,
        categories: tuple[str, ...] | None = None,
        bias_frac: float = R2_BIAS_FRAC,
        use_replace: bool = True,
    ):
        super().__init__(categories)
        self.min_side_price = min_side_price
        self.vol_frac = vol_frac
        self.standdown_s = standdown_s
        self.days_window = days_window
        self.bias_frac = bias_frac
        self.use_replace = use_replace
        self._last_quote: dict[str, tuple[Side, int]] = {}

    def _trailing_volume(self, view: MarketView) -> int:
        series = view.candles(self.candle_period_s)
        return series[-1].volume if series else 0

    def on_decision_point(
        self, view: MarketView, portfolio: Portfolio
    ) -> list[OrderIntent]:
        if not self._category_ok(view):
            return []
        ticker = view.market.ticker
        ttc = view.market.close_ts - view.t
        if ttc <= self.standdown_s or ttc > self.days_window * DAY:
            return []  # stand down near close (GWU: maker returns die there)
        if _holding(portfolio, ticker):
            return []
        mid = view.mid_cents
        if mid is None:
            return []
        yb, ya = view.best_bid, view.best_ask
        last = view.last_price
        if mid >= 50:
            side: Side = "yes"
            limit = yb if yb is not None else (last - 1 if last is not None else None)
            cross_at = ya  # buying YES crosses at the yes ask
        else:
            side = "no"
            # NO best bid = 100 - yes ask; NO ask = 100 - yes bid.
            limit = 100 - ya if ya is not None else (
                100 - last - 1 if last is not None else None
            )
            cross_at = 100 - yb if yb is not None else None
        if limit is None or not (1 <= limit <= 99):
            return []
        if limit < self.min_side_price:
            return []  # only the >=50c side qualifies for the GWU edge
        if cross_at is not None and limit >= cross_at:
            return []  # would cross => taker; this variant is maker-only
        size = int(self.vol_frac * self._trailing_volume(view))
        if size < 1:
            return []  # no recent taker flow to fill against
        prev = self._last_quote.get(ticker)
        if prev == (side, limit):
            return []  # same quote already resting; keep queue position
        self._last_quote[ticker] = (side, limit)
        intents: list[OrderIntent] = []
        if self.use_replace and prev is not None and prev[0] != side:
            # Side flipped across 50c: replace only cancels same-direction
            # orders, so explicitly cancel the old side's resting quote.
            intents.append(_pure_cancel(ticker, prev[0], prev[1], "R2-maker"))
        intents.append(
            OrderIntent(
                ticker=ticker,
                side=side,
                action="buy",
                limit_price_cents=limit,
                size=size,
                tif="rest",
                p_hat=biased_p_hat(side, limit, self.bias_frac),
                rationale=f"R2-maker rest {side}@{limit}c vol1h*{self.vol_frac:g}",
                replace=self.use_replace and prev is not None,
            )
        )
        return intents


class R5Endgame(_FLBBase):
    """R5-endgame: in the final window, maker-buy the side priced 90-98c.

    Joins the best bid of whichever side's *bid* is inside the band
    (qualifying on the join price we would actually rest at). One episode
    per market, hold to resolution. Window is a parameter so the runner
    can sweep 6/12/24h. Repricing uses cancel/replace like R2 (see module
    docstring); `use_replace=False` restores the stale-ladder behavior.
    """

    def __init__(
        self,
        window_s: int = 6 * HOUR,
        band: tuple[int, int] = (90, 98),
        vol_frac: float = 0.25,
        categories: tuple[str, ...] | None = None,
        bias_frac: float = R5_BIAS_FRAC,
        use_replace: bool = True,
    ):
        super().__init__(categories)
        self.window_s = window_s
        self.band = band
        self.vol_frac = vol_frac
        self.bias_frac = bias_frac
        self.use_replace = use_replace
        self._last_quote: dict[str, tuple[Side, int]] = {}

    def _trailing_volume(self, view: MarketView) -> int:
        series = view.candles(self.candle_period_s)
        return series[-1].volume if series else 0

    def on_decision_point(
        self, view: MarketView, portfolio: Portfolio
    ) -> list[OrderIntent]:
        if not self._category_ok(view):
            return []
        ticker = view.market.ticker
        ttc = view.market.close_ts - view.t
        if ttc <= 0 or ttc > self.window_s:
            return []
        if _holding(portfolio, ticker):
            return []
        lo, hi = self.band
        yb, ya = view.best_bid, view.best_ask
        side: Side | None = None
        limit = None
        cross_at = None
        if yb is not None and lo <= yb <= hi:
            side, limit, cross_at = "yes", yb, ya
        elif ya is not None and lo <= 100 - ya <= hi:
            side, limit = "no", 100 - ya
            cross_at = 100 - yb if yb is not None else None
        if side is None or limit is None or not (1 <= limit <= 99):
            return []
        if cross_at is not None and limit >= cross_at:
            return []  # maker-only
        size = int(self.vol_frac * self._trailing_volume(view))
        if size < 1:
            return []
        prev = self._last_quote.get(ticker)
        if prev == (side, limit):
            return []  # same quote already resting; keep queue position
        self._last_quote[ticker] = (side, limit)
        intents: list[OrderIntent] = []
        if self.use_replace and prev is not None and prev[0] != side:
            # Side flipped: replace only cancels same-direction orders, so
            # explicitly cancel the old side's resting quote.
            intents.append(_pure_cancel(ticker, prev[0], prev[1], "R5-endgame"))
        intents.append(
            OrderIntent(
                ticker=ticker,
                side=side,
                action="buy",
                limit_price_cents=limit,
                size=size,
                tif="rest",
                p_hat=biased_p_hat(side, limit, self.bias_frac),
                rationale=(
                    f"R5-endgame rest {side}@{limit}c "
                    f"window={self.window_s // HOUR}h"
                ),
                replace=self.use_replace and prev is not None,
            )
        )
        return intents

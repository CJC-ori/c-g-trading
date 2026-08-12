"""Composable Round-2 integration wrappers around the Round-1 champion.

Every wrapper below is an ENTRY FILTER or PORTFOLIO COMBINATOR layered on
the frozen R5-endgame strategy (bot/strategies/flb.R5Endgame). No R5
parameter is re-tuned; the wrappers only decline entries (I-b, I-c) or add
an independent, previously-parked leg (I-e). All filter parameters trace
to pre-registered sources (census/protocol constants), never to P&L
iteration — provenance is documented per class.

PARAMS_RATIONALE (frozen 2026-08-12 BEFORE any Round-2 backtest was run):

- I-b AdverseSwing filter (R5SwingFiltered):
    X = 20c, T = 24h  — the protocol names ">=20c/24h adverse swing"
                        verbatim (TOURNAMENT_PROTOCOL.md Round 2 I-b);
                        this is the swing census's X20_T24 config
                        (bot/strategies/swings/census.py, frozen there
                        before any census run).
    N = 24h           — "entries within N hours after the swing" uses the
                        census's own FILL_WINDOW_H / entry_window_h = 24h
                        (swings/strategy.py FROZEN entry_window_h, frozen
                        before the swings backtest): a swing older than
                        the census's own fill window is a different
                        regime. N is NOT tuned here.
    Detection is point-in-time from the strategy's own MarketView hourly
    candles (census-identical sliding-extreme reference over [tau-T, tau)),
    in the *bought side's* price space: an adverse swing is the side we
    would join falling >= X cents within T hours, at any point in the last
    N hours. Blocked markets get a pure-cancel for any resting quote.

- I-c PanicNight stand-down (R5PanicStandDown):
    Blocked when the market is in an election-like category
    (panic/episodes.py FROZEN categories = Politics, Elections) AND the
    decision time's ET calendar date is a scheduled event night
    (episodes.py KNOWN_EVENT_NIGHTS — a public ex-ante calendar, frozen
    2026-08-12 — union the ET dates of discovered R4 episode windows), OR
    the decision time falls inside a discovered R4 episode window for the
    specific ticker. Blocked markets get a pure-cancel for any resting
    quote. CAVEAT for the Round-3 audit: discovered windows are anchored
    on realized volume (ex-post within a market's life); the calendar
    dates are ex-ante. On the Round-1 train boundary all discovered
    windows post-date split_ts, so only the ex-ante calendar can bind.

- I-e portfolio combinator (CombinedStrategy):
    Merges intents from independent legs with identical cadence
    (decision_interval_s / candle_period_s must match). Legs keep their
    own state; the harness risk layer enforces the SHARED caps (one
    bankroll, 5%/market, 10%/event, 80% total) exactly as SPEC §5 demands
    for a portfolio. Known composition artifact (documented, not fixed:
    fixing it would change the frozen legs): the engine's replace
    semantics cancel ALL of the run's resting same-direction orders in a
    market, so one leg's replace/cancel can cancel the other leg's resting
    quote in the (rare) market both legs trade simultaneously. The runner
    counts and reports these overlap markets.
"""
from __future__ import annotations

from bot.backtest.types import MarketView, OrderIntent, Portfolio
from bot.strategies.flb import R5Endgame, _holding, _pure_cancel

HOUR = 3600

# I-b constants (provenance in module docstring — census X20_T24 + its
# 24h fill window; the protocol text names ">=20c/24h" itself).
SWING_X_CENTS = 20
SWING_T_HOURS = 24
SWING_BLOCK_HOURS = 24


class R5SwingFiltered(R5Endgame):
    """I-b: R5-endgame that declines entries within SWING_BLOCK_HOURS of a
    >= SWING_X_CENTS / SWING_T_HOURS adverse swing (side-space drop) —
    'swings are information' (reports/swings/ANALYSIS.md §1)."""

    def __init__(self, *args, x_cents: int = SWING_X_CENTS,
                 t_hours: int = SWING_T_HOURS,
                 block_hours: int = SWING_BLOCK_HOURS, **kwargs):
        super().__init__(*args, **kwargs)
        self.x_cents = x_cents
        self.t_hours = t_hours
        self.block_hours = block_hours

    # -- census-identical point-in-time swing scan -------------------------
    def _adverse_swing_recent(self, view: MarketView, side: str) -> bool:
        candles = view.candles(3600)
        if not candles:
            return False
        horizon = view.t - (self.block_hours + self.t_hours) * HOUR
        recent = [c for c in candles if c.end_ts > horizon]
        if len(recent) < 2:
            return False
        # side-space closes: price of the contract we would buy
        pts = [
            (c.end_ts, c.close if side == "yes" else 100 - c.close)
            for c in recent
        ]
        block_lo = view.t - self.block_hours * HOUR
        for j in range(1, len(pts)):
            tj, sj = pts[j]
            if tj <= block_lo:
                continue
            ref = None
            for i in range(j - 1, -1, -1):
                ti, si = pts[i]
                if ti < tj - self.t_hours * HOUR:
                    break
                if ref is None or si > ref:
                    ref = si
            if ref is not None and ref - sj >= self.x_cents:
                return True
        return False

    def _intended_side(self, view: MarketView) -> str | None:
        """The side R5 would join right now (same logic as R5Endgame)."""
        lo, hi = self.band
        yb, ya = view.best_bid, view.best_ask
        if yb is not None and lo <= yb <= hi:
            return "yes"
        if ya is not None and lo <= 100 - ya <= hi:
            return "no"
        return None

    def on_decision_point(
        self, view: MarketView, portfolio: Portfolio
    ) -> list[OrderIntent]:
        ticker = view.market.ticker
        if not _holding(portfolio, ticker):
            side = self._intended_side(view)
            if side is not None and self._adverse_swing_recent(view, side):
                prev = self._last_quote.pop(ticker, None)
                if prev is not None:
                    return [_pure_cancel(ticker, prev[0], prev[1],
                                         "I-b swing-filter")]
                return []
        return super().on_decision_point(view, portfolio)


class R5PanicStandDown(R5Endgame):
    """I-c: R5-endgame that stands down in election-like categories on
    scheduled event nights / inside discovered R4 panic windows."""

    BLOCK_CATEGORIES = ("Politics", "Elections")

    def __init__(self, *args, night_dates: frozenset[str] = frozenset(),
                 windows: dict[str, tuple[int, int]] | None = None,
                 et_date_fn=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.night_dates = night_dates
        self.windows = windows or {}
        if et_date_fn is None:
            from bot.strategies.panic.episodes import et_date as et_date_fn
        self._et_date = et_date_fn

    def _blocked(self, view: MarketView) -> bool:
        w = self.windows.get(view.market.ticker)
        if w is not None and w[0] <= view.t <= w[1]:
            return True
        return (
            view.market.category in self.BLOCK_CATEGORIES
            and self._et_date(view.t) in self.night_dates
        )

    def on_decision_point(
        self, view: MarketView, portfolio: Portfolio
    ) -> list[OrderIntent]:
        ticker = view.market.ticker
        if not _holding(portfolio, ticker) and self._blocked(view):
            prev = self._last_quote.pop(ticker, None)
            if prev is not None:
                return [_pure_cancel(ticker, prev[0], prev[1],
                                     "I-c panic-standdown")]
            return []
        return super().on_decision_point(view, portfolio)


class CombinedStrategy:
    """I-d/I-e: merge intents from independent legs (same cadence).

    The harness enforces the shared risk caps; legs never see each other.
    """

    price_move_trigger_cents = None

    def __init__(self, legs: list):
        assert legs, "need at least one leg"
        periods = {getattr(l, "candle_period_s", 3600) for l in legs}
        assert len(periods) == 1, f"legs disagree on candle period: {periods}"
        self.candle_period_s = periods.pop()
        self.decision_interval_s = min(
            getattr(l, "decision_interval_s", 3600) for l in legs
        )
        self.legs = legs
        self.last_inference_cost_cents = 0

    def on_decision_point(
        self, view: MarketView, portfolio: Portfolio
    ) -> list[OrderIntent]:
        out: list[OrderIntent] = []
        cost = 0
        for leg in self.legs:
            out.extend(leg.on_decision_point(view, portfolio))
            cost += getattr(leg, "last_inference_cost_cents", 0)
        self.last_inference_cost_cents = cost
        return out

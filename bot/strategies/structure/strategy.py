"""Engine-facing strategies for the structure scanner (SPEC.md §2).

Four strategies, all price-only (``last_inference_cost_cents = 0``):

``NoSetSweep``      R3 executable NO-set arb, all-or-nothing, depth-capped.
``BracketFade``     R3's higher-capacity clause: fade the one cheap bracket
                    that supplies most of an event's overround.
``ReferenceDivergence``
                    R7 and the consistency monitor share one body: trade
                    the Kalshi market toward an *external reference price*
                    when the gap exceeds a gate and has persisted, exit
                    when the gap closes or at resolution. The reference is
                    injected as a callable so the same tested code serves
                    the Polymarket mid (R7) and the ladder-implied band
                    (cross-instrument consistency).
``DirectionShuffled``
                    The no-signal control: identical trigger times, sizes
                    and costs, direction drawn from a seeded RNG. It
                    isolates "did the reference price tell us which way to
                    go" from "does trading this market at these moments
                    make money".

Two structural facts about the harness constrain the design and are worth
stating because they shape the R3 result:

* a strategy is called **per market**, and the engine rejects an intent
  whose ticker isn't the market it was called for, so a multi-leg set can
  only be assembled leg by leg. ``NoSetSweep`` therefore keeps a shared
  quote cache across its per-leg calls and accepts the resulting leg risk
  (a set arb executed sequentially is exposed to partial fills — this is
  real, not an artifact).
* the risk layer sizes every intent by fractional Kelly against the
  strategy's ``p_hat``. For a set arb the honest per-leg probability is the
  market's own quote vector *renormalised to sum to 1* — which is exactly
  what makes each leg individually +EV when the raw quotes are incoherent.
"""
from __future__ import annotations

import random
from typing import Callable

from bot.backtest.types import MarketView, OrderIntent, Portfolio
from bot.strategies.structure import overround as ov


class _Base:
    decision_interval_s: int | None = 3600
    price_move_trigger_cents: int | None = None
    candle_period_s: int = 3600
    last_inference_cost_cents: int = 0


# ---------------------------------------------------------------------------
# R3
# ---------------------------------------------------------------------------

class NoSetSweep(_Base):
    """Buy NO on every leg of a mutually exclusive event when Σ yes_bid
    exceeds 100¢ by more than fees + buffer.

    Riskless at the set level (payoff >= (n−1)·100 whenever at most one leg
    can resolve YES), but executed leg by leg, so partial fills leave a
    partial set. Emits IOC (taker) NO buys — the whole point is that the
    quote is about to disappear.
    """

    def __init__(
        self,
        event_legs: dict[str, list[str]],
        buffer_cc: int = 100,
        size: int = 200,
        fee_multiplier: float = 1.0,
    ):
        self.legs_of_event = event_legs
        self.event_of = {t: e for e, ts in event_legs.items() for t in ts}
        self.buffer_cc = buffer_cc
        self.size = size
        self.fee_multiplier = fee_multiplier
        self.quotes: dict[str, tuple[int, int, int, float]] = {}   # tk -> (ts,bid,ask,vol)
        self.fired: set[tuple[str, int]] = set()

    def _snapshot(self, view: MarketView) -> None:
        b, a = view.best_bid, view.best_ask
        if b is None or a is None:
            return
        series = view.candles(self.candle_period_s)
        vol = series[-1].volume if series else 0
        self.quotes[view.market.ticker] = (view.t, b * 100, a * 100, vol)

    def on_decision_point(self, view: MarketView, portfolio: Portfolio) -> list[OrderIntent]:
        self._snapshot(view)
        tk = view.market.ticker
        ev = self.event_of.get(tk) or view.market.event_ticker
        legs = self.legs_of_event.get(ev, [])
        if len(legs) < 2:
            return []
        quotes = [self.quotes.get(l) for l in legs]
        if any(q is None for q in quotes):
            return []
        # Only act on quotes observed at this same decision instant or the
        # immediately preceding one (no stale-quote phantom arbs).
        if any(view.t - q[0] > self.candle_period_s for q in quotes):
            return []
        lq = [
            ov.LegQuote(l, q[1], q[2], q[3])
            for l, q in zip(legs, quotes)
        ]
        sig = ov.no_set_signal(lq, self.fee_multiplier, self.buffer_cc)
        if not sig.fires:
            return []
        pos = portfolio.position(tk)
        if pos is not None and pos.qty != 0:
            return []
        me = next(q for l, q in zip(legs, quotes) if l == tk)
        # Coherent probability vector implied by the (incoherent) quotes.
        bid_sum = sum(q[1] for q in quotes)
        p_hat = max(0.001, min(0.999, me[1] / bid_sum))
        no_price = 100 - me[1] // 100
        if not (1 <= no_price <= 99):
            return []
        return [
            OrderIntent(
                ticker=tk,
                side="no",
                action="buy",
                limit_price_cents=no_price,
                size=self.size,
                tif="ioc",
                p_hat=p_hat,
                rationale=(
                    f"R3 NO-set: Σbid={bid_sum / 100:.1f}c over {len(legs)} legs, "
                    f"net {sig.net_edge_cents:.2f}c/set"
                ),
            )
        ]


class BracketFade(_Base):
    """R3's second clause: when one <=10¢ bracket supplies >= half of an
    event's overround, buy NO on that bracket alone and hold to
    resolution. Directional (not riskless) but single-leg, so capacity is
    the bracket's own book rather than the thinnest leg of the set."""

    def __init__(
        self,
        event_legs: dict[str, list[str]],
        max_price_cents: int = 10,
        min_share: float = 0.5,
        size: int = 200,
        maker: bool = True,
    ):
        self.legs_of_event = event_legs
        self.event_of = {t: e for e, ts in event_legs.items() for t in ts}
        self.max_price_cc = max_price_cents * 100
        self.min_share = min_share
        self.size = size
        self.maker = maker
        self.quotes: dict[str, tuple[int, int, int, float]] = {}

    def on_decision_point(self, view: MarketView, portfolio: Portfolio) -> list[OrderIntent]:
        b, a = view.best_bid, view.best_ask
        if b is not None and a is not None:
            series = view.candles(self.candle_period_s)
            self.quotes[view.market.ticker] = (
                view.t, b * 100, a * 100, series[-1].volume if series else 0
            )
        tk = view.market.ticker
        ev = self.event_of.get(tk) or view.market.event_ticker
        legs = self.legs_of_event.get(ev, [])
        quotes = [self.quotes.get(l) for l in legs]
        if len(legs) < 2 or any(q is None for q in quotes):
            return []
        if any(view.t - q[0] > self.candle_period_s for q in quotes):
            return []
        lq = [ov.LegQuote(l, q[1], q[2], q[3]) for l, q in zip(legs, quotes)]
        fade = ov.dominant_cheap_bracket(lq, self.max_price_cc, self.min_share)
        if fade is None or fade.ticker != tk:
            return []
        pos = portfolio.position(tk)
        if pos is not None and pos.qty != 0:
            return []
        bid_cc = fade.yes_bid_cc
        # Fair value for this leg = its coherent share of the quote vector.
        bid_sum = sum(q[1] for q in quotes)
        p_hat = max(0.001, min(0.999, bid_cc / bid_sum))
        limit = 100 - (bid_cc // 100) - (0 if not self.maker else 0)
        limit = max(1, min(99, limit))
        return [
            OrderIntent(
                ticker=tk,
                side="no",
                action="buy",
                limit_price_cents=limit,
                size=self.size,
                tif="ioc" if not self.maker else "rest",
                p_hat=p_hat,
                rationale=(
                    f"R3 bracket fade: {fade.share:.0%} of a "
                    f"{(bid_sum - ov.CONTRACT_CC) / 100:.1f}c overround"
                ),
            )
        ]


# ---------------------------------------------------------------------------
# R7 / consistency
# ---------------------------------------------------------------------------

ReferenceFn = Callable[[str, int], float | None]   # (ticker, t) -> cents


class ReferenceDivergence(_Base):
    """Trade a Kalshi market toward an external reference price.

    Rule (SYNTHESIS R7): when |Kalshi mid − reference| > ``gate_cents``
    **sustained for at least ``persist_s``**, buy the cheap side maker-style
    (rest at the near touch); exit when the gap closes to <
    ``exit_gap_cents`` or at resolution.

    The reference callable must be point-in-time: it is only ever asked for
    a value strictly before ``t``. ``persist_s`` is enforced from the
    strategy's own observations, so a gap that appears and vanishes between
    two decision points never triggers.
    """

    def __init__(
        self,
        reference: ReferenceFn,
        gate_cents: float = 4.0,
        exit_gap_cents: float = 1.0,
        persist_s: int = 600,
        size: int = 200,
        decision_interval_s: int = 60,
        candle_period_s: int = 60,
        maker: bool = True,
        edge_haircut_cents: float = 1.0,
    ):
        self.reference = reference
        self.gate = gate_cents
        self.exit_gap = exit_gap_cents
        self.persist_s = persist_s
        self.size = size
        self.decision_interval_s = decision_interval_s
        self.candle_period_s = candle_period_s
        self.maker = maker
        self.haircut = edge_haircut_cents
        self.streak: dict[str, tuple[int, int]] = {}   # ticker -> (since_ts, sign)
        self.n_signals = 0

    def _update_streak(self, ticker: str, t: int, gap: float) -> int | None:
        """Returns the seconds the current directional gap has persisted."""
        sign = 1 if gap > 0 else -1
        if abs(gap) <= self.gate:
            self.streak.pop(ticker, None)
            return None
        since, prev_sign = self.streak.get(ticker, (t, sign))
        if prev_sign != sign:
            since = t
        self.streak[ticker] = (since, sign)
        return t - since

    def on_decision_point(self, view: MarketView, portfolio: Portfolio) -> list[OrderIntent]:
        tk = view.market.ticker
        bid, ask = view.best_bid, view.best_ask
        mid = view.mid_cents
        ref = self.reference(tk, view.t)
        if mid is None or ref is None or bid is None or ask is None:
            return []
        gap = ref - mid                      # >0: reference says Kalshi is cheap
        pos = portfolio.position(tk)
        held = pos.qty if pos is not None else 0

        if held != 0:
            # exit when the gap has closed (or flipped past the exit band)
            if abs(gap) < self.exit_gap or (held > 0) != (gap > 0):
                side = "yes" if held > 0 else "no"
                limit = (bid if held > 0 else 100 - ask) if self.maker else (
                    bid if held > 0 else 100 - ask
                )
                limit = max(1, min(99, int(limit)))
                return [
                    OrderIntent(
                        ticker=tk, side=side, action="sell",
                        limit_price_cents=limit, size=abs(held),
                        tif="rest" if self.maker else "ioc",
                        p_hat=max(0.01, min(0.99, ref / 100)),
                        rationale=f"exit: gap {gap:+.1f}c",
                    )
                ]
            return []

        persisted = self._update_streak(tk, view.t, gap)
        if persisted is None or persisted < self.persist_s:
            return []
        self.n_signals += 1
        # Maker-style entry: rest at the near touch on the side we want.
        if gap > 0:                          # buy YES
            limit = max(1, min(99, int(bid)))
            side = "yes"
        else:                                # buy NO (= sell YES)
            limit = max(1, min(99, int(100 - ask)))
            side = "no"
        p_ref = max(0.01, min(0.99, ref / 100))
        # Haircut the stated edge: the reference is a *midpoint* on the other
        # venue (research/polymarket-data.md §2.5), never an executable price.
        p_hat = (
            max(0.01, min(0.99, p_ref - self.haircut / 100))
            if gap > 0
            else max(0.01, min(0.99, p_ref + self.haircut / 100))
        )
        return [
            OrderIntent(
                ticker=tk, side=side, action="buy", limit_price_cents=limit,
                size=self.size, tif="rest" if self.maker else "ioc", p_hat=p_hat,
                rationale=(
                    f"divergence {gap:+.1f}c (ref {ref:.1f} vs mid {mid:.1f}) "
                    f"persisted {persisted}s"
                ),
            )
        ]


class DirectionShuffled(ReferenceDivergence):
    """No-signal control: same triggers, same sizes, same costs, direction
    drawn from a seeded RNG. Any P&L difference vs ``ReferenceDivergence``
    is attributable to the reference price, not to the trading schedule."""

    def __init__(self, *args, seed: int = 20260811, **kwargs):
        super().__init__(*args, **kwargs)
        self.rng = random.Random(seed)

    def on_decision_point(self, view: MarketView, portfolio: Portfolio) -> list[OrderIntent]:
        intents = super().on_decision_point(view, portfolio)
        out = []
        for i in intents:
            if i.action == "sell" or self.rng.random() < 0.5:
                out.append(i)
                continue
            flipped = "no" if i.side == "yes" else "yes"
            bid, ask = view.best_bid, view.best_ask
            if bid is None or ask is None:
                continue
            limit = int(bid) if flipped == "yes" else int(100 - ask)
            out.append(
                OrderIntent(
                    ticker=i.ticker, side=flipped, action="buy",
                    limit_price_cents=max(1, min(99, limit)), size=i.size,
                    tif=i.tif, p_hat=1.0 - (i.p_hat or 0.5),
                    rationale="no-signal control (direction shuffled)",
                )
            )
        return out

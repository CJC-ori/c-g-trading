"""SwingFade: resting fade ladders on census-registered swing cells.

PARAMS_RATIONALE — parameters FROZEN 2026-08-12 from the swing census's
TRAIN-WINDOW STRUCTURE (reports/swings/train_cells.json, episodes with
ts0 < t_split = 2026-03-20, the first 60% of episodes by time), BEFORE any
engine backtest of this strategy was run (SPEC.md par.8). Nothing below
was tuned on strategy P&L. Provenance of every number:

- CELLS (census shortlist rule, registered in census.py before results):
  the three tail cells that are train-EV-positive at maker depth with
  adverse selection under the 35% kill line:
    pol_panic_dip:  Politics/Elections, down-swing, ref >= 90c
                    (train n=621, settled-with-move 11.3%, d5 EV +7.55c)
    pol_spike_fade: Politics/Elections, up-swing, ref <= 15c
                    (train n=969, swm 29.8%, d5 EV +2.90c)
    eco_spike_fade: Economics, up-swing, ref <= 15c
                    (train n=397, swm 12.1%, d5 EV +7.17c)
  Explicitly EXCLUDED by the same rule: all Climate-and-Weather tails
  (weather spike_fade d5 EV -2.25c, weather panic_dip -6.81c on train:
  weather swings are mostly forecast information, not panic) and all
  mid-range cells (settled-with-move 66% — swings from mid prices ARE
  information; kill line 35%).
- TRIGGER: X=10c move within T=24h of hourly marks (the census config the
  cells were measured at). ref = sliding window extreme over [t-24h, t);
  honesty gates exactly as census: >= $500 traded notional in the window,
  two-sided book (0 < bid, ask < 100) at ref and trigger candles.
- ENTRY: two resting maker rungs at trigger -/+ {5, 10}c (the census's
  measured fade depths; d0 taker entry is EV-negative in EVERY cell —
  paying the spread to chase a swing loses; the edge is in being resting).
  Per-market budget = harness per-market cap (5% of bankroll), split in
  equal premium halves across rungs. Rungs canceled 24h after trigger
  (census FILL_WINDOW_H): a rung resting for weeks is a different trade.
- EXIT: one replace-managed resting order at the 50% retrace of the
  trigger move (census reversion target). No stop-loss — binary market,
  sized for total loss (same doctrine as panic R4). Position rides to
  settlement if the target never trades.
- EPISODE / RE-ARM: one episode at a time per market; a new episode may
  arm only when flat and the prior episode is closed (target crossed or
  t0 + 72h passed) — mirrors the census cooldown, so strategy episode
  counts are comparable to census episode counts.
- p_hat = ref/100 (capped [0.01, 0.99]): the fade thesis is that the
  pre-swing consensus is right and the market reverts toward ref.
- arm_before (run discipline, not a tuned parameter): triggers at
  t >= arm_before are ignored so a train run can never initiate a
  position inside the held-out window.

Known deviations from census accounting (both stated before running):
- the census assumed the exit only fills if the target trades within 72h,
  else ride to settlement; the strategy leaves the exit order resting
  until market close (an exit that fills at the target after 72h is the
  same doctrine, and the engine's queue model prices its feasibility);
- census fades are per-contract and gross of fees; the engine charges the
  exact per-series fee schedule (Economics CPI/Fed series charge maker
  fees; Politics/Elections charge none).
"""
from __future__ import annotations

from dataclasses import dataclass

from bot.backtest.types import Candle, MarketView, OrderIntent, Portfolio

HOUR = 3600

FROZEN = dict(
    x_cents=10,
    t_hours=24,
    rung_depths_cents=(5, 10),
    budget_frac=0.05,            # = harness per-market cap, split across rungs
    min_window_notional_cents=50_000,   # $500
    entry_window_h=24,           # cancel unfilled rungs after this
    episode_max_h=72,            # episode closes (re-arm allowed) after this
    panic_dip_min_ref_c=90,
    spike_fade_max_ref_c=15,
    p_hat_lo=0.01,
    p_hat_hi=0.99,
)

#: cell -> (categories, direction)
CELLS = {
    "pol_panic_dip": (("Politics", "Elections"), "down"),
    "pol_spike_fade": (("Politics", "Elections"), "up"),
    "eco_spike_fade": (("Economics",), "up"),
}


@dataclass(slots=True)
class _EpisodeState:
    t0: int
    ref_c: int
    trig_c: int
    direction: str          # "down" | "up"
    target_c: int           # 50% retrace, integer (rounded toward entry side)
    rungs_canceled: bool = False
    exit_qty: int = 0


class SwingFade:
    """Census-cell swing fader (see module docstring for the frozen spec)."""

    decision_interval_s = 3600
    price_move_trigger_cents = None
    last_inference_cost_cents = 0   # price-only strategy
    candle_period_s = 3600

    def __init__(self, bankroll_cents: int = 1_000_000,
                 arm_before: int | None = None,
                 cells: dict[str, tuple[tuple[str, ...], str]] | None = None):
        self.bankroll_cents = bankroll_cents
        self.arm_before = arm_before
        self.cells = cells or CELLS
        self.f = FROZEN
        self._eps: dict[str, _EpisodeState] = {}

    # -- census-identical trigger math ---------------------------------------

    def _cell_directions(self, category: str) -> list[str]:
        return [d for (cats, d) in self.cells.values() if category in cats]

    def _detect(self, view: MarketView) -> _EpisodeState | None:
        """Re-derive the census trigger from the point-in-time view."""
        candles = view.candles(3600)
        if not candles:
            return None
        cur = candles[-1]
        if cur.end_ts != view.t:  # stale mark: no fresh hourly candle at t
            return None
        t_lo = view.t - self.f["t_hours"] * HOUR
        window: list[Candle] = []
        for c in reversed(candles[:-1]):
            if c.end_ts < t_lo:
                break
            window.append(c)
        if not window:
            return None
        # liquidity gate: traded notional over [t-24h, t] incl. current
        notional_c = sum(c.volume * c.close for c in window) + cur.volume * cur.close
        if notional_c < self.f["min_window_notional_cents"]:
            return None
        if not _two_sided(cur):
            return None
        for direction in self._cell_directions(view.market.category):
            sign = 1 if direction == "down" else -1
            ref_candle = max(window, key=lambda c: sign * c.close)
            ref = ref_candle.close
            if sign * (ref - cur.close) < self.f["x_cents"]:
                continue
            if not _two_sided(ref_candle):
                continue
            # tail-cell price gate
            if direction == "down" and ref < self.f["panic_dip_min_ref_c"]:
                continue
            if direction == "up" and ref > self.f["spike_fade_max_ref_c"]:
                continue
            move0 = abs(ref - cur.close)
            # target rounded toward the entry side (conservative exit)
            if direction == "down":
                target = ref - (move0 + 1) // 2   # floor of ref - move/2
            else:
                target = ref + (move0 + 1) // 2
            return _EpisodeState(
                t0=view.t, ref_c=ref, trig_c=cur.close,
                direction=direction, target_c=max(1, min(99, target)),
            )
        return None

    # -- order construction ---------------------------------------------------

    def _rung_intents(self, ticker: str, ep: _EpisodeState) -> list[OrderIntent]:
        sign = 1 if ep.direction == "down" else -1
        p_hat = max(self.f["p_hat_lo"],
                    min(self.f["p_hat_hi"], ep.ref_c / 100.0))
        budget_per_rung = int(self.bankroll_cents * self.f["budget_frac"]) // len(
            self.f["rung_depths_cents"]
        )
        out = []
        for d in self.f["rung_depths_cents"]:
            yes_px = ep.trig_c - sign * d
            if not (1 <= yes_px <= 99):
                continue
            if ep.direction == "down":
                side, limit, prem = "yes", yes_px, yes_px
            else:
                side, limit, prem = "no", 100 - yes_px, 100 - yes_px
            size = budget_per_rung // prem
            if size <= 0:
                continue
            out.append(OrderIntent(
                ticker=ticker, side=side, action="buy",
                limit_price_cents=limit, size=size, tif="rest",
                p_hat=p_hat,
                rationale=(f"SwingFade {ep.direction} rung {d}c beyond trigger "
                           f"{ep.trig_c}c (ref {ep.ref_c}c)"),
            ))
        return out

    def _exit_intent(self, ticker: str, ep: _EpisodeState, qty: int) -> OrderIntent:
        if ep.direction == "down":       # long YES, sell at target
            side, limit = "yes", ep.target_c
        else:                            # long NO, sell at NO-space target
            side, limit = "no", 100 - ep.target_c
        return OrderIntent(
            ticker=ticker, side=side, action="sell",
            limit_price_cents=max(1, min(99, limit)), size=qty, tif="rest",
            replace=True,
            rationale=f"SwingFade exit at 50% retrace {ep.target_c}c (yes-space)",
        )

    # -- Strategy protocol -----------------------------------------------------

    def on_decision_point(
        self, view: MarketView, portfolio: Portfolio
    ) -> list[OrderIntent]:
        tkr = view.market.ticker
        ep = self._eps.get(tkr)
        pos = portfolio.position(tkr)
        qty = abs(pos.qty) if pos is not None else 0
        intents: list[OrderIntent] = []

        if ep is not None:
            sign = 1 if ep.direction == "down" else -1
            # cancel unfilled entry rungs at the entry-window edge
            if (not ep.rungs_canceled
                    and view.t >= ep.t0 + self.f["entry_window_h"] * HOUR):
                ep.rungs_canceled = True
                buy_side = "yes" if ep.direction == "down" else "no"
                intents.append(OrderIntent(
                    ticker=tkr, side=buy_side, action="buy",
                    limit_price_cents=1, size=0, tif="rest", replace=True,
                    rationale="SwingFade entry window closed: cancel rungs",
                ))
            # exit management: one resting order at the retrace target
            if qty > 0 and qty != ep.exit_qty:
                ep.exit_qty = qty
                intents.append(self._exit_intent(tkr, ep, qty))
            # episode close: re-arm only when flat and past the episode
            episode_over = view.t >= ep.t0 + self.f["episode_max_h"] * HOUR
            cur_mark = view.last_price
            # reverted through the target (down: mark >= target; up: <=)
            crossed = cur_mark is not None and sign * (cur_mark - ep.target_c) >= 0
            if qty == 0 and ep.rungs_canceled and (episode_over or crossed):
                del self._eps[tkr]
                ep = None

        if ep is None and qty == 0:
            if self.arm_before is not None and view.t >= self.arm_before:
                return intents
            new_ep = self._detect(view)
            if new_ep is not None:
                self._eps[tkr] = new_ep
                intents.extend(self._rung_intents(tkr, new_ep))
        return intents


def _two_sided(c: Candle) -> bool:
    return (
        c.yes_bid_close is not None and c.yes_ask_close is not None
        and 0 < c.yes_bid_close and c.yes_ask_close < 100
    )

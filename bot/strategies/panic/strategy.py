"""P-3 strategies: R4 dip-side YES bid ladder and R4b cheap-NO convexity
(research/SYNTHESIS.md §1 P-3; docs/viability.md §2).

Both strategies trade ONE episode = one (market, scheduled event window)
pair, discovered by episodes.py. The engine runs one episode per backtest
(bot/strategies/panic/run_backtest.py); strategies re-derive the
qualification condition point-in-time from their MarketView — the episode
file only supplies the window location, never prices.

PARAMS_RATIONALE — parameters FROZEN 2026-08-12 BEFORE any engine backtest
was run (SPEC §8). Every number below comes from research/SYNTHESIS.md P-3
or docs/viability.md §2, both written before this code existed; nothing
here was tuned on backtest output. The taxonomy in reports/panic/
episodes.json (episode counts/classes) was produced before this freeze but
contains no P&L and no fill simulation; no parameter was changed in
response to it.

R4 (dip-side ladder):
- rungs (85, 80, 75)c YES bids, GTC rest, placed at the event-window start
  once the market qualifies. Source: SYNTHESIS P-3 trading rule verbatim.
  Rationale: Michigan's wick printed 74c for ~3 minutes; a resting ladder
  is the only execution that plausibly captures it (viability §2).
- qualification: time-weighted YES avg >= 95c over the prior 24h with
  >= 12h data coverage (SYNTHESIS: "YES averaged >=95c over prior 24h";
  coverage floor so a single stale print can't qualify a dead market).
- budget: the harness per-market cap (5% of bankroll) split in equal
  premium thirds across rungs. SYNTHESIS says "size split by measured
  depth" but no historical order-book depth exists (SYNTHESIS §3 risk 3);
  equal premium split is the neutral prior and is declared here instead of
  silently improvised later.
- p_hat = pre-event 24h average (capped 0.99). The thesis IS that the
  pre-event consensus survives the panic; using it as p_hat sizes the
  ladder consistently with that thesis.
- exit: resting YES ask at (pre-event avg, rounded) - 2c for the whole
  position, replace-managed; hold to resolution if never filled. Source:
  SYNTHESIS P-3 ("exit via resting ask at pre-event - 2c or hold").
- no stop-loss: binary market, sized for total loss (SYNTHESIS P-3).
- unfilled ladder rungs are canceled at window end: R4 prices only the
  scheduled event window; a YES bid left resting for months afterwards is
  a different, unpriced trade.

R4b (cheap-NO convexity):
- entry: buy NO at <= 3c (limit 3, taker ok) in the 24h before the window
  starts. Source: SYNTHESIS P-3 ("buy NO at <=3c pre-event").
- stake: 1% of bankroll premium per episode. Source: viability §2 —
  "position sizing has to assume [total loss]"; the bleed-to-zero world is
  the base case, so the stake is a small fixed fraction, not Kelly-on-
  resolution (which would be ~0: buying 2% probability at 3c is resolution-
  EV-negative; the trade is a volatility bet).
- p_hat = 0.90 emitted on entry intents. This is NOT a resolution
  forecast; it is the Kelly proxy the risk layer needs: p_win = 1 - p_hat
  = 0.10 is the frozen assumed probability that a panic materializes and
  the exit ladder pays. 10% is deliberately below the taxonomy-free prior
  (~4 events/yr per viability §5) — conservative sizing, declared here.
- exit ladder: resting NO asks at (15, 20, 25)c in equal thirds
  (remainder to the 15c rung, the first to fill), placed at window start,
  left resting until market close. Source: SYNTHESIS P-3 verbatim.
- bleed accounting: an entered episode with no panic exit rides to
  settlement; YES resolution = 100% stake loss, counted per episode.
- tick structure: entry/exit limits are whole cents, which every observed
  Kalshi price structure admits (types.PriceStructure); the engine
  quantizes through the per-market tapered_deci_cent structure served by
  the provider. Sub-cent (0.1c) execution inside the harness stays
  integer-cent by design (dataport unit decision): the simulated 3c entry
  is an UPPER bound on the achievable deci-cent entry (real fills at
  2.5-2.9c were available on tapered books), so R4b's simulated cost is
  conservative by up to ~17% of stake. Called out in the report.
"""
from __future__ import annotations

from dataclasses import dataclass

from bot.backtest.types import MarketView, OrderIntent, Portfolio
from bot.strategies.panic.episodes import time_weighted_avg

HOUR = 3600

FROZEN_R4 = dict(
    rungs_cents=(85, 80, 75),
    qual_min_avg_cents=95.0,
    qual_hours=24,
    qual_min_coverage_h=12.0,
    budget_frac=0.05,          # = harness per-market cap; split across rungs
    exit_discount_cents=2,
    p_hat_cap=0.99,
)

FROZEN_R4B = dict(
    entry_max_cents=3,         # buy NO at <= 3c
    entry_lead_h=24,           # entry window: 24h before event-window start
    stake_frac=0.01,           # 1% of bankroll premium per episode
    p_hat=0.90,                # Kelly proxy: assumed 10% panic-arm rate
    exit_rungs_cents=(15, 20, 25),
    qual_min_avg_cents=95.0,   # same episode qualification as R4
    qual_hours=24,
    qual_min_coverage_h=12.0,
)


@dataclass(frozen=True, slots=True)
class EpisodeWindow:
    ticker: str
    window_start: int
    window_end: int


def view_pre_avg(
    view: MarketView, window_start: int, hours: int
) -> tuple[float | None, float]:
    """Time-weighted YES average over [window_start - hours, window_start)
    from the strategy's own point-in-time view (candle closes + trade
    prints, all strictly < view.t by construction)."""
    t0 = window_start - hours * HOUR
    points: list[tuple[int, float]] = []
    for period, series in view.candles_by_period.items():
        for c in series:
            if c.end_ts <= window_start:
                points.append((c.end_ts, float(c.close)))
    for tr in view.trades_before_t:
        if tr.ts <= window_start:
            points.append((tr.ts, float(tr.yes_price)))
    return time_weighted_avg(points, t0, window_start)


class R4PanicDipLadder:
    """Resting YES bid ladder through a scheduled event window (SYNTHESIS
    P-3 R4). One instance trades the episodes it was constructed with;
    everything else is ignored."""

    decision_interval_s = 60
    price_move_trigger_cents = None
    last_inference_cost_cents = 0  # price-only

    def __init__(
        self,
        windows: dict[str, EpisodeWindow],
        bankroll_cents: int = 1_000_000,
        candle_period_s: int = 60,
    ):
        self.windows = windows
        self.bankroll_cents = bankroll_cents
        self.candle_period_s = candle_period_s
        self.f = FROZEN_R4
        # per-ticker state: status in {pending, armed, disarmed, canceled},
        # arm_avg (qualification average), exit_qty (size of resting exit).
        self._state: dict[str, dict] = {}

    # -- helpers ------------------------------------------------------------

    def _ladder_intents(self, ticker: str, arm_avg: float) -> list[OrderIntent]:
        budget_per_rung = int(self.bankroll_cents * self.f["budget_frac"]) // len(
            self.f["rungs_cents"]
        )
        p_hat = min(self.f["p_hat_cap"], arm_avg / 100.0)
        out = []
        for rung in self.f["rungs_cents"]:
            size = budget_per_rung // rung
            if size <= 0:
                continue
            out.append(
                OrderIntent(
                    ticker=ticker,
                    side="yes",
                    action="buy",
                    limit_price_cents=rung,
                    size=size,
                    tif="rest",
                    p_hat=p_hat,
                    rationale=f"R4 ladder rung {rung}c (pre-avg {arm_avg:.1f}c)",
                )
            )
        return out

    # -- Strategy protocol ---------------------------------------------------

    def on_decision_point(
        self, view: MarketView, portfolio: Portfolio
    ) -> list[OrderIntent]:
        ep = self.windows.get(view.market.ticker)
        if ep is None or view.t < ep.window_start:
            return []
        st = self._state.setdefault(ep.ticker, {"status": "pending", "exit_qty": 0})
        intents: list[OrderIntent] = []

        if st["status"] == "pending":
            avg, cov = view_pre_avg(view, ep.window_start, self.f["qual_hours"])
            if (
                avg is None
                or cov < self.f["qual_min_coverage_h"]
                or avg < self.f["qual_min_avg_cents"]
            ):
                st["status"] = "disarmed"
                return []
            st["status"] = "armed"
            st["arm_avg"] = avg
            intents.extend(self._ladder_intents(ep.ticker, avg))

        if st["status"] != "armed":
            return intents

        # Cancel unfilled rungs once the window closes (pure cancel:
        # replace=True + size=0 on the buy side).
        if view.t >= ep.window_end and not st.get("canceled"):
            st["canceled"] = True
            intents.append(
                OrderIntent(
                    ticker=ep.ticker,
                    side="yes",
                    action="buy",
                    limit_price_cents=1,
                    size=0,
                    tif="rest",
                    replace=True,
                    rationale="R4 window closed: cancel unfilled rungs",
                )
            )

        # Exit management: keep one resting YES ask at (pre-avg - 2)c sized
        # to the current position (replace-managed; sells never cancel the
        # buy-side ladder).
        pos = portfolio.position(ep.ticker)
        qty = pos.qty if pos is not None else 0
        if qty > 0 and qty != st["exit_qty"]:
            exit_px = max(1, min(99, round(st["arm_avg"]) - self.f["exit_discount_cents"]))
            st["exit_qty"] = qty
            intents.append(
                OrderIntent(
                    ticker=ep.ticker,
                    side="yes",
                    action="sell",
                    limit_price_cents=exit_px,
                    size=qty,
                    tif="rest",
                    replace=True,
                    rationale=f"R4 exit at pre-avg-2 = {exit_px}c for {qty}",
                )
            )
        return intents


class R4bConvexityNo:
    """Cheap-NO convexity: buy NO <= 3c pre-event, rest a NO ask ladder at
    15/20/25c through the window; ride to settlement otherwise (SYNTHESIS
    P-3 R4b). Bleed-to-zero episodes are the base case and are counted."""

    decision_interval_s = 60
    price_move_trigger_cents = None
    last_inference_cost_cents = 0

    def __init__(
        self,
        windows: dict[str, EpisodeWindow],
        bankroll_cents: int = 1_000_000,
        candle_period_s: int = 60,
    ):
        self.windows = windows
        self.bankroll_cents = bankroll_cents
        self.candle_period_s = candle_period_s
        self.f = FROZEN_R4B
        self._state: dict[str, dict] = {}

    def on_decision_point(
        self, view: MarketView, portfolio: Portfolio
    ) -> list[OrderIntent]:
        ep = self.windows.get(view.market.ticker)
        if ep is None:
            return []
        t = view.t
        entry_start = ep.window_start - self.f["entry_lead_h"] * HOUR
        if t < entry_start:
            return []
        st = self._state.setdefault(
            ep.ticker, {"qualified": False, "ladder_placed": False}
        )
        pos = portfolio.position(ep.ticker)
        no_qty = -pos.qty if pos is not None and pos.qty < 0 else 0
        intents: list[OrderIntent] = []

        if t < ep.window_start:
            # ---- entry phase: accumulate NO at <= 3c ----------------------
            # Qualification mirrors R4 but is checked at entry time over the
            # trailing 24h (all data < t): the market must already be a
            # >=95c favorite for its NO to be the 2-3c lottery ticket.
            # Once qualified it stays qualified (the entry limit still
            # bounds what we pay); until then re-check.
            if not st["qualified"]:
                avg, cov = view_pre_avg(view, t, self.f["qual_hours"])
                if (
                    avg is None
                    or cov < self.f["qual_min_coverage_h"]
                    or avg < self.f["qual_min_avg_cents"]
                ):
                    return []
                st["qualified"] = True
                st["qual_avg"] = avg
            avg = st["qual_avg"]
            target = int(self.bankroll_cents * self.f["stake_frac"]) // self.f[
                "entry_max_cents"
            ]
            want = target - no_qty
            bid = view.best_bid
            # Only emit when the book can actually fill us (NO ask <= 3c
            # <=> YES bid >= 97c); otherwise the ioc dies unfilled anyway.
            if want > 0 and bid is not None and bid >= 100 - self.f["entry_max_cents"]:
                intents.append(
                    OrderIntent(
                        ticker=ep.ticker,
                        side="no",
                        action="buy",
                        limit_price_cents=self.f["entry_max_cents"],
                        size=want,
                        tif="ioc",
                        p_hat=self.f["p_hat"],
                        rationale=(
                            f"R4b entry: NO <= {self.f['entry_max_cents']}c "
                            f"pre-event (pre-avg {avg:.1f}c)"
                        ),
                    )
                )
            return intents

        # ---- window phase: rest the NO ask ladder over the position -------
        # Placed exactly ONCE, over the position held at window start
        # (frozen: "rest an NO ask ladder at 15/20/25c through the window";
        # re-placing after partial exits would silently re-arm the lowest
        # rung — a more aggressive strategy than the one frozen above).
        if no_qty > 0 and not st["ladder_placed"]:
            st["ladder_placed"] = True
            rungs = self.f["exit_rungs_cents"]
            third = no_qty // len(rungs)
            sizes = [no_qty - third * (len(rungs) - 1)] + [third] * (len(rungs) - 1)
            for rung, size in zip(rungs, sizes):
                if size <= 0:
                    continue
                intents.append(
                    OrderIntent(
                        ticker=ep.ticker,
                        side="no",
                        action="sell",
                        limit_price_cents=rung,
                        size=size,
                        tif="rest",
                        rationale=f"R4b exit rung NO {rung}c",
                    )
                )
        return intents

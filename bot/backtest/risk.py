"""Harness-enforced sizing (SPEC.md §5).

Strategies express conviction (limit price + requested size + p_hat); the
harness clamps every intent so all strategies are comparable:

- fractional Kelly (default 1/4) from p_hat vs the estimated fill price,
  fee-aware, for binary contracts;
- per-market cap (5% of bankroll), per-event cap (10%), total deployed
  cap (80%), all measured in premium-at-risk (cost basis + premium
  committed by resting orders);
- depth cap: at most cap_frac (25%) of recently observed per-window volume;
- hard no-borrowing floor: an order may never commit more premium than
  uncommitted cash.

Clamps only ever SHRINK an intent, never inflate it. Intents that reduce an
existing position are exempt from Kelly/bankroll caps for the reducing
portion (closing risk is always allowed); any excess beyond the flip is
sized as a fresh opening.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from bot.backtest.fills import NormalizedIntent

DEFAULT_BANKROLL_CENTS = 1_000_000  # $10,000


@dataclass(frozen=True, slots=True)
class RiskConfig:
    bankroll_cents: int = DEFAULT_BANKROLL_CENTS
    kelly_fraction: float = 0.25
    per_market_frac: float = 0.05
    per_event_frac: float = 0.10
    total_deploy_frac: float = 0.80
    depth_cap_frac: float = 0.25   # also used by fill simulation volume caps
    depth_multiplier: float = 1.0  # capacity-curve hook (1x / 3x / 10x)


@dataclass(frozen=True, slots=True)
class ExposureState:
    """Premium currently at risk, as seen by the risk layer."""
    market_cost_cents: int = 0        # open basis + committed, this market
    event_cost_cents: int = 0         # ... this event
    total_cost_cents: int = 0         # ... whole portfolio
    available_cash_cents: int = DEFAULT_BANKROLL_CENTS  # cash minus committed
    position_qty: int = 0             # signed yes-equivalent qty in this market


@dataclass(frozen=True, slots=True)
class ClampResult:
    size: int
    reasons: tuple[str, ...] = field(default=())


def kelly_max_contracts(
    p_hat: float,
    yes_buy: bool,
    est_fill_yes_price: int,
    fee_per_contract_cents: int,
    bankroll_cents: int,
    kelly_fraction: float,
) -> int:
    """Max contracts by fractional Kelly for a binary contract, fee-aware.

    For a yes-buy at yes price c with fee f: cost per contract = c + f,
    win profit = 100 - c - f, win prob = p_hat. For a no-buy (yes-sell)
    the roles complement: cost = (100 - c) + f, win = c - f, prob = 1-p_hat.
    Kelly stake fraction f* = p - (1-p)/b with b = win/cost; stake is
    kelly_fraction * f* of bankroll, converted to contracts at full cost.
    """
    if not (0.0 < p_hat < 1.0):
        return 0
    p_win = p_hat if yes_buy else 1.0 - p_hat
    cost = (est_fill_yes_price if yes_buy else 100 - est_fill_yes_price) + fee_per_contract_cents
    win = 100 - cost
    if cost <= 0 or win <= 0:
        return 0
    b = win / cost
    f_star = p_win - (1.0 - p_win) / b
    if f_star <= 0:
        return 0
    stake_cents = bankroll_cents * kelly_fraction * f_star
    return int(stake_cents // cost)


def clamp_intent_size(
    norm: NormalizedIntent,
    p_hat: float | None,
    est_fill_yes_price: int,
    fee_per_contract_cents: int,
    exposure: ExposureState,
    cfg: RiskConfig,
    observed_window_volume: int | None = None,
) -> ClampResult:
    """Clamp a normalized intent's size per SPEC §5. Never inflates.

    observed_window_volume: recent per-window traded volume estimate for the
    depth cap (e.g. mean volume of the last few candles). None = unknown =
    no pre-trade depth clamp (the fill simulator still enforces per-print /
    per-candle caps).
    """
    reasons: list[str] = []
    size = norm.size
    if size <= 0:
        return ClampResult(0, ("zero-size",))

    # Split into reducing portion (exempt from Kelly/caps) and opening rest.
    reducing = 0
    if exposure.position_qty != 0 and (norm.qty_sign > 0) != (exposure.position_qty > 0):
        reducing = min(size, abs(exposure.position_qty))
    opening = size - reducing

    if opening > 0:
        # Kelly (requires p_hat).
        if p_hat is None:
            reasons.append("no-p_hat")
            opening = 0
        else:
            k = kelly_max_contracts(
                p_hat, norm.yes_buy, est_fill_yes_price,
                fee_per_contract_cents, cfg.bankroll_cents, cfg.kelly_fraction,
            )
            if k < opening:
                reasons.append("kelly")
                opening = k

        if opening > 0:
            premium = (
                est_fill_yes_price if norm.yes_buy else 100 - est_fill_yes_price
            ) + fee_per_contract_cents
            rooms = {
                "per-market-cap": int(cfg.per_market_frac * cfg.bankroll_cents)
                - exposure.market_cost_cents,
                "per-event-cap": int(cfg.per_event_frac * cfg.bankroll_cents)
                - exposure.event_cost_cents,
                "total-deploy-cap": int(cfg.total_deploy_frac * cfg.bankroll_cents)
                - exposure.total_cost_cents,
                "cash": exposure.available_cash_cents,
            }
            for name, room_cents in rooms.items():
                max_n = max(0, room_cents) // premium
                if max_n < opening:
                    reasons.append(name)
                    opening = max_n

    clamped = reducing + opening

    # Depth pre-clamp applies to the whole order (even exits can't be most
    # of the tape).
    if observed_window_volume is not None:
        depth_max = min(
            observed_window_volume,
            math.floor(cfg.depth_cap_frac * cfg.depth_multiplier * observed_window_volume),
        )
        if depth_max < clamped:
            reasons.append("depth-cap")
            clamped = depth_max

    return ClampResult(max(0, min(clamped, size)), tuple(reasons))

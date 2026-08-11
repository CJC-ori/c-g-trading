"""Kalshi fee model (SPEC.md §4).

Taker fee per SPEC: fee = ceil_to_cent(rate * P * (1-P)) PER CONTRACT,
times contracts, where P is the execution price in dollars and the default
rate is 0.07.

TODO(verify): Kalshi's official schedule computes ceil on the *total*
  (ceil_to_cent(0.07 * C * P * (1-P))), which is slightly cheaper for
  multi-contract orders. The SPEC's per-contract ceil is the conservative
  upper bound; keep it until verified against the official fee schedule at
  https://kalshi.com/docs/kalshi-fee-schedule.pdf.
TODO(verify): per-category multipliers. Some series (e.g. certain index
  markets) historically used different rates and non-zero maker fees.
  Populate CATEGORY_TAKER_RATES / CATEGORY_MAKER_RATES from the official
  schedule before trusting category-level P&L.

Note P*(1-P) is symmetric under P -> 1-P, so YES and NO executions at
complementary prices pay identical fees.
"""
from __future__ import annotations

import math

DEFAULT_TAKER_RATE = 0.07
DEFAULT_MAKER_RATE = 0.0

# category (MarketInfo.category, lowercased) -> rate override.
CATEGORY_TAKER_RATES: dict[str, float] = {
    # TODO(verify): e.g. "s&p 500": 0.035, per official schedule.
}
CATEGORY_MAKER_RATES: dict[str, float] = {
    # TODO(verify)
}


def _ceil_cents(dollars: float) -> int:
    """Round a dollar amount up to the next cent, tolerating float dust."""
    return math.ceil(round(dollars * 100, 9))


def per_contract_fee_cents(
    price_cents: int,
    rate: float = DEFAULT_TAKER_RATE,
    stress_multiplier: float = 1.0,
) -> int:
    """ceil_to_cent(rate * P * (1-P)) for one contract at price_cents.

    stress_multiplier is the fee-stress hook (SPEC §6.7 runs at x1.5).
    """
    if not (1 <= price_cents <= 99):
        raise ValueError(f"price out of range: {price_cents}")
    p = price_cents / 100
    return _ceil_cents(rate * stress_multiplier * p * (1 - p))


def taker_fee_cents(
    price_cents: int,
    count: int,
    category: str | None = None,
    rate: float | None = None,
    stress_multiplier: float = 1.0,
) -> int:
    """Total taker fee for `count` contracts executed at price_cents."""
    if count <= 0:
        return 0
    if rate is None:
        rate = CATEGORY_TAKER_RATES.get((category or "").lower(), DEFAULT_TAKER_RATE)
    return count * per_contract_fee_cents(price_cents, rate, stress_multiplier)


def maker_fee_cents(
    price_cents: int,
    count: int,
    category: str | None = None,
    rate: float | None = None,
    stress_multiplier: float = 1.0,
) -> int:
    """Total maker fee (0 by default on most Kalshi markets)."""
    if count <= 0:
        return 0
    if rate is None:
        rate = CATEGORY_MAKER_RATES.get((category or "").lower(), DEFAULT_MAKER_RATE)
    if rate == 0.0:
        return 0
    return count * per_contract_fee_cents(price_cents, rate, stress_multiplier)

"""Kalshi fee model (SPEC.md §4, amended 2026-08-11 per research/).

Two fee paths coexist:

1. **Exact per-series model (the honest one — SYNTHESIS §2.1).**
   ``trade_fee_centicents``: fee = coef × fee_multiplier × contracts ×
   P × (1−P), **ceiling-rounded to $0.0001** (a "centicent"), per
   https://docs.kalshi.com/getting_started/fee_rounding (verified in
   research/kalshi-api.md §3.4 — it is NOT round-to-cent). Taker coef
   0.07; maker coef 0.0175 on ``quadratic_with_maker_fees`` series only
   (⚠ 0.0175 UNVERIFIED — triangulated from three secondary sources, the
   official fee PDF 429s; research/kalshi-api.md §3.3). The per-series
   ``fee_type``/``fee_multiplier`` come from the API's series objects
   (observed multipliers: 0, 0.5, 1 — never >1) and are resolved via
   ``FeeSchedule`` (bundled snapshot ``series_fees.json`` or the live DB
   series table). The engine uses this path when ``EngineConfig`` carries
   a ``fee_schedule``, accumulating exact centicents per order and
   charging whole cents via a per-order accumulator (mirrors Kalshi's
   own rounding-fee/rebate mechanics at cent precision).

2. **Legacy per-contract ceil (backward-compatible default).**
   ``per_contract_fee_cents``/``taker_fee_cents``/``maker_fee_cents``:
   ceil_to_cent(rate·P·(1−P)) per contract. This strictly OVERSTATES
   taker fees vs. the exact model (conservative) but understates maker
   fees on the 130 maker-fee series (it charges 0). Kept so existing
   runs/tests are untouched; real-data runs should pass a FeeSchedule.

Note P*(1-P) is symmetric under P -> 1-P, so YES and NO executions at
complementary prices pay identical fees.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Mapping

DEFAULT_TAKER_RATE = 0.07
DEFAULT_MAKER_RATE = 0.0

# Exact-model coefficients (dollars per contract, times P(1-P)).
TAKER_COEF = Decimal("0.07")
# TODO(verify): maker coefficient triangulated as 25% of taker; the official
# fee-schedule PDF has never been fetched (HTTP 429). Applies ONLY to
# fee_type == "quadratic_with_maker_fees" series. UNVERIFIED.
MAKER_COEF = Decimal("0.0175")
CENTICENT = Decimal("0.0001")  # $0.0001 — Kalshi's trade-fee rounding unit

# category (MarketInfo.category, lowercased) -> rate override (legacy path).
CATEGORY_TAKER_RATES: dict[str, float] = {}
CATEGORY_MAKER_RATES: dict[str, float] = {}


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


# ---------------------------------------------------------------------------
# Exact per-series fee model (SYNTHESIS §2.1; research/kalshi-api.md §3)
# ---------------------------------------------------------------------------

#: fee_type values observed on the API (research/kalshi-api.md §3.1).
FEE_TYPES = ("quadratic", "quadratic_with_maker_fees", "flat")


@dataclass(frozen=True, slots=True)
class SeriesFeeConfig:
    """One series' fee configuration, as carried on the API series object.

    fee_type "flat" exists in the API enum but 0 series were observed using
    it; its schedule ("Specific Trading Fees Table") is unfetched, so it is
    modeled as quadratic (flagged in trade_fee_centicents).
    """
    fee_type: str = "quadratic"
    fee_multiplier: float = 1.0

    @property
    def charges_maker(self) -> bool:
        return self.fee_type == "quadratic_with_maker_fees"


DEFAULT_FEE_CONFIG = SeriesFeeConfig("quadratic", 1.0)


def trade_fee_centicents(
    price_cents: int,
    count: int,
    *,
    is_taker: bool = True,
    config: SeriesFeeConfig = DEFAULT_FEE_CONFIG,
    stress_multiplier: float = 1.0,
) -> int:
    """Exact Kalshi trade fee in CENTICENTS ($0.0001 units), ceiling-rounded.

    fee = coef × fee_multiplier × count × P × (1−P), ceiled to $0.0001
    (docs.kalshi.com/getting_started/fee_rounding — NOT round-to-cent).
    Taker coef 0.07; maker coef 0.0175 only on quadratic_with_maker_fees
    series (⚠ 0.0175 unverified — see MAKER_COEF note), else maker = 0.
    "flat" fee_type is modeled as quadratic (schedule unfetched, 0 series
    observed). stress_multiplier is the fee-stress hook (SPEC §7 ×1.5).
    """
    if count <= 0:
        return 0
    if not (1 <= price_cents <= 99):
        raise ValueError(f"price out of range: {price_cents}")
    if is_taker:
        coef = TAKER_COEF
    elif config.charges_maker:
        coef = MAKER_COEF
    else:
        return 0
    p = Decimal(price_cents) / 100
    raw = (
        coef
        * Decimal(str(config.fee_multiplier))
        * Decimal(str(stress_multiplier))
        * Decimal(count)
        * p
        * (1 - p)
    )
    quantized = raw.quantize(CENTICENT, rounding=ROUND_CEILING)
    return int(quantized * 10_000)


def cents_ceil_from_centicents(centicents: int) -> int:
    """Ceil a centicent amount to whole cents (100 centicents = 1 cent)."""
    return -(-centicents // 100)


def series_ticker_of(market_ticker: str) -> str:
    """Series ticker for a market ticker (segment before the first dash).

    Matches bot.data.store.series_of; kept local so the harness has no
    bot.data import dependency.
    """
    return market_ticker.split("-", 1)[0]


_DEFAULT_FEE_MAP_PATH = os.path.join(os.path.dirname(__file__), "series_fees.json")


class FeeSchedule:
    """Resolves a series ticker to its SeriesFeeConfig.

    Sources, in order of preference:
    - ``FeeSchedule.from_store(db_path)`` — the live DB series table
      (bot/data pulled the full /series listing: fee_type + fee_multiplier
      for every series).
    - ``FeeSchedule.load_default()`` — the bundled ``series_fees.json``
      snapshot (only non-default series are listed; everything else is
      quadratic ×1). Generated 2026-08-11 from the same /series pull.

    Unknown series fall back to the default config (quadratic ×1) — correct
    for 12,012 of 12,174 observed series.

    Known gap: fee configs change over time (/series/fee_changes). This is
    a present-day snapshot, not point-in-time resolution (research/
    kalshi-api.md §3.1 caveat); the fee-stress run bounds the error.
    """

    def __init__(
        self,
        overrides: Mapping[str, SeriesFeeConfig] | None = None,
        default: SeriesFeeConfig = DEFAULT_FEE_CONFIG,
    ):
        self.overrides: dict[str, SeriesFeeConfig] = dict(overrides or {})
        self.default = default

    def for_series(self, series_ticker: str) -> SeriesFeeConfig:
        return self.overrides.get(series_ticker, self.default)

    def for_market_ticker(self, market_ticker: str) -> SeriesFeeConfig:
        return self.for_series(series_ticker_of(market_ticker))

    def __len__(self) -> int:
        return len(self.overrides)

    @classmethod
    def load_default(cls, path: str | None = None) -> "FeeSchedule":
        """Load the bundled (or given) JSON snapshot of non-default series."""
        with open(path or _DEFAULT_FEE_MAP_PATH) as fh:
            data = json.load(fh)
        overrides = {
            ticker: SeriesFeeConfig(
                fee_type=cfg.get("fee_type", "quadratic"),
                fee_multiplier=float(cfg.get("fee_multiplier", 1.0)),
            )
            for ticker, cfg in data.get("overrides", {}).items()
        }
        return cls(overrides)

    @classmethod
    def from_store(cls, db_path: str = "data/kalshi.db") -> "FeeSchedule":
        """Build from the DB series table (full coverage, incl. defaults)."""
        import sqlite3

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            overrides = {}
            for ticker, ft, fm in conn.execute(
                "SELECT series_ticker, fee_type, fee_multiplier FROM series"
            ):
                cfg = SeriesFeeConfig(ft or "quadratic", fm if fm is not None else 1.0)
                if cfg != DEFAULT_FEE_CONFIG:
                    overrides[ticker] = cfg
            return cls(overrides)
        finally:
            conn.close()

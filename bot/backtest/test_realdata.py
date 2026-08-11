"""Integration tests against the real data/kalshi.db (skipped if absent).

Fast subset of bot/backtest/validate_wiring.py — the full gauntlet (incl.
the ~1 min full-universe baseline run) lives there; these tests pin the
wiring facts that must never regress, on a handful of markets.
"""
from pathlib import Path

import pytest

from bot.backtest import fills
from bot.backtest.dataport import SqliteHistoryProvider
from bot.backtest.engine import run_backtest
from bot.backtest.universe import UniverseConfig, research_universe, select_universe
from bot.strategies.baseline import HoldFavorite

DB = Path(__file__).resolve().parents[2] / "data" / "kalshi.db"

pytestmark = pytest.mark.skipif(not DB.exists(), reason="data/kalshi.db not pulled")


@pytest.fixture(scope="module")
def provider():
    return SqliteHistoryProvider(str(DB))


class TestMichiganReplay:
    def test_settlements(self, provider):
        s = provider.settlement("KXSENATEMID-26-AELS")
        assert s.result == "yes"
        h = provider.settlement("KXSENATEMID-26-HSTE")
        assert h.result == "no"
        # settled ~30 min after close, on 2026-08-05 (14:32Z)
        assert 1785938520 < s.settled_ts < 1785942120

    def test_panic_dip_visible(self, provider):
        # Election night (2026-08-04/05 UTC): winner dips 98 -> ~74 -> recovers.
        candles = [
            c
            for c in provider.candles("KXSENATEMID-26-AELS", 3600)
            if 1785880800 <= c.end_ts <= 1785938400  # 08-04 22:00Z .. 08-05 14:00Z
        ]
        assert candles, "no election-night candles"
        mids = [
            (c.yes_bid_close + c.yes_ask_close) / 2
            for c in candles
            if c.yes_bid_close is not None
        ]
        assert mids[0] >= 90
        assert min(c.low for c in candles) <= 80  # the panic dip
        assert mids[-1] >= 95  # the recovery

    def test_candle_history_is_deep(self, provider):
        candles = provider.candles("KXSENATEMID-26-AELS", 3600)
        assert len(candles) > 9000  # back to 2025-03


class TestUniverse:
    def test_research_universe_shape(self):
        u = research_universe(str(DB))
        assert 100 <= len(u) <= 5000
        assert "KXSENATEMID-26-AELS" in u
        assert "KXSENATEMID-26-HSTE" in u
        assert not any(t.startswith("KXMVE") for t in u)

    def test_universe_markets_all_settled_with_candles(self, provider):
        u = research_universe(str(DB))[:25]
        for t in u:
            assert provider.settlement(t) is not None
            assert provider.candles(t, 3600)

    def test_price_band_filter(self):
        # A week before election night AELS quoted ~82c mid, HSTE ~17.5c:
        # a 75-95c favorite band must include only the former.
        ref = 1785326400  # 2026-07-29T12:00:00Z
        u = select_universe(
            str(DB),
            UniverseConfig(
                categories=("Elections",), price_band=(75, 95), price_band_ref_ts=ref
            ),
        )
        assert "KXSENATEMID-26-AELS" in u
        assert "KXSENATEMID-26-HSTE" not in u


class TestSmallBacktestConservation:
    def test_fills_and_settlement_conserve_cash_exactly(self, provider):
        tickers = research_universe(str(DB))
        # Michigan complex + enough others to actually trade.
        sample = [t for t in tickers if t.startswith("KXSENATEMID")] + tickers[:30]
        result = run_backtest(
            provider, HoldFavorite(), market_filters={"tickers": sample}
        )
        cash = result.config.risk.bankroll_cents
        pos: dict[str, tuple[int, int]] = {}
        for f in result.fills:
            yes_price = f.price_cents if f.side == "yes" else 100 - f.price_cents
            sign = 1 if (f.side == "yes") == (f.action == "buy") else -1
            q, b = pos.get(f.ticker, (0, 0))
            q, b, dcash, _ = fills.apply_fill_to_position(q, b, sign * f.count, yes_price)
            pos[f.ticker] = (q, b)
            cash += dcash - f.fee_cents
        for t, (q, b) in pos.items():
            s = result.settlements[t]  # research universe is settled-only
            cash += fills.settlement_payout_cents(q, b, s.result, s.settlement_value_cents)
        assert cash == result.final_cash_cents  # exact integer identity
        assert result.fills, "expected the favorite strategy to trade in this sample"


class TestScalarInRealData:
    def test_scalar_settlements_load(self, provider):
        import sqlite3

        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        tickers = [
            r[0]
            for r in conn.execute(
                "SELECT ticker FROM markets WHERE result='scalar' LIMIT 20"
            )
        ]
        conn.close()
        assert tickers
        for t in tickers:
            s = provider.settlement(t)
            assert s.result == "scalar"
            assert 0 <= s.settlement_value_cents <= 100

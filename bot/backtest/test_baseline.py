"""End-to-end smoke: baseline strategies through the full engine + report."""
import json
import os

from bot.backtest.engine import run_backtest
from bot.backtest.metrics import full_report
from bot.backtest.testkit import synthetic_provider
from bot.strategies.baseline import CoinFlip, HoldFavorite


class TestHoldFavoriteSmoke:
    def test_full_report_end_to_end(self, tmp_path):
        provider = synthetic_provider(seed=7)
        out = str(tmp_path / "report")
        report = full_report(
            provider,
            lambda: HoldFavorite(edge_cents=4),
            out_dir=out,
            label="hold-favorite-smoke",
        )
        # report artifacts written
        assert os.path.exists(os.path.join(out, "report.json"))
        assert os.path.exists(os.path.join(out, "report.md"))
        on_disk = json.load(open(os.path.join(out, "report.json")))
        assert on_disk["label"] == "hold-favorite-smoke"
        # it actually traded, and favorites bought in the 90s that settle YES
        # are profitable after fees
        ts = report["base"]["trade_stats"]
        assert ts["contracts_filled"] > 0
        assert report["base"]["pnl"]["net_pnl_cents_after_inference"] > 0
        # capacity curve has all three depth points and monotone fills
        mults = [c["depth_multiplier"] for c in report["capacity_curve"]]
        assert mults == [1.0, 3.0, 10.0]
        fills = [c["contracts_filled"] for c in report["capacity_curve"]]
        assert fills[0] <= fills[1] <= fills[2]
        # fee stress section present
        assert report["fee_stress"]["multiplier"] == 1.5

    def test_deterministic(self):
        r1 = run_backtest(synthetic_provider(seed=7), HoldFavorite(edge_cents=4))
        r2 = run_backtest(synthetic_provider(seed=7), HoldFavorite(edge_cents=4))
        assert r1.final_cash_cents == r2.final_cash_cents
        assert len(r1.fills) == len(r2.fills)


class TestCoinFlipSmoke:
    def test_runs_and_scores_brier(self):
        provider = synthetic_provider(seed=7)
        res = run_backtest(provider, CoinFlip(seed=42))
        # emitted probabilistic intents -> Brier is computable
        assert res.decisions, "CoinFlip emitted no p_hat decisions"
        from bot.backtest.metrics import brier_vs_baseline

        b = brier_vs_baseline(res)
        assert b["n"] > 0
        # a coin flip should not beat the market baseline on favorites-heavy
        # synthetic data; don't assert direction, just that both scored
        assert b["strategy"] is not None and b["market_baseline"] is not None

    def test_seeded_determinism(self):
        p = synthetic_provider(seed=7)
        r1 = run_backtest(p, CoinFlip(seed=42))
        r2 = run_backtest(p, CoinFlip(seed=42))
        assert r1.final_cash_cents == r2.final_cash_cents

    def test_never_borrows_or_blows_caps(self):
        res = run_backtest(synthetic_provider(seed=7), CoinFlip(seed=42))
        assert all(eq >= 0 for _, eq in res.equity_curve)
        # committed + basis never exceeded 80% of bankroll
        assert all(d <= 800_000 for _, d in res.deployed_curve)

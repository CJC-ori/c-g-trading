"""Unit tests for harness-enforced sizing (SPEC §5)."""
from bot.backtest.fills import NormalizedIntent
from bot.backtest.risk import (
    ClampResult,
    ExposureState,
    RiskConfig,
    clamp_intent_size,
    kelly_max_contracts,
)

CFG = RiskConfig()  # $10k bankroll, 1/4 Kelly, 5/10/80% caps, 25% depth


def norm(yes_buy=True, yes_limit=50, size=100):
    return NormalizedIntent("MKT", yes_buy, yes_limit, size)


def exp(**kw):
    return ExposureState(**{"available_cash_cents": CFG.bankroll_cents, **kw})


class TestKelly:
    def test_fee_free_even_odds(self):
        # p=0.6 at 50c, no fee: b=1, f*=0.2; 1/4 Kelly on $10k = $500 -> 1000
        assert kelly_max_contracts(0.6, True, 50, 0, 1_000_000, 0.25) == 1000

    def test_fee_reduces_size(self):
        # cost 52, win 48: f* = 0.6 - 0.4*52/48 = 1/6; stake $416.67 -> 801
        assert kelly_max_contracts(0.6, True, 50, 2, 1_000_000, 0.25) == 801

    def test_no_side_symmetry(self):
        # Betting NO with p_hat=0.4 at yes 50 == betting YES with p_hat 0.6.
        yes = kelly_max_contracts(0.6, True, 50, 0, 1_000_000, 0.25)
        no = kelly_max_contracts(0.4, False, 50, 0, 1_000_000, 0.25)
        assert yes == no == 1000

    def test_no_edge_gives_zero(self):
        assert kelly_max_contracts(0.5, True, 50, 0, 1_000_000, 0.25) == 0

    def test_fees_flip_marginal_edge_to_zero(self):
        assert kelly_max_contracts(0.5, True, 50, 2, 1_000_000, 0.25) == 0

    def test_negative_edge_gives_zero(self):
        assert kelly_max_contracts(0.3, True, 50, 0, 1_000_000, 0.25) == 0

    def test_degenerate_p_hat(self):
        assert kelly_max_contracts(0.0, True, 50, 0, 1_000_000, 0.25) == 0
        assert kelly_max_contracts(1.0, True, 50, 0, 1_000_000, 0.25) == 0

    def test_price_leaves_no_win_room(self):
        # 99c + 1c fee = 100c cost, 0 win -> no bet at any p_hat
        assert kelly_max_contracts(0.999, True, 99, 1, 1_000_000, 0.25) == 0


class TestClamp:
    def test_kelly_clamps_requested_size(self):
        r = clamp_intent_size(norm(size=5000), 0.6, 50, 0, exp(), CFG)
        assert r == ClampResult(1000, ("kelly",))

    def test_never_inflates(self):
        r = clamp_intent_size(norm(size=10), 0.6, 50, 0, exp(), CFG)
        assert r.size == 10 and r.reasons == ()

    def test_no_p_hat_blocks_opening(self):
        r = clamp_intent_size(norm(size=100), None, 50, 0, exp(), CFG)
        assert r.size == 0 and "no-p_hat" in r.reasons

    def test_per_market_cap(self):
        # cap 5% of $10k = $500; $450 already at risk -> room $50 = 100 @ 50c
        r = clamp_intent_size(
            norm(size=500), 0.9, 50, 0, exp(market_cost_cents=45_000), CFG
        )
        assert r.size == 100 and "per-market-cap" in r.reasons

    def test_per_event_cap(self):
        # cap 10% = $1000; $990 at event level -> room $10 = 20 @ 50c
        r = clamp_intent_size(
            norm(size=500), 0.9, 50, 0, exp(event_cost_cents=99_000), CFG
        )
        assert r.size == 20 and "per-event-cap" in r.reasons

    def test_total_deploy_cap(self):
        # cap 80% = $8000; $7990 deployed -> room $10 = 20 @ 50c
        r = clamp_intent_size(
            norm(size=500), 0.9, 50, 0, exp(total_cost_cents=799_000), CFG
        )
        assert r.size == 20 and "total-deploy-cap" in r.reasons

    def test_cash_floor_no_borrowing(self):
        r = clamp_intent_size(
            norm(size=500), 0.9, 50, 0, exp(available_cash_cents=1000), CFG
        )
        assert r.size == 20 and "cash" in r.reasons

    def test_depth_cap(self):
        r = clamp_intent_size(
            norm(size=40), 0.9, 50, 0, exp(), CFG, observed_window_volume=100
        )
        assert r.size == 25 and "depth-cap" in r.reasons

    def test_depth_multiplier_relaxes_depth_cap(self):
        cfg = RiskConfig(depth_multiplier=10.0)
        r = clamp_intent_size(
            norm(size=200), 0.9, 50, 0, exp(), cfg, observed_window_volume=100
        )
        # 10x * 25% = 250% but never beyond the tape itself
        assert r.size == 100

    def test_reducing_position_is_exempt(self):
        """Closing risk is always allowed, even without p_hat."""
        r = clamp_intent_size(
            norm(yes_buy=True, size=100), None, 50, 0, exp(position_qty=-100), CFG
        )
        assert r.size == 100

    def test_reduce_plus_open_splits(self):
        # 100 reduce freely; 50 opening blocked by missing p_hat
        r = clamp_intent_size(
            norm(yes_buy=True, size=150), None, 50, 0, exp(position_qty=-100), CFG
        )
        assert r.size == 100 and "no-p_hat" in r.reasons

    def test_depth_cap_applies_even_to_reduction(self):
        r = clamp_intent_size(
            norm(yes_buy=True, size=100), None, 50, 0,
            exp(position_qty=-100), CFG, observed_window_volume=100,
        )
        assert r.size == 25 and "depth-cap" in r.reasons

    def test_zero_size(self):
        r = clamp_intent_size(norm(size=0), 0.9, 50, 0, exp(), CFG)
        assert r.size == 0

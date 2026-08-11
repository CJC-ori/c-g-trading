"""Tests for the P-1 FLB strategy family (synthetic fixtures only)."""
from __future__ import annotations

import math

from bot.backtest.dataport import InMemoryHistoryProvider
from bot.backtest.engine import run_backtest
from bot.backtest.testkit import mk_candle, mk_market, synthetic_provider
from bot.backtest.types import MarketView, Portfolio, Position, SettlementResult
from bot.strategies import flb_analysis as fa
from bot.strategies.flb import R1Taker, R2Maker, R5Endgame, biased_p_hat

HOUR = 3600
DAY = 86400
T0 = 1_000_000 * HOUR  # candle-grid aligned, matches testkit


def view_for(
    ticker="MKT",
    t=T0 + 2 * HOUR,
    close_ts=T0 + 3 * DAY,
    price=85,
    bid=None,
    ask=None,
    vol=400,
    category="test",
):
    """One-candle MarketView ending exactly at t (fully observable)."""
    market = mk_market(ticker=ticker, close_ts=close_ts)
    market = type(market)(
        ticker=market.ticker, event_ticker=market.event_ticker,
        category=category, title=market.title, open_ts=market.open_ts,
        close_ts=close_ts,
    )
    c = mk_candle(
        ticker=ticker, start=t - HOUR, period=HOUR, o=price, c=price, vol=vol,
        bid=bid if bid is not None else max(1, price - 1),
        ask=ask if ask is not None else min(99, price + 1),
    )
    return MarketView(market=market, t=t, candles_by_period={HOUR: (c,)})


def empty_portfolio(cash=1_000_000):
    return Portfolio(cash_cents=cash)


def holding_portfolio(ticker="MKT", qty=10):
    return Portfolio(
        cash_cents=1_000_000, positions=(Position(ticker, qty, qty * 80),)
    )


# ---------------------------------------------------------------------------
# p_hat
# ---------------------------------------------------------------------------

class TestBiasedPHat:
    def test_yes_side(self):
        assert math.isclose(biased_p_hat("yes", 80, 0.03), 0.824)

    def test_no_side_is_yes_prob(self):
        # buying NO at 80c with 2.6% bias: P(no) = .8208 -> P(yes) = .1792
        assert math.isclose(biased_p_hat("no", 80, 0.026), 1 - 0.8208)

    def test_clipped(self):
        assert biased_p_hat("yes", 99, 0.03) == 0.995
        assert biased_p_hat("no", 99, 0.03) == 1 - 0.995


# ---------------------------------------------------------------------------
# R1 taker
# ---------------------------------------------------------------------------

class TestR1Taker:
    def test_buys_yes_at_ask_in_band(self):
        s = R1Taker()
        intents = s.on_decision_point(view_for(price=85), empty_portfolio())
        assert len(intents) == 1
        it = intents[0]
        assert (it.side, it.action, it.tif) == ("yes", "buy", "ioc")
        assert it.limit_price_cents == 86  # the ask
        assert math.isclose(it.p_hat, min(0.995, 0.86 * 1.03))

    def test_buys_no_side_when_no_priced_in_band(self):
        # yes ~ 15c -> NO costs 100 - yes_bid = 86c, in band
        s = R1Taker()
        intents = s.on_decision_point(view_for(price=15), empty_portfolio())
        assert len(intents) == 1
        it = intents[0]
        assert it.side == "no"
        assert it.limit_price_cents == 100 - 14  # 100 - yes bid
        assert it.p_hat < 0.5  # P(yes) low when buying NO favorite

    def test_out_of_band_no_trade(self):
        s = R1Taker()
        assert s.on_decision_point(view_for(price=50), empty_portfolio()) == []
        assert s.on_decision_point(view_for(price=98, ask=99), empty_portfolio()) == []

    def test_days_window(self):
        s = R1Taker()
        v = view_for(price=85, t=T0, close_ts=T0 + 11 * DAY)
        assert s.on_decision_point(v, empty_portfolio()) == []
        v = view_for(price=85, t=T0, close_ts=T0 + 9 * DAY)
        assert len(s.on_decision_point(v, empty_portfolio())) == 1

    def test_holds_do_not_reenter(self):
        s = R1Taker()
        assert s.on_decision_point(view_for(price=85), holding_portfolio()) == []

    def test_category_filter(self):
        s = R1Taker(categories=("Elections",))
        assert s.on_decision_point(
            view_for(price=85, category="Economics"), empty_portfolio()
        ) == []
        assert len(
            s.on_decision_point(
                view_for(price=85, category="Elections"), empty_portfolio()
            )
        ) == 1

    def test_end_to_end_on_synthetic_favorites(self):
        # favorites walk 91-96 and settle YES: R1 should trade and profit
        res = run_backtest(synthetic_provider(seed=7), R1Taker())
        assert res.fills, "R1 never filled on favorite-heavy synthetic data"
        assert all(f.is_taker for f in res.fills)
        assert res.net_pnl_cents > 0


# ---------------------------------------------------------------------------
# R2 maker
# ---------------------------------------------------------------------------

class TestR2Maker:
    def test_rests_bid_on_ge50_side(self):
        s = R2Maker()
        intents = s.on_decision_point(view_for(price=70, vol=400), empty_portfolio())
        assert len(intents) == 1
        it = intents[0]
        assert (it.side, it.action, it.tif) == ("yes", "buy", "rest")
        assert it.limit_price_cents == 69  # joins best bid, below ask
        assert it.size == 100  # 25% of trailing 400

    def test_no_side_when_yes_below_50(self):
        s = R2Maker()
        intents = s.on_decision_point(view_for(price=30), empty_portfolio())
        assert len(intents) == 1
        it = intents[0]
        assert it.side == "no"
        # NO join = 100 - yes ask = 100 - 31 = 69
        assert it.limit_price_cents == 69

    def test_standdown_final_6h(self):
        s = R2Maker()
        v = view_for(price=70, t=T0, close_ts=T0 + 5 * HOUR)
        assert s.on_decision_point(v, empty_portfolio()) == []
        v = view_for(price=70, t=T0, close_ts=T0 + 7 * HOUR)
        assert len(s.on_decision_point(v, empty_portfolio())) == 1

    def test_size_capped_by_trailing_volume(self):
        s = R2Maker(vol_frac=0.20)
        intents = s.on_decision_point(view_for(price=70, vol=50), empty_portfolio())
        assert intents[0].size == 10
        # zero trailing volume -> no quote at all
        s2 = R2Maker()
        assert s2.on_decision_point(view_for(price=70, vol=0), empty_portfolio()) == []

    def test_reprice_only_on_quote_change(self):
        s = R2Maker()
        v = view_for(price=70)
        assert len(s.on_decision_point(v, empty_portfolio())) == 1
        # same quote next hour -> no duplicate order
        assert s.on_decision_point(v, empty_portfolio()) == []
        # price moved -> new quote emitted
        v2 = view_for(price=72, t=T0 + 3 * HOUR)
        assert len(s.on_decision_point(v2, empty_portfolio())) == 1

    def test_no_quote_when_holding(self):
        s = R2Maker()
        assert s.on_decision_point(view_for(price=70), holding_portfolio()) == []

    def test_maker_fill_requires_strict_through(self):
        """End-to-end: resting bid at 69 fills only when a candle's low
        prints strictly below 69, and pays maker (zero) fees."""
        t0 = T0
        close = t0 + 3 * DAY
        m = mk_market(ticker="MKT", close_ts=close)
        candles = [
            mk_candle(ticker="MKT", start=t0 + i * HOUR, o=70, c=70, vol=400)
            for i in range(3)
        ]
        # hour 3 dips through 69 -> fill; then recovers and settles YES
        candles.append(
            mk_candle(ticker="MKT", start=t0 + 3 * HOUR, o=70, c=70, l=66, vol=400)
        )
        prov = InMemoryHistoryProvider(
            [m], candles, [], [SettlementResult("MKT", "yes", close)]
        )
        res = run_backtest(prov, R2Maker())
        assert res.fills, "dip through the bid should have filled"
        f = res.fills[0]
        assert not f.is_taker
        assert f.fee_cents == 0
        assert f.price_cents == 69
        assert res.net_pnl_cents > 0  # bought 69, settled 100


# ---------------------------------------------------------------------------
# R5 endgame
# ---------------------------------------------------------------------------

class TestR5Endgame:
    def test_quotes_only_inside_window(self):
        s = R5Endgame(window_s=6 * HOUR)
        v = view_for(price=95, t=T0, close_ts=T0 + 7 * HOUR)
        assert s.on_decision_point(v, empty_portfolio()) == []
        v = view_for(price=95, t=T0, close_ts=T0 + 5 * HOUR)
        intents = s.on_decision_point(v, empty_portfolio())
        assert len(intents) == 1
        it = intents[0]
        assert (it.side, it.tif) == ("yes", "rest")
        assert it.limit_price_cents == 94  # joins the bid

    def test_band_on_join_price(self):
        s = R5Endgame(window_s=6 * HOUR)
        # bid 89 -> below band, no quote
        v = view_for(price=90, bid=89, ask=91, t=T0, close_ts=T0 + 5 * HOUR)
        assert s.on_decision_point(v, empty_portfolio()) == []
        # NO side: yes ask 8 -> NO join 92 in band
        v = view_for(price=7, bid=6, ask=8, t=T0, close_ts=T0 + 5 * HOUR)
        intents = s.on_decision_point(v, empty_portfolio())
        assert len(intents) == 1
        assert intents[0].side == "no"
        assert intents[0].limit_price_cents == 92

    def test_p_hat_uses_endgame_bias(self):
        s = R5Endgame(window_s=6 * HOUR)
        v = view_for(price=95, t=T0, close_ts=T0 + 5 * HOUR)
        it = s.on_decision_point(v, empty_portfolio())[0]
        assert math.isclose(it.p_hat, min(0.995, 0.94 * 1.013))


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

class TestClusteredSE:
    def test_single_observation_per_cluster_matches_iid_scale(self):
        vals = [0.1, -0.1, 0.2, -0.2]
        out = fa.clustered_mean_se(vals, ["a", "b", "c", "d"])
        assert out["n"] == 4 and out["n_clusters"] == 4
        assert math.isclose(out["mean"], 0.0)
        # CR SE with G clusters of 1: sqrt(G/(G-1) * sum e^2) / n
        expect = math.sqrt(4 / 3 * (0.01 + 0.01 + 0.04 + 0.04)) / 4
        assert math.isclose(out["se"], expect)

    def test_perfectly_correlated_cluster_inflates_se(self):
        # two clusters, each internally identical: clustering must widen SE
        vals = [0.1, 0.1, -0.1, -0.1]
        clustered = fa.clustered_mean_se(vals, ["a", "a", "b", "b"])
        naive = fa.clustered_mean_se(vals, ["a", "b", "c", "d"])
        assert clustered["se"] > naive["se"]

    def test_empty(self):
        out = fa.clustered_mean_se([], [])
        assert out["n"] == 0 and out["mean"] is None


class TestEpisodesAndCurves:
    def _run(self):
        # Two markets: winner (settles YES, bought ~86) and loser (settles NO)
        t0 = T0
        close = t0 + 2 * DAY
        markets = [mk_market(ticker="WIN", event="EV-1", close_ts=close),
                   mk_market(ticker="LOSE", event="EV-1", close_ts=close)]
        candles = []
        for tk in ("WIN", "LOSE"):
            candles += [
                mk_candle(ticker=tk, start=t0 + i * HOUR, o=85, c=85, vol=400)
                for i in range(6)
            ]
        prov = InMemoryHistoryProvider(
            markets, candles, [],
            [SettlementResult("WIN", "yes", close),
             SettlementResult("LOSE", "no", close)],
        )
        return run_backtest(prov, R1Taker())

    def test_episode_extraction(self):
        res = self._run()
        eps = fa.episodes(res)
        assert len(eps) == 2
        by = {e["ticker"]: e for e in eps}
        assert by["WIN"]["return"] > 0
        assert by["LOSE"]["return"] < 0
        assert by["WIN"]["event_ticker"] == "EV-1"
        # basis reconciles with fills
        assert by["WIN"]["basis_cents"] == sum(
            f.price_cents * f.count for f in res.fills if f.ticker == "WIN"
        )

    def test_episode_stats_cluster_by_event(self):
        res = self._run()
        stats = fa.episode_return_stats(fa.episodes(res))
        assert stats["n"] == 2
        assert stats["n_clusters"] == 1  # same event -> one cluster

    def test_winrate_curve(self):
        res = self._run()
        rows = fa.winrate_by_price(res)
        assert rows, "no winrate buckets"
        # all entries at the same price bucket: win rate is fraction of
        # contracts on the winning market
        total = sum(r["contracts"] for r in rows)
        wins = sum(r["win_rate"] * r["contracts"] for r in rows)
        win_contracts = sum(f.count for f in res.fills if f.ticker == "WIN")
        assert math.isclose(wins, win_contracts)
        assert total == sum(f.count for f in res.fills)

    def test_fill_stats_taker_only(self):
        res = self._run()
        fs = fa.fill_stats(res)
        assert fs["n_maker_orders"] == 0
        assert fs["maker_order_fill_rate"] is None
        assert fs["taker_order_fill_rate"] > 0
        assert not fs["maker_fill_rate_flag_gt60"]


class TestOpportunityLifetime:
    def test_counts_qualifying_hours(self):
        t0 = T0
        close = t0 + 12 * HOUR
        m = mk_market(ticker="MKT", close_ts=close)
        # 5 hours in band (85), then out of band (50)
        candles = [
            mk_candle(ticker="MKT", start=t0 + i * HOUR, o=85, c=85, vol=100)
            for i in range(5)
        ] + [
            mk_candle(ticker="MKT", start=t0 + i * HOUR, o=50, c=50, vol=100)
            for i in range(5, 10)
        ]
        prov = InMemoryHistoryProvider([m], candles, [], [])
        out = fa.opportunity_lifetime(
            prov, ["MKT"], {"MKT": close}, fa.r1_qualifier((70, 97), 10.0)
        )
        assert out["n_markets"] == 1
        assert out["median_hours"] == 5.0

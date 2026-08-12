"""CLI runner for the P-1 FLB strategy family (SYNTHESIS.md §1).

Runs R1-taker, R2-maker, R5-endgame (6/12/24h) and the HoldFavorite
baseline through the backtest engine on the TRAIN split only (first
`--train-frac` of the dataset's time span; the held-out tail is never
touched here - it belongs to the tournament). Writes, per variant:

    reports/flb/<variant>/report.json + report.md   (metrics.full_report)
    reports/flb/<variant>/extras.json               (episodes, clustered
                                                     SEs, fill rates,
                                                     win-rate curve,
                                                     opportunity lifetime)
    reports/flb/<variant>/episodes.csv

plus reports/flb/universe.json (the frozen universe + split) and
reports/flb/ANALYSIS.md (the generated analysis document).

Re-running on a bigger DB (e.g. once history is extended to 2022) is the
same one command - the universe, split point and every report regenerate
from whatever data/kalshi.db holds:

    python -m bot.strategies.run_flb

Useful extensions for the full-history pull:

    python -m bot.strategies.run_flb --close-after 2022-11-01 \\
        --include-sports --out reports/flb-full

(--include-sports matters for R5: the endgame calibration literature is
sports-driven, but the current 95-day pull has no sports candles.)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
from typing import Any, Callable

from bot.backtest.dataport import SqliteHistoryProvider
from bot.backtest.engine import EngineConfig, run_backtest
from bot.backtest.fees import FeeSchedule
from bot.backtest.fills import MakerQueueConfig
from bot.backtest.metrics import full_report
from bot.backtest.universe import (
    DEFAULT_EXCLUDE_CATEGORIES,
    UniverseConfig,
    select_universe,
)
from bot.strategies import flb, flb_analysis as fa
from bot.strategies.baseline import HoldFavorite

HOUR = 3600
GWU_MAKER_BENCHMARK = 0.026  # +2.6%/episode, makers >=50c (GWU 2026-001)


# ---------------------------------------------------------------------------
# Universe + time split
# ---------------------------------------------------------------------------

def _parse_when(s: str | None) -> int | None:
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return int(
            dt.datetime.fromisoformat(s)
            .replace(tzinfo=dt.timezone.utc)
            .timestamp()
        )


def build_universe(args) -> tuple[list[str], UniverseConfig]:
    exclude = list(DEFAULT_EXCLUDE_CATEGORIES)
    if args.include_sports and "Sports" in exclude:
        exclude.remove("Sports")
    if args.include_crypto and "Crypto" in exclude:
        exclude.remove("Crypto")
    cfg = UniverseConfig(
        categories=tuple(args.categories.split(",")) if args.categories else None,
        exclude_categories=tuple(exclude),
        min_volume=args.min_volume,
        close_after=_parse_when(args.close_after),
        close_before=_parse_when(args.close_before),
    )
    return select_universe(args.db, cfg), cfg


def split_train(
    provider: SqliteHistoryProvider, tickers: list[str], train_frac: float
) -> dict[str, Any]:
    """Assign each market to train/test by its settlement time (fallback:
    close time). Train = settle_ts <= t0 + train_frac * (t1 - t0). The
    test tail is recorded but NEVER run here."""
    settle_ts: dict[str, int] = {}
    close_ts: dict[str, int] = {}
    for m in provider.iter_markets(tickers=tickers):
        close_ts[m.ticker] = m.close_ts
        s = provider.settlement(m.ticker)
        settle_ts[m.ticker] = s.settled_ts if s else m.close_ts
    if not settle_ts:
        raise SystemExit("universe is empty - nothing to backtest")
    t0, t1 = min(settle_ts.values()), max(settle_ts.values())
    split_ts = t0 + int(train_frac * (t1 - t0))
    train = sorted(t for t, s in settle_ts.items() if s <= split_ts)
    test = sorted(t for t, s in settle_ts.items() if s > split_ts)
    return {
        "t0": t0,
        "t1": t1,
        "split_ts": split_ts,
        "train": train,
        "test_size_only": len(test),  # tickers withheld; not listed on purpose
        "close_ts": close_ts,
    }


# ---------------------------------------------------------------------------
# Variants
# ---------------------------------------------------------------------------

def fee_schedule_for(args) -> "FeeSchedule | None":
    """Exact per-series centicent fee model (SYNTHESIS §2.1): resolve
    fee_type/fee_multiplier from the live DB series table, falling back to
    the bundled snapshot; --legacy-fees keeps the old per-contract-ceil
    path (which overstates taker fees ~3x at 90-98c)."""
    if args.legacy_fees:
        return None
    try:
        return FeeSchedule.from_store(args.db)
    except Exception:
        return FeeSchedule.load_default()


def maker_queue_for(args) -> MakerQueueConfig:
    """Maker-queue model config (SPEC §3). Default: enabled with the
    fill-rate-calibrated depth_windows; --no-maker-queue restores the
    legacy every-qualifying-print model (comparison runs only);
    --depth-windows overrides the pessimism factor (calibration sweeps)."""
    if args.no_maker_queue:
        return MakerQueueConfig(enabled=False)
    if args.depth_windows is not None:
        return MakerQueueConfig(depth_windows=args.depth_windows)
    return MakerQueueConfig()


def variant_factories(args) -> dict[str, dict[str, Any]]:
    """name -> {factory, qualifier(close_ts map aware), description}."""
    cats = tuple(args.strategy_categories.split(",")) if args.strategy_categories else None
    replace = not args.no_replace
    v: dict[str, dict[str, Any]] = {
        "r1_taker": {
            "factory": lambda: flb.R1Taker(categories=cats),
            "qualifier": fa.r1_qualifier((70, 97), 10.0),
            "description": "buy 70-97c side at ask, <=10d to close, hold",
            "maker_variant": False,
        },
        "r2_maker": {
            "factory": lambda: flb.R2Maker(categories=cats,
                                           use_replace=replace),
            "qualifier": fa.r2_qualifier(50, 6 * HOUR, 10.0),
            "description": ">=50c side, rest at best bid, <=25% 1h vol, "
                           "reprice hourly (cancel/replace), "
                           "stand down final 6h",
            "maker_variant": True,
        },
    }
    for hours in (6, 12, 24):
        v[f"r5_endgame_{hours}h"] = {
            "factory": (lambda h=hours: flb.R5Endgame(window_s=h * HOUR,
                                                      categories=cats,
                                                      use_replace=replace)),
            "qualifier": fa.r5_qualifier((90, 98), hours * HOUR),
            "description": f"final {hours}h, maker-join bid on 90-98c side",
            "maker_variant": True,
        }
    v["baseline_hold_favorite"] = {
        "factory": lambda: HoldFavorite(),
        "qualifier": fa.r1_qualifier((90, 97), 10_000.0),
        "description": "null baseline: taker-buy YES 90-97c once, hold",
        "maker_variant": False,
    }
    if args.variants:
        keep = set(args.variants.split(","))
        unknown = keep - set(v)
        if unknown:
            raise SystemExit(f"unknown variants: {sorted(unknown)}")
        v = {k: v[k] for k in v if k in keep}
    return v


# ---------------------------------------------------------------------------
# Per-variant run
# ---------------------------------------------------------------------------

def run_variant(
    name: str,
    spec: dict[str, Any],
    provider: SqliteHistoryProvider,
    train: list[str],
    close_ts: dict[str, int],
    out_dir: str,
    args,
) -> dict[str, Any]:
    filters = {"tickers": train}
    vdir = os.path.join(out_dir, name)
    config = EngineConfig(
        fee_schedule=fee_schedule_for(args), maker_queue=maker_queue_for(args)
    )
    report = full_report(
        provider,
        spec["factory"],
        config=config,
        out_dir=vdir,
        market_filters=filters,
        fee_stress_multiplier=args.fee_stress,
        label=name,
    )
    # Re-run base deterministically for the raw fill/episode analysis
    # (full_report only returns the metric dict).
    res = run_backtest(provider, spec["factory"](), config, filters)
    eps = fa.episodes(res)
    ep_stats = fa.episode_return_stats(eps)
    extras = {
        "variant": name,
        "description": spec["description"],
        "episodes_n": len(eps),
        "episode_stats": ep_stats,
        "per_category": fa.per_category_stats(eps),
        "fill_stats": fa.fill_stats(res),
        "winrate_by_price": fa.winrate_by_price(res),
        "opportunity_lifetime": fa.opportunity_lifetime(
            provider, train, close_ts, spec["qualifier"]
        ),
    }
    os.makedirs(vdir, exist_ok=True)
    with open(os.path.join(vdir, "extras.json"), "w") as fh:
        json.dump(extras, fh, indent=2, default=str)
    if eps:
        with open(os.path.join(vdir, "episodes.csv"), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(eps[0].keys()))
            w.writeheader()
            w.writerows(eps)
    return {"report": report, "extras": extras}


# ---------------------------------------------------------------------------
# Kill-criteria verdicts (SYNTHESIS P-1; evaluated on TRAIN, provisional)
# ---------------------------------------------------------------------------

def kill_verdicts(name: str, out: dict[str, Any], span_years: float,
                  maker_variant: bool) -> dict[str, str]:
    ep = out["extras"]["episode_stats"]
    fills = out["extras"]["fill_stats"]
    stress = out["report"].get("fee_stress", {})
    v: dict[str, str] = {}
    if ep["n"] == 0:
        v["a_mean_return"] = "NO TRADES - cannot evaluate"
    elif ep["mean"] is None or ep["mean"] <= 0:
        v["a_mean_return"] = (
            f"FAIL (train): mean {ep['mean']:.4f} <= 0"
            if ep["mean"] is not None else "FAIL: no returns"
        )
    elif ep["ci95_lo"] is not None and ep["ci95_lo"] <= 0:
        v["a_mean_return"] = (
            f"BORDERLINE (train): mean {ep['mean']:+.4f}, event-clustered "
            f"95% CI [{ep['ci95_lo']:+.4f}, {ep['ci95_hi']:+.4f}] spans 0 "
            f"(n={ep['n']}, clusters={ep['n_clusters']})"
        )
    else:
        v["a_mean_return"] = (
            f"PASS (train, provisional): mean {ep['mean']:+.4f}, CI "
            f"[{ep['ci95_lo']:+.4f}, {ep['ci95_hi']:+.4f}] > 0 "
            f"(n={ep['n']}, clusters={ep['n_clusters']})"
        )
    v["b_2025_26_vs_2021_24"] = (
        "N/A on this dataset: entire pull is 2026; requires the full-history "
        "DB (2022+) to compare subsamples."
        if span_years < 1.5
        else "TODO: recompute on the full-history DB"
    )
    mrate = fills.get("maker_order_fill_rate")
    if not maker_variant:
        v["c_maker_fill_rate"] = "n/a (taker variant)"
    elif mrate is None:
        v["c_maker_fill_rate"] = "no maker orders placed"
    elif mrate > 0.60:
        v["c_maker_fill_rate"] = (
            f"FLAG: simulated maker order fill rate {mrate:.0%} > 60% - "
            "fill model may be lying (SPEC §3)"
        )
    else:
        v["c_maker_fill_rate"] = f"OK: maker order fill rate {mrate:.0%} (<=60%)"
    if not stress:
        v["d_fee_stress"] = "not computed"
    elif out["extras"]["episodes_n"] == 0:
        v["d_fee_stress"] = "NO TRADES"
    elif stress.get("still_positive"):
        v["d_fee_stress"] = (
            f"PASS: fee x{stress['multiplier']:g} net P&L "
            f"{stress['net_pnl_cents_after_inference'] / 100:+.2f} USD > 0"
        )
    else:
        v["d_fee_stress"] = (
            f"FAIL: fee x{stress['multiplier']:g} net P&L "
            f"{stress['net_pnl_cents_after_inference'] / 100:+.2f} USD <= 0"
        )
    return v


# ---------------------------------------------------------------------------
# ANALYSIS.md
# ---------------------------------------------------------------------------

def _fmt_pct(x, digits=2):
    return f"{x * 100:+.{digits}f}%" if x is not None else "n/a"


def _fmt_usd(cents):
    return f"${cents / 100:+,.2f}" if cents is not None else "n/a"


def _iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def write_analysis(
    out_dir: str,
    universe_meta: dict[str, Any],
    results: dict[str, dict[str, Any]],
    verdicts: dict[str, dict[str, str]],
    args,
) -> None:
    L: list[str] = []
    L.append("# P-1 FLB strategy family - TRAIN-split analysis")
    L.append("")
    L.append(f"Generated by `python -m bot.strategies.run_flb` on "
             f"{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%MZ')}. "
             f"**Train split only** (first {args.train_frac:.0%} of the dataset "
             "by settlement time). The held-out tail was selected but never "
             "run - it is reserved for the tournament (SPEC §7/§8). "
             "Re-running this command on an extended DB regenerates "
             "everything, split point included.")
    L.append("")
    L.append("## Dataset & universe")
    L.append("")
    L.append(f"- DB: `{args.db}`; universe: {universe_meta['n_universe']} settled "
             f"markets ({universe_meta['n_events']} events) with hourly candles, "
             f"volume >= {args.min_volume:g} contracts, categories excluded: "
             f"{universe_meta['excluded_categories']}.")
    L.append(f"- Data span (settlements): {_iso(universe_meta['t0'])} .. "
             f"{_iso(universe_meta['t1'])}; split at "
             f"{_iso(universe_meta['split_ts'])}.")
    L.append(f"- TRAIN: {universe_meta['n_train']} markets; held out (untouched): "
             f"{universe_meta['n_test']} markets.")
    L.append("- Fee model: " + (
        "legacy per-contract cent-ceil (conservative; --legacy-fees)."
        if args.legacy_fees else
        "exact per-series centicent model (fees.FeeSchedule; taker 0.07, "
        "maker 0.0175 on quadratic_with_maker_fees series only - maker "
        "coefficient still UNVERIFIED per fees.py, and the schedule is a "
        "present-day snapshot, not point-in-time)."
    ))
    L.append("- Maker fill model: " + (
        "LEGACY (--no-maker-queue): every qualifying print/candle fills a "
        "resting order up to the volume cap - known to overstate fill "
        "rates (SPEC §3 flag)."
        if args.no_maker_queue else
        "maker-queue (SPEC §3, 2026-08-12): resting orders join behind "
        f"depth_windows={maker_queue_for(args).depth_windows:g}x the recent "
        "per-window traded volume; only qualifying volume in excess of the "
        "queue fills them (fill-rate-calibrated to the 40-50% live-fill "
        "band, never on P&L - see reports/flb/QUEUE_IMPACT.md)."
    ))
    if args.universe_file:
        L.append(f"- Universe PINNED to `{args.universe_file}` (frozen "
                 "train tickers + split from a prior run; not re-selected "
                 "from the DB, which has since grown).")
    span_days = (universe_meta["t1"] - universe_meta["t0"]) / 86400
    L.append(f"- Span: {span_days:.0f} days. NOTE: with a ~95-day 2026-only "
             "pull, every number here is provisional; the GWU effect is "
             "measured on 2021-2025 and weakened in 2025 - the full-history "
             "re-run is the real test.")
    L.append("")
    L.append("## Frozen parameters (tuning log - no test-set tuning ever)")
    L.append("")
    L.append("All parameters below were fixed from the research documents "
             "before any backtest was run; nothing was tuned on results, and "
             "nothing here may be changed based on the held-out split.")
    L.append("")
    L.append("| variant | frozen parameters | honest p_hat (documented bias) |")
    L.append("|---|---|---|")
    L.append("| r1_taker | band 70-97c, <=10 days to close, buy at ask (ioc), "
             "hold to resolution, no category filter | price x 1.03 "
             "(GWU: +1-3%/episode post-fee above 70c; top of range so the "
             "fee-aware Kelly layer does not self-censor the 70-84c half of "
             "the band - see bot/strategies/flb.py docstring) |")
    L.append("| r2_maker | >=50c side by mid, join best bid (last-1c "
             "fallback), size = 25% x trailing-1h volume, reprice hourly, "
             "stand down final 6h, <=10 days to close | price x 1.026 "
             "(GWU headline: makers >=50c earn +2.6%/episode) |")
    L.append("| r5_endgame_{6,12,24}h | final window, maker-join bid on the "
             "side whose bid is 90-98c, size = 25% x trailing-1h volume | "
             "price x 1.013 (half the maker bias: GWU says maker returns "
             "deteriorate at closing prices) |")
    L.append("| baseline_hold_favorite | 90-97c YES taker buy once, hold "
             "(bot/strategies/baseline.py) | price + 2c |")
    L.append("")
    L.append("## Results (train split)")
    L.append("")
    L.append("| variant | episodes | orders | maker fill rate | net P&L | "
             "per-episode mean +/- SE (event-clustered) | dollar-weighted | "
             "fee x1.5 P&L | median opp. lifetime |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for name, out in results.items():
        ep = out["extras"]["episode_stats"]
        fs = out["extras"]["fill_stats"]
        pnl = out["report"]["base"]["pnl"]
        stress = out["report"]["fee_stress"]
        life = out["extras"]["opportunity_lifetime"]
        mean_se = (
            f"{_fmt_pct(ep['mean'])} +/- {_fmt_pct(ep['se'])}"
            if ep["mean"] is not None and ep["se"] is not None
            else "n/a"
        )
        mrate = fs["maker_order_fill_rate"]
        L.append(
            f"| {name} | {ep['n']} | {fs['n_orders']} | "
            f"{f'{mrate:.0%}' if mrate is not None else 'n/a'} | "
            f"{_fmt_usd(pnl['net_pnl_cents_after_inference'])} | {mean_se} | "
            f"{_fmt_pct(ep['dollar_weighted_return'])} | "
            f"{_fmt_usd(stress['net_pnl_cents_after_inference'])} | "
            f"{life['median_hours']:.0f}h |"
            if life["median_hours"] is not None
            else f"| {name} | {ep['n']} | {fs['n_orders']} | n/a | "
            f"{_fmt_usd(pnl['net_pnl_cents_after_inference'])} | {mean_se} | "
            f"{_fmt_pct(ep['dollar_weighted_return'])} | "
            f"{_fmt_usd(stress['net_pnl_cents_after_inference'])} | n/a |"
        )
    L.append("")
    L.append(f"Benchmark: GWU makers buying >=50c earn "
             f"**{GWU_MAKER_BENCHMARK:+.1%} per episode** after fees (SD 33%). "
             "Per-episode means above are directly comparable (same "
             "definition: one market bought and held to resolution).")
    L.append("")
    for name, out in results.items():
        ep = out["extras"]["episode_stats"]
        L.append(f"## {name}")
        L.append("")
        L.append(f"*{out['extras']['description']}*")
        L.append("")
        d = ep.get("distribution") or {}
        if ep["n"]:
            L.append(f"- Episodes: {ep['n']} across {ep['n_clusters']} events; "
                     f"mean return {_fmt_pct(ep['mean'])}, event-clustered SE "
                     f"{_fmt_pct(ep['se'])}, 95% CI "
                     f"[{_fmt_pct(ep['ci95_lo'])}, {_fmt_pct(ep['ci95_hi'])}].")
            L.append(f"- Distribution: min {_fmt_pct(d.get('min'))}, p25 "
                     f"{_fmt_pct(d.get('p25'))}, median {_fmt_pct(d.get('median'))}, "
                     f"p75 {_fmt_pct(d.get('p75'))}, max {_fmt_pct(d.get('max'))}; "
                     f"SD {_fmt_pct(d.get('sd'))}; "
                     f"{d.get('frac_positive', 0):.0%} of episodes positive.")
            L.append(f"- Capital: {_fmt_usd(ep['total_basis_cents'])} total "
                     f"premium deployed, net {_fmt_usd(ep['total_net_pnl_cents'])} "
                     f"({_fmt_pct(ep['dollar_weighted_return'])} dollar-weighted).")
        else:
            L.append("- No episodes (no qualifying fills on the train split).")
        cap = out["report"].get("capacity_curve", [])
        if cap:
            cap_s = ", ".join(
                f"{c['depth_multiplier']:g}x -> "
                f"{_fmt_usd(c['net_pnl_cents_after_inference'])}"
                f" ({c['contracts_filled']} fills)"
                for c in cap
            )
            L.append(f"- Capacity curve: {cap_s}.")
        life = out["extras"]["opportunity_lifetime"]
        if life["n_markets"]:
            L.append(f"- Opportunity lifetime (hours the entry condition held, "
                     f"from candles): median {life['median_hours']:.0f}h, mean "
                     f"{life['mean_hours']:.0f}h over {life['n_markets']} "
                     "qualifying markets - hours, not seconds, so the edge is "
                     "ours to trade (oss-arb §7.3 bar).")
        if ep["n"] and d.get("frac_positive") == 1.0:
            L.append("- **Zero losing episodes in this sample.** The "
                     "clustered CI above reflects only the dispersion of "
                     "winning returns; the true risk is the unobserved "
                     "failure tail (one favorite failing at 95c is a -100% "
                     "episode, which would move the mean by roughly "
                     f"-{1 / max(1, ep['n']):.0%} per occurrence). The mean "
                     "is an upper bound until the sample contains losses - "
                     "another reason the full-history re-run is binding.")
        cats = out["extras"]["per_category"]
        if cats:
            L.append("")
            L.append("| category | episodes | mean return | clustered SE | "
                     "net P&L |")
            L.append("|---|---|---|---|---|")
            for cat, cs in cats.items():
                L.append(f"| {cat} | {cs['n']} | {_fmt_pct(cs['mean'])} | "
                         f"{_fmt_pct(cs['se'])} | "
                         f"{_fmt_usd(cs['total_net_pnl_cents'])} |")
        wr = [r for r in out["extras"]["winrate_by_price"]]
        if wr and name.startswith("r5"):
            L.append("")
            L.append("Win-rate vs entry price (the R5 deliverable; "
                     "contract-weighted, breakeven = price + paid fee):")
            L.append("")
            L.append("| category | entry price | contracts | markets | "
                     "win rate | breakeven | above? |")
            L.append("|---|---|---|---|---|---|---|")
            for r in wr:
                L.append(
                    f"| {r['category']} | {r['entry_price_cents']}c | "
                    f"{r['contracts']} | {r['n_markets']} | "
                    f"{r['win_rate']:.3f} | {r['breakeven_win_rate']:.3f} | "
                    f"{'YES' if r['above_breakeven'] else 'no'} |"
                )
        L.append("")
        L.append("Kill criteria (SYNTHESIS P-1; **train-split verdicts, "
                 "provisional** - the binding evaluation is the held-out "
                 "split in the tournament):")
        L.append("")
        for k, verdict in verdicts[name].items():
            L.append(f"- ({k[0]}) {verdict}")
        L.append("")
    L.append("## Harness notes / issues")
    L.append("")
    L.append("1. **Cancel/replace: FIXED 2026-08-12** (OrderIntent.replace). "
             "R2/R5 'reprice hourly' is now a real cancel/replace: the old "
             "same-direction resting order is canceled (freeing its "
             "committed premium) and the new quote joins the simulated "
             "maker queue afresh. --no-replace restores the old stale-"
             "ladder behavior for comparisons.")
    L.append("2. **Fee estimation for sizing still ceils per contract.** "
             "Charged fees use the exact per-series centicent model (fixed "
             "mid-build by the harness team), but the Kelly sizing layer "
             "estimates the per-contract fee ceil-to-cent, so at 70-84c the "
             "estimated fee (2c) exceeds the true fee (~1.4-1.5c) and "
             "shrinks (or zeroes) positions whose stated edge is small. "
             "This is one reason R1 uses the top of GWU's bias range for "
             "p_hat (see flb.py docstring). Maker coefficient 0.0175 is "
             "still unverified, and fee configs are a present-day snapshot "
             "rather than point-in-time (/series/fee_changes).")
    L.append("3. **Sparse trade tape.** Only a handful of universe tickers "
             "have trades locally, so most maker fills come from the "
             "conservative candle OHLC rule (low strictly through the limit, "
             "<=25% of hourly volume) rather than tape prints. The maker-"
             "queue model layers on top of that rule; its depth estimate is "
             "a volume proxy, not real book depth (candles carry no sizes).")
    L.append("4. **Candle coverage defines the universe.** The universe is a "
             "property of the local dataset (universe.py docstring); the "
             "full-history pull grew it from ~300 to >10k markets. Use "
             "--universe-file to reproduce a prior run's frozen universe.")
    L.append("5. **Depth pre-clamp: FIXED 2026-08-12.** The risk layer's "
             "3-candle depth pre-clamp now applies to taker intents only; "
             "resting orders are bounded at fill time by the per-print/"
             "per-candle caps plus the maker queue (pre-clamping them too "
             "double-counted the same mechanism and zeroed quiet-hour "
             "quotes).")
    L.append("")
    with open(os.path.join(out_dir, "ANALYSIS.md"), "w") as fh:
        fh.write("\n".join(L))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default="data/kalshi.db")
    ap.add_argument("--out", default="reports/flb")
    ap.add_argument("--train-frac", type=float, default=0.6)
    ap.add_argument("--variants", default=None,
                    help="comma list; default all "
                         "(r1_taker,r2_maker,r5_endgame_{6,12,24}h,"
                         "baseline_hold_favorite)")
    ap.add_argument("--min-volume", type=float, default=1000.0)
    ap.add_argument("--categories", default=None,
                    help="universe: only these categories (comma list)")
    ap.add_argument("--strategy-categories", default=None,
                    help="strategy-level category filter (ablations)")
    ap.add_argument("--include-sports", action="store_true")
    ap.add_argument("--include-crypto", action="store_true")
    ap.add_argument("--close-after", default=None,
                    help="ISO date or unix ts (full-history runs)")
    ap.add_argument("--close-before", default=None)
    ap.add_argument("--fee-stress", type=float, default=1.5)
    ap.add_argument("--legacy-fees", action="store_true",
                    help="use the old per-contract-ceil fee path instead of "
                         "the exact per-series centicent model")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap universe size (smoke runs)")
    ap.add_argument("--universe-file", default=None,
                    help="pin the run to a prior universe.json (frozen "
                         "train tickers + split) instead of re-selecting "
                         "from the DB - required for apples-to-apples "
                         "fill-model comparisons after the DB grows")
    ap.add_argument("--no-maker-queue", action="store_true",
                    help="disable the maker-queue fill model (legacy "
                         "every-qualifying-print fills; comparison runs "
                         "only - the queue model is the honest default)")
    ap.add_argument("--no-replace", action="store_true",
                    help="strategies do not use cancel/replace (legacy "
                         "stale-ladder repricing; comparison runs only)")
    ap.add_argument("--depth-windows", type=float, default=None,
                    help="override MakerQueueConfig.depth_windows "
                         "(calibration sweeps)")
    args = ap.parse_args(argv)

    provider = SqliteHistoryProvider(args.db)
    if args.universe_file:
        # Pinned mode: replay a prior run's frozen train universe + split
        # verbatim (the held-out tail stays untouched, as always).
        with open(args.universe_file) as fh:
            uj = json.load(fh)
        train = list(uj["train_tickers"])
        close_ts = {
            m.ticker: m.close_ts for m in provider.iter_markets(tickers=train)
        }
        split = {
            "t0": uj["t0"], "t1": uj["t1"], "split_ts": uj["split_ts"],
            "train": train, "test_size_only": uj["n_test"],
            "close_ts": close_ts,
        }
        universe_meta = {
            k: uj[k]
            for k in ("n_universe", "n_events", "n_train", "n_test",
                      "t0", "t1", "split_ts", "excluded_categories")
        }
        universe = train  # only the train half is ever run here
        os.makedirs(args.out, exist_ok=True)
        # do NOT rewrite universe.json: the pinned file stays the record
    else:
        universe, ucfg = build_universe(args)
        if args.limit:
            universe = universe[: args.limit]
        split = split_train(provider, universe, args.train_frac)
        train = split["train"]
        events = set()
        for m in provider.iter_markets(tickers=universe):
            events.add(m.event_ticker)
        universe_meta = {
            "n_universe": len(universe),
            "n_events": len(events),
            "n_train": len(train),
            "n_test": split["test_size_only"],
            "t0": split["t0"],
            "t1": split["t1"],
            "split_ts": split["split_ts"],
            "excluded_categories": list(ucfg.exclude_categories),
        }
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "universe.json"), "w") as fh:
            json.dump(
                {**universe_meta, "train_tickers": train,
                 "universe_config": str(ucfg)}, fh, indent=2,
            )
    print(f"universe={len(universe)} train={len(train)} "
          f"held-out={split['test_size_only']} "
          f"span={_iso(split['t0'])}..{_iso(split['t1'])} "
          f"split={_iso(split['split_ts'])}")

    span_years = (split["t1"] - split["t0"]) / (365 * 86400)
    results: dict[str, dict[str, Any]] = {}
    verdicts: dict[str, dict[str, str]] = {}
    for name, spec in variant_factories(args).items():
        print(f"-- running {name} ...", flush=True)
        out = run_variant(
            name, spec, provider, train, split["close_ts"], args.out, args
        )
        results[name] = out
        verdicts[name] = kill_verdicts(name, out, span_years,
                                       spec["maker_variant"])
        ep = out["extras"]["episode_stats"]
        print(f"   episodes={ep['n']} mean={ep['mean']} se={ep['se']} "
              f"netP&L={out['report']['base']['pnl']['net_pnl_cents_after_inference']}c")
    write_analysis(args.out, universe_meta, results, verdicts, args)
    print(f"wrote {os.path.join(args.out, 'ANALYSIS.md')}")


if __name__ == "__main__":
    main()

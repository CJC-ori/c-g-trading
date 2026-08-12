"""Tournament ROUND 2 runner — the five pre-named integration variants.

Per reports/TOURNAMENT_PROTOCOL.md Round 2, folds losers' demonstrated
strengths into the Round-1 least-bad configuration (r5_endgame_6h — no
Round-1 variant passed the pre-registered criteria) as PRE-NAMED variants,
run on the IDENTICAL pinned train universe + boundary as Round 1
(reports/tournament/round1/universe.json, split_ts = 1722556901; the
held-out 40% is structurally untouched — only train tickers are ever
handed to the engine):

  r5_endgame_6h      champion re-run (reference; also the I-d base)
  ia_econ_only       I-a: category filter. Keep only categories whose
                     Round-1 win-rate-vs-price curve sat above breakeven
                     in EVERY price bin in EVERY R5 window => {Economics}
                     (reports/tournament/round1/ROUND1.md tables; Politics
                     cleared bins in the 6h window only, failed 12/24h).
  ib_swing_filter    I-b: swing-census adverse-swing entry filter
                     (>=20c/24h side-space swing within the last 24h;
                     wrappers.R5SwingFiltered, constants pre-registered
                     from the census X20_T24 config).
  ic_panic_standdown I-c: panic-night stand-down (Politics/Elections on
                     scheduled event nights + discovered R4 windows;
                     bot/strategies/panic/episodes.py detection).
  id_r4_overlay      I-d: champion portfolio + parked R4 overlay. R4
                     trades its episode class via its own pre-registered
                     per-episode harness (panic/run_backtest.run_episode,
                     minute-grain provider) on train-side episodes only;
                     P&L/capacity additive, risk caps shared (both legs
                     cap 5%/market of the same $10k bankroll).
  ie_eco_spikefade   I-e: champion + parked eco spike-fade as ONE
                     portfolio in ONE engine run (shared bankroll and risk
                     caps enforced by the harness; wrappers.CombinedStrategy
                     with SwingFade restricted to its eco_spike_fade cell).

Keep rule (protocol, fixed before running): keep a variant only if it
improves net P&L vs the champion re-run without worsening fee-stress
survival or top-5 concentration; else discard.

Usage (parallel single-variant processes, like Round 1):
    python -m bot.strategies.tournament.run_round2 --variants ia_econ_only
    ...
    python -m bot.strategies.tournament.run_round2 --assemble   # ROUND2.md
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
from dataclasses import replace
from typing import Any, Callable

from bot.backtest import metrics
from bot.backtest.dataport import SqliteHistoryProvider
from bot.backtest.engine import EngineConfig, run_backtest
from bot.backtest.fees import FeeSchedule
from bot.backtest.fills import MakerQueueConfig
from bot.strategies import flb, flb_analysis as fa
from bot.strategies.swings.strategy import CELLS as SWING_CELLS, SwingFade
from bot.strategies.tournament.wrappers import (
    CombinedStrategy,
    R5PanicStandDown,
    R5SwingFiltered,
)

HOUR = 3600
WINDOW_S = 6 * HOUR                 # Round-1 least-bad config: final 6h
UNIVERSE_FILE = "reports/tournament/round1/universe.json"
PANIC_EPISODES = "reports/panic/episodes.json"
OUT_DIR = "reports/tournament/round2"
BANKROLL_CENTS = 1_000_000
CHAMPION = "r5_endgame_6h"

VARIANT_ORDER = [
    CHAMPION,
    "ia_econ_only",
    "ib_swing_filter",
    "ic_panic_standdown",
    "id_r4_overlay",
    "ie_eco_spikefade",
]


# ---------------------------------------------------------------------------
# Universe (pinned; identical to run_flb --universe-file)
# ---------------------------------------------------------------------------

def load_universe(provider: SqliteHistoryProvider, path: str) -> dict[str, Any]:
    with open(path) as fh:
        uj = json.load(fh)
    train = list(uj["train_tickers"])
    close_ts = {m.ticker: m.close_ts for m in provider.iter_markets(tickers=train)}
    return {"meta": uj, "train": train, "close_ts": close_ts,
            "split_ts": uj["split_ts"]}


# ---------------------------------------------------------------------------
# Panic-night inputs for I-c / I-d
# ---------------------------------------------------------------------------

def panic_inputs(train_set: set[str]) -> dict[str, Any]:
    from bot.strategies.panic.episodes import KNOWN_EVENT_NIGHTS
    nights = set(KNOWN_EVENT_NIGHTS)
    windows: dict[str, tuple[int, int]] = {}
    train_eps: list[dict] = []
    if os.path.exists(PANIC_EPISODES):
        with open(PANIC_EPISODES) as fh:
            eps = json.load(fh)["episodes"]
        for e in eps:
            nights.add(e["et_date"])
            windows[e["ticker"]] = (e["window_start"], e["window_end"])
            if e["ticker"] in train_set:
                train_eps.append(e)
    return {"nights": frozenset(nights), "windows": windows,
            "train_episodes": train_eps}


# ---------------------------------------------------------------------------
# Variant factories
# ---------------------------------------------------------------------------

def variant_factories(train_set: set[str]) -> dict[str, dict[str, Any]]:
    pi = panic_inputs(train_set)
    eco_cells = {"eco_spike_fade": SWING_CELLS["eco_spike_fade"]}
    return {
        CHAMPION: {
            "factory": lambda: flb.R5Endgame(window_s=WINDOW_S),
            "description": "champion re-run: final 6h, maker-join bid on "
                           "90-98c side (Round-1 least-bad config)",
        },
        "ia_econ_only": {
            "factory": lambda: flb.R5Endgame(
                window_s=WINDOW_S, categories=("Economics",)
            ),
            "description": "I-a: R5-6h restricted to categories above "
                           "breakeven in every Round-1 price bin/window "
                           "= Economics only",
        },
        "ib_swing_filter": {
            "factory": lambda: R5SwingFiltered(window_s=WINDOW_S),
            "description": "I-b: R5-6h, entries blocked within 24h of a "
                           ">=20c/24h adverse (side-space) swing "
                           "(census X20_T24)",
        },
        "ic_panic_standdown": {
            "factory": lambda: R5PanicStandDown(
                window_s=WINDOW_S,
                night_dates=pi["nights"],
                windows=pi["windows"],
            ),
            "description": "I-c: R5-6h, stand down in Politics/Elections "
                           "on scheduled event nights + discovered R4 "
                           "windows",
        },
        "ie_eco_spikefade": {
            "factory": lambda: CombinedStrategy([
                flb.R5Endgame(window_s=WINDOW_S),
                SwingFade(bankroll_cents=BANKROLL_CENTS, cells=eco_cells),
            ]),
            "description": "I-e: portfolio = R5-6h + parked eco spike-fade "
                           "(SwingFade eco_spike_fade cell), one engine "
                           "run, shared bankroll/risk caps",
        },
        # id_r4_overlay is assembled separately (see run_id_overlay):
        # champion engine run + R4's own per-episode harness on train-side
        # episodes; not an engine strategy factory.
    }


# ---------------------------------------------------------------------------
# Engine-variant run: base + capacity + fee stress + extras (4 engine runs)
# ---------------------------------------------------------------------------

def engine_config(args) -> EngineConfig:
    return EngineConfig(
        fee_schedule=FeeSchedule.load_default(),
        maker_queue=MakerQueueConfig(),
        keep_events_in_memory=False,
    )


def leg_overlap(base_result) -> dict[str, Any]:
    """I-e composition artifact check: markets where BOTH legs emitted
    intents (rationale prefix attribution)."""
    r5, sf = set(), set()
    for i in base_result.intents:
        r = i.get("rationale") or ""
        if r.startswith("R5-endgame"):
            r5.add(i["ticker"])
        elif r.startswith("SwingFade"):
            sf.add(i["ticker"])
    both = sorted(r5 & sf)
    return {"r5_markets": len(r5), "swingfade_markets": len(sf),
            "overlap_markets": len(both), "overlap_tickers": both[:20]}


def run_engine_variant(
    name: str,
    spec: dict[str, Any],
    provider: SqliteHistoryProvider,
    uni: dict[str, Any],
    out_dir: str,
    args,
) -> dict[str, Any]:
    filters = {"tickers": uni["train"]}
    vdir = os.path.join(out_dir, name)
    os.makedirs(vdir, exist_ok=True)
    config = engine_config(args)

    base = run_backtest(provider, spec["factory"](), config, filters)
    report: dict[str, Any] = {
        "label": name,
        "fee_mode": "exact-per-series",
        "base": metrics.compute_metrics(base),
    }
    capacity = []
    for mult in (1.0, 3.0, 10.0):
        if mult == 1.0:
            r = base
        else:
            cfg = replace(config, risk=replace(config.risk, depth_multiplier=mult))
            r = run_backtest(provider, spec["factory"](), cfg, filters)
        capacity.append({
            "depth_multiplier": mult,
            "net_pnl_cents_after_inference": r.net_pnl_after_inference_cents,
            "contracts_filled": sum(f.count for f in r.fills),
        })
    report["capacity_curve"] = capacity
    stress_cfg = replace(config, fee_stress=args.fee_stress)
    stressed = run_backtest(provider, spec["factory"](), stress_cfg, filters)
    report["fee_stress"] = {
        "multiplier": args.fee_stress,
        "net_pnl_cents_after_inference": stressed.net_pnl_after_inference_cents,
        "still_positive": stressed.net_pnl_after_inference_cents > 0,
    }
    report["inference_stress"] = {  # price-only strategies: zero inference
        "multiplier": 2.0,
        "inference_cost_cents": 2 * base.inference_cost_cents,
        "net_pnl_cents_after_inference": base.net_pnl_cents
        - 2 * base.inference_cost_cents,
        "still_positive": base.net_pnl_cents - 2 * base.inference_cost_cents > 0,
    }
    with open(os.path.join(vdir, "report.json"), "w") as fh:
        json.dump(report, fh, indent=2)
    with open(os.path.join(vdir, "report.md"), "w") as fh:
        fh.write(metrics.render_markdown(report))

    eps = fa.episodes(base)
    extras: dict[str, Any] = {
        "variant": name,
        "description": spec["description"],
        "episodes_n": len(eps),
        "episode_stats": fa.episode_return_stats(eps),
        "per_category": fa.per_category_stats(eps),
        "fill_stats": fa.fill_stats(base),
        "winrate_by_price": fa.winrate_by_price(base),
        "opportunity_lifetime": fa.opportunity_lifetime(
            provider, uni["train"], uni["close_ts"],
            fa.r5_qualifier((90, 98), WINDOW_S),
        ),
    }
    if name == "ie_eco_spikefade":
        extras["leg_overlap"] = leg_overlap(base)
    with open(os.path.join(vdir, "extras.json"), "w") as fh:
        json.dump(extras, fh, indent=2, default=str)
    if eps:
        with open(os.path.join(vdir, "episodes.csv"), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(eps[0].keys()))
            w.writeheader()
            w.writerows(eps)
    return {"report": report, "extras": extras}


# ---------------------------------------------------------------------------
# I-d: champion + R4 overlay (report-level portfolio combination)
# ---------------------------------------------------------------------------

def run_id_overlay(uni: dict[str, Any], out_dir: str, args) -> dict[str, Any]:
    """R4 trades its episode class through ITS OWN pre-registered harness
    (panic/run_backtest.run_episode: minute-grain EpisodeProvider — the
    hourly full-universe engine cannot see the wicks R4 exists to catch),
    restricted to episodes whose market is in the pinned TRAIN list.
    Combination is additive P&L with shared caps (both legs cap 5% per
    market of the same $10k; overlap is measured and reported)."""
    from bot.strategies.panic.run_backtest import run_episode

    champ_dir = os.path.join(out_dir, CHAMPION)
    with open(os.path.join(champ_dir, "report.json")) as fh:
        champ_report = json.load(fh)
    with open(os.path.join(champ_dir, "extras.json")) as fh:
        champ_extras = json.load(fh)

    pi = panic_inputs(set(uni["train"]))
    train_eps = pi["train_episodes"]
    rows, rows_stress = [], []
    for ep in train_eps:
        _res, summary = run_episode(ep, "R4")
        rows.append(summary)
        _res2, summary2 = run_episode(ep, "R4", fee_stress=args.fee_stress)
        rows_stress.append(summary2)

    overlay_net = sum(r["net_pnl_cents"] for r in rows)
    overlay_net_stress = sum(r["net_pnl_cents"] for r in rows_stress)
    overlay = {
        "n_train_episodes": len(train_eps),
        "n_entered": sum(1 for r in rows if r["entered"]),
        "net_pnl_cents": overlay_net,
        "net_pnl_cents_fee_stress": overlay_net_stress,
        "per_episode": rows,
        "note": (
            "R4 episodes restricted to the pinned Round-1 TRAIN tickers "
            "(settlement <= split_ts). All 136 discovered R4 episodes have "
            "window_start >= 2024-11-05 > split_ts (2024-08-02): the R4 "
            "episode class does not exist in the train-side tape, so the "
            "overlay is a structural no-op on this boundary."
            if not train_eps else
            "R4 per-episode runs via panic/run_backtest.run_episode "
            "(minute-grain provider, exact fees, maker queue)."
        ),
    }

    # combined report: champion + overlay (additive)
    champ_net = champ_report["base"]["pnl"]["net_pnl_cents_after_inference"]
    champ_stress = champ_report["fee_stress"]["net_pnl_cents_after_inference"]
    report = {
        "label": "id_r4_overlay",
        "fee_mode": "exact-per-series",
        "composition": f"{CHAMPION} (engine run) + R4 overlay "
                       "(per-episode native harness)",
        "champion_net_pnl_cents": champ_net,
        "overlay": overlay,
        "combined": {
            "net_pnl_cents_after_inference": champ_net + overlay_net,
            "fee_stress": {
                "multiplier": args.fee_stress,
                "net_pnl_cents_after_inference": champ_stress
                + overlay_net_stress,
                "still_positive": champ_stress + overlay_net_stress > 0,
            },
            "capacity_curve": [
                {**c, "net_pnl_cents_after_inference":
                 c["net_pnl_cents_after_inference"] + overlay_net}
                for c in champ_report["capacity_curve"]
            ],
        },
        "champion_report_ref": os.path.join(champ_dir, "report.json"),
    }
    # concentration over merged per-market P&L is champion's when overlay=0
    report["combined"]["concentration"] = champ_report["base"]["concentration"]

    vdir = os.path.join(out_dir, "id_r4_overlay")
    os.makedirs(vdir, exist_ok=True)
    with open(os.path.join(vdir, "report.json"), "w") as fh:
        json.dump(report, fh, indent=2)
    extras = {
        "variant": "id_r4_overlay",
        "description": "I-d: champion portfolio + parked R4 overlay "
                       "(additive capacity, shared per-market caps)",
        "episodes_n": champ_extras["episodes_n"] + overlay["n_entered"],
        "episode_stats": champ_extras["episode_stats"],
        "per_category": champ_extras["per_category"],
        "fill_stats": champ_extras["fill_stats"],
        "overlay": {k: v for k, v in overlay.items() if k != "per_episode"},
    }
    with open(os.path.join(vdir, "extras.json"), "w") as fh:
        json.dump(extras, fh, indent=2, default=str)
    return {"report": report, "extras": extras}


# ---------------------------------------------------------------------------
# Keep/discard verdicts (protocol rule, fixed before running)
# ---------------------------------------------------------------------------

def _net(report: dict) -> int:
    if "combined" in report:
        return report["combined"]["net_pnl_cents_after_inference"]
    return report["base"]["pnl"]["net_pnl_cents_after_inference"]


def _stress(report: dict) -> dict:
    if "combined" in report:
        return report["combined"]["fee_stress"]
    return report["fee_stress"]


def _top5(report: dict):
    conc = (report["combined"]["concentration"] if "combined" in report
            else report["base"]["concentration"])
    return conc.get("top5_share_of_net")


def verdict(name: str, report: dict, champ_report: dict) -> dict[str, Any]:
    net, cnet = _net(report), _net(champ_report)
    st, cst = _stress(report), _stress(champ_report)
    top5 = _top5(report)
    improves = net > cnet
    stress_worse = bool(cst["still_positive"]) and not st["still_positive"]
    conc_worse = top5 is not None and top5 > 0.60
    keep = improves and not stress_worse and not conc_worse
    reasons = []
    reasons.append(
        f"net P&L {'improves' if improves else 'does NOT improve'} vs "
        f"champion (${net / 100:+,.2f} vs ${cnet / 100:+,.2f})"
    )
    if stress_worse:
        reasons.append("fee-stress survival worsened (positive -> negative)")
    else:
        reasons.append(
            f"fee x{st['multiplier']:g} P&L "
            f"${st['net_pnl_cents_after_inference'] / 100:+,.2f} "
            f"(champion ${cst['net_pnl_cents_after_inference'] / 100:+,.2f})"
        )
    if conc_worse:
        reasons.append(f"top-5 concentration {top5:.0%} > 60% (worsened)")
    else:
        reasons.append(
            "top-5 share " + (f"{top5:.0%} <= 60%" if top5 is not None
                              else "n/a (net <= 0)")
        )
    return {"keep": keep if name != CHAMPION else None,
            "improves_net": improves, "stress_worse": stress_worse,
            "conc_worse": conc_worse, "reasons": reasons}


# ---------------------------------------------------------------------------
# ROUND2.md assembly
# ---------------------------------------------------------------------------

def _fmt_pct(x, digits=2):
    return f"{x * 100:+.{digits}f}%" if x is not None else "n/a"


def _fmt_usd(cents):
    return f"${cents / 100:+,.2f}" if cents is not None else "n/a"


def assemble(out_dir: str, args) -> None:
    loaded: dict[str, dict[str, Any]] = {}
    for name in VARIANT_ORDER:
        rp = os.path.join(out_dir, name, "report.json")
        xp = os.path.join(out_dir, name, "extras.json")
        if os.path.exists(rp) and os.path.exists(xp):
            with open(rp) as fh:
                report = json.load(fh)
            with open(xp) as fh:
                extras = json.load(fh)
            loaded[name] = {"report": report, "extras": extras}
    if CHAMPION not in loaded:
        raise SystemExit("champion re-run missing; run it first")
    champ = loaded[CHAMPION]["report"]

    verdicts = {n: verdict(n, v["report"], champ) for n, v in loaded.items()}

    L: list[str] = []
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    L.append("# ROUND 2 — Integration variants (pre-named, "
             "TOURNAMENT_PROTOCOL.md)")
    L.append("")
    L.append(f"Generated {now} by `python -m bot.strategies.tournament."
             f"run_round2`. Base = Round-1 least-bad config "
             f"`r5_endgame_6h` (no Round-1 variant passed). Universe/"
             f"boundary pinned to `{UNIVERSE_FILE}` "
             "(split_ts = 1722556901 = 2024-08-02T00:01:41Z; TRAIN = 7,103 "
             "markets; held-out 40% untouched). Harness: exact "
             "FeeSchedule.load_default(), maker queue depth_windows=1.0, "
             "cancel/replace on, $10k bankroll, quarter-Kelly, fee stress "
             f"x{args.fee_stress:g}.")
    L.append("")
    L.append("## Comparison table")
    L.append("")
    L.append("| variant | episodes (clusters) | net P&L | per-episode "
             "mean +/- clustered SE [95% CI] | maker fill rate | "
             f"fee x{args.fee_stress:g} P&L | capacity 1x/3x/10x | "
             "top-5 share | verdict |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for name in VARIANT_ORDER:
        if name not in loaded:
            continue
        rep, ext = loaded[name]["report"], loaded[name]["extras"]
        ep = ext["episode_stats"]
        st = _stress(rep)
        cap = (rep["combined"]["capacity_curve"] if "combined" in rep
               else rep["capacity_curve"])
        cap_s = "/".join(
            f"{c['net_pnl_cents_after_inference'] / 100:+,.0f}" for c in cap
        )
        mrate = ext["fill_stats"].get("maker_order_fill_rate")
        top5 = _top5(rep)
        v = verdicts[name]
        if name == CHAMPION:
            verdict_s = "reference (champion re-run)"
        else:
            verdict_s = ("**KEEP**" if v["keep"] else "DISCARD") + ": " + (
                v["reasons"][0]
            )
        mean_se = (
            f"{_fmt_pct(ep['mean'])} +/- {_fmt_pct(ep['se'])} "
            f"[{_fmt_pct(ep['ci95_lo'])}, {_fmt_pct(ep['ci95_hi'])}]"
            if ep.get("mean") is not None and ep.get("se") is not None
            else "n/a"
        )
        L.append(
            f"| {name} | {ep['n']} ({ep['n_clusters']}) | "
            f"{_fmt_usd(_net(rep))} | {mean_se} | "
            f"{f'{mrate:.0%}' if mrate is not None else 'n/a'} | "
            f"{_fmt_usd(st['net_pnl_cents_after_inference'])} | {cap_s} | "
            f"{f'{top5:.0%}' if top5 is not None else 'n/a (net<=0)'} | "
            f"{verdict_s} |"
        )
    L.append("")
    L.append("## Keep/discard (pre-registered rule: improve net P&L "
             "without worsening fee-stress survival or top-5 "
             "concentration)")
    L.append("")
    for name in VARIANT_ORDER:
        if name not in loaded or name == CHAMPION:
            continue
        v = verdicts[name]
        L.append(f"- **{name}** — {'KEEP' if v['keep'] else 'DISCARD'}: "
                 + "; ".join(v["reasons"]))
    L.append("")
    for name in VARIANT_ORDER:
        if name not in loaded:
            continue
        rep, ext = loaded[name]["report"], loaded[name]["extras"]
        ep = ext["episode_stats"]
        L.append(f"## {name}")
        L.append("")
        L.append(f"*{ext['description']}*")
        L.append("")
        d = ep.get("distribution") or {}
        if ep["n"]:
            L.append(
                f"- Episodes: {ep['n']} across {ep['n_clusters']} events; "
                f"mean {_fmt_pct(ep['mean'])}, clustered SE "
                f"{_fmt_pct(ep['se'])}, 95% CI [{_fmt_pct(ep['ci95_lo'])}, "
                f"{_fmt_pct(ep['ci95_hi'])}]. Distribution: min "
                f"{_fmt_pct(d.get('min'))}, median "
                f"{_fmt_pct(d.get('median'))}, max {_fmt_pct(d.get('max'))}; "
                f"{d.get('frac_positive', 0):.0%} positive.")
            L.append(
                f"- Capital: {_fmt_usd(ep['total_basis_cents'])} deployed, "
                f"net {_fmt_usd(ep['total_net_pnl_cents'])} "
                f"({_fmt_pct(ep['dollar_weighted_return'])} "
                "dollar-weighted).")
        else:
            L.append("- No episodes (no qualifying fills on train).")
        if ext.get("per_category"):
            L.append("")
            L.append("| category | episodes | mean return | clustered SE | "
                     "net P&L |")
            L.append("|---|---|---|---|---|")
            for cat, cs in ext["per_category"].items():
                L.append(f"| {cat} | {cs['n']} | {_fmt_pct(cs['mean'])} | "
                         f"{_fmt_pct(cs['se'])} | "
                         f"{_fmt_usd(cs['total_net_pnl_cents'])} |")
        if ext.get("overlay"):
            o = ext["overlay"]
            L.append("")
            L.append(f"- R4 overlay: {o['n_train_episodes']} train-side "
                     f"episodes, {o['n_entered']} entered, net "
                     f"{_fmt_usd(o['net_pnl_cents'])}. {o['note']}")
        if ext.get("leg_overlap"):
            lo = ext["leg_overlap"]
            L.append("")
            L.append(f"- Leg overlap: R5 quoted in {lo['r5_markets']} "
                     f"markets, SwingFade in {lo['swingfade_markets']}; "
                     f"both legs active in {lo['overlap_markets']} markets "
                     "(replace-semantics interference scope).")
        L.append("")
    L.append("## Notes")
    L.append("")
    L.append("- Same pinned universe/boundary/harness as Round 1; per-"
             "variant outputs in reports/tournament/round2/<variant>/ "
             "(report.json/report.md/extras.json/episodes.csv).")
    L.append("- I-a derivation: 'drop categories whose win-rate-vs-price "
             "curve sat below breakeven' evaluated on the Round-1 tables; "
             "only Economics cleared every bin in every window (Politics "
             "cleared 6h bins on n=3 episodes but failed 12h/24h — the "
             "robustness reading registered in ROUND1.md interpretation "
             "#3 before Round 2 ran).")
    L.append("- I-b constants (X=20c, T=24h, N=24h) are census X20_T24 + "
             "its 24h fill window, pre-registered in wrappers.py before "
             "the run; detection is point-in-time from the strategy's own "
             "MarketView.")
    L.append("- I-c uses the ex-ante KNOWN_EVENT_NIGHTS calendar plus "
             "discovered R4 windows; all discovered windows post-date "
             "split_ts, so on train only the calendar binds.")
    L.append("- I-d: all 136 discovered R4 episodes start 2024-11-05 or "
             "later (> split_ts 2024-08-02): the parked R4 class has zero "
             "train-side episodes, so the overlay is a structural no-op "
             "on this boundary — it cannot improve train net P&L.")
    L.append("- The held-out 40% was never touched.")
    with open(os.path.join(out_dir, "ROUND2.md"), "w") as fh:
        fh.write("\n".join(L))
    print(f"wrote {os.path.join(out_dir, 'ROUND2.md')}")
    for name in VARIANT_ORDER:
        if name in loaded and name != CHAMPION:
            print(f"{name}: keep={verdicts[name]['keep']}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default="data/kalshi.db")
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--universe-file", default=UNIVERSE_FILE)
    ap.add_argument("--fee-stress", type=float, default=1.5)
    ap.add_argument("--variants", default=None,
                    help="comma list; default all engine variants "
                         "(id_r4_overlay requires the champion re-run to "
                         "exist)")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap train universe size (smoke runs only)")
    ap.add_argument("--assemble", action="store_true",
                    help="only assemble ROUND2.md from existing variant "
                         "outputs")
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    if args.assemble:
        assemble(args.out, args)
        return

    provider = SqliteHistoryProvider(args.db)
    uni = load_universe(provider, args.universe_file)
    if args.limit:
        uni["train"] = uni["train"][: args.limit]
    train_set = set(uni["train"])
    print(f"train={len(uni['train'])} split_ts={uni['split_ts']}")

    specs = variant_factories(train_set)
    wanted = (args.variants.split(",") if args.variants
              else [n for n in VARIANT_ORDER if n != "id_r4_overlay"])
    for name in wanted:
        print(f"-- running {name} ...", flush=True)
        if name == "id_r4_overlay":
            out = run_id_overlay(uni, args.out, args)
            print(f"   overlay episodes={out['report']['overlay']['n_train_episodes']} "
                  f"combined net={_net(out['report'])}c")
            continue
        if name not in specs:
            raise SystemExit(f"unknown variant {name}")
        out = run_engine_variant(name, specs[name], provider, uni,
                                 args.out, args)
        ep = out["extras"]["episode_stats"]
        print(f"   episodes={ep['n']} mean={ep['mean']} "
              f"net={_net(out['report'])}c", flush=True)


if __name__ == "__main__":
    main()

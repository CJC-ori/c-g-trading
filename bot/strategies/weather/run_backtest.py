"""P-2 weather backtest runner (train split only — SPEC §7/§8).

Replays the KXHIGH* daily-high markets in data/kalshi.db against
point-in-time previous-runs forecasts (day-ahead variant) and the
4 PM max-so-far reconstruction (intraday variant).

Discipline:
- Bias offsets + residual sd: fit on 2025-08-12..2026-05-20 previous-runs
  vs ACIS — strictly BEFORE the backtest window (2026-05-21+).
- Rise-after-4PM PMFs: fit on 2024-06-01..2026-05-20 ACIS hourly/daily —
  also pre-window.
- Forecasts at decision time: previous-runs lead-2 on D-1, lead-1 on the
  morning of D (never current-run data for past dates).
- Time split: train = first 60% of target DATES; the last 40% is never
  loaded into the engine here (held out for the tournament).

Usage: python -m bot.strategies.weather.run_backtest [--variant dayahead|intraday|both]
Writes reports/weather/report_<variant>.{json,md}.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sqlite3
from collections import defaultdict
from dataclasses import replace
from typing import Any

from bot.backtest.engine import BacktestResult, EngineConfig, run_backtest
from bot.strategies.weather.dataport_ext import TradeSynthCandleProvider
from bot.backtest.metrics import compute_metrics, render_markdown
from bot.backtest.risk import RiskConfig
from bot.groundtruth import weather as wx
from bot.groundtruth.analyze_weather import (
    BACKTEST_START,
    fit_all_biases,
    fit_rise_pmfs,
)
from bot.strategies.weather.strategy import (
    CityModel,
    WeatherDayAheadStrategy,
    WeatherIntradayStrategy,
    load_strikes_from_db,
)

REPORT_DIR = os.path.join("reports", "weather")
DB_PATH = os.path.join("data", "kalshi.db")
TRAIN_FRAC = 0.6


# ---------------------------------------------------------------------------
# Universe: settled weather markets with candles, split by target date
# ---------------------------------------------------------------------------

def discover_universe(db_path: str = DB_PATH) -> tuple[dict, list[str], list[str]]:
    """(strikes-with-data, train_dates, all_dates).

    Only settled markets whose hourly candles are already pulled count;
    the data agent may still be filling the DB — rerun when coverage
    grows.
    """
    strikes = load_strikes_from_db(db_path, tuple(wx.STATIONS))
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        have_candles = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT ticker FROM candlesticks"
                " WHERE period_interval = 60 AND ticker LIKE 'KXHIGH%'"
            )
        }
        # trade tape alone is enough: the runner's provider synthesizes
        # the decision-clock candles from prints (dataport_ext)
        have_candles |= {
            r[0] for r in conn.execute(
                "SELECT DISTINCT ticker FROM trades WHERE ticker LIKE 'KXHIGH%'"
            )
        }
        settled = {
            r[0] for r in conn.execute(
                "SELECT ticker FROM markets WHERE ticker LIKE 'KXHIGH%'"
                " AND result IN ('yes','no')"
            )
        }
    finally:
        conn.close()
    usable = {
        tk: s for tk, s in strikes.items()
        if tk in have_candles and tk in settled and s.target_date >= BACKTEST_START
    }
    dates = sorted({s.target_date for s in usable.values()})
    n_train = max(1, math.ceil(len(dates) * TRAIN_FRAC))
    train_dates = dates[:n_train]
    return usable, train_dates, dates


# ---------------------------------------------------------------------------
# Models (all inputs pre-fit / point-in-time, see module docstring)
# ---------------------------------------------------------------------------

WALK_FORWARD_PRIOR_N = 30  # pre-window prior strength (pseudo-days)


def _walk_forward_offsets(
    forecasts: dict[str, float],
    station: dict[str, float | None],
    prior: "wx.BiasFit",
    window_dates: list[str],
) -> dict[str, float]:
    """Point-in-time per-date offsets: shrunk mean of (station − forecast)
    residuals over settled window dates <= D-2, seeded by the pre-window
    fit as a prior of strength WALK_FORWARD_PRIOR_N.

    Motivation (measured): Chicago Midway's grid-station offset FLIPS
    SIGN between the pre-window fit (+1.6 °F, Aug'25-May'26) and the
    summer backtest window (−0.9 °F). A frozen offset would be
    systematically wrong by >2 °F there; daily recalibration is what a
    live trader would run and uses only settled data.
    """
    out: dict[str, float] = {}
    hist: list[tuple[str, float]] = []  # (date, residual), ascending
    for d in window_dates:
        d_cut = (dt.date.fromisoformat(d) - dt.timedelta(days=2)).isoformat()
        usable = [r for dd, r in hist if dd <= d_cut]
        k = len(usable)
        prior_off = prior.offset_for(d)
        out[d] = (
            (WALK_FORWARD_PRIOR_N * prior_off + sum(usable))
            / (WALK_FORWARD_PRIOR_N + k)
        )
        fc, stn = forecasts.get(d), station.get(d)
        if fc is not None and stn is not None:
            hist.append((d, stn - fc))
    return out


def build_city_models(window_dates: list[str]) -> dict[str, CityModel]:
    biases = fit_all_biases()  # pre-window fit only
    models: dict[str, CityModel] = {}
    lo, hi = window_dates[0], window_dates[-1]
    for series, st in wx.STATIONS.items():
        prev = wx.previous_runs_daily_max(st, leads=(1, 2))
        forecasts = {
            lead: {d: v for d, v in prev.get(lead, {}).items() if lo <= d <= hi}
            for lead in (1, 2)
        }
        station = wx.acis_daily_maxt(st.sid, lo, hi)
        offset_by_date = {
            lead: _walk_forward_offsets(
                forecasts[lead], station, biases[series][lead], window_dates
            )
            for lead in (1, 2)
        }
        models[series] = CityModel(
            tz=st.tz, bias=biases[series], forecasts=forecasts,
            offset_by_date=offset_by_date,
        )
    return models


def build_max16(window_dates: list[str]) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    lo, hi = window_dates[0], window_dates[-1]
    for series, st in wx.STATIONS.items():
        hourly = wx.acis_hourly_temps(st.sid, lo, hi)
        for date, obs in hourly.items():
            rm = wx.running_max_by_wall_hour(obs, date, st.tz, 16)
            if rm is not None:
                out[(series, date)] = rm
    return out


# ---------------------------------------------------------------------------
# Scoring extensions beyond bot.backtest.metrics
# ---------------------------------------------------------------------------

MAX_SCORING_SPREAD_CENTS = 20


def brier_table(
    p_hat_log: list[dict],
    settlements: dict,
    traded: set[tuple[str, str]],
) -> dict[str, Any]:
    """Ours-vs-market Brier on strike outcomes, per phase + pooled +
    traded subset, deduped to the FIRST look per (ticker, phase).

    Rows where the book is wider than MAX_SCORING_SPREAD_CENTS are
    excluded: a 0/100 just-opened book makes 'mid=50' a fake market
    forecast that would flatter us.
    """
    seen: set[tuple[str, str]] = set()
    rows = []
    for r in p_hat_log:
        key = (r["ticker"], r["phase"])
        if key in seen or r["mid_cents"] is None:
            continue
        bid, ask = r.get("bid"), r.get("ask")
        if bid is None or ask is None or ask - bid > MAX_SCORING_SPREAD_CENTS:
            continue
        s = settlements.get(r["ticker"])
        if s is None or s.result not in ("yes", "no"):
            continue
        seen.add(key)
        o = 1.0 if s.result == "yes" else 0.0
        rows.append(
            {"ticker": r["ticker"], "phase": r["phase"], "p": r["p_hat"],
             "m": r["mid_cents"] / 100.0, "o": o, "traded": key in traded}
        )

    def _score(sub: list[dict]) -> dict[str, Any]:
        n = len(sub)
        if n == 0:
            return {"n": 0}
        ours = sum((r["p"] - r["o"]) ** 2 for r in sub) / n
        mkt = sum((r["m"] - r["o"]) ** 2 for r in sub) / n
        diffs = [(r["m"] - r["o"]) ** 2 - (r["p"] - r["o"]) ** 2 for r in sub]
        mean_d = sum(diffs) / n
        var = sum((d - mean_d) ** 2 for d in diffs) / max(1, n - 1)
        se = math.sqrt(var / n)
        return {
            "n": n, "brier_ours": ours, "brier_market": mkt,
            "delta_market_minus_ours": mean_d,
            "delta_ci95": [mean_d - 1.96 * se, mean_d + 1.96 * se],
            "we_beat_market": mean_d > 0,
        }

    phases = sorted({r["phase"] for r in rows})
    return {
        "pooled": _score(rows),
        "by_phase": {ph: _score([r for r in rows if r["phase"] == ph]) for ph in phases},
        "traded_subset": _score([r for r in rows if r["traded"]]),
        "rows": rows,  # kept for calibration
    }


def calibration_curve(rows: list[dict], n_bins: int = 10) -> dict[str, Any]:
    bins: list[list[dict]] = [[] for _ in range(n_bins)]
    for r in rows:
        bins[min(n_bins - 1, int(r["p"] * n_bins))].append(r)
    total = len(rows)
    curve, ece = [], 0.0
    for i, b in enumerate(bins):
        if not b:
            continue
        conf = sum(r["p"] for r in b) / len(b)
        freq = sum(r["o"] for r in b) / len(b)
        curve.append({"bin": f"[{i/n_bins:.1f},{(i+1)/n_bins:.1f})", "n": len(b),
                      "mean_p_hat": conf, "observed_freq": freq})
        ece += len(b) / total * abs(freq - conf)
    return {"n": total, "ece": ece if total else None, "curve": curve}


def per_city_breakdown(result: BacktestResult, strikes: dict) -> dict[str, dict]:
    by_city: dict[str, dict] = defaultdict(
        lambda: {"net_pnl_cents": 0, "fees_cents": 0, "n_markets_traded": 0,
                 "contracts": 0, "dates": set()}
    )
    for tk, m in result.per_market.items():
        if m["contracts_traded"] == 0:
            continue
        s = strikes.get(tk)
        city = s.series_ticker if s else "?"
        c = by_city[city]
        c["net_pnl_cents"] += m["net_pnl_cents"]
        c["fees_cents"] += m["fees_cents"]
        c["n_markets_traded"] += 1
        c["contracts"] += m["contracts_traded"]
        if s:
            c["dates"].add(s.target_date)
    return {
        city: {**{k: v for k, v in c.items() if k != "dates"},
               "n_dates": len(c["dates"])}
        for city, c in sorted(by_city.items())
    }


def kill_criteria(
    result: BacktestResult, brier: dict, by_city: dict, strikes: dict
) -> dict[str, Any]:
    """SYNTHESIS P-2 kill criteria, evaluated on the train split."""
    net = result.net_pnl_after_inference_cents
    traded_dates = {
        strikes[tk].target_date
        for tk, m in result.per_market.items()
        if m["contracts_traded"] > 0 and tk in strikes
    }
    n_cities = len(by_city)
    pnl_by_city = {c: v["net_pnl_cents"] for c, v in by_city.items()}
    total_pos = sum(v for v in pnl_by_city.values() if v > 0)
    top_city_share = (
        max(pnl_by_city.values()) / total_pos if total_pos > 0 and pnl_by_city else None
    )
    # (d) deployable dollars per day per city at the 25% depth cap
    fills_premium = sum(
        f.count * f.price_cents for f in result.fills
    )
    days_x_cities = max(1, len(traded_dates)) * max(1, n_cities)
    per_day_city_usd = fills_premium / 100.0 / days_x_cities
    b = brier["pooled"]
    return {
        "a_postfee_pnl": {
            "net_pnl_cents": net,
            "n_resolved_days": len(traded_dates),
            "n_cities": n_cities,
            "fired": bool(net <= 0 and len(traded_dates) >= 60 and n_cities >= 6),
            "note": "fires only with >=60 days across >=6 cities of evidence",
        },
        "b_brier_vs_market": {
            "delta_market_minus_ours": b.get("delta_market_minus_ours"),
            "ci95": b.get("delta_ci95"),
            "fired": bool(b.get("n", 0) > 0 and not b.get("we_beat_market", False)),
        },
        "c_concentration": {
            "top_city_share_of_positive_pnl": top_city_share,
            "fired": bool(top_city_share is not None and top_city_share > 0.8),
        },
        "d_capacity": {
            "avg_deployed_usd_per_day_per_city": per_day_city_usd,
            "fired": bool(per_day_city_usd < 200.0),
            "note": ">$200/day/city deployable required",
        },
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_variant(
    variant: str,
    strikes: dict,
    train_tickers: list[str],
    models: dict[str, CityModel],
    max16: dict | None,
    rise_pmf: dict | None,
    db_path: str = DB_PATH,
) -> dict[str, Any]:
    def make_strategy():
        if variant == "dayahead":
            return WeatherDayAheadStrategy(strikes, models)
        return WeatherIntradayStrategy(strikes, models, max16, rise_pmf)

    provider = TradeSynthCandleProvider(db_path)
    filters = {"tickers": train_tickers}
    # event logs are large and auditable-but-disposable: keep them under
    # the gitignored data/ tree, not reports/
    log_dir = os.path.join(wx.DATA_DIR, "backtest_logs")
    os.makedirs(log_dir, exist_ok=True)
    config = EngineConfig(
        event_log_path=os.path.join(log_dir, f"events_{variant}.jsonl"),
        keep_events_in_memory=False,
    )
    strat = make_strategy()
    base = run_backtest(provider, strat, config, filters)

    # traded (ticker, phase) pairs for the traded-subset Brier
    # (the intent rationale prefix carries the phase, e.g. "d1: ...")
    traded = set()
    for i in base.intents:
        if i["clamped"] > 0:
            phase = (i["rationale"].split(":", 1)[0] or "").strip()
            traded.add((i["ticker"], phase))

    brier = brier_table(strat.p_hat_log, base.settlements, traded)
    calib = calibration_curve(brier["rows"])
    by_city = per_city_breakdown(base, strikes)
    kills = kill_criteria(base, brier, by_city, strikes)

    capacity = []
    for mult in (1.0, 3.0, 10.0):
        if mult == 1.0:
            r = base
        else:
            cfg = replace(
                config, risk=replace(RiskConfig(), depth_multiplier=mult),
                event_log_path=None,
            )
            r = run_backtest(provider, make_strategy(), cfg, filters)
        capacity.append({
            "depth_multiplier": mult,
            "net_pnl_cents_after_inference": r.net_pnl_after_inference_cents,
            "contracts_filled": sum(f.count for f in r.fills),
        })
    stress_cfg = replace(config, fee_stress=1.5, event_log_path=None)
    stressed = run_backtest(provider, make_strategy(), stress_cfg, filters)

    report: dict[str, Any] = {
        "label": f"weather-{variant} (train split)",
        "base": compute_metrics(base),
        "capacity_curve": capacity,
        "fee_stress": {
            "multiplier": 1.5,
            "net_pnl_cents_after_inference": stressed.net_pnl_after_inference_cents,
            "still_positive": stressed.net_pnl_after_inference_cents > 0,
        },
        "brier_vs_market": {k: v for k, v in brier.items() if k != "rows"},
        "calibration_all_strikes": calib,
        "per_city": by_city,
        "kill_criteria": kills,
    }
    return report


def _md_extras(report: dict) -> str:
    lines = ["", "## Brier vs market (all scored strikes)"]
    for scope in ("pooled", "traded_subset"):
        b = report["brier_vs_market"][scope]
        if b.get("n", 0) == 0:
            lines.append(f"- {scope}: n=0")
            continue
        lo, hi = b["delta_ci95"]
        lines.append(
            f"- {scope}: n={b['n']}  ours {b['brier_ours']:.4f} vs market "
            f"{b['brier_market']:.4f}  Δ(mkt−ours) {b['delta_market_minus_ours']:+.4f} "
            f"[{lo:+.4f}, {hi:+.4f}] -> "
            f"{'WE BEAT MARKET' if b['we_beat_market'] else 'market wins'}"
        )
    for ph, b in report["brier_vs_market"]["by_phase"].items():
        if b.get("n", 0):
            lines.append(
                f"  - phase {ph}: n={b['n']} ours {b['brier_ours']:.4f} "
                f"vs mkt {b['brier_market']:.4f} Δ {b['delta_market_minus_ours']:+.4f}"
            )
    lines += ["", "## Per-city breakdown", "| city | markets | dates | contracts | net P&L |", "|---|---|---|---|---|"]
    for city, c in report["per_city"].items():
        lines.append(
            f"| {city} | {c['n_markets_traded']} | {c['n_dates']} | "
            f"{c['contracts']} | ${c['net_pnl_cents']/100:,.2f} |"
        )
    lines += ["", "## Kill criteria (SYNTHESIS P-2)"]
    for name, k in report["kill_criteria"].items():
        detail = {kk: vv for kk, vv in k.items() if kk not in ("fired", "note")}
        lines.append(f"- {name}: {'FIRED' if k['fired'] else 'ok'} — {detail}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="both",
                    choices=["dayahead", "intraday", "both"])
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    os.makedirs(REPORT_DIR, exist_ok=True)
    strikes, train_dates, all_dates = discover_universe(args.db)
    if not strikes:
        print("no settled KXHIGH markets with candles in the DB yet - rerun later")
        return 1
    train_tickers = sorted(
        tk for tk, s in strikes.items() if s.target_date in set(train_dates)
    )
    print(
        f"universe: {len(strikes)} markets over {len(all_dates)} dates "
        f"({all_dates[0]}..{all_dates[-1]}); train {len(train_dates)} dates "
        f"({train_dates[0]}..{train_dates[-1]}), {len(train_tickers)} markets. "
        f"Held-out 40% untouched."
    )

    models = build_city_models(train_dates)
    max16 = rise_pmf = None
    variants = ["dayahead", "intraday"] if args.variant == "both" else [args.variant]
    if "intraday" in variants:
        max16 = build_max16(train_dates)
        rise_pmf = fit_rise_pmfs()

    for variant in variants:
        print(f"\n=== {variant} ===")
        report = run_variant(
            variant, strikes, train_tickers, models, max16, rise_pmf, args.db
        )
        jpath = os.path.join(REPORT_DIR, f"report_{variant}.json")
        with open(jpath, "w") as fh:
            json.dump(report, fh, indent=2)
        md = render_markdown(report) + _md_extras(report)
        mpath = os.path.join(REPORT_DIR, f"report_{variant}.md")
        with open(mpath, "w") as fh:
            fh.write(md)
        b = report["base"]["pnl"]
        print(
            f"net P&L ${b['net_pnl_cents_after_inference']/100:,.2f} | "
            f"fees ${b['fees_cents']/100:,.2f} | wrote {jpath}, {mpath}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

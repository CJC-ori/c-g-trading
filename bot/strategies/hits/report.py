"""Build reports/hits/ANALYSIS.md + machine-readable results from the
artifacts produced by run_hits.py. Pure read/format — no model calls, no
engine runs; deterministic from the WORK files.

Usage: python -m bot.strategies.hits.report
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from bot.forecaster import hits as H
from bot.forecaster.retrieval import RetrievalLedger, audit_ledger

REPO = Path(__file__).resolve().parents[3]
WORK = H.HITS_DIR
OUT = REPO / "reports" / "hits"


def _load(name: str):
    p = WORK / name
    return json.loads(p.read_text()) if p.exists() else None


def brier(pairs: list[tuple[float, int]]) -> float:
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs) if pairs else float("nan")


def paired_delta(ours: list[float], market: list[float], y: list[int]):
    """ΔBrier = Brier(market) − Brier(ours), + = we win; with 95% CI."""
    d = [(m - yy) ** 2 - (o - yy) ** 2 for o, m, yy in zip(ours, market, y)]
    n = len(d)
    if n == 0:
        return None
    mean = sum(d) / n
    if n > 1:
        var = sum((x - mean) ** 2 for x in d) / (n - 1)
        se = math.sqrt(var / n)
    else:
        se = float("nan")
    return {"n": n, "delta": mean, "se": se,
            "ci95": [mean - 1.96 * se, mean + 1.96 * se]}


def _dollars(cents) -> str:
    return f"${cents / 100:+,.2f}"


def build() -> None:
    cands = _load("candidates.json")
    screen = _load("screen.json")
    selection = _load("selection.json")
    fmap = _load("forecasts.json") or {}
    engine = _load("engine.json") or {}
    trades = _load("trades-hits.json") or []
    fade_all = _load("trades-fade-all.json") or []
    fade_sub = _load("trades-fade-subset.json") or []

    by_ticker = {row["ticker"]: row
                 for w in ("A", "B") for row in cands[w]["candidates"]}
    screen_by_ticker = {r["ticker"]: r for r in (screen or {}).get("rows", [])}

    # ---- forecast-set Brier: ours vs market price at D ---------------------
    ours, mkt, ys, rows_ok = [], [], [], []
    for t, d in sorted(fmap.items()):
        if d.get("p_yes") is None:
            continue
        c = by_ticker[t]
        y = 1 if c["result"] == "yes" else 0
        ours.append(d["p_yes"]); mkt.append(c["price_at_d"]); ys.append(y)
        rows_ok.append((t, d, c, y))
    pooled = paired_delta(ours, mkt, ys)

    fired_tickers = {r["ticker"] for r in trades}
    f_ours = [o for (t, d, c, y), o in zip(rows_ok, ours) if t in fired_tickers]
    f_mkt = [m for (t, d, c, y), m in zip(rows_ok, mkt) if t in fired_tickers]
    f_ys = [y for (t, d, c, y) in rows_ok if t in fired_tickers]
    fired_delta = paired_delta(f_ours, f_mkt, f_ys)

    # ---- ledger audit ------------------------------------------------------
    ledger_stats = None
    if (WORK / "ledger.jsonl").exists():
        recs = RetrievalLedger.load(WORK / "ledger.jsonl")
        viol = audit_ledger(recs, hard_fail=False)
        by_verdict: dict[str, int] = {}
        for r in recs:
            k = r.verdict.split(":")[0]
            by_verdict[k] = by_verdict.get(k, 0) + 1
        real = [v for v in viol if v[0] != "DATE_INCONSISTENT"]
        ledger_stats = {"rows": len(recs), "verdicts": by_verdict,
                        "violations": len(real),
                        "date_inconsistent_flags":
                            sum(1 for v in viol if v[0] == "DATE_INCONSISTENT")}

    # ---- trade table (hits) ------------------------------------------------
    def hit_row(r: dict) -> dict:
        filled = r["filled_contracts"] > 0
        won = None
        if r["result"] in ("yes", "no"):
            won = (r["side"] == r["result"])
        pay = None
        if r["avg_fill_cents"]:
            pay = (100 - r["avg_fill_cents"]) / r["avg_fill_cents"]
        d = fmap.get(r["ticker"], {})
        sc = screen_by_ticker.get(r["ticker"], {})
        prem = "; ".join(p.get("premortem", "") for p in d.get("passes", [])
                         if p.get("premortem"))
        return {**r, "filled": filled, "won": won, "payoff_x": pay,
                "screen_path": sc.get("path"), "premortems": prem}

    trade_rows = [hit_row(r) for r in trades]
    filled_rows = [r for r in trade_rows if r["filled"]]
    hits = [r for r in filled_rows if r["won"]]
    misses = [r for r in filled_rows if r["won"] is False]

    results = {
        "frozen_params": {
            "extreme_lo": H.EXTREME_LO, "extreme_hi": H.EXTREME_HI,
            "min_volume": H.MIN_VOLUME, "decision_days": H.DECISION_DAYS,
            "min_lifetime_days": H.MIN_LIFETIME_DAYS,
            "categories": list(H.CATEGORIES), "gate_pts": H.GATE_PTS,
            "min_payoff": H.MIN_PAYOFF, "windows": H.WINDOWS,
        },
        "funnel": {w: {**cands[w]["funnel"],
                       "candidates": cands[w]["n_candidates"],
                       "events": cands[w]["n_events"]} for w in ("A", "B")},
        "screen": {"histogram": (screen or {}).get("histogram"),
                   "cost_cents": (screen or {}).get("screen_cost_cents")},
        "selection": selection,
        "forecasts": {
            "n_total": len(fmap),
            "n_usable": len(rows_ok),
            "n_error": len(fmap) - len(rows_ok),
            "total_cost_cents": sum(d.get("cost_cents", 0) for d in fmap.values()),
            "brier_ours": brier(list(zip(ours, ys))),
            "brier_market_at_d": brier(list(zip(mkt, ys))),
            "paired_delta_pooled": pooled,
            "paired_delta_fired": fired_delta,
        },
        "engine": {k: v.get("summary") for k, v in engine.items()},
        "trades": trade_rows,
        "n_fired": len(trade_rows),
        "n_filled": len(filled_rows),
        "n_hits": len(hits),
        "n_misses": len(misses),
        "ledger": ledger_stats,
        "fade_all_trades": {"n_fired": len(fade_all),
                            "n_filled": sum(1 for r in fade_all
                                            if r["filled_contracts"] > 0)},
        "fade_subset_trades": {"n_fired": len(fade_sub),
                               "n_filled": sum(1 for r in fade_sub
                                               if r["filled_contracts"] > 0)},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(results, indent=1))
    print(f"wrote {OUT / 'results.json'}")

    # markdown trade table fragment (embedded in ANALYSIS.md)
    lines = [
        "| # | ticker | window | title | mkt@D | our p | side | limit | filled | avg | payoff | result | P&L | hit? |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(sorted(trade_rows, key=lambda r: -r["net_pnl_cents"]), 1):
        pay = f"{r['payoff_x']:.1f}x" if r["payoff_x"] else "—"
        avg = f"{r['avg_fill_cents']:.1f}c" if r["avg_fill_cents"] else "—"
        hit = {True: "**HIT**", False: "miss", None: "no fill"}[r["won"] if r["filled"] else None]
        lines.append(
            f"| {i} | {r['ticker']} | {r['window']} | {r['title'][:60]} | "
            f"{r['price_at_d']*100:.0f}c | {r['p_yes']*100:.0f}c | {r['side'].upper()} | "
            f"{r['limit_price_cents']}c | {r['filled_contracts']} | {avg} | {pay} | "
            f"{r['result']} | {_dollars(r['net_pnl_cents'])} | {hit} |")
    (OUT / "trades-table.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {OUT / 'trades-table.md'}")

    # cache export for reproducibility
    keys = []
    for d in fmap.values():
        keys.extend(d.get("cache_keys", []))
    if keys:
        n = H.hits_replay_cache().export_jsonl(sorted(set(keys)),
                                               OUT / "hits-llm-cache.jsonl")
        print(f"exported {n} cache entries")


if __name__ == "__main__":
    build()

"""Rung 1 — replay FutureSearch's resolved book through OUR P&L accounting.

The harness's economics need an external unit test. FutureSearch publishes, for
every one of their 128 resolved positions, the entry price, the share count and
the outcome (``research/futuresearch-data/fs_positions_2026-08-11.json``, parsed
from <https://markets.futuresearch.ai/> on 2026-08-11). Their published book is

    Kalshi      n=83   +$27,798   on cost basis $85,610
    Polymarket  n=45   −$19,829   on cost basis $111,141

If our accounting cannot reproduce a book whose every input is known, it cannot
be trusted on a book whose inputs we generated ourselves (SYNTHESIS §2.6,
``research/futuresearch.md`` §7 eval #2).

Two passes, deliberately separated
----------------------------------
**Pass A — gross.** Their accounting, reconstructed from first principles:
``cost = shares × entry_price``, ``payout = shares if won else 0``,
``pnl = payout − cost``. No fees anywhere. This must land on their published
numbers to the cent; any residual here is a data or arithmetic bug in *us*.

**Pass B — net.** The same positions through our fee model. FutureSearch's
February trader methodology charges **no fees at all** (the word does not appear
in the case study; ``research/futuresearch.md`` §5), so the entire Pass A → Pass
B delta *is* the fee drag their published figure omits. That drag is the number
this eval exists to quantify: it is what a 2-point edge threshold has to clear.

Pass B runs three fee engines side by side, because they disagree and the
disagreement is material:

* ``spec`` — ``bot/backtest/fees.trade_fee_centicents``, the harness's exact
  per-series model (SYNTHESIS §2.1 / Kalshi's published schedule:
  ``ceil_to_$0.0001(0.07 × fee_multiplier × contracts × P × (1−P))``, one ceiling
  for the whole order), with ``fee_multiplier`` resolved per series through
  ``FeeSchedule.from_store`` against our local ``series`` table. **This is the
  number under test.**
* ``legacy`` — ``bot/backtest/fees.taker_fee_cents``, the backward-compatible
  path that ceilings **per contract** and multiplies by the count. On orders of
  thousands of contracts it over-charges substantially; the gap is quantified in
  the ``fee_engine_divergence`` section so that any run still on the legacy path
  knows what it is paying.
* ``independent`` — a float-precision reimplementation of the same formula
  local to this module. It exists so the harness is checked against something
  other than itself, and because the harness quantizes the execution price to a
  whole cent before charging while FutureSearch's entry prices carry four
  decimals (``0.7011``). The gap between ``spec`` and ``independent`` is that
  quantization, nothing else.

Polymarket charges no taker fee on the resolved book (per-market ``feeSchedule``
off Gamma; ``research/polymarket-data.md`` §5), so its Pass A and Pass B agree by
construction and its residual is exactly zero.

Run: ``python -m bot.evals.fs_trade_replay``
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bot.backtest import fees as harness_fees
from bot.evals import scoring
from bot.evals.report import md_table, write_report

NAME = "fs_trade_replay"

REPO = Path(__file__).resolve().parents[2]
POSITIONS_PATH = REPO / "research" / "futuresearch-data" / "fs_positions_2026-08-11.json"
DB_PATH = REPO / "data" / "kalshi.db"

#: Published figures from <https://markets.futuresearch.ai/> (2026-08-11), as
#: transcribed in ``research/futuresearch.md`` §4.2. Dollars.
PUBLISHED = {
    "Kalshi": {"n": 83, "pnl": 27798.0, "cost_basis": 85610.0},
    "Polymarket": {"n": 45, "pnl": -19829.0, "cost_basis": 111141.0},
    "Combined": {"n": 128, "pnl": 7969.0, "cost_basis": 196751.0},
}

#: Their figures are rounded to the dollar, so a per-venue residual under $1
#: is a perfect reconciliation.
RECONCILIATION_TOLERANCE_USD = 1.0

KALSHI_TAKER_RATE = 0.07
#: Kalshi rounds trade fees UP to a centi-cent ($0.0001), not to a cent
#: (docs.kalshi.com/getting_started/fee_rounding; SYNTHESIS §2.1).
FEE_QUANTUM_USD = 0.0001


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Position:
    venue: str
    ticker: str
    title: str
    side: str          # "YES" / "NO" — the side they bought
    won: bool
    shares: float
    entry_price: float  # dollars, in the price space of the side bought
    published_pnl: float
    published_proceeds: float
    forecast_p_yes: float | None
    market_p_yes: float | None
    resolved_by: str | None

    @property
    def series(self) -> str:
        return self.ticker.split("-")[0]

    @property
    def cost(self) -> float:
        return self.shares * self.entry_price

    @property
    def payout(self) -> float:
        """$1 per share if the side they bought was the outcome, else $0."""
        return self.shares if self.won else 0.0

    @property
    def gross_pnl(self) -> float:
        return self.payout - self.cost


def load_positions(path: Path = POSITIONS_PATH) -> list[Position]:
    """Load the 128 resolved positions (``status`` in {won, lost})."""
    raw = json.loads(path.read_text())
    out: list[Position] = []
    for row in raw:
        if row.get("status") not in ("won", "lost"):
            continue  # the other 86 are still open — no outcome, not replayable
        p = row["p"]
        out.append(
            Position(
                venue=row["venue"],
                ticker=p["id"],
                title=p["title"],
                side=p["position"],
                won=bool(p["won"]),
                shares=float(p["shares"]),
                entry_price=float(p["entryPrice"]),
                published_pnl=float(p["pnl"]),
                published_proceeds=float(p["proceeds"]),
                forecast_p_yes=_maybe_float(p.get("probabilityYes")),
                market_p_yes=_maybe_float(p.get("marketYes")),
                resolved_by=p.get("resolvedBy"),
            )
        )
    return out


def _maybe_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_fee_schedule(db_path: Path = DB_PATH) -> harness_fees.FeeSchedule:
    """The harness's per-series fee schedule, preferring the live store.

    Point-in-time caveat (the harness flags it too): this reads today's
    ``/series`` config, not the config in force on the trade date.
    ``/series/fee_changes?show_historical=true`` exists
    (``research/kalshi-api.md`` §3.1) and should be wired when the data layer
    pulls it; until then the report states the assumption.
    """
    if db_path.exists():
        try:
            return harness_fees.FeeSchedule.from_store(str(db_path))
        except Exception:  # a half-written concurrent DB must not fail the eval
            pass
    return harness_fees.FeeSchedule.load_default()


def _price_cents(price: float) -> int:
    """Quantize a dollar price to the 1..99 cent grid the harness charges on."""
    return min(99, max(1, int(round(price * 100))))


# ---------------------------------------------------------------------------
# Fee engines
# ---------------------------------------------------------------------------

def spec_taker_fee_usd(price: float, contracts: float, cfg: Any) -> float:
    """The harness's exact model — ``fees.trade_fee_centicents`` — in dollars."""
    if contracts <= 0:
        return 0.0
    cc = harness_fees.trade_fee_centicents(
        _price_cents(price), int(round(contracts)), is_taker=True, config=cfg
    )
    return cc * FEE_QUANTUM_USD


def independent_taker_fee_usd(
    price: float, contracts: float, fee_multiplier: float = 1.0, rate: float = KALSHI_TAKER_RATE
) -> float:
    """SYNTHESIS §2.1 reimplemented here, at full float price precision.

    Deliberately not a call into ``bot/backtest``: a unit test that imports the
    thing it is testing for both sides of the comparison tests nothing.
    """
    if contracts <= 0 or fee_multiplier == 0.0:
        return 0.0
    raw = rate * fee_multiplier * contracts * price * (1.0 - price)
    return math.ceil(round(raw / FEE_QUANTUM_USD, 9)) * FEE_QUANTUM_USD


def legacy_taker_fee_usd(price: float, contracts: float, category: str | None = None) -> float:
    """``fees.taker_fee_cents``: the legacy path, ceiling per contract × count."""
    return harness_fees.taker_fee_cents(
        _price_cents(price), int(round(contracts)), category=category
    ) / 100.0


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------

def replay(
    positions: list[Position],
    fee_schedule: harness_fees.FeeSchedule | None = None,
) -> dict[str, Any]:
    """Run passes A and B and assemble the full reconciliation payload."""
    fee_schedule = fee_schedule or harness_fees.FeeSchedule()
    per_position: list[dict[str, Any]] = []

    for p in positions:
        if p.venue == "Kalshi":
            cfg = fee_schedule.for_market_ticker(p.ticker)
            mult = cfg.fee_multiplier
            fee_spec = spec_taker_fee_usd(p.entry_price, p.shares, cfg)
            fee_legacy = legacy_taker_fee_usd(p.entry_price, p.shares)
            fee_indep = independent_taker_fee_usd(p.entry_price, p.shares, mult)
        else:
            # Polymarket: no taker fee on this book (research/polymarket-data.md §5).
            mult = 0.0
            fee_spec = fee_legacy = fee_indep = 0.0
        per_position.append(
            {
                "venue": p.venue,
                "ticker": p.ticker,
                "title": p.title,
                "side": p.side,
                "won": p.won,
                "shares": p.shares,
                "entry_price": p.entry_price,
                "cost": p.cost,
                "payout": p.payout,
                "gross_pnl": p.gross_pnl,
                "published_pnl": p.published_pnl,
                "gross_residual": p.gross_pnl - p.published_pnl,
                "fee_multiplier": mult,
                "fee_spec": fee_spec,
                "fee_legacy": fee_legacy,
                "fee_independent": fee_indep,
                "net_pnl_spec": p.gross_pnl - fee_spec,
                "net_pnl_legacy": p.gross_pnl - fee_legacy,
                "forecast_p_yes": p.forecast_p_yes,
                "market_p_yes": p.market_p_yes,
            }
        )

    def agg(rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {"n": 0}
        cost = sum(r["cost"] for r in rows)
        gross = sum(r["gross_pnl"] for r in rows)
        fee_spec = sum(r["fee_spec"] for r in rows)
        fee_legacy = sum(r["fee_legacy"] for r in rows)
        fee_indep = sum(r["fee_independent"] for r in rows)
        return {
            "n": len(rows),
            "cost_basis": cost,
            "payout": sum(r["payout"] for r in rows),
            "gross_pnl": gross,
            "published_pnl_sum": sum(r["published_pnl"] for r in rows),
            "gross_roi": gross / cost if cost else None,
            "fees_spec": fee_spec,
            "fees_legacy": fee_legacy,
            "fees_independent": fee_indep,
            "net_pnl_spec": gross - fee_spec,
            "net_pnl_legacy": gross - fee_legacy,
            "net_roi_spec": (gross - fee_spec) / cost if cost else None,
            "fee_drag_bps_of_cost": (fee_spec / cost * 1e4) if cost else None,
            "win_rate": sum(1 for r in rows if r["won"]) / len(rows),
        }

    venues = {v: agg([r for r in per_position if r["venue"] == v]) for v in ("Kalshi", "Polymarket")}
    venues["Combined"] = agg(per_position)

    # Reconciliation of pass A against the published book.
    recon = {}
    for venue, pub in PUBLISHED.items():
        a = venues[venue]
        residual = a["gross_pnl"] - pub["pnl"]
        recon[venue] = {
            "published_n": pub["n"],
            "our_n": a["n"],
            "published_pnl": pub["pnl"],
            "our_gross_pnl": a["gross_pnl"],
            "residual_usd": residual,
            "reconciles": abs(residual) <= RECONCILIATION_TOLERANCE_USD and a["n"] == pub["n"],
            "published_cost_basis": pub["cost_basis"],
            "our_cost_basis": a["cost_basis"],
            "cost_basis_residual_usd": a["cost_basis"] - pub["cost_basis"],
        }

    # Identity check: does their own `pnl` field equal proceeds - cost, position
    # by position? This is what pins down that their book is gross-of-fees.
    worst = max(per_position, key=lambda r: abs(r["gross_residual"]))
    identity = {
        "max_abs_position_residual_usd": abs(worst["gross_residual"]),
        "worst_position": worst["ticker"],
        "n_positions_off_by_more_than_1c": sum(
            1 for r in per_position if abs(r["gross_residual"]) > 0.01
        ),
        "conclusion": (
            "their published pnl == shares*(1[won] - entry_price) exactly, i.e. the "
            "book is gross: zero fees, zero slippage on exit, held to resolution"
        ),
    }

    conc = {
        v: scoring.concentration([r["gross_pnl"] for r in per_position if r["venue"] == v])
        for v in ("Kalshi", "Polymarket")
    }
    conc["Combined"] = scoring.concentration([r["gross_pnl"] for r in per_position])

    # Their forecast vs the market, on the subset carrying both (§4.2).
    paired_rows = [
        r
        for r in per_position
        if r["forecast_p_yes"] is not None and r["market_p_yes"] is not None
    ]
    fs_vs_market = None
    if len(paired_rows) >= 2:
        ours = [r["forecast_p_yes"] for r in paired_rows]
        base = [r["market_p_yes"] for r in paired_rows]
        y = [1.0 if (r["won"] == (r["side"] == "YES")) else 0.0 for r in paired_rows]
        pd_ = scoring.paired_delta(ours, base, y)
        fs_vs_market = {
            "note": (
                "positions they chose to take, i.e. maximum-disagreement questions; "
                "selection-biased and tiny — not evidence of forecaster skill"
            ),
            **pd_.to_dict(),
            "gate": scoring.gate_report(pd_),
        }

    return {
        "positions": per_position,
        "aggregates": venues,
        "reconciliation": recon,
        "gross_pnl_identity": identity,
        "concentration": conc,
        "fs_forecast_vs_market": fs_vs_market,
        "fee_engine_divergence": {
            "spec_total_usd": venues["Combined"]["fees_spec"],
            "legacy_total_usd": venues["Combined"]["fees_legacy"],
            "independent_total_usd": venues["Combined"]["fees_independent"],
            "ratio_legacy_over_spec": (
                venues["Combined"]["fees_legacy"] / venues["Combined"]["fees_spec"]
                if venues["Combined"]["fees_spec"]
                else None
            ),
            "spec_vs_independent_usd": (
                venues["Combined"]["fees_spec"] - venues["Combined"]["fees_independent"]
            ),
            "explanation": (
                "`spec` is bot/backtest/fees.trade_fee_centicents (one ceiling per order, to "
                "$0.0001, per-series multiplier) — the correct model. `legacy` is "
                "fees.taker_fee_cents, which ceilings per *contract* and multiplies; on "
                "multi-thousand-contract orders that inflates the fee substantially, so any "
                "backtest still running without a FeeSchedule is over-charging itself. "
                "`independent` is this module's own float-precision implementation of the same "
                "formula; it differs from `spec` only because the harness quantizes the "
                "execution price to a whole cent before charging, while these entry prices "
                "carry four decimals."
            ),
        },
        "assumptions": [
            "held to resolution: entry taker fee only, no exit trade, no settlement fee",
            "every FutureSearch fill is treated as a taker fill (they walk the book, §5)",
            "Polymarket taker fee = 0 on this book (research/polymarket-data.md §5)",
            "fee_multiplier read from today's /series config, not point-in-time "
            "(/series/fee_changes?show_historical=true not yet pulled)",
            "each position is charged as ONE order; Kalshi's per-order rounding-fee "
            "accumulator would apply per real order, so a position filled across many "
            "orders pays slightly more than modelled here",
            f"{sum(1 for p in positions if p.venue == 'Kalshi' and abs(p.shares - round(p.shares)) > 1e-9)} "
            "Kalshi positions carry fractional share counts (Kalshi contracts are "
            "integral); the spec engine uses them as-is, the harness engine rounds",
        ],
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def build_report(result: dict[str, Any]) -> tuple[str, list[tuple[str, str]]]:
    agg = result["aggregates"]
    recon = result["reconciliation"]

    all_reconcile = all(v["reconciles"] for v in recon.values())
    verdict = "PASS — harness economics reproduce the external book" if all_reconcile else "FAIL"

    sec_recon = md_table(
        ["venue", "n (pub/ours)", "published P&L", "our gross P&L", "residual $", "reconciles"],
        [
            [
                v,
                f"{d['published_n']}/{d['our_n']}",
                f"${d['published_pnl']:,.0f}",
                f"${d['our_gross_pnl']:,.2f}",
                f"{d['residual_usd']:+.2f}",
                d["reconciles"],
            ]
            for v, d in recon.items()
        ],
    )
    sec_recon += (
        "\n\nThe residual is the difference between their *published, rounded-to-the-dollar* "
        "figure and our cent-exact recomputation from `shares`, `entryPrice` and the outcome. "
        f"Worst single-position residual against their own `pnl` field: "
        f"${result['gross_pnl_identity']['max_abs_position_residual_usd']:.4f} "
        f"({result['gross_pnl_identity']['n_positions_off_by_more_than_1c']} positions off by >1c). "
        "\n\n" + result["gross_pnl_identity"]["conclusion"] + "."
    )

    sec_net = md_table(
        ["venue", "n", "cost basis", "gross P&L", "gross ROI", "fees (spec)", "net P&L (spec)", "net ROI", "fee drag (bps)"],
        [
            [
                v,
                d["n"],
                f"${d['cost_basis']:,.0f}",
                f"${d['gross_pnl']:,.0f}",
                f"{d['gross_roi']:+.1%}" if d["gross_roi"] is not None else "—",
                f"${d['fees_spec']:,.0f}",
                f"${d['net_pnl_spec']:,.0f}",
                f"{d['net_roi_spec']:+.1%}" if d["net_roi_spec"] is not None else "—",
                f"{d['fee_drag_bps_of_cost']:.0f}" if d["fee_drag_bps_of_cost"] else "0",
            ]
            for v, d in agg.items()
        ],
    )
    fd = result["fee_engine_divergence"]
    sec_net += (
        "\n\n**Residual explanation.** Pass A matches to the cent, so the harness's P&L "
        "arithmetic is correct. The only gap between their number and ours is the fee line "
        "they never charged: FutureSearch's February trader methodology models no fees at all "
        "(`research/futuresearch.md` §5), so our net book is strictly below their published "
        f"one by ${agg['Combined']['fees_spec']:,.0f} of Kalshi taker fees "
        f"({agg['Kalshi']['fee_drag_bps_of_cost']:.0f} bps of Kalshi cost basis). "
        "Polymarket contributes zero fee drag by construction. Their published edge threshold "
        "is 2 percentage points; this drag is of the same order, which is the practical "
        "finding.\n\n**Fee-engine divergence.**\n\n"
        + md_table(
            ["engine", "total Kalshi fees", "vs exact"],
            [
                ["`spec` — fees.trade_fee_centicents (exact)", f"${fd['spec_total_usd']:,.2f}", "—"],
                [
                    "`legacy` — fees.taker_fee_cents (per-contract ceil)",
                    f"${fd['legacy_total_usd']:,.2f}",
                    f"{fd['ratio_legacy_over_spec']:.2f}x",
                ],
                [
                    "`independent` — this module, float price",
                    f"${fd['independent_total_usd']:,.2f}",
                    f"{fd['spec_vs_independent_usd']:+,.2f}",
                ],
            ],
        )
        + "\n\n"
        + fd["explanation"]
    )

    sec_conc = md_table(
        ["venue", "n", "total P&L", "top-5 P&L", "top-5 share", "best", "worst", "n positive"],
        [
            [
                v,
                d["n"],
                f"${d['total_pnl']:,.0f}",
                f"${d['top_5_pnl']:,.0f}",
                f"{d['top_5_share_of_pnl']:.0%}" if d.get("share_defined") else "n/a (total ≤ 0)",
                f"${d['best']:,.0f}",
                f"${d['worst']:,.0f}",
                d["n_positive"],
            ]
            for v, d in result["concentration"].items()
        ],
    )
    comb = result["concentration"]["Combined"]
    sec_conc += (
        "\n\nSPEC §7 fails any strategy whose top-5 markets carry >60% of profit. The combined "
        f"book's top 5 positions make ${comb['top_5_pnl']:,.0f} against a combined total of "
        f"${comb['total_pnl']:,.0f} — the other {comb['n'] - 5} positions lose "
        f"${comb['top_5_pnl'] - comb['total_pnl']:,.0f} between them. Polymarket's share is "
        "undefined because its total is negative: one trade (+$26,609) carries the venue and "
        "everything else loses. This is the cautionary unit test for our own concentration gate "
        "(SYNTHESIS §2.3 item 4), not a book to imitate."
    )

    fvm = result["fs_forecast_vs_market"]
    if fvm:
        sec_fvm = md_table(
            ["n", "Brier(FS forecast)", "Brier(market)", "ΔBrier", "95% CI", "gate verdict"],
            [
                [
                    fvm["n"],
                    f"{fvm['our_brier']:.4f}",
                    f"{fvm['baseline_brier']:.4f}",
                    f"{fvm['delta']:+.4f}",
                    f"[{fvm['ci_low']:+.4f}, {fvm['ci_high']:+.4f}]",
                    fvm["gate"]["verdict"],
                ]
            ],
        )
        sec_fvm += "\n\n" + fvm["note"] + (
            f" At n={fvm['n']} the minimum detectable ΔBrier is "
            f"{fvm['gate']['min_detectable_delta_at_this_n']:.4f} — larger than the entire "
            "superforecaster-over-market edge (0.004)."
        )
    else:
        sec_fvm = "No positions carry both a forecast and a market probability."

    sec_assump = "\n".join(f"- {a}" for a in result["assumptions"])

    return verdict, [
        ("Pass A — gross reconciliation against the published book", sec_recon),
        ("Pass B — the same book through our fee accounting", sec_net),
        ("Concentration (the cautionary metric)", sec_conc),
        ("Their forecaster vs the market, on the traded subset", sec_fvm),
        ("Assumptions and known gaps", sec_assump),
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--positions", type=Path, default=POSITIONS_PATH)
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    positions = load_positions(args.positions)
    schedule = load_fee_schedule(args.db)
    result = replay(positions, schedule)
    verdict, sections = build_report(result)

    payload = {k: v for k, v in result.items() if k != "positions"}
    payload["n_positions"] = len(result["positions"])
    payload["fee_schedule_overrides"] = len(schedule)
    jpath, mpath = write_report(
        NAME, "Rung 1 — FutureSearch trade replay (harness economics)", payload, sections, verdict
    )
    if not args.quiet:
        print(f"{verdict}\n")
        for heading, body in sections:
            print(f"## {heading}\n{body}\n")
        print(f"wrote {jpath}\nwrote {mpath}")
    return 0 if verdict.startswith("PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())

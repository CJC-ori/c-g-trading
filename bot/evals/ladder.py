"""Run the whole eval ladder, in order, and emit a single summary.

Each rung is gated on the previous one: a rung that fails stops the ladder,
because a number produced on top of a broken rung is worse than no number.

    rung 0  harness self-tests      pytest bot/backtest bot/evals
    rung 1  fs_trade_replay         economics vs an external book
    rung 2  forecastbench           Brier vs the market-consensus baseline
    rung 2a fs_replication          3-way Brier on FutureSearch's Kalshi set
    rung 3  Kalshi replay           not in this package — bot/backtest owns it

See ``bot/evals/README.md``. Run: ``python -m bot.evals.ladder``
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from bot.evals.report import md_table, reports_dir, write_report

NAME = "ladder"
REPO = Path(__file__).resolve().parents[2]


def _pytest(paths: list[str]) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *paths],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    tail = (proc.stdout or proc.stderr).strip().splitlines()
    return {
        "rung": "0",
        "name": "harness self-tests",
        "command": "pytest -q " + " ".join(paths),
        "ok": proc.returncode == 0,
        "detail": tail[-1] if tail else "",
    }


def _module(rung: str, module: str, argv: list[str]) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, "-m", f"bot.evals.{module}", *argv],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    verdict = ""
    rpath = reports_dir() / f"{module}.json"
    if rpath.exists():
        try:
            verdict = json.loads(rpath.read_text()).get("verdict", "")
        except json.JSONDecodeError:
            pass
    if not verdict:
        verdict = (proc.stdout or proc.stderr).strip().splitlines()[:1]
        verdict = verdict[0] if verdict else "(no output)"
    return {
        "rung": rung,
        "name": module,
        "command": f"python -m bot.evals.{module} " + " ".join(argv),
        "ok": proc.returncode == 0,
        "detail": verdict,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--skip-rung-0", action="store_true", help="skip the pytest sweep")
    ap.add_argument("--online", action="store_true", help="let rung 2a query the Kalshi API")
    ap.add_argument("--continue-on-fail", action="store_true")
    args = ap.parse_args(argv)

    results: list[dict[str, Any]] = []
    if not args.skip_rung_0:
        results.append(_pytest(["bot/backtest", "bot/evals"]))
        if not results[-1]["ok"] and not args.continue_on_fail:
            return _finish(results, stopped_at="0")

    for rung, module, extra in (
        ("1", "fs_trade_replay", ["--quiet"]),
        ("2", "forecastbench", ["--quiet"]),
        ("2a", "fs_replication", ["--quiet"] + (["--online"] if args.online else [])),
    ):
        results.append(_module(rung, module, extra))
        if not results[-1]["ok"] and not args.continue_on_fail:
            return _finish(results, stopped_at=rung)

    results.append(
        {
            "rung": "3",
            "name": "Kalshi tournament replay",
            "command": "(owned by bot/backtest)",
            "ok": None,
            "detail": "NOT IMPLEMENTED HERE — see README rung 3",
        }
    )
    return _finish(results, stopped_at=None)


def _finish(results: list[dict[str, Any]], stopped_at: str | None) -> int:
    table = md_table(
        ["rung", "eval", "ok", "verdict / detail"],
        [
            [r["rung"], r["name"], "—" if r["ok"] is None else ("PASS" if r["ok"] else "FAIL"), r["detail"]]
            for r in results
        ],
    )
    verdict = (
        f"STOPPED at rung {stopped_at}"
        if stopped_at
        else ("PASS" if all(r["ok"] is not False for r in results) else "FAIL")
    )
    body = table + (
        "\n\nEach rung is gated on the one below it. A rung 2 Brier computed on a harness "
        "that failed rung 1 is not a measurement."
    )
    jpath, mpath = write_report(
        NAME, "Eval ladder — full run", {"results": results, "stopped_at": stopped_at}, [("Rungs", body)], verdict
    )
    print(f"{verdict}\n\n{table}\n\nwrote {jpath}\nwrote {mpath}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Hits-based selective forecasting — the asymmetric-payoff variant of P-4.

Motivation (reports/forecaster/RUNG2.md + research/futuresearch.md §4):
rung 2 showed the pipeline is market-parity on the *average* question, and
FutureSearch's entire live P&L came from a handful of asymmetric trades.
This module hunts ONLY the places where a 3x+ payoff is on offer by
construction — markets priced at an extreme with real volume and time left —
and spends the expensive pipeline only where a cheap screen says the
extreme price might be wrong.

FROZEN PARAMETERS (fixed 2026-08-12 BEFORE any forecast or engine run;
per SPEC §8 no parameter here may be tuned on results):

- Candidate screen (price-only, over data/kalshi.db settled markets):
  * YES price at the decision date D in [0, 0.15] or [0.85, 1.0]
    (an extreme: the cheap side pays >= 85/15 ≈ 5.7x if it wins)
  * volume >= 2,000 contracts (the "~$2k traded" proxy; the store has no
    dollar-volume column)
  * market lifetime >= 14 days and D = close − 10 days (inside the spec'd
    7–14-day pre-resolution band, so >= 7 days to close at decision time
    holds by construction)
  * category in {Politics, Elections, World, Economics, Companies} —
    research-amenable; NOT Sports/Crypto/Climate/Financials
  * result in {yes, no} (settled binaries only)
- Windows (judge-cutoff rules per bot/backtest/SPEC.md §1):
  * A: close 2026-02-01..2026-05-31 — judged by Sonnet 5 (Jan-2026 cutoff)
  * B: close 2026-06-15..2026-08-05 — judged by Opus 5 (May-2026 cutoff).
    B starts 06-15, not 06-01, so the earliest D (= close − 10d, 06-05)
    still postdates the Opus cutoff: the judge must not know anything
    after D, not merely after resolution.
- Trade rule (bot/strategies/hits): enter maker-first only when
  |forecast − price| >= 15 points AND the payoff if the forecast is right
  is >= 3x (entry price <= 25c on the side bought), i.e. fading the extreme.
- Price at D: hourly-candle bid/ask mid (trade close as fallback) from the
  shared store. Coverage note: the store carries hourly candles for the
  last ~15 days of life for roughly 40% of the otherwise-eligible
  markets (an artifact of which series the pull jobs covered, decided
  long before this analysis); the candidate set is conditioned on that
  coverage and the report says so.

Cache/workspace namespace: everything lives under
``data/forecaster-cache/hits/`` — its own ReplayCache root, retrieval
ledger, and work files — so a concurrently-running rung-2/3 sibling is
never touched.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from bot.forecaster import event_anchor as EA
from bot.forecaster.llm import LLMClient, LLMRequest, LLMResponse, ReplayCache

REPO = Path(__file__).resolve().parents[2]
DB = REPO / "data" / "kalshi.db"
HITS_DIR = REPO / "data" / "forecaster-cache" / "hits"
HITS_CACHE_DIR = HITS_DIR / "llm"

# ---- frozen screen parameters (see module docstring) -----------------------
EXTREME_LO = 0.15
EXTREME_HI = 0.85
MIN_VOLUME = 2_000
DECISION_DAYS = 10          # D = close − 10d (inside the 7–14d band)
MIN_LIFETIME_DAYS = 14
PRICE_LOOKBACK_H = 48       # candle must be within 48h before D
CATEGORIES = ("Politics", "Elections", "World", "Economics", "Companies")
WINDOWS = {
    "A": {"close_min": "2026-02-01", "close_max": "2026-05-31",
          "judge_model": "sonnet"},
    "B": {"close_min": "2026-06-15", "close_max": "2026-08-05",
          "judge_model": "opus"},
}
# trade rule constants (consumed by bot/strategies/hits)
GATE_PTS = 15               # |forecast − price| >= 15 points
MIN_PAYOFF = 3.0            # (100 − entry) / entry >= 3  ⇔  entry <= 25c

DAY = 86400


def hits_replay_cache() -> ReplayCache:
    """The hits-namespace LLM cache (never the shared rung-2/3 root)."""
    return ReplayCache(HITS_CACHE_DIR)


# ---------------------------------------------------------------------------
# 1. Candidate screen — cheap, price-only
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    ticker: str
    event_ticker: str
    series_ticker: str
    window: str                 # "A" | "B"
    title: str
    category: str
    rules: str
    open_ts: int
    close_ts: int
    close_time: str
    decision_ts: int            # D
    price_at_d: float           # YES price in [0,1] at D
    cheap_side: str             # "yes" if price <= EXTREME_LO else "no"
    volume: float
    result: str                 # settled outcome (NEVER shown to any model)
    # v2 event anchoring (post-hoc infrastructure fix; defaults keep frozen
    # close-anchored rows loadable): see bot/forecaster/event_anchor.py.
    event_ts: int | None = None         # derived event date (12:00 UTC) or None
    anchor_source: str = "close_time"   # source actually used for D
    anchor_provenance: str = ""         # matched text / field / calendar key

    @property
    def cheap_price(self) -> float:
        """Price of the side an extreme-fader would buy, in [0,1]."""
        return self.price_at_d if self.cheap_side == "yes" else 1.0 - self.price_at_d

    def to_row(self) -> dict:
        return asdict(self)


def _iso_to_ts(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def is_extreme(p: float) -> bool:
    return p <= EXTREME_LO or p >= EXTREME_HI


def price_at(con: sqlite3.Connection, ticker: str, ts: int,
             lookback_s: int = PRICE_LOOKBACK_H * 3600) -> float | None:
    """Hourly-candle bid/ask mid (trade close fallback) at <= ts, in [0,1]."""
    r = con.execute(
        """SELECT price_close, yes_bid_close, yes_ask_close FROM candlesticks
           WHERE ticker=? AND period_interval=60 AND ts<=? AND ts>=?
           ORDER BY ts DESC LIMIT 1""",
        (ticker, ts, ts - lookback_s),
    ).fetchone()
    if r is None:
        return None
    pc, yb, ya = r
    if yb is not None and ya is not None:
        return (float(yb) + float(ya)) / 2.0
    return float(pc) if pc is not None else None


def enumerate_candidates(
    window: str,
    db_path: Path = DB,
    anchoring: str = "close",
    windows: dict | None = None,
) -> tuple[list[Candidate], dict]:
    """The full price-only candidate set for one window, plus funnel counts.

    ``anchoring``:
      * ``"close"`` — the FROZEN behaviour: D = close_time − DECISION_DAYS.
      * ``"event"`` — v2 anchoring (post-hoc infrastructure fix): derive the
        decision-relevant event date from market metadata
        (bot/forecaster/event_anchor.py — explicit text dates, statutory
        election calendar, expected_expiration_time; NEVER the
        outcome-adjacent activity signature) and set D = event − k whenever
        the event precedes close by more than k days. Everything else
        (extreme band, volume, lifetime, categories) stays frozen.

    ``windows`` overrides the frozen WINDOWS table (report-only reruns).
    """
    if anchoring not in ("close", "event"):
        raise ValueError(f"unknown anchoring: {anchoring!r}")
    w = (windows or WINDOWS)[window]
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            f"""SELECT ticker, event_ticker, series_ticker, title, yes_sub_title,
                       category, open_time, close_time, volume, result,
                       rules_primary, raw_json
                FROM markets
                WHERE result IN ('yes','no')
                  AND close_time >= ? AND close_time <= ?||'T23:59:59Z'
                  AND volume >= ?
                  AND category IN ({','.join('?' * len(CATEGORIES))})
                ORDER BY ticker""",
            (w["close_min"], w["close_max"], MIN_VOLUME, *CATEGORIES),
        ).fetchall()
        funnel = {"settled_vol_cat": len(rows), "lifetime_ok": 0,
                  "price_at_d": 0, "extreme": 0}
        if anchoring == "event":
            funnel["event_anchored"] = 0
        out: list[Candidate] = []
        for r in rows:
            close_ts = _iso_to_ts(r["close_time"])
            open_ts = _iso_to_ts(r["open_time"])
            if close_ts - open_ts < MIN_LIFETIME_DAYS * DAY:
                continue
            funnel["lifetime_ok"] += 1
            d_ts = close_ts - DECISION_DAYS * DAY
            event_ts: int | None = None
            anchor_source, anchor_prov = "close_time", ""
            if anchoring == "event":
                anchor = EA.derive_event_anchor(
                    title=r["title"] or "",
                    subtitle=r["yes_sub_title"] or "",
                    rules=(r["rules_primary"] or "")[:2000],
                    open_ts=open_ts, close_ts=close_ts,
                    raw_json=r["raw_json"],
                )
                d_ts, used = EA.anchored_decision_ts(
                    anchor, close_ts, k_days=DECISION_DAYS)
                if used != "close_time":
                    funnel["event_anchored"] += 1
                    event_ts = anchor.event_ts
                    anchor_source, anchor_prov = used, anchor.provenance
            p = price_at(con, r["ticker"], d_ts)
            if p is None:
                continue
            funnel["price_at_d"] += 1
            if not is_extreme(p):
                continue
            funnel["extreme"] += 1
            title = r["title"] or ""
            if r["yes_sub_title"]:
                title = f"{title} — {r['yes_sub_title']}"
            out.append(Candidate(
                ticker=r["ticker"], event_ticker=r["event_ticker"] or r["ticker"],
                series_ticker=r["series_ticker"] or r["ticker"].split("-")[0],
                window=window, title=title, category=r["category"],
                rules=(r["rules_primary"] or "")[:1200],
                open_ts=open_ts, close_ts=close_ts, close_time=r["close_time"],
                decision_ts=d_ts, price_at_d=p,
                cheap_side="yes" if p <= EXTREME_LO else "no",
                volume=float(r["volume"]), result=r["result"],
                event_ts=event_ts, anchor_source=anchor_source,
                anchor_provenance=anchor_prov,
            ))
        return out, funnel
    finally:
        con.close()


def dedupe_by_event(cands: Sequence[Candidate]) -> list[Candidate]:
    """One candidate per event (highest volume): mutually-exclusive legs of
    the same event are one bet, not many independent hits."""
    by_ev: dict[str, Candidate] = {}
    for c in cands:
        cur = by_ev.get(c.event_ticker)
        if cur is None or c.volume > cur.volume:
            by_ev[c.event_ticker] = c
    return sorted(by_ev.values(), key=lambda c: c.ticker)


# ---------------------------------------------------------------------------
# 2. Haiku mispricing screen — "is genuine uncertainty possibly mispriced?"
# ---------------------------------------------------------------------------
# Adapted from bot/forecaster/screen.py (FutureSearch's insider/methodology
# screens) with a third screen specific to the hits thesis: at an extreme
# price the question is not "should a desk forecast this?" but "is there any
# research-resolvable path to the unexpected outcome, or is the extreme
# price structurally correct?". Haiku 4.5 (knowledge cutoff Feb 2025 —
# probed) predates every market in both windows, so it cannot know outcomes.

HITS_SCREEN_SYSTEM = """\
You are a market-screening classifier for a forecasting desk that hunts
mispriced longshots. You never use tools and never do web research; you
judge only from the text given. Your knowledge of events after your training
cutoff is nil and you must not pretend otherwise — judge STRUCTURE, not
outcomes. Answer ONLY with the JSON array requested — no prose, no fence.
"""

HITS_SCREEN_RUBRIC = """\
Each numbered item below is a prediction market that is trading at an
EXTREME price (shown): the market is nearly certain of the outcome. A
research desk will do deep research ONLY on items where that near-certainty
might be wrong. For each item apply ALL THREE screens; PASS only if it
clears all three.

SCREEN A — insider/adverse-selection. REJECT if the outcome is a pre-taped
show or committee-decided award; a private individual's personal decision
(what someone will wear/say/post/announce); something already determined but
unannounced, or known to a small group; or subject to moral hazard (a cheap
stunt could resolve it, or a few people can resolve it in their own favor).

SCREEN B — methodology fit. REJECT sports of any kind, cryptocurrency,
short-horizon financial price targets (a specific stock/index/commodity
level by a date), weather/climate station readings, and video-game/
streaming/fan minutiae. PASS elections, legislation, geopolitics, government
policy and legal outcomes, economic indicators and central-bank decisions,
public-health/science/tech milestones, company events (IPOs, M&A,
leadership changes).

SCREEN C — genuine uncertainty at the extreme. The market says this outcome
is ~certain. REJECT if that near-certainty is STRUCTURAL — e.g. a
constitutional or procedural impossibility on this timescale; a dominant
incumbent/frontrunner where the trailing outcome needs a shock; a status quo
that requires many independent things to change within days; a deadline
that is arithmetically almost unreachable. PASS only if you can name a
CONCRETE, research-resolvable path by which the unexpected side happens at
meaningfully higher probability than the price implies — e.g. a scheduled
event (vote, ruling, filing, election, negotiation deadline) before close
that could flip it; a close contest that polls/filings could illuminate; an
actor with declared intent and capability; a data release whose value is
genuinely uncertain. Name the path in "path" (<= 20 words) or REJECT.

When in doubt, REJECT — the desk would rather skip ten real mispricings
than research a hundred correctly-priced certainties.

Answer with a JSON array, one object per item, same order:
[{"i": <number>, "pass": true|false, "code": "insider"|"sports"|"crypto"|
"fin_price"|"weather"|"fan_minutiae"|"moral_hazard"|"structural_certainty"|
"other"|null, "path": "<if pass: the concrete path>"|null}]

Items:
"""


@dataclass(frozen=True)
class HitsScreenVerdict:
    index: int
    passed: bool
    code: str | None
    path: str | None = None
    parse_failed: bool = False


def _fmt_screen_item(i: int, c: Candidate) -> str:
    days = (c.close_ts - c.decision_ts) // DAY
    return (
        f"{i}. [{c.category}] {c.title.strip()}\n"
        f"   Market price for YES: {c.price_at_d * 100:.0f}c "
        f"(the market says {'NO' if c.price_at_d < 0.5 else 'YES'} is ~certain). "
        f"Resolves in ~{days} days."
    )


def build_hits_screen_prompt(cands: Sequence[Candidate]) -> str:
    return HITS_SCREEN_RUBRIC + "\n".join(
        _fmt_screen_item(i + 1, c) for i, c in enumerate(cands)
    )


def parse_hits_screen_response(text: str, n: int) -> list[HitsScreenVerdict]:
    """Fail closed: any missing/malformed element ⇒ REJECT."""
    m = re.search(r"\[.*\]", text, re.S)
    verdicts: dict[int, HitsScreenVerdict] = {}
    if m:
        try:
            arr = json.loads(m.group(0))
        except ValueError:
            arr = []
        for obj in arr if isinstance(arr, list) else []:
            if not isinstance(obj, dict):
                continue
            i = obj.get("i")
            if not isinstance(i, int) or not (1 <= i <= n):
                continue
            path = obj.get("path")
            verdicts[i - 1] = HitsScreenVerdict(
                index=i - 1,
                passed=bool(obj.get("pass") is True),
                code=obj.get("code"),
                path=str(path) if path else None,
            )
    out = []
    for i in range(n):
        v = verdicts.get(i)
        if v is None:
            v = HitsScreenVerdict(index=i, passed=False, code="parse_failure",
                                  parse_failed=True)
        out.append(v)
    return out


def screen_candidates(
    client: LLMClient,
    cands: Sequence[Candidate],
    batch_size: int = 12,
    model: str = "haiku",
    workers: int = 4,
) -> tuple[list[HitsScreenVerdict], list[LLMResponse]]:
    """Batched Haiku mispricing screen; verdicts aligned with input order."""
    from concurrent.futures import ThreadPoolExecutor

    starts = list(range(0, len(cands), batch_size))

    def one(start: int) -> tuple[int, LLMResponse]:
        chunk = cands[start:start + batch_size]
        req = LLMRequest(
            model=model,
            system=HITS_SCREEN_SYSTEM,
            prompt=build_hits_screen_prompt(chunk),
            params={"stage": "hits_screen", "effort": "low"},
        )
        return start, client.complete(req)

    verdicts: list[HitsScreenVerdict] = []
    responses: list[LLMResponse] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for start, resp in ex.map(one, starts):
            responses.append(resp)
            chunk = cands[start:start + batch_size]
            for v in parse_hits_screen_response(resp.text, len(chunk)):
                verdicts.append(HitsScreenVerdict(
                    index=start + v.index, passed=v.passed, code=v.code,
                    path=v.path, parse_failed=v.parse_failed,
                ))
    verdicts.sort(key=lambda v: v.index)
    return verdicts, responses


# ---------------------------------------------------------------------------
# 3. Full-pipeline forecasts on the screened survivors
# ---------------------------------------------------------------------------

def forecast_candidate(client: LLMClient, ledger, cand: Candidate,
                       cost_ledger=None) -> dict:
    """One full-pipeline forecast at D (dossier + 2-pass judge), PIT-bounded.

    The judge model follows the window's cutoff rule (module docstring).
    Returns a JSON-serializable row (never the settled result — that stays
    out of every prompt by construction: Candidate.result is not passed).
    """
    from bot.backtest.costs import CostLedger
    from bot.forecaster.pipeline import ForecastPipeline

    judge_model = WINDOWS[cand.window]["judge_model"]
    costs = cost_ledger or CostLedger()
    pipe = ForecastPipeline(
        client, ledger, costs, run_id=f"hits-{cand.window}",
        judge_model=judge_model, dossier_model="sonnet",
        judge_effort="medium",
    )
    asof = datetime.fromtimestamp(cand.decision_ts, tz=timezone.utc)
    res = pipe.forecast(
        question_key=cand.ticker,
        question=cand.title,
        background=cand.rules,
        criteria=cand.rules,
        asof=asof.strftime("%Y-%m-%dT%H:%M:%SZ"),
        close=cand.close_time,
        market_p=cand.price_at_d,
        mode="full",
    )
    verdict = res.verdict
    return {
        "ticker": cand.ticker,
        "window": cand.window,
        "judge_model": judge_model,
        "p_yes": res.p_yes,
        "price_at_d": cand.price_at_d,
        "cheap_side": cand.cheap_side,
        "decision_ts": cand.decision_ts,
        "cost_cents": res.cost_cents,
        "error": res.error,
        "divergent": verdict.divergent if verdict else None,
        "passes": (
            [{"stance": p.stance, "p_yes": p.p_yes,
              "premortem": p.premortem,
              "key_uncertainties": list(p.key_uncertainties)}
             for p in verdict.passes] if verdict else []
        ),
        "n_sources": res.pack.n_sources() if res.pack else 0,
        "cache_keys": res.cache_keys,
    }

"""Tier 0 — deterministic prefilter (free, no LLM).

research/cost-architecture.md §3.1 + SYNTHESIS P-4: price band 3–97¢,
volume floor, >10 days to close (**actually enforced** — FutureSearch stated
it but didn't enforce it, research/futuresearch.md §2.3), category blocklist,
dedupe to the event level.

Works on two shapes:
- Kalshi market dicts (live/backtest universe), via :func:`prefilter_kalshi`;
- ForecastBench rows (rung-2 eval), via :func:`prefilter_forecastbench` —
  volume is not available there, so that filter is skipped and said so.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

PRICE_LO = 0.03
PRICE_HI = 0.97
MIN_VOLUME = 5_000
MIN_DAYS_TO_CLOSE = 10

#: Cheap keyword screens for categories the LLM screen would reject anyway —
#: applied first because they are free. The Haiku screen (screen.py) remains
#: the authority; this only removes the unambiguous bulk.
CATEGORY_BLOCK_PATTERNS: tuple[tuple[str, str], ...] = (
    ("sports", r"\b(nba|nfl|mlb|nhl|ncaa|premier league|la liga|serie a|"
               r"bundesliga|champions league|uefa|fifa|world cup|super bowl|"
               r"grand slam|wimbledon|us open|f1|formula 1|ufc|boxing match|"
               r"playoffs?|touchdowns?|home runs?|espn|vs\.? .* game|"
               r"win the .* (?:game|match|series|title|championship)|"
               r"be relegated|top scorer|golden boot)\b"),
    ("crypto", r"\b(bitcoin|btc|ethereum|eth\b|solana|dogecoin|memecoin|"
               r"nft|defi|on-chain|market cap of .*coin|crypto)\b"),
    ("entertainment_taped", r"\b(bachelor|bachelorette|survivor|big brother|"
               r"love island|rupaul|masked singer|reality tv)\b"),
)


@dataclass
class PrefilterResult:
    kept: list[Any]
    rejected: list[tuple[Any, str]] = field(default_factory=list)

    @property
    def reject_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for _, reason in self.rejected:
            out[reason] = out.get(reason, 0) + 1
        return out


def category_block(text: str) -> str | None:
    low = text.lower()
    for name, pat in CATEGORY_BLOCK_PATTERNS:
        if re.search(pat, low):
            return name
    return None


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def prefilter_forecastbench(rows: Sequence[Any]) -> PrefilterResult:
    """Tier-0 on ForecastBench rows (bot.evals.forecastbench.Row).

    Volume floor is NOT applicable (the dataset carries no volume); the
    price band uses ``market_p`` at freeze and the >10d rule uses
    close − freeze.
    """
    res = PrefilterResult(kept=[])
    for r in rows:
        if not (PRICE_LO <= r.market_p <= PRICE_HI):
            res.rejected.append((r, "price_band"))
            continue
        f, c = _parse(r.freeze), _parse(r.close)
        if f and c and (c - f).days <= MIN_DAYS_TO_CLOSE:
            res.rejected.append((r, "too_close_to_close"))
            continue
        blocked = category_block(r.question)
        if blocked:
            res.rejected.append((r, f"category:{blocked}"))
            continue
        res.kept.append(r)
    return res


def prefilter_kalshi(markets: Iterable[dict[str, Any]],
                     now: datetime | None = None) -> PrefilterResult:
    """Tier-0 on Kalshi market dicts (needs keys: yes_price or last_price in
    cents, volume, close_time ISO, title, event_ticker)."""
    now = now or datetime.now(timezone.utc)
    res = PrefilterResult(kept=[])
    seen_events: set[str] = set()
    for m in markets:
        price_c = m.get("yes_price", m.get("last_price"))
        if price_c is None:
            res.rejected.append((m, "no_price"))
            continue
        p = float(price_c) / 100.0
        if not (PRICE_LO <= p <= PRICE_HI):
            res.rejected.append((m, "price_band"))
            continue
        if float(m.get("volume", 0)) < MIN_VOLUME:
            res.rejected.append((m, "volume"))
            continue
        close = _parse(m.get("close_time"))
        if close is None or (close - now).days <= MIN_DAYS_TO_CLOSE:
            res.rejected.append((m, "too_close_to_close"))
            continue
        blocked = category_block(str(m.get("title", "")))
        if blocked:
            res.rejected.append((m, f"category:{blocked}"))
            continue
        ev = str(m.get("event_ticker", m.get("ticker", "")))
        if ev in seen_events:
            res.rejected.append((m, "event_dedupe"))
            continue
        seen_events.add(ev)
        res.kept.append(m)
    return res

"""Minimal self-contained Polymarket client (read-only, no API keys).

Scope: exactly what R7 needs — market metadata + fee schedule from Gamma,
resolution truth from the CLOB, and 1-minute midpoint history from
``prices-history``. Everything else (trade tape, holders, books) is out of
scope. Rules encoded here come from research/polymarket-data.md, all of
which were verified live against production:

* ``prices-history`` takes the CLOB **token id**, not conditionId/market id.
* ``startTs``/``endTs`` window is capped at exactly 1,296,000 s = **15 days**.
* ``interval=max``/``all`` return only the last 31 days and are **empty for
  resolved markets** — resolved history is reachable ONLY via explicit
  startTs/endTs windows.
* Anchor the window chain on **``closedTime``**, never ``endDate``
  (MicroStrategy: endDate 2026-07-01 vs closedTime 2026-06-04; anchoring on
  endDate returns empty).
* ``closedTime`` is ``"YYYY-MM-DD HH:MM:SS+00"`` — not ISO-8601.
* ``outcomes``/``outcomePrices``/``clobTokenIds`` are JSON-encoded *strings*.
* ``p`` is the **midpoint**, forward-filled every minute — never evidence a
  trade happened, and filling at it is filling at mid. Callers must add a
  spread model.
* Fees: read ``feeSchedule`` off the market record; ``fee = shares × rate ×
  p × (1−p)``, takers only, makers 0. Fees only rolled out ~Q2 2026, and the
  stored schedule is today's, not point-in-time.
* data-api returns ``{"error": "Too Many Requests"}`` with HTTP 200 — check
  the body, not just the status.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

WINDOW_CAP_S = 1_296_000          # 15 days, hard API limit
DEFAULT_DATA_DIR = "data/polymarket"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


def _request(url: str, retries: int = 4, timeout: int = 60, method: str = "GET",
             body: dict | None = None):
    last = None
    for attempt in range(retries):
        try:
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(
                url, data=data, method=method,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    # Gamma sits behind Cloudflare, which answers urllib's
                    # default UA with "Error 1010: Access denied" (403).
                    "User-Agent": USER_AGENT,
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read())
            # data-api / clob signal throttling in the BODY with HTTP 200.
            if isinstance(out, dict) and str(out.get("error", "")).lower().startswith(
                "too many"
            ):
                raise RuntimeError("rate limited (body error)")
            return out
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:200]
            except Exception:
                pass
            last = f"HTTP {e.code}: {detail}"
            if e.code == 400:
                raise ValueError(last) from None
            time.sleep(0.6 * 2**attempt)
        except Exception as e:  # noqa: BLE001
            last = repr(e)
            time.sleep(0.6 * 2**attempt)
    raise RuntimeError(f"{method} failed after {retries}: {url} :: {last}")


def parse_closed_time(value: str | None) -> int | None:
    """``"2026-06-04 00:34:19+00"`` (space separator, 2-digit offset) or
    ISO-8601 -> unix seconds. Returns None when absent."""
    if not value:
        return None
    import datetime as dt

    s = str(value).strip().replace("Z", "+00:00")
    if len(s) >= 3 and (s[-3] in "+-") and s.count(":") <= 2:
        s = s + ":00"           # "+00" -> "+00:00"
    s = s.replace(" ", "T", 1) if " " in s and "T" not in s else s
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp())


@dataclass
class PmMarket:
    """The point-in-time-safe subset plus the scorer-only resolution fields."""
    id: str
    slug: str
    question: str
    description: str
    condition_id: str
    clob_token_ids: list[str]
    start_ts: int | None
    end_ts: int | None
    closed_ts: int | None
    closed: bool
    outcomes: list[str] = field(default_factory=list)
    outcome_prices: list[str] = field(default_factory=list)
    fees_enabled: bool = False
    fee_schedule: dict = field(default_factory=dict)
    enable_order_book: bool = True
    tick_size: float | None = None
    raw: dict = field(default_factory=dict)

    @property
    def yes_token(self) -> str | None:
        return self.clob_token_ids[0] if self.clob_token_ids else None

    @property
    def resolved_yes(self) -> bool | None:
        """Gamma's view of the winner (cross-check with ``clob_winner``)."""
        if not self.outcome_prices:
            return None
        try:
            p0 = float(self.outcome_prices[0])
        except (TypeError, ValueError):
            return None
        if p0 == 1.0:
            return True
        if p0 == 0.0:
            return False
        return None

    @property
    def fee_rate(self) -> float:
        if not self.fees_enabled:
            return 0.0
        return float(self.fee_schedule.get("rate", 0.0) or 0.0)

    def taker_fee_dollars(self, shares: float, price: float) -> float:
        """``shares × rate × p × (1−p)``; makers pay 0."""
        return shares * self.fee_rate * price * (1.0 - price)


def _jsonish(value, default):
    if value is None:
        return default
    if isinstance(value, list):
        return value
    try:
        out = json.loads(value)
        return out if isinstance(out, list) else default
    except (TypeError, ValueError):
        return default


def market_from_row(row: dict) -> PmMarket:
    return PmMarket(
        id=str(row.get("id", "")),
        slug=row.get("slug", ""),
        question=row.get("question", ""),
        description=row.get("description", "") or "",
        condition_id=row.get("conditionId", "") or "",
        clob_token_ids=[str(t) for t in _jsonish(row.get("clobTokenIds"), [])],
        start_ts=parse_closed_time(row.get("startDate")),
        end_ts=parse_closed_time(row.get("endDate")),
        closed_ts=parse_closed_time(row.get("closedTime")),
        closed=bool(row.get("closed")),
        outcomes=[str(o) for o in _jsonish(row.get("outcomes"), [])],
        outcome_prices=[str(p) for p in _jsonish(row.get("outcomePrices"), [])],
        fees_enabled=bool(row.get("feesEnabled")),
        fee_schedule=row.get("feeSchedule") or {},
        enable_order_book=bool(row.get("enableOrderBook", True)),
        tick_size=(
            float(row["orderPriceMinTickSize"])
            if row.get("orderPriceMinTickSize") is not None
            else None
        ),
        raw=row,
    )


class PolymarketClient:
    """Thin, polite, cache-friendly. No auth anywhere on these paths."""

    def __init__(self, cache_dir: str = DEFAULT_DATA_DIR, sleep_s: float = 0.05):
        self.cache_dir = cache_dir
        self.sleep_s = sleep_s
        os.makedirs(cache_dir, exist_ok=True)

    # -- Gamma metadata ---------------------------------------------------

    def market_by_slug(self, slug: str) -> PmMarket | None:
        try:
            row = _request(f"{GAMMA}/markets/slug/{urllib.parse.quote(slug)}")
        except (ValueError, RuntimeError):
            return None
        if isinstance(row, list):
            row = row[0] if row else None
        return market_from_row(row) if row else None

    def search_markets(self, limit: int = 100, **params) -> list[PmMarket]:
        """``/markets/keyset`` (``/markets`` is deprecated, sunset passed).
        Cursor param is ``after_cursor`` — ``cursor``/``next_cursor`` are
        silently ignored and you page forever."""
        q = {"limit": min(limit, 100), **params}
        url = f"{GAMMA}/markets/keyset?" + urllib.parse.urlencode(q, doseq=True)
        out = _request(url)
        rows = out.get("markets", []) if isinstance(out, dict) else out
        return [market_from_row(r) for r in rows]

    def clob_market(self, condition_id: str) -> dict | None:
        """Settlement truth: ``tokens[].winner`` + ``price ∈ {0,1}``."""
        try:
            return _request(f"{CLOB}/markets/{condition_id}")
        except (ValueError, RuntimeError):
            return None

    def clob_winner(self, condition_id: str) -> str | None:
        m = self.clob_market(condition_id)
        if not m:
            return None
        for t in m.get("tokens", []):
            if t.get("winner"):
                return t.get("outcome")
        return None

    # -- price history ----------------------------------------------------

    def prices_history(
        self, token_id: str, start_ts: int, end_ts: int, fidelity: int = 1
    ) -> list[dict]:
        """One window; caller must respect the 15-day cap."""
        if end_ts - start_ts > WINDOW_CAP_S:
            raise ValueError("window exceeds the 15-day API cap")
        url = (
            f"{CLOB}/prices-history?market={token_id}&startTs={start_ts}"
            f"&endTs={end_ts}&fidelity={fidelity}"
        )
        out = _request(url)
        return out.get("history", []) if isinstance(out, dict) else []

    def full_history(
        self,
        token_id: str,
        start_ts: int,
        end_ts: int,
        fidelity: int = 1,
        max_windows: int = 60,
    ) -> list[tuple[int, float]]:
        """Chained half-open 15-day windows -> deduped [(ts, mid)] ascending.

        The chain is half-open so the last point of window n and the first
        of n+1 cannot collide.
        """
        pts: dict[int, float] = {}
        s = int(start_ts)
        end_ts = int(end_ts)
        windows = 0
        while s < end_ts and windows < max_windows:
            e = min(s + WINDOW_CAP_S, end_ts)
            for row in self.prices_history(token_id, s, e, fidelity):
                try:
                    pts[int(row["t"])] = float(row["p"])
                except (KeyError, TypeError, ValueError):
                    continue
            windows += 1
            s = e
            if self.sleep_s:
                time.sleep(self.sleep_s)
        return sorted(pts.items())

    def market_life_history(
        self, m: PmMarket, fidelity: int = 1, pad_s: int = 0
    ) -> list[tuple[int, float]]:
        """Whole life of a resolved market: startDate -> closedTime."""
        if not m.yes_token:
            return []
        start = m.start_ts or ((m.closed_ts or m.end_ts or 0) - 90 * 86400)
        end = m.closed_ts or m.end_ts
        if not end:
            return []
        return self.full_history(m.yes_token, start - pad_s, end + pad_s, fidelity)

"""Thin client for the Kalshi **public, unauthenticated** REST API.

Base: ``https://api.elections.kalshi.com/trade-api/v2/``

Everything here is read-only market data. No API key is required or used.
The quirks handled below were all determined empirically against the live
API on 2026-08-11; see ``bot/data/NOTES.md`` for the write-up.

Key quirks baked into this module
---------------------------------
* Monetary fields come back as **decimal-dollar strings** (``"0.9700"``) under
  ``*_dollars`` keys, and sizes as fixed-point strings (``"8.40"``) under
  ``*_fp`` keys. There is no integer-cent representation any more, and prices
  can be sub-cent (``price_level_structure == "deci_cent"``). We parse to
  ``float`` dollars and keep the raw JSON alongside.
* ``/markets`` ``status`` filter accepts only ``unopened|open|closed|settled``.
  ``settled`` matches markets whose ``status`` field reads ``finalized``.
* Candlesticks are capped at **5000 periods per request**; longer ranges 400
  with ``"max candlesticks: 5000"``. :meth:`KalshiClient.get_candlesticks`
  chunks transparently.
* Candlestick periods with no trades omit the ``price`` OHLC sub-keys
  entirely (only ``price.previous_dollars`` plus bid/ask OHLC are present).
* Settled markets are purged from the API roughly 90 days after close, so
  neither ``/markets`` enumeration nor ``/markets/{ticker}`` can reach older
  history. Candlesticks for markets *still* served go back to market open.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence

import httpx

__all__ = ["KalshiClient", "KalshiAPIError", "DEFAULT_BASE_URL"]

DEFAULT_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

log = logging.getLogger(__name__)

#: Max candlestick periods the API will return for a single request.
MAX_CANDLESTICKS = 5000

#: Valid values for the ``/markets`` ``status`` query parameter.
MARKET_STATUSES = ("unopened", "open", "closed", "settled")

#: Valid ``period_interval`` values (minutes) for candlesticks.
CANDLE_INTERVALS = (1, 60, 1440)


class KalshiAPIError(RuntimeError):
    """Non-retryable API error (4xx other than 429), carrying the response."""

    def __init__(self, status_code: int, url: str, body: str) -> None:
        super().__init__(f"HTTP {status_code} for {url}: {body[:300]}")
        self.status_code = status_code
        self.url = url
        self.body = body


class _RateLimiter:
    """Simple thread-safe token-free rate limiter: min interval between calls."""

    def __init__(self, rate_per_sec: float) -> None:
        self._min_interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
        self._lock = threading.Lock()
        self._next_at = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next_at = now + self._min_interval


@dataclass
class KalshiClient:
    """Polite, retrying HTTP client for the public Kalshi market-data API.

    Args:
        base_url: API root, without a trailing slash.
        rate_per_sec: Target request rate. Kalshi's unauthenticated tier starts
            returning 429s somewhere above ~10 req/s; 5 is comfortable.
        max_retries: Attempts per request for 429/5xx/transport errors.
        timeout: Per-request timeout in seconds.
    """

    base_url: str = DEFAULT_BASE_URL
    rate_per_sec: float = 5.0
    max_retries: int = 6
    timeout: float = 45.0

    _client: httpx.Client = field(init=False, repr=False)
    _limiter: _RateLimiter = field(init=False, repr=False)
    request_count: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self._limiter = _RateLimiter(self.rate_per_sec)
        self._client = httpx.Client(
            timeout=self.timeout,
            headers={"Accept": "application/json", "User-Agent": "c-g-trading-research/0.1"},
            follow_redirects=True,
        )

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "KalshiClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- core --------------------------------------------------------------
    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET ``path`` with rate limiting, retries and exponential backoff.

        Raises:
            KalshiAPIError: on a non-retryable 4xx, or after exhausting retries.
        """
        url = f"{self.base_url}{path}"
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        delay = 1.0
        last: Exception | None = None

        for attempt in range(self.max_retries):
            self._limiter.acquire()
            try:
                self.request_count += 1
                resp = self._client.get(url, params=clean)
            except httpx.HTTPError as exc:  # transport-level; retry
                last = exc
                log.warning("transport error %s (attempt %d): %s", url, attempt + 1, exc)
            else:
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 429 or resp.status_code >= 500:
                    last = KalshiAPIError(resp.status_code, url, resp.text)
                    log.warning(
                        "retryable %s on %s (attempt %d)", resp.status_code, url, attempt + 1
                    )
                else:
                    # 400/404 etc. are deterministic: surface immediately.
                    raise KalshiAPIError(resp.status_code, url, resp.text)

            time.sleep(delay + random.uniform(0, 0.4))
            delay = min(delay * 2, 30.0)

        assert last is not None
        raise last

    def _paginate(
        self,
        path: str,
        collection: str,
        params: dict[str, Any] | None = None,
        page_limit: int = 1000,
        max_pages: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield every record from a cursor-paginated list endpoint.

        The API returns ``{"<collection>": [...], "cursor": "..."}``; an empty
        or absent cursor, or an empty page, terminates the walk. Some endpoints
        keep returning the same cursor when exhausted, so repeats also stop it.
        """
        p = dict(params or {})
        p["limit"] = page_limit
        cursor: str | None = None
        seen_cursors: set[str] = set()
        pages = 0

        while True:
            if cursor:
                p["cursor"] = cursor
            payload = self._get(path, p)
            rows = payload.get(collection) or []
            for row in rows:
                yield row
            pages += 1

            cursor = payload.get("cursor") or None
            if not rows or not cursor or cursor in seen_cursors:
                return
            if max_pages is not None and pages >= max_pages:
                return
            seen_cursors.add(cursor)

    # -- endpoints ---------------------------------------------------------
    def get_markets(
        self,
        *,
        status: str | None = None,
        min_close_ts: int | None = None,
        max_close_ts: int | None = None,
        event_ticker: str | None = None,
        series_ticker: str | None = None,
        tickers: Sequence[str] | None = None,
        page_limit: int = 1000,
        max_pages: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Iterate markets, following cursor pagination to exhaustion.

        Args:
            status: One of :data:`MARKET_STATUSES`. ``"settled"`` selects
                markets whose ``status`` field reads ``finalized``.
            min_close_ts / max_close_ts: Unix-second bounds on ``close_time``.
                Results come back newest-close-first within the window.
            tickers: Explicit ticker list (comma-joined server side).
        """
        if status is not None and status not in MARKET_STATUSES:
            raise ValueError(f"status must be one of {MARKET_STATUSES}, got {status!r}")
        params: dict[str, Any] = {
            "status": status,
            "min_close_ts": min_close_ts,
            "max_close_ts": max_close_ts,
            "event_ticker": event_ticker,
            "series_ticker": series_ticker,
            "tickers": ",".join(tickers) if tickers else None,
        }
        yield from self._paginate(
            "/markets", "markets", params, page_limit=page_limit, max_pages=max_pages
        )

    def get_market(self, ticker: str) -> dict[str, Any] | None:
        """Fetch a single market, or ``None`` if the API no longer serves it.

        Returns ``None`` rather than raising on 404 because Kalshi purges
        settled markets after roughly 90 days, which is an expected outcome.
        """
        try:
            return self._get(f"/markets/{ticker}").get("market")
        except KalshiAPIError as exc:
            if exc.status_code == 404:
                return None
            raise

    def get_events(
        self,
        *,
        status: str | None = None,
        series_ticker: str | None = None,
        with_nested_markets: bool = False,
        page_limit: int = 200,
        max_pages: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Iterate events. Events carry the ``category`` field markets lack."""
        params = {
            "status": status,
            "series_ticker": series_ticker,
            "with_nested_markets": "true" if with_nested_markets else None,
        }
        yield from self._paginate(
            "/events", "events", params, page_limit=page_limit, max_pages=max_pages
        )

    def get_series(self, series_ticker: str) -> dict[str, Any] | None:
        """Fetch series metadata (category, frequency, fee_type, tags)."""
        try:
            return self._get(f"/series/{series_ticker}").get("series")
        except KalshiAPIError as exc:
            if exc.status_code == 404:
                return None
            raise

    def list_series(self, category: str) -> list[dict[str, Any]]:
        """List every series in a category.

        ``GET /series?category=<c>`` is unpaginated and returns the full set.
        The category string must match exactly (see
        :func:`known_categories`); unknown categories return an empty list.
        """
        payload = self._get("/series", {"category": category})
        return payload.get("series") or []

    def get_candlesticks(
        self,
        series_ticker: str,
        ticker: str,
        start_ts: int,
        end_ts: int,
        period_interval: int = 60,
    ) -> list[dict[str, Any]]:
        """Fetch candlesticks for one market, chunking around the 5000 cap.

        Args:
            series_ticker: Parent series (the path requires it).
            ticker: Market ticker.
            start_ts / end_ts: Inclusive unix-second bounds.
            period_interval: Minutes per candle; one of :data:`CANDLE_INTERVALS`.

        Returns:
            Candles ordered by ``end_period_ts`` ascending, deduplicated.
            Empty if the market is unknown or has no history in range.

        Note:
            Candles for periods with no trades carry **no** ``price`` OHLC keys
            (only ``price.previous_dollars``); bid/ask OHLC are still present.
        """
        if period_interval not in CANDLE_INTERVALS:
            raise ValueError(
                f"period_interval must be one of {CANDLE_INTERVALS}, got {period_interval}"
            )
        if end_ts <= start_ts:
            return []

        span = period_interval * 60 * (MAX_CANDLESTICKS - 2)
        out: dict[int, dict[str, Any]] = {}
        cur = start_ts
        while cur < end_ts:
            chunk_end = min(cur + span, end_ts)
            try:
                payload = self._get(
                    f"/series/{series_ticker}/markets/{ticker}/candlesticks",
                    {
                        "start_ts": cur,
                        "end_ts": chunk_end,
                        "period_interval": period_interval,
                    },
                )
            except KalshiAPIError as exc:
                if exc.status_code in (400, 404):
                    # Unknown market/series, or a range the API dislikes.
                    log.debug("candlesticks %s %s: %s", ticker, period_interval, exc)
                    return sorted(out.values(), key=lambda c: c["end_period_ts"])
                raise
            for candle in payload.get("candlesticks") or []:
                out[int(candle["end_period_ts"])] = candle
            cur = chunk_end
        return sorted(out.values(), key=lambda c: c["end_period_ts"])

    def get_trades(
        self,
        *,
        ticker: str | None = None,
        min_ts: int | None = None,
        max_ts: int | None = None,
        page_limit: int = 1000,
        max_pages: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Iterate the public trade tape, newest first.

        Settled markets keep serving their full trade history for as long as
        the market itself is served (~90 days after close).
        """
        params = {"ticker": ticker, "min_ts": min_ts, "max_ts": max_ts}
        yield from self._paginate(
            "/markets/trades", "trades", params, page_limit=page_limit, max_pages=max_pages
        )


def known_categories() -> tuple[str, ...]:
    """Categories that ``GET /series?category=`` actually resolves.

    Determined by probing; the API has no endpoint that lists them and returns
    an empty set (not an error) for unrecognised strings.
    """
    return (
        "Politics",
        "Elections",
        "Economics",
        "World",
        "Financials",
        "Crypto",
        "Commodities",
        "Sports",
        "Climate and Weather",
        "Science and Technology",
        "Health",
        "Entertainment",
        "Companies",
        "Transportation",
    )

"""Structured ground-truth injection — the differentiator no OSS bot has.

research/oss-forecasters.md §5: every surveyed bot is news-only; none consumes
poll aggregates, FEC filings, or economic vintages. Where a question maps to a
covered category, we inject these as a DISTINCT dossier section:

- **VoteHub polls** (``api.votehub.com/polls``): filter ``created_at < D``
  client-side — the API is live, so the date filter is the PIT guarantee.
- **FEC Schedule E** independent expenditures (``api.open.fec.gov``,
  ``DEMO_KEY`` at 40 calls/hr): filter by ``filing_date < D``. The rate cap
  makes this a garnish, not a staple — applied only to election questions
  and cached hard.
- **ALFRED vintages**: the right tool for econ PIT, but it needs a (free)
  key that is NOT registered in this container — the client is written and
  returns None cleanly without one, and the dossier says "no vintage data
  available" rather than silently substituting a leaky latest-vintage
  series (fredgraph keyless = latest vintage only = banned).
- **BLS v1** (keyless): non-revised series only (CPI-U NSA ``CUUR*``);
  revised/seasonally-adjusted series are refused (silent-leak risk).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests

from bot.forecaster.retrieval import _DiskCache, _iso, parse_dt

UA = {"User-Agent": "c-g-trading-research/0.1 (point-in-time backtest; polite)"}

_FEC_LOCK = threading.Lock()
_FEC_LAST = [0.0]
_FEC_MIN_INTERVAL = 95.0  # DEMO_KEY: 40/hr -> stay safely under

#: BLS series safe for PIT use (never revised). CUUR* = CPI-U NSA.
BLS_SAFE_PREFIXES = ("CUUR",)


class GroundTruthClient:
    def __init__(self, cache: _DiskCache | None = None):
        self.cache = cache or _DiskCache()

    # -- VoteHub ------------------------------------------------------------

    def votehub_polls(
        self, asof: datetime, poll_type: str | None = None,
        subject_terms: list[str] | None = None, limit: int = 12,
    ) -> list[dict[str, Any]]:
        """Polls created strictly before ``asof`` (client-side date gate),
        newest first, optionally filtered by subject substring."""
        key = json.dumps(["votehub", poll_type or "", _iso(asof)])
        cached = self.cache.get("gt", key)
        if cached is None:
            try:
                params = {"page_size": "200"}
                if poll_type:
                    params["poll_type"] = poll_type
                r = requests.get("https://api.votehub.com/polls",
                                 params=params, headers=UA, timeout=45)
                cached = {"status": r.status_code,
                          "polls": r.json() if r.status_code == 200 else []}
            except (requests.RequestException, ValueError):
                cached = {"status": 0, "polls": []}
            self.cache.put("gt", key, cached)
        polls = cached.get("polls") or []
        if isinstance(polls, dict):
            polls = polls.get("polls") or polls.get("data") or []
        out = []
        for p in polls:
            created = parse_dt(str(p.get("created_at") or p.get("end_date") or ""))
            if created is None or created >= asof:
                continue  # the PIT gate
            if subject_terms:
                blob = json.dumps(p, ensure_ascii=False).lower()
                if not any(t.lower() in blob for t in subject_terms):
                    continue
            out.append(p)
        out.sort(key=lambda p: str(p.get("created_at") or ""), reverse=True)
        return out[:limit]

    # -- FEC Schedule E -----------------------------------------------------

    def fec_schedule_e(
        self, candidate_name: str, asof: datetime, limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Independent expenditures with ``filing_date < asof``. DEMO_KEY is
        rate-capped at 40/hr; everything is cached, and a rate-limit failure
        returns [] (the dossier simply lacks the section)."""
        key = json.dumps(["fec_se", candidate_name.lower(), _iso(asof)])
        cached = self.cache.get("gt", key)
        if cached is None:
            api_key = os.environ.get("FEC_API_KEY", "DEMO_KEY")
            with _FEC_LOCK:
                wait = _FEC_LAST[0] + _FEC_MIN_INTERVAL - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                _FEC_LAST[0] = time.monotonic()
            try:
                r = requests.get(
                    "https://api.open.fec.gov/v1/schedules/schedule_e/",
                    params={
                        "api_key": api_key,
                        "candidate_search": candidate_name,
                        "max_filing_date": asof.strftime("%Y-%m-%d"),
                        "sort": "-filing_date",
                        "per_page": str(limit * 2),
                    },
                    headers=UA, timeout=45,
                )
                cached = {"status": r.status_code,
                          "results": (r.json().get("results", [])
                                      if r.status_code == 200 else [])}
            except (requests.RequestException, ValueError):
                cached = {"status": 0, "results": []}
            self.cache.put("gt", key, cached)
        out = []
        for row in cached.get("results", []):
            fd = parse_dt(str(row.get("filing_date") or ""))
            if fd is None or fd >= asof:
                continue  # client-side re-check of the server filter
            out.append({
                "filing_date": row.get("filing_date"),
                "committee": (row.get("committee") or {}).get("name"),
                "candidate": row.get("candidate_name"),
                "support_oppose": row.get("support_oppose_indicator"),
                "amount": row.get("expenditure_amount"),
                "purpose": row.get("expenditure_description"),
            })
        return out[:limit]

    # -- ALFRED (needs key) + BLS (keyless, non-revised only) ----------------

    def alfred_vintage(
        self, series_id: str, asof: datetime, limit: int = 24,
    ) -> list[dict[str, Any]] | None:
        """Series as it was published on ``asof`` (realtime_start = realtime_end
        = D). Returns None when no FRED_API_KEY is registered — callers must
        treat that as 'no data', never fall back to a latest-vintage source."""
        api_key = os.environ.get("FRED_API_KEY")
        if not api_key:
            return None
        key = json.dumps(["alfred", series_id, _iso(asof)])
        cached = self.cache.get("gt", key)
        if cached is None:
            try:
                r = requests.get(
                    "https://api.stlouisfed.org/fred/series/observations",
                    params={
                        "series_id": series_id, "api_key": api_key,
                        "file_type": "json",
                        "realtime_start": asof.strftime("%Y-%m-%d"),
                        "realtime_end": asof.strftime("%Y-%m-%d"),
                        "sort_order": "desc", "limit": str(limit),
                    },
                    headers=UA, timeout=45,
                )
                cached = {"status": r.status_code,
                          "observations": (r.json().get("observations", [])
                                           if r.status_code == 200 else [])}
            except (requests.RequestException, ValueError):
                cached = {"status": 0, "observations": []}
            self.cache.put("gt", key, cached)
        return cached.get("observations", [])

    def bls_series(
        self, series_id: str, asof: datetime,
    ) -> list[dict[str, Any]]:
        """Keyless BLS v1 — REFUSES revised series (only ``CUUR*`` CPI-U NSA
        is never revised; anything else via BLS v1 is a silent leak)."""
        if not any(series_id.startswith(p) for p in BLS_SAFE_PREFIXES):
            raise ValueError(
                f"BLS series {series_id} is not on the never-revised allowlist "
                f"{BLS_SAFE_PREFIXES}; latest-revised values would leak"
            )
        key = json.dumps(["bls", series_id, str(asof.year)])
        cached = self.cache.get("gt", key)
        if cached is None:
            try:
                r = requests.post(
                    "https://api.bls.gov/publicAPI/v1/timeseries/data/",
                    json={"seriesid": [series_id],
                          "startyear": str(asof.year - 1),
                          "endyear": str(asof.year)},
                    headers={**UA, "Content-Type": "application/json"},
                    timeout=45,
                )
                cached = {"status": r.status_code,
                          "payload": r.json() if r.status_code == 200 else {}}
            except (requests.RequestException, ValueError):
                cached = {"status": 0, "payload": {}}
            self.cache.put("gt", key, cached)
        series = ((cached.get("payload") or {}).get("Results") or {}).get("series") or []
        if not series:
            return []
        out = []
        for row in series[0].get("data", []):
            # keep only reference periods that END before asof (monthly data
            # for month M is published in M+1; conservatively require the
            # month itself to be complete a month before asof)
            m = re.match(r"M(\d{2})", row.get("period", ""))
            if not m:
                continue
            y, mo = int(row["year"]), int(m.group(1))
            ref_end = datetime(y + (mo // 12), (mo % 12) + 1, 1, tzinfo=timezone.utc)
            publish_proxy = ref_end.replace(day=15)  # CPI publishes mid-next-month
            if publish_proxy >= asof:
                continue
            out.append({"year": row["year"], "period": row["period"],
                        "value": row["value"]})
        return out

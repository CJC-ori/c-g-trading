"""P-2 weather ground-truth pipeline (research/SYNTHESIS.md §1 P-2,
research/ground-truth.md §2 + §6).

Components (all verified live 2026-08-11 from this container):

1. **Station map** — the settlement station for every KXHIGH* daily-high
   series, extracted from the Kalshi series records (`settlement_sources`
   URL carries the CLI issuing office + location code) cross-checked
   against `rules_primary` text. 20 city series exist (not 12): the
   research's 12 plus BOS/HOU/LV/MIN/NOLA/OKC/SATX/SFO. Chicago settles
   on **Midway (KMDW)**, Houston on **Hobby (KHOU)**, Dallas on **DFW**,
   DC on **DCA** — all confirmed from `settlement_sources[].url`
   (`...site=LOT&product=CLI&issuedby=MDW` etc.), which is authoritative.

2. **ACIS client** (data.rcc-acis.org) — daily station max temps
   (settlement ground truth; matches the NWS CLI values) and hourly
   station temps (`{"vX": 23}`). Empirical notes:
   - hourly arrays are LOCAL STANDARD TIME, index i = the obs taken at
     ~i:51 LST (verified against IEM ASOS for KNYC winter+summer days);
   - the endpoint intermittently returns `{"error": "no data available"}`
     for ranges it can serve — retry; month-sized chunks are reliable;
   - "T"/"M" appear as strings in values; handled.

3. **Open-Meteo clients**:
   - previous-runs API: `temperature_2m_previous_dayN` = the forecast for
     a valid hour from the model run ~N days earlier. **Empirically it
     does NOT cap at past_days=92**: past_days up to 365 returned fully
     non-null hourly data (tested 92/150/183/270/365 on 2026-08-11).
     This lets bias/σ be fit on data strictly BEFORE the backtest window.
   - ensemble API: daily `temperature_2m_max` per member is the variable
     that maps cleanly to the settlement quantity (daily high, local
     calendar day). gfs025 = 31 members, ecmwf_ifs025 = 51 members.
     (Hourly per-member `temperature_2m` also works but the daily
     aggregation in local time is exactly what Kalshi settles on.)

4. **Bias correction** — per-city grid→station offset + residual sd fit
   on previous-runs forecasts vs ACIS station truth (fit window disjoint
   from the backtest window). Seasonal (month-harmonic) offset supported
   when n is large enough to estimate it.

5. **NWS CLI parser** — parses the Climatological Report (Daily) product
   (api.weather.gov/products), including the ~4:30 PM local intermediate
   issuance that carries max-so-far + the clock time it occurred.

All caches live under data/weather/ (gitignored via the root-anchored
`/data/` entry in .gitignore).
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

DATA_DIR = os.path.join("data", "weather")
ACIS_STN_URL = "https://data.rcc-acis.org/StnData"
ACIS_MULTI_URL = "https://data.rcc-acis.org/MultiStnData"
ACIS_META_URL = "https://data.rcc-acis.org/StnMeta"
PREV_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
NWS_API = "https://api.weather.gov"
USER_AGENT = "c-g-trading-research/0.1 (weather ground-truth; github repo bot)"

DEFAULT_ENSEMBLE_MODELS = ("gfs025", "ecmwf_ifs025")

#: Named deterministic models available on the previous-runs API (verified
#: 2026-08-12: `models=a,b` suffixes the previous-day variables per model,
#: fully non-null). Used by the day-ahead sensitivity grid; models=()
#: keeps Open-Meteo's `best_match` blend.
PREV_RUNS_MODELS = ("gfs_seamless", "ecmwf_ifs025")


# ---------------------------------------------------------------------------
# 1. Station map
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Station:
    """Settlement station for one KXHIGH* series.

    cli_office/cli_loc are the WFO + location codes from the settlement
    source URL (forecast.weather.gov/product.php?site={office}&product=CLI
    &issuedby={loc}); `sid` is the ACIS/ICAO station id ("K"+loc except
    NYC->KNYC which ACIS resolves to Central Park).
    """
    series_ticker: str
    city: str
    cli_office: str
    cli_loc: str
    sid: str
    lat: float
    lon: float
    tz: str


def _st(series, city, office, loc, sid, lat, lon, tz) -> Station:
    return Station(series, city, office, loc, sid, lat, lon, tz)


#: Canonical map, generated from data/kalshi.db series.settlement_sources
#: (2026-08-11 pull) + ACIS StnMeta coordinates. station_map_from_db()
#: re-derives this from a live DB and must agree (tested).
STATIONS: dict[str, Station] = {s.series_ticker: s for s in [
    _st("KXHIGHNY",    "NYC",          "OKX", "NYC", "KNYC", 40.77898, -73.96925, "America/New_York"),
    _st("KXHIGHCHI",   "Chicago",      "LOT", "MDW", "KMDW", 41.78412, -87.75514, "America/Chicago"),
    _st("KXHIGHAUS",   "Austin",       "EWX", "AUS", "KAUS", 30.18311, -97.67989, "America/Chicago"),
    _st("KXHIGHDEN",   "Denver",       "BOU", "DEN", "KDEN", 39.84657, -104.65623, "America/Denver"),
    _st("KXHIGHLAX",   "Los Angeles",  "LOX", "LAX", "KLAX", 33.93816, -118.38660, "America/Los_Angeles"),
    _st("KXHIGHMIA",   "Miami",        "MFL", "MIA", "KMIA", 25.78805, -80.31694, "America/New_York"),
    _st("KXHIGHPHIL",  "Philadelphia", "PHI", "PHL", "KPHL", 39.87326, -75.22681, "America/New_York"),
    _st("KXHIGHTATL",  "Atlanta",      "FFC", "ATL", "KATL", 33.62972, -84.44224, "America/New_York"),
    _st("KXHIGHTBOS",  "Boston",       "BOX", "BOS", "KBOS", 42.36057, -71.00975, "America/New_York"),
    _st("KXHIGHTDAL",  "Dallas",       "FWD", "DFW", "KDFW", 32.89744, -97.02196, "America/Chicago"),
    _st("KXHIGHTDC",   "Washington DC","LWX", "DCA", "KDCA", 38.84721, -77.03454, "America/New_York"),
    _st("KXHIGHTHOU",  "Houston",      "HGX", "HOU", "KHOU", 29.64586, -95.28212, "America/Chicago"),
    _st("KXHIGHTLV",   "Las Vegas",    "VEF", "LAS", "KLAS", 36.07190, -115.16343, "America/Los_Angeles"),
    _st("KXHIGHTMIN",  "Minneapolis",  "MPX", "MSP", "KMSP", 44.88523, -93.23133, "America/Chicago"),
    _st("KXHIGHTNOLA", "New Orleans",  "LIX", "MSY", "KMSY", 29.99755, -90.27772, "America/Chicago"),
    _st("KXHIGHTOKC",  "Oklahoma City","OUN", "OKC", "KOKC", 35.38843, -97.60035, "America/Chicago"),
    _st("KXHIGHTPHX",  "Phoenix",      "PSR", "PHX", "KPHX", 33.42780, -112.00365, "America/Phoenix"),
    _st("KXHIGHTSATX", "San Antonio",  "EWX", "SAT", "KSAT", 29.54429, -98.48395, "America/Chicago"),
    _st("KXHIGHTSEA",  "Seattle",      "SEW", "SEA", "KSEA", 47.44467, -122.31442, "America/Los_Angeles"),
    _st("KXHIGHTSFO",  "San Francisco","MTR", "SFO", "KSFO", 37.61962, -122.36562, "America/Los_Angeles"),
]}

#: Sanity keywords: rules_primary must name this site (Chicago = Midway
#: not O'Hare, Houston = Hobby via issuedby, etc.). Series whose rules
#: only carry the city name are checked against the city.
_RULES_KEYWORD = {
    "KXHIGHNY": "central park",
    "KXHIGHCHI": "midway",
    "KXHIGHAUS": "bergstrom",
    "KXHIGHLAX": "los angeles airport",
    "KXHIGHMIA": "miami international",
    "KXHIGHPHIL": "philadelphia international",
}


def station_map_from_db(db_path: str = "data/kalshi.db") -> dict[str, Station]:
    """Extract the settlement-station map from the Kalshi DB.

    Primary source: series.settlement_sources[].url (`site=` + `issuedby=`
    query params — exact and machine-readable). Cross-checked against a
    sample market's rules_primary text where the rules name a specific
    site. Coordinates/tz come from the canonical STATIONS table (they are
    physical constants; a new series would need an ACIS StnMeta lookup).
    """
    import sqlite3

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    out: dict[str, Station] = {}
    try:
        for r in conn.execute(
            "SELECT series_ticker, raw_json FROM series"
            " WHERE series_ticker LIKE 'KXHIGH%'"
        ):
            raw = json.loads(r["raw_json"] or "{}")
            for src in raw.get("settlement_sources", []) or []:
                url = src.get("url") or ""
                q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                if q.get("product", [""])[0] != "CLI":
                    continue
                office = q.get("site", [""])[0]
                loc = q.get("issuedby", [""])[0]
                if not office or not loc:
                    continue
                canon = STATIONS.get(r["series_ticker"])
                if canon is None:
                    continue  # new series: needs a coordinate lookup
                # DB is authoritative for office/loc; canon for coords.
                out[r["series_ticker"]] = Station(
                    series_ticker=r["series_ticker"],
                    city=canon.city,
                    cli_office=office,
                    cli_loc=loc,
                    sid="KNYC" if loc == "NYC" else f"K{loc}",
                    lat=canon.lat,
                    lon=canon.lon,
                    tz=canon.tz,
                )
                break
        # rules_primary cross-check where the rules name a site.
        for series, kw in _RULES_KEYWORD.items():
            if series not in out:
                continue
            row = conn.execute(
                "SELECT rules_primary FROM markets WHERE series_ticker = ?"
                " AND rules_primary IS NOT NULL LIMIT 1",
                (series,),
            ).fetchone()
            if row and kw not in (row["rules_primary"] or "").lower():
                raise ValueError(
                    f"{series}: rules_primary does not mention {kw!r} - "
                    "station map may be stale, re-verify settlement_sources"
                )
    finally:
        conn.close()
    return out


# ---------------------------------------------------------------------------
# HTTP helpers (retry; the proxy and ACIS both flake intermittently)
# ---------------------------------------------------------------------------

def _http_json(
    url: str,
    post_body: dict | None = None,
    tries: int = 5,
    timeout: int = 90,
    retry_if=None,
) -> dict:
    last: Exception | None = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(post_body).encode() if post_body is not None else None,
                headers={
                    "User-Agent": USER_AGENT,
                    **({"Content-Type": "application/json"} if post_body is not None else {}),
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                out = json.load(resp)
            if retry_if is not None and retry_if(out) and attempt < tries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            return out
        except Exception as e:  # noqa: BLE001 - network retry loop
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"HTTP failed after {tries} tries: {url}") from last


def _read_cache(path: str) -> dict:
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    return {}


def _write_cache(path: str, obj: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh)
    os.replace(tmp, path)


def _dates_between(sdate: str, edate: str) -> list[str]:
    d0 = dt.date.fromisoformat(sdate)
    d1 = dt.date.fromisoformat(edate)
    return [(d0 + dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


# ---------------------------------------------------------------------------
# 2. ACIS client (daily maxt = settlement truth; hourly temps)
# ---------------------------------------------------------------------------

def _acis_value(v):
    """ACIS numeric parse: 'M' (missing) -> None, 'T' (trace) -> 0.0."""
    if v is None:
        return None
    s = str(v).strip()
    if s in ("M", ""):
        return None
    if s == "T":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return None


def acis_daily_maxt(
    sid: str,
    sdate: str,
    edate: str,
    cache_dir: str = DATA_DIR,
    refresh: bool = False,
) -> dict[str, float | None]:
    """Daily station max temperature (°F) date->value, locally cached.

    These are the same values the NWS CLI reports (and Kalshi settles on).
    """
    path = os.path.join(cache_dir, "acis", f"{sid}_daily_maxt.json")
    cache = _read_cache(path)
    want = _dates_between(sdate, edate)
    missing = [d for d in want if d not in cache] if not refresh else want
    if missing:
        j = _http_json(
            ACIS_STN_URL,
            {"sid": sid, "sdate": missing[0], "edate": missing[-1], "elems": ["maxt"]},
            retry_if=lambda o: "data" not in o,
        )
        for date, val in j.get("data", []):
            cache[date] = _acis_value(val)
        _write_cache(path, cache)
    return {d: cache.get(d) for d in want}


def acis_hourly_temps(
    sid: str,
    sdate: str,
    edate: str,
    cache_dir: str = DATA_DIR,
) -> dict[str, list[float | None]]:
    """Hourly station temps date -> [24 obs], locally cached.

    Convention (verified vs IEM ASOS): values are in LOCAL STANDARD TIME;
    index i is the routine obs taken ~i:51 LST. Fetched month-by-month —
    ACIS intermittently 'no data available's short ranges it can serve.
    """
    path = os.path.join(cache_dir, "acis", f"{sid}_hourly.json")
    cache = _read_cache(path)
    want = _dates_between(sdate, edate)
    missing = [d for d in want if d not in cache]
    if missing:
        # fetch whole months covering the missing range
        first = dt.date.fromisoformat(missing[0]).replace(day=1)
        last = dt.date.fromisoformat(missing[-1])
        month = first
        while month <= last:
            nxt = (month.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
            m_end = min(nxt - dt.timedelta(days=1), dt.date.today())
            j = _http_json(
                ACIS_STN_URL,
                {
                    "sid": sid,
                    "sdate": month.isoformat(),
                    "edate": m_end.isoformat(),
                    "elems": [{"vX": 23}],
                },
                retry_if=lambda o: "data" not in o,
                tries=6,
            )
            for row in j.get("data", []):
                date, vals = row[0], row[1]
                cache[date] = [_acis_value(v) for v in vals]
            month = nxt
        _write_cache(path, cache)
    return {d: cache.get(d) for d in want if cache.get(d) is not None}


# ---------------------------------------------------------------------------
# 3a. Open-Meteo previous-runs client (point-in-time forecasts)
# ---------------------------------------------------------------------------

def previous_runs_daily_max(
    station: Station,
    leads: tuple[int, ...] = (1, 2),
    past_days: int = 365,
    cache_dir: str = DATA_DIR,
    refresh: bool = False,
    models: tuple[str, ...] = (),
) -> dict[int, dict[str, float]]:
    """Point-in-time daily-max forecasts {lead: {date: forecast °F}}.

    For target date D and lead N, the value is the max over D's local
    hours of `temperature_2m_previous_dayN` — i.e. the daily high
    predicted by the model run initialized ~N days before each valid
    hour (runs from calendar day D-N). Grouping is by the API's local
    calendar day (timezone=auto), matching CLI's midnight-to-midnight.

    Empirical: past_days up to 365 verified fully non-null (no 92 cap).

    models=() uses Open-Meteo's default `best_match` blend (one series).
    Pass a subset of PREV_RUNS_MODELS to get named deterministic models
    instead; the value returned is then the **mean of the per-model daily
    maxima** (multi-model consensus when more than one is named). One
    network pull covers the whole PREV_RUNS_MODELS set and is cached
    separately from the best_match cache, so subsets are free.
    """
    if models:
        unknown = [m for m in models if m not in PREV_RUNS_MODELS]
        if unknown:
            raise ValueError(f"unknown previous-runs models: {unknown}")
        suffixes = [f"_{m}" for m in models]
        fetch_models = PREV_RUNS_MODELS
        tag = "_models"
    else:
        suffixes = [""]
        fetch_models = ()
        tag = ""
    path = os.path.join(cache_dir, "openmeteo", f"prev_runs_{station.sid}{tag}.json")
    cache = _read_cache(path)
    key = f"past{past_days}_leads{'_'.join(map(str, leads))}"
    if fetch_models:
        key += f"_models{'_'.join(fetch_models)}"
    if refresh or cache.get("key") != key:
        hourly_vars = ",".join(f"temperature_2m_previous_day{n}" for n in leads)
        url = (
            f"{PREV_RUNS_URL}?latitude={station.lat}&longitude={station.lon}"
            f"&hourly={hourly_vars}&temperature_unit=fahrenheit"
            f"&timezone=auto&past_days={past_days}&forecast_days=1"
        )
        if fetch_models:
            url += f"&models={','.join(fetch_models)}"
        j = _http_json(url, tries=6, retry_if=lambda o: bool(o.get("error")))
        if j.get("error"):
            raise RuntimeError(f"open-meteo error for {station.sid}: {j.get('reason')}")
        cache = {"key": key, "timezone": j.get("timezone"), "hourly": j["hourly"]}
        _write_cache(path, cache)
    hourly = cache["hourly"]
    times = hourly["time"]
    out: dict[int, dict[str, float]] = {}
    for n in leads:
        per_model: list[dict[str, float]] = []
        for sfx in suffixes:
            vals = hourly.get(f"temperature_2m_previous_day{n}{sfx}")
            if vals is None:
                continue
            by_day: dict[str, float] = {}
            n_hours: dict[str, int] = {}
            for t, v in zip(times, vals):
                if v is None:
                    continue
                day = t[:10]
                by_day[day] = v if day not in by_day else max(by_day[day], v)
                n_hours[day] = n_hours.get(day, 0) + 1
            # drop partial days (first/last day of the window)
            per_model.append({d: v for d, v in by_day.items() if n_hours[d] >= 20})
        if not per_model:
            continue
        # consensus = mean of the per-model daily maxima, on days every
        # requested model has a value (no silent single-model fallback).
        common = set(per_model[0])
        for m in per_model[1:]:
            common &= set(m)
        out[n] = {d: sum(m[d] for m in per_model) / len(per_model) for d in common}
    return out


# ---------------------------------------------------------------------------
# 3b. Open-Meteo ensemble client (live distribution)
# ---------------------------------------------------------------------------

def ensemble_daily_max(
    station: Station,
    models: tuple[str, ...] = DEFAULT_ENSEMBLE_MODELS,
    forecast_days: int = 3,
) -> dict[str, list[float]]:
    """Current ensemble daily-max forecast {date: [member values °F]}.

    Uses daily=temperature_2m_max — the variable that maps 1:1 to the
    settlement quantity (max temp over the local calendar day). Members
    across all requested models are pooled (gfs025: 31, ecmwf_ifs025: 51).
    """
    url = (
        f"{ENSEMBLE_URL}?latitude={station.lat}&longitude={station.lon}"
        f"&daily=temperature_2m_max&models={','.join(models)}"
        f"&temperature_unit=fahrenheit&timezone=auto&forecast_days={forecast_days}"
    )
    j = _http_json(url, tries=6, retry_if=lambda o: bool(o.get("error")))
    objs = j if isinstance(j, list) else [j]
    out: dict[str, list[float]] = {}
    for obj in objs:
        daily = obj.get("daily", {})
        times = daily.get("time", [])
        for k, series in daily.items():
            if not k.startswith("temperature_2m_max"):
                continue
            for t, v in zip(times, series):
                if v is not None:
                    out.setdefault(t, []).append(v)
    return out


# ---------------------------------------------------------------------------
# 4. Bias correction (grid -> station) + probability integration
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class BiasFit:
    """Per-city forecast->station correction: station ≈ forecast + offset.

    offset/sd/mae are fit on (station - forecast) residuals. If seasonal
    coefficients are present, offset(t) adds a first-order annual
    harmonic (only fit when n >= min_seasonal_n and it beats the flat
    fit's residual sd by >2%).
    """
    offset: float
    sd: float
    mae: float
    n: int
    seasonal: tuple[float, float] | None = None  # (a_cos, a_sin) on day-of-year

    def offset_for(self, date: str | None = None) -> float:
        if self.seasonal is None or date is None:
            return self.offset
        doy = dt.date.fromisoformat(date).timetuple().tm_yday
        ang = 2 * math.pi * doy / 365.25
        return self.offset + self.seasonal[0] * math.cos(ang) + self.seasonal[1] * math.sin(ang)

    def correct(self, forecast_f: float, date: str | None = None) -> float:
        return forecast_f + self.offset_for(date)


def fit_bias(
    forecast: dict[str, float],
    station: dict[str, float | None],
    min_seasonal_n: int = 150,
) -> BiasFit:
    """Fit station-minus-forecast offset (+ optional annual harmonic) + sd."""
    pairs = [
        (d, station[d] - forecast[d])
        for d in forecast
        if station.get(d) is not None
    ]
    if len(pairs) < 5:
        raise ValueError(f"not enough overlap to fit bias (n={len(pairs)})")
    resid = [r for _, r in pairs]
    n = len(resid)
    mean = sum(resid) / n
    var = sum((r - mean) ** 2 for r in resid) / max(1, n - 1)
    mae = sum(abs(r) for r in resid) / n
    fit = BiasFit(offset=mean, sd=math.sqrt(var), mae=mae, n=n)
    if n < min_seasonal_n:
        return fit
    # least-squares annual harmonic on the residuals
    rows = []
    for d, r in pairs:
        doy = dt.date.fromisoformat(d).timetuple().tm_yday
        ang = 2 * math.pi * doy / 365.25
        rows.append((math.cos(ang), math.sin(ang), r - mean))
    scc = sum(c * c for c, _, _ in rows)
    sss = sum(s * s for _, s, _ in rows)
    scs = sum(c * s for c, s, _ in rows)
    scr = sum(c * r for c, _, r in rows)
    ssr = sum(s * r for _, s, r in rows)
    det = scc * sss - scs * scs
    if abs(det) < 1e-9:
        return fit
    a_cos = (scr * sss - ssr * scs) / det
    a_sin = (ssr * scc - scr * scs) / det
    resid2 = [r - a_cos * c - a_sin * s for c, s, r in rows]
    var2 = sum(r * r for r in resid2) / max(1, n - 3)
    sd2 = math.sqrt(var2)
    if sd2 < fit.sd * 0.98:  # only keep seasonality if it actually helps
        return BiasFit(
            offset=mean, sd=sd2, mae=mae, n=n, seasonal=(a_cos, a_sin)
        )
    return fit


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def strike_prob(
    mu: float,
    sd: float,
    strike_type: str,
    floor_strike: float | None,
    cap_strike: float | None,
) -> float:
    """P(settled integer high satisfies the strike) under N(mu, sd).

    The CLI reports an integer °F; strikes are integer-parameterized:
      greater T=k : high >  k  (i.e. >= k+1)  -> 1 - Φ((k+0.5-mu)/sd)
      less    T=k : high <  k  (i.e. <= k-1)  -> Φ((k-0.5-mu)/sd)
      between a-b : a <= high <= b            -> Φ((b+0.5-mu)/sd) - Φ((a-0.5-mu)/sd)
    (continuity correction at half-integers).
    """
    if sd <= 0:
        raise ValueError("sd must be positive")
    if strike_type == "greater":
        if floor_strike is None:
            raise ValueError("greater strike needs floor_strike")
        return 1.0 - _phi((floor_strike + 0.5 - mu) / sd)
    if strike_type == "less":
        if cap_strike is None:
            raise ValueError("less strike needs cap_strike")
        return _phi((cap_strike - 0.5 - mu) / sd)
    if strike_type == "between":
        if floor_strike is None or cap_strike is None:
            raise ValueError("between strike needs floor and cap")
        return _phi((cap_strike + 0.5 - mu) / sd) - _phi((floor_strike - 0.5 - mu) / sd)
    raise ValueError(f"unknown strike_type: {strike_type}")


def strike_prob_ensemble(
    members_f: list[float],
    kernel_sd: float,
    strike_type: str,
    floor_strike: float | None,
    cap_strike: float | None,
    bias: BiasFit | None = None,
    date: str | None = None,
) -> float:
    """Strike probability from a bias-corrected ensemble.

    Mixture of normals: each member, bias-corrected, contributes a
    N(member+offset, kernel_sd) component; kernel_sd should be the
    residual sd of the corrected forecast vs the station (it absorbs
    both grid->station scatter and under-dispersion of the ensemble).
    """
    if not members_f:
        raise ValueError("no ensemble members")
    ps = [
        strike_prob(
            bias.correct(m, date) if bias is not None else m,
            kernel_sd, strike_type, floor_strike, cap_strike,
        )
        for m in members_f
    ]
    return sum(ps) / len(ps)


# ---------------------------------------------------------------------------
# 5. NWS CLI product parser (settlement truth + 4 PM intermediate report)
# ---------------------------------------------------------------------------

_MONTHS = {m.upper(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}

_RE_SUMMARY_DATE = re.compile(
    r"CLIMATE SUMMARY FOR\s+([A-Z]+)\s+(\d{1,2})\s+(\d{4})"
)
_RE_VALID = re.compile(
    r"VALID (?:TODAY|YESTERDAY)? ?AS OF (\d{3,4}) ([AP]M) LOCAL TIME"
)
_RE_MAX = re.compile(
    r"^\s*MAXIMUM\s+(-?\d+|MM)R?(?:\s+(\d{1,2}:?\d{2})\s+([AP]M))?",
    re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class CliReport:
    """One parsed Climatological Report (Daily).

    is_final: True when the report covers the full day (midnight issuance
    or a 'VALID ... AS OF MIDNIGHT' / no as-of clause final); False for
    the afternoon intermediate ('VALID TODAY AS OF 0400 PM LOCAL TIME'),
    which is the tradeable max-so-far signal.
    """
    summary_date: str            # YYYY-MM-DD the report is FOR
    max_temp: int | None         # max so far (intermediate) or final max
    max_time_local: str | None   # "13:26" clock time the max occurred
    as_of_local: str | None      # "16:00" for the 4 PM intermediate
    is_final: bool


def _clock(hhmm: str, ampm: str) -> str:
    hhmm = hhmm.replace(":", "").zfill(4)
    h, m = int(hhmm[:-2]), int(hhmm[-2:])
    if ampm == "PM" and h != 12:
        h += 12
    if ampm == "AM" and h == 12:
        h = 0
    return f"{h:02d}:{m:02d}"


def parse_cli_product(text: str) -> CliReport:
    """Parse a CLI productText (see tests for a live-captured sample)."""
    md = _RE_SUMMARY_DATE.search(text.upper())
    if not md:
        raise ValueError("no CLIMATE SUMMARY FOR <date> line found")
    mon = _MONTHS.get(md.group(1))
    if mon is None:
        raise ValueError(f"bad month {md.group(1)!r}")
    summary_date = f"{int(md.group(3)):04d}-{mon:02d}-{int(md.group(2)):02d}"

    mv = _RE_VALID.search(text.upper())
    as_of = _clock(mv.group(1), mv.group(2)) if mv else None
    # Intermediate reports carry an afternoon as-of; final reports either
    # have no as-of clause or say midnight.
    is_final = as_of is None or as_of == "00:00"

    mm = _RE_MAX.search(text.upper())
    max_temp = None
    max_time = None
    if mm:
        if mm.group(1) != "MM":
            max_temp = int(mm.group(1))
        if mm.group(2) and mm.group(3):
            max_time = _clock(mm.group(2), mm.group(3))
    return CliReport(
        summary_date=summary_date,
        max_temp=max_temp,
        max_time_local=max_time,
        as_of_local=as_of,
        is_final=is_final,
    )


def fetch_cli_products(cli_loc: str, limit: int = 14) -> list[dict]:
    """List recent CLI products for a location (rolling ~1 week window)."""
    j = _http_json(f"{NWS_API}/products/types/CLI/locations/{cli_loc}")
    return (j.get("@graph") or [])[:limit]


def fetch_cli_product_text(product_id: str) -> str:
    j = _http_json(f"{NWS_API}/products/{product_id}")
    return j.get("productText", "")


# ---------------------------------------------------------------------------
# 6. "Is the daily max already set by 4 PM?" (measured, not assumed)
# ---------------------------------------------------------------------------

def _lst_cutoff_index(date: str, tz: str, wall_hour: int) -> int:
    """Highest hourly-array index whose obs (~i:51 LST) is before
    `wall_hour`:00 local WALL time on `date` (DST-aware).

    In DST, wall = LST+1, so 'by 4 PM wall' = obs through 14:51 LST
    (index 14); in standard time index 15. Index i covers obs ~i:51 LST.
    """
    d = dt.date.fromisoformat(date)
    is_dst = ZoneInfo(tz).utcoffset(
        dt.datetime(d.year, d.month, d.day, 12, tzinfo=ZoneInfo(tz))
    ) != ZoneInfo(tz).utcoffset(dt.datetime(d.year, 1, 15, tzinfo=ZoneInfo(tz)))
    # note: for Phoenix (no DST) is_dst is always False.
    lst_hour = wall_hour - 1 if is_dst else wall_hour
    return lst_hour - 1  # obs at (lst_hour-1):51 LST is the last before H:00


def running_max_by_wall_hour(
    hourly: list[float | None],
    date: str,
    tz: str,
    wall_hour: int = 16,
) -> float | None:
    """Max of the day's hourly obs up to `wall_hour`:00 local wall time."""
    idx = _lst_cutoff_index(date, tz, wall_hour)
    vals = [v for v in hourly[: idx + 1] if v is not None]
    return max(vals) if vals else None


def max_by_hour_stats(
    hourly_by_date: dict[str, list[float | None]],
    daily_maxt: dict[str, float | None],
    tz: str,
    wall_hours: tuple[int, ...] = (16, 17, 18),
    min_obs: int = 20,
) -> dict:
    """Fraction of days whose daily max is already set by H:00 local.

    'Set' is measured two ways per day:
      exact:  running hourly max by H >= the day's settled maxt
      within1: running hourly max by H >= maxt - 1°F
    (hourly :51 obs undersample the continuous max by ~0-1°F, so
    `within1` brackets the truth from above and `exact` from below).
    Also split by season (Nov-Mar vs Apr-Oct).
    """
    out: dict = {}
    for wall_hour in wall_hours:
        rows = {"all": [0, 0, 0], "warm": [0, 0, 0], "cool": [0, 0, 0]}
        # counts: [n, exact_hits, within1_hits]
        for date, hourly in hourly_by_date.items():
            mt = daily_maxt.get(date)
            if mt is None or hourly is None:
                continue
            if sum(1 for v in hourly if v is not None) < min_obs:
                continue
            rm = running_max_by_wall_hour(hourly, date, tz, wall_hour)
            if rm is None:
                continue
            season = "cool" if dt.date.fromisoformat(date).month in (11, 12, 1, 2, 3) else "warm"
            for bucket in ("all", season):
                rows[bucket][0] += 1
                rows[bucket][1] += 1 if rm >= mt else 0
                rows[bucket][2] += 1 if rm >= mt - 1 else 0
        out[wall_hour] = {
            b: {
                "n": n,
                "frac_exact": (e / n) if n else None,
                "frac_within1": (w / n) if n else None,
            }
            for b, (n, e, w) in rows.items()
        }
    return out


def rise_after_cutoff_pmf(
    hourly_by_date: dict[str, list[float | None]],
    daily_maxt: dict[str, float | None],
    tz: str,
    wall_hour: int = 16,
    max_rise: int = 12,
    laplace: float = 0.5,
) -> dict[int, float]:
    """Empirical PMF of (final daily max - running max by H:00 wall).

    This is the intraday variant's engine: at 4 PM the remaining
    uncertainty is exactly this distribution. Laplace-smoothed so tail
    rises keep nonzero mass. Rise is clamped to >= 0 (same sensor).
    """
    counts = [0.0] * (max_rise + 1)
    n = 0
    for date, hourly in hourly_by_date.items():
        mt = daily_maxt.get(date)
        if mt is None or hourly is None:
            continue
        rm = running_max_by_wall_hour(hourly, date, tz, wall_hour)
        if rm is None:
            continue
        rise = max(0, min(max_rise, int(round(mt - rm))))
        counts[rise] += 1
        n += 1
    if n == 0:
        raise ValueError("no usable days for rise PMF")
    total = n + laplace * (max_rise + 1)
    return {r: (counts[r] + laplace) / total for r in range(max_rise + 1)}


def strike_prob_from_rise_pmf(
    max_so_far: float,
    rise_pmf: dict[int, float],
    strike_type: str,
    floor_strike: float | None,
    cap_strike: float | None,
) -> float:
    """P(strike) at 4 PM given max-so-far and the rise PMF.

    final = round(max_so_far) + rise, rise >= 0 integer.
    """
    s = int(round(max_so_far))
    p_final: dict[int, float] = {}
    for r, p in rise_pmf.items():
        p_final[s + r] = p_final.get(s + r, 0.0) + p
    def in_strike(v: int) -> bool:
        if strike_type == "greater":
            return v > floor_strike
        if strike_type == "less":
            return v < cap_strike
        if strike_type == "between":
            return floor_strike <= v <= cap_strike
        raise ValueError(f"unknown strike_type: {strike_type}")
    return sum(p for v, p in p_final.items() if in_strike(v))

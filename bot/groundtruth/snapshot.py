"""Snapshot today's live weather-trading data (re-runnable, cron-able).

Captures, for all 20 KXHIGH* cities, into
data/weather/snapshots/<UTC-timestamp>/:

- cli/{LOC}.json            recent NWS CLI product list + parsed texts
                            (max-so-far + time for intermediates)
- ensemble/{SERIES}.json    Open-Meteo ensemble daily-max members
                            (gfs025 + ecmwf_ifs025), 3 forecast days
- kalshi/{SERIES}.json      live Kalshi markets for the series (quotes,
                            volume, OI) + order books for open markets

Forward value: no historical order books exist anywhere (SYNTHESIS risk
#3) and api.weather.gov keeps only ~1 week of CLI products — every day
without a snapshot is data lost. Run:  python -m bot.groundtruth.snapshot
(add --skip-books to skip per-market order books; ~240 extra requests).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

from bot.groundtruth import weather as wx

KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"


def _get(url: str, tries: int = 4, timeout: int = 45):
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": wx.USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.0 + attempt)
    raise RuntimeError(f"GET failed: {url}") from last


def snapshot_cli(out_dir: str) -> None:
    d = os.path.join(out_dir, "cli")
    os.makedirs(d, exist_ok=True)
    for series, st in sorted(wx.STATIONS.items()):
        try:
            products = wx.fetch_cli_products(st.cli_loc)
            rows = []
            for p in products:
                text = wx.fetch_cli_product_text(p["id"])
                parsed = None
                try:
                    r = wx.parse_cli_product(text)
                    parsed = {
                        "summary_date": r.summary_date,
                        "max_temp": r.max_temp,
                        "max_time_local": r.max_time_local,
                        "as_of_local": r.as_of_local,
                        "is_final": r.is_final,
                    }
                except ValueError:
                    pass
                rows.append(
                    {"id": p["id"], "issuanceTime": p.get("issuanceTime"),
                     "parsed": parsed, "productText": text}
                )
                time.sleep(0.2)
            with open(os.path.join(d, f"{st.cli_loc}.json"), "w") as fh:
                json.dump({"series": series, "station": st.sid, "products": rows}, fh)
            print(f"cli {st.cli_loc}: {len(rows)} products", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"cli {st.cli_loc}: FAILED {e}", flush=True)


def snapshot_ensemble(out_dir: str) -> None:
    d = os.path.join(out_dir, "ensemble")
    os.makedirs(d, exist_ok=True)
    for series, st in sorted(wx.STATIONS.items()):
        try:
            members = wx.ensemble_daily_max(st, forecast_days=3)
            with open(os.path.join(d, f"{series}.json"), "w") as fh:
                json.dump(
                    {"station": st.sid, "lat": st.lat, "lon": st.lon,
                     "models": list(wx.DEFAULT_ENSEMBLE_MODELS),
                     "daily_max_members": members}, fh,
                )
            n = {k: len(v) for k, v in members.items()}
            print(f"ensemble {series}: {n}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"ensemble {series}: FAILED {e}", flush=True)
        time.sleep(0.3)


def snapshot_kalshi(out_dir: str, with_books: bool = True) -> None:
    d = os.path.join(out_dir, "kalshi")
    os.makedirs(d, exist_ok=True)
    for series in sorted(wx.STATIONS):
        try:
            j = _get(f"{KALSHI_API}/markets?series_ticker={series}&status=open&limit=100")
            markets = j.get("markets", [])
            books = {}
            if with_books:
                for m in markets:
                    tk = m["ticker"]
                    try:
                        books[tk] = _get(
                            f"{KALSHI_API}/markets/{urllib.parse.quote(tk)}/orderbook"
                        ).get("orderbook")
                    except Exception as e:  # noqa: BLE001
                        books[tk] = {"error": str(e)}
                    time.sleep(0.4)  # Kalshi 429s sustained polling
            with open(os.path.join(d, f"{series}.json"), "w") as fh:
                json.dump({"markets": markets, "orderbooks": books}, fh)
            print(f"kalshi {series}: {len(markets)} open markets, "
                  f"{len(books)} books", flush=True)
            time.sleep(0.5)
        except Exception as e:  # noqa: BLE001
            print(f"kalshi {series}: FAILED {e}", flush=True)


def main(argv: list[str]) -> int:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    out_dir = os.path.join(wx.DATA_DIR, "snapshots", stamp)
    os.makedirs(out_dir, exist_ok=True)
    print(f"snapshot -> {out_dir}")
    snapshot_cli(out_dir)
    snapshot_ensemble(out_dir)
    snapshot_kalshi(out_dir, with_books="--skip-books" not in argv)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

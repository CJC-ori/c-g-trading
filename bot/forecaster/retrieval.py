"""Point-in-time retrieval — GDELT + Wikipedia revisions + URL fetch, all
behind a retrieval ledger with two independent date gates.

Design source: research/point-in-time-retrieval.md (verified live probes).
The load-bearing rules implemented here:

1. GDELT's ``enddatetime`` bound LEAKS (up to ~24h, 0–28% of rows) and even
   its per-article ``seendate`` can be wrong by days (§1.3/§1.3b). Defence:
   * GATE 1 — hard client-side ``seendate < D`` filter (server bound is a
     recall hint only);
   * GATE 2 — fetch the article and re-verify the publisher's own
     ``datePublished``/``dateModified`` (schema.org JSON-LD / og:article),
     quarantining undated sources and anything published/modified >= D.
2. ``sort=datedesc`` pinned; ``hybridrel`` (relevance) reintroduces a
   present-day ranking signal and is banned (§5.3).
3. Every retrieval writes one immutable ledger row BEFORE content reaches a
   model; the audit checker runs on the ledger, not the code (§5.1–5.2).
4. Live search engines are banned outright (§5.3): ``BANNED_KINDS``.
5. Everything cached to disk keyed by ``(source_kind, query, D)`` so re-runs
   are cache-only and network-free (§5.4 item 6); GDELT sits behind a global
   token bucket (~6s spacing, exponential backoff on 429).
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

REPO = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO / "data" / "forecaster-cache" / "retrieval"

#: Live search engines / rankers — never allowed in a backtest retrieval path.
BANNED_KINDS = frozenset({
    "web_search", "google", "bing", "brave", "tavily", "exa", "serpapi",
    "perplexity", "llm_builtin_search", "asknews_live",
})

GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"
UA = {"User-Agent": "c-g-trading-research/0.1 (point-in-time backtest; polite)"}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_dt(s: str | None) -> datetime | None:
    """Lenient ISO/GDELT timestamp parse -> aware UTC datetime (or None)."""
    if not s:
        return None
    s = s.strip()
    m = re.match(r"^(\d{4})(\d{2})(\d{2})T?(\d{2})?(\d{2})?(\d{2})?Z?$", s)
    if m:
        g = [int(x) if x else 0 for x in m.groups()]
        try:
            return datetime(g[0], g[1], g[2], g[3], g[4], g[5], tzinfo=timezone.utc)
        except ValueError:
            return None
    try:
        s2 = s.replace("Z", "+00:00")
        d = datetime.fromisoformat(s2)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RetrievalRecord:
    """One immutable ledger row (point-in-time-retrieval.md §5.1)."""

    run_id: str
    question_key: str
    decision_time: str          # D, ISO — the point-in-time cutoff
    source_kind: str            # gdelt_doc | article_fetch | wikipedia_rev | votehub | fec | bls
    query: str                  # exact query / API params, verbatim
    request_url: str
    url: str
    source_date: str | None     # gdelt seendate | revision timestamp | filing date
    published_at: str | None    # schema.org datePublished
    modified_at: str | None     # schema.org dateModified
    content_sha256: str
    n_chars: int
    verdict: str                # PASS | QUARANTINE:<reasons> | FETCH_FAIL:<type>


class RetrievalLedger:
    """Append-only JSONL ledger. A row is written BEFORE its content may be
    used; the audit reads the file, so retrieval refactors can't disable it."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.records: list[RetrievalRecord] = []

    def append(self, rec: RetrievalRecord) -> RetrievalRecord:
        with self._lock:
            with open(self.path, "a") as fh:
                fh.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
            self.records.append(rec)
        return rec

    @staticmethod
    def load(path: Path | str) -> list[RetrievalRecord]:
        out = []
        with open(path) as fh:
            for line in fh:
                if line.strip():
                    out.append(RetrievalRecord(**json.loads(line)))
        return out


class LeakageError(RuntimeError):
    """A leak invalidates the run (SPEC §1)."""


def audit_ledger(
    records: Iterable[RetrievalRecord], hard_fail: bool = True
) -> list[tuple[str, RetrievalRecord]]:
    """The §5.2 blocking checker. PASS rows must satisfy every gate; rows the
    pipeline already quarantined are checked for *having been* quarantined
    correctly (their content must never have verdict PASS)."""
    violations: list[tuple[str, RetrievalRecord]] = []
    for r in records:
        D = parse_dt(r.decision_time)
        if r.source_kind in BANNED_KINDS:
            violations.append(("BANNED_SOURCE", r))
            continue
        if not r.verdict.startswith("PASS"):
            continue  # quarantined/failed rows never reached a model
        sd = parse_dt(r.source_date)
        pub = parse_dt(r.published_at)
        mod = parse_dt(r.modified_at)
        if sd is not None and D is not None and sd >= D:
            violations.append(("SOURCE_AFTER_D", r))
        if pub is not None and D is not None and pub >= D:
            violations.append(("PUBLISHED_AFTER_D", r))
        if mod is not None and D is not None and mod >= D:
            violations.append(("MODIFIED_AFTER_D", r))
        if r.source_kind == "article_fetch" and pub is None:
            violations.append(("UNDATED_SOURCE", r))
        if pub is not None and sd is not None and pub > sd + timedelta(hours=1):
            violations.append(("DATE_INCONSISTENT", r))
    if hard_fail and violations:
        raise LeakageError(
            f"{len(violations)} leak violations; run is void: "
            + ", ".join(sorted({v for v, _ in violations}))
        )
    return violations


# ---------------------------------------------------------------------------
# Disk cache + polite HTTP
# ---------------------------------------------------------------------------

class _DiskCache:
    def __init__(self, root: Path = CACHE_DIR):
        self.root = root

    def _path(self, kind: str, key: str) -> Path:
        h = hashlib.sha256(key.encode()).hexdigest()
        return self.root / kind / h[:2] / f"{h}.json"

    def get(self, kind: str, key: str) -> Any | None:
        p = self._path(kind, key)
        if p.exists():
            with open(p) as fh:
                return json.load(fh)
        return None

    def put(self, kind: str, key: str, value: Any) -> None:
        p = self._path(kind, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w") as fh:
            json.dump(value, fh, ensure_ascii=False)
        tmp.replace(p)


class _TokenBucket:
    """Global serialized spacing (GDELT: 429s are the normal case)."""

    def __init__(self, min_interval_s: float):
        self.min_interval_s = min_interval_s
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delta = self._last + self.min_interval_s - now
            if delta > 0:
                time.sleep(delta)
            self._last = time.monotonic()


_GDELT_BUCKET = _TokenBucket(6.0)
_FETCH_BUCKET = _TokenBucket(0.5)


# ---------------------------------------------------------------------------
# GDELT DOC 2.0
# ---------------------------------------------------------------------------

@dataclass
class GdeltArticle:
    url: str
    title: str
    seendate: str
    domain: str
    language: str = ""


class GdeltClient:
    """DOC 2.0 artlist with the mandatory mitigations of §1.3."""

    def __init__(self, cache: _DiskCache | None = None, max_attempts: int = 6):
        self.cache = cache or _DiskCache()
        self.max_attempts = max_attempts

    def artlist(
        self, query: str, asof: datetime, lookback_days: int = 21
    ) -> tuple[list[GdeltArticle], str, int]:
        """Date-gated article list. Returns (kept, request_url, n_dropped):
        ``kept`` already passed GATE 1 (seendate < asof, strict)."""
        start = asof - timedelta(days=lookback_days)
        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": "250",            # over-request: leaked rows steal slots
            "startdatetime": start.strftime("%Y%m%d%H%M%S"),
            "enddatetime": asof.strftime("%Y%m%d%H%M%S"),  # advisory, NOT trusted
            "sort": "datedesc",             # never hybridrel (present-day ranking)
        }
        cache_key = json.dumps([params["query"], params["startdatetime"],
                                params["enddatetime"]], sort_keys=True)
        cached = self.cache.get("gdelt", cache_key)
        if cached is None:
            cached = self._fetch(params)
            self.cache.put("gdelt", cache_key, cached)
        request_url = cached["request_url"]
        arts = cached.get("articles", [])
        kept, dropped = [], 0
        for a in arts:
            sd = parse_dt(a.get("seendate"))
            if sd is None or sd >= asof:      # GATE 1 — the actual guarantee
                dropped += 1
                continue
            kept.append(GdeltArticle(
                url=a.get("url", ""), title=a.get("title", ""),
                seendate=_iso(sd), domain=a.get("domain", ""),
                language=a.get("language", ""),
            ))
        return kept, request_url, dropped

    def _fetch(self, params: dict[str, str]) -> dict[str, Any]:
        backoff = 8.0
        last = "?"
        for _ in range(self.max_attempts):
            _GDELT_BUCKET.wait()
            try:
                r = requests.get(GDELT_DOC, params=params, headers=UA, timeout=60)
                last = f"http {r.status_code}"
                if r.status_code == 200:
                    try:
                        data = r.json()
                    except ValueError:
                        # rate-limit text with HTTP 200 happens; treat as 429
                        data = None
                    if data is not None:
                        return {"request_url": r.url,
                                "articles": data.get("articles", [])}
            except requests.RequestException as e:
                last = f"exc {type(e).__name__}"
            time.sleep(backoff)
            backoff = min(backoff * 1.7, 60)
        raise RuntimeError(f"GDELT artlist failed after retries ({last})")


# ---------------------------------------------------------------------------
# Article fetch + publisher date extraction (GATE 2)
# ---------------------------------------------------------------------------

_META_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:article:published_time|'
    r'og:article:published_time|article:modified_time|og:updated_time|'
    r'datePublished|dateModified)["\'][^>]*>', re.I)
_CONTENT_RE = re.compile(r'content=["\']([^"\']+)["\']', re.I)
_LDJSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S)
_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_HTML_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def extract_dates(html_text: str) -> tuple[str | None, str | None]:
    """(datePublished, dateModified) from schema.org JSON-LD then og/meta."""
    pub = mod = None

    def walk(obj: Any) -> None:
        nonlocal pub, mod
        if isinstance(obj, dict):
            if pub is None and isinstance(obj.get("datePublished"), str):
                pub = obj["datePublished"]
            if mod is None and isinstance(obj.get("dateModified"), str):
                mod = obj["dateModified"]
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    for m in _LDJSON_RE.finditer(html_text):
        try:
            walk(json.loads(m.group(1).strip()))
        except ValueError:
            continue
        if pub and mod:
            break
    if pub is None or mod is None:
        for m in _META_RE.finditer(html_text):
            tag = m.group(0)
            c = _CONTENT_RE.search(tag)
            if not c:
                continue
            low = tag.lower()
            if "published" in low and pub is None:
                pub = c.group(1)
            elif ("modified" in low or "updated" in low) and mod is None:
                mod = c.group(1)
    return pub, mod


def extract_text(html_text: str, max_chars: int = 2000) -> str:
    import html as _html

    body = _TAG_RE.sub(" ", html_text)
    # crude main-content heuristic: prefer <p> blocks when present
    paras = re.findall(r"<p[^>]*>(.*?)</p>", body, re.I | re.S)
    text = " ".join(paras) if paras else body
    text = _WS_RE.sub(" ", _HTML_RE.sub(" ", text)).strip()
    return _html.unescape(text)[:max_chars]


class ArticleFetcher:
    """Fetch a discovered URL now; GATE 2 verdicts per §1.5/§5.2."""

    def __init__(self, cache: _DiskCache | None = None):
        self.cache = cache or _DiskCache()

    def fetch(self, url: str, asof: datetime) -> dict[str, Any]:
        cache_key = url  # bodies are cached per-URL; the verdict re-derives per asof
        cached = self.cache.get("article", cache_key)
        if cached is None:
            _FETCH_BUCKET.wait()
            try:
                r = requests.get(url, headers=UA, timeout=30, allow_redirects=True)
                cached = {
                    "status": r.status_code,
                    "final_url": r.url,
                    "body": r.text[:400_000] if r.status_code == 200 else "",
                }
            except requests.RequestException as e:
                cached = {"status": 0, "final_url": url, "body": "",
                          "error": type(e).__name__}
            self.cache.put("article", cache_key, cached)
        out: dict[str, Any] = {
            "url": url, "final_url": cached.get("final_url", url),
            "status": cached.get("status", 0),
        }
        if cached.get("status") != 200 or not cached.get("body"):
            out.update(verdict=f"FETCH_FAIL:{cached.get('status', 0)}",
                       published_at=None, modified_at=None, text="",
                       sha256="")
            return out
        body = cached["body"]
        pub, mod = extract_dates(body)
        pub_dt, mod_dt = parse_dt(pub), parse_dt(mod)
        reasons = []
        if pub_dt is None:
            reasons.append("NO_PUBDATE")
        if pub_dt is not None and pub_dt >= asof:
            reasons.append("PUB_AFTER_ASOF")
        if mod_dt is not None and mod_dt >= asof:
            reasons.append("MODIFIED_AFTER_ASOF")
        out.update(
            verdict="PASS" if not reasons else "QUARANTINE:" + ",".join(reasons),
            published_at=_iso(pub_dt) if pub_dt else None,
            modified_at=_iso(mod_dt) if mod_dt else None,
            text=extract_text(body),
            sha256=hashlib.sha256(body.encode("utf-8", "ignore")).hexdigest(),
        )
        return out


# ---------------------------------------------------------------------------
# Wikipedia revisions (the strongest PIT source)
# ---------------------------------------------------------------------------

WIKI_API = "https://en.wikipedia.org/w/api.php"


class WikipediaClient:
    def __init__(self, cache: _DiskCache | None = None):
        self.cache = cache or _DiskCache()

    def rev_at(self, title: str, asof: datetime,
               max_chars: int = 12_000) -> dict[str, Any] | None:
        """Newest revision at or before ``asof``: wikitext + revid + ts.
        Revision timestamps are immutable and authoritative (§3.1)."""
        key = json.dumps([title, _iso(asof)])
        cached = self.cache.get("wikirev", key)
        if cached is None:
            params = {
                "action": "query", "prop": "revisions", "titles": title,
                "rvlimit": "1", "rvstart": _iso(asof), "rvdir": "older",
                "rvprop": "ids|timestamp|content", "rvslots": "main",
                "format": "json", "formatversion": "2", "redirects": "1",
            }
            try:
                r = requests.get(WIKI_API, params=params, headers=UA, timeout=60)
                data = r.json() if r.status_code == 200 else {}
            except (requests.RequestException, ValueError):
                data = {}
            pages = (data.get("query") or {}).get("pages") or []
            cached = {"found": False}
            if pages and pages[0].get("revisions"):
                rev = pages[0]["revisions"][0]
                cached = {
                    "found": True,
                    "title": pages[0].get("title", title),
                    "revid": rev.get("revid"),
                    "timestamp": rev.get("timestamp"),
                    "content": ((rev.get("slots") or {}).get("main") or {})
                    .get("content", "")[:200_000],
                }
            self.cache.put("wikirev", key, cached)
        if not cached.get("found"):
            return None
        out = dict(cached)
        out["content"] = _strip_wikitext(out.get("content", ""))[:max_chars]
        return out


def _strip_wikitext(w: str) -> str:
    """Light wikitext -> text: keep prose and table cells, drop refs/files."""
    w = re.sub(r"<ref[^>]*/>", " ", w)
    w = re.sub(r"<ref[^>]*>.*?</ref>", " ", w, flags=re.S)
    w = re.sub(r"\[\[(?:File|Image|Category):[^\]]*\]\]", " ", w)
    w = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", w)
    w = re.sub(r"\{\{[^{}]*\}\}", " ", w)      # one nesting level is enough
    w = re.sub(r"'''?", "", w)
    w = re.sub(r"<[^>]+>", " ", w)
    return _WS_RE.sub(" ", w).strip()

# Point-in-time retrieval for LLM-forecast backtests

**Research agent output — 2026-08-11.** All findings marked ✅ VERIFIED were produced by live
HTTP probes from this container on 2026-08-11 (proxied egress, `HTTPS_PROXY` via the CCR agent
proxy). Truncated real responses are inline. Probe scripts live in the session scratchpad
(`.../scratchpad/probes/`), not in the repo.

Context: `bot/backtest/SPEC.md` §1 requires that at decision time `t` an LLM strategy receives
"an information pack assembled under point-in-time discipline (sources dated `< t`; retrieval is
logged and auditable; **any leak invalidates the run**)". `ORCHESTRATION.md` constraint #2 limits
LLM-strategy scoring to markets resolving after 2026-02-01. This document specifies how to
actually build and prove that.

---

## 0. TL;DR — the recommended stack

| Layer | Primary | Fallback | PIT guarantee |
|---|---|---|---|
| News discovery (all categories) | **GDELT DOC 2.0 `artlist`** (free, no key) | GDELT GKG 15-min raw files (`data.gdeltproject.org`, no rate limit) | **Weak** — server date bound leaks (§1.3) *and* `seendate` itself can be wrong (§1.3b) |
| News full text | **Live fetch of the discovered URL + schema.org `datePublished`/`dateModified` gate** | CC-NEWS WARC (immutable `WARC-Date`) for a pre-built corpus | Independent second date; quarantines in-place updates and bad `seendate` |
| Politics / polling ground truth | **Wikipedia revision at `≤ D`** (`prop=revisions&rvstart&rvdir=older`) | GDELT DOC restricted to `domain:` polling outlets | Revision timestamp is immutable and authoritative |
| Attention / salience | **Wikimedia pageviews daily API** (truncate at `D`) | GDELT `timelinevol` | Daily buckets, no revision |
| Economics | **ALFRED vintage API** (`realtime_start=realtime_end=D`) — needs free key | BLS v1 public API (non-revised series only) | ALFRED vintages are the gold standard for econ PIT |
| Campaign finance | FEC `api.open.fec.gov` with `receipt_date`/`report` filters (free key) | — | Filing dates are in the record |
| Web-page snapshots | **Wayback CDX + `id_` raw fetch** | *(currently unreachable from this container — see §2)* | Snapshot timestamp is immutable |
| **Search engines (Google/Bing/Brave/Tavily/Exa/Perplexity)** | **BANNED in backtests** | — | Ranking is computed *now*; unfixable leak (§5.3) |

**Biggest single finding:** GDELT — the only free, keyless, date-bounded news index available to
us — **cannot be trusted on dates at two independent levels**:

1. Its `enddatetime` parameter is not reliably enforced. In one measured window, **21 of 75**
   returned articles were published *after* the requested cutoff (§1.3).
2. Even its per-article `seendate` is sometimes wrong — a live run found an article stamped
   `seendate 2026-07-24` whose publisher stamped it `2026-07-27` (§1.3b).

So the defence must be **two independent date checks**, not one: filter on `seendate` *and*
re-verify against the publisher's own `datePublished`/`dateModified`. In a 12-URL production
sample, gate 1 rejected 25 of 250 articles and gate 2 caught **three more** that gate 1 passed
(§6.1). A single-gate pipeline leaks silently.

---

## 1. GDELT DOC 2.0 API

**Endpoint:** `https://api.gdeltproject.org/api/v2/doc/doc`
**Docs:** <https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/> ·
<https://blog.gdeltproject.org/gdelt-2-0-doc-api-expands-to-2017/>

Parameters (verified against docs + live behaviour): `query`, `mode`
(`artlist|timelinevol|tonechart|wordcloud|…`), `format` (`json|csv|rss|html`), `maxrecords`
(default 75, **max 250**), `startdatetime`/`enddatetime` (`YYYYMMDDHHMMSS`), `timespan`
(alternative to the pair), `sort` (`datedesc|dateasc|tonedesc|toneasc|hybridrel`).
Query operators: `"exact phrase"`, `(a OR b)`, `-negation`, `domain:cnn.com`,
`domainis:un.org`, `sourcecountry:US`, `sourcelang:english`, `theme:ECON_INTEREST_RATE`,
`near20:"a" "b"`, `repeat3:"word"`, `tone>5`.

### 1.1 ✅ VERIFIED — date-bounded search works and coverage for 2026 events is good

Query (July 2026 window, before the 2026-08-04 Michigan Democratic Senate primary):

```
https://api.gdeltproject.org/api/v2/doc/doc
  ?query=%22Michigan%22+%22Senate+primary%22
  &mode=artlist&maxrecords=75&format=json
  &startdatetime=20260701000000&enddatetime=20260731235959&sort=datedesc
```

Real response (truncated, `articles[]`):

```
20260731T224500Z | mininggazette.com | Senate primary heats up - The Mining Gazette
20260731T221500Z | motherjones.com   | How Michigan Became the Most Expensive Democratic Primary Ever
20260731T213000Z | dailynews.com     | As progressives take on inequality, some cite religion as motivation
20260729T024500Z | ...               | (oldest of the 75 returned)
```

Article record fields: `url`, `url_mobile`, `title`, `seendate`, `socialimage`, `domain`,
`language`, `sourcecountry`. **No snippet, no body text.** GDELT DOC gives you a *URL index*,
not a text corpus — the body must come from elsewhere (§1.5).

Also verified:

- `maxrecords=250` returns exactly 250 (`n=250`); `maxrecords=400` did not return a valid
  response. The documented **250 cap is real** — over-requesting beyond it is not an option, which
  is why the §1.3 crowd-out matters.
- **Back-coverage starts 2017-01-01**, as documented: a `20170101000000` window returns results
  (`seendate 20170101T000000Z`); a `20160101000000` window returns none. Irrelevant for our
  post-Feb-2026 scoring window, but it bounds any price-only backfill.
- The **Context API** (`/api/v2/context/context`), GDELT's only mode that returns text snippets,
  rejects historical windows outright: `HTTP 200` body `Invalid query start date.` for a
  2026-07-20 → 2026-07-24 range. **It cannot be used for point-in-time retrieval.**

Contrast run, same query, window `20260801000000`–`20260811235959` (i.e. *after* the primary):

```
20260811T210000Z | forbes.com          | Woke 1? El-Sayed Says His Views Have Changed After July 4th Criticism Resurfaced
20260811T203000Z | politicalwire.com   | With El-Sayed Victory Comes a Wave of Islamophobia
```

The post-cutoff window hands you the answer (El-Sayed won); the pre-cutoff window hands you a
contested three-way race. **This is exactly the contrast a leak destroys**, and it is the
ready-made adversarial spot-check for this market (§5.5).

### 1.2 ✅ VERIFIED — rate limits are severe and shared-IP

GDELT returns HTTP 429 with a plain-text body:

```
Please limit requests to one every 5 seconds or contact kalev.leetaru5@gmail.com for larger
queries. All high-traffic users should switch to our ngrams dataset ...
```

Measured behaviour from this container:

- 429 even at **6 s** spacing, and at 12–15 s spacing when two processes ran concurrently.
- Once serialised to a single process with 12–20 s spacing plus exponential backoff, requests
  succeeded, typically after 0–3 retries.
- The limit is **per egress IP**, and this container shares an egress IP with other traffic, so
  budget for 429s as the *normal* case.

**Implication for the harness:** GDELT must sit behind a single global token bucket
(1 req / 5 s hard, 1 req / 15 s in practice) with retry-on-429, and **every response must be
cached to disk keyed by `(query, start, end)`**. A backtest re-run must hit the cache, never the
network — otherwise the run is neither reproducible nor polite. Budget: at 1 query per market per
decision point and ~15 s/query, 250 markets × 20 decision points = 5 000 queries ≈ 21 hours.
Plan to build the cache **once**, offline, ahead of the backtest.

### 1.3 🔴 VERIFIED LEAK — `enddatetime` is not reliably enforced

Five windows tested with `sort=datedesc`, counting returned articles whose `seendate` exceeded
the requested `enddatetime`:

| Window | `enddatetime` | n | out-of-window (after end) | max `seendate` | overshoot |
|---|---|---|---|---|---|
| W1 Jul 2026 | `20260731235959` | 75 | **4 (5.3%)** | `20260801T174500Z` | **+17.75 h** |
| W2 Jul 1–31 00:00 | `20260731000000` | 75 | **21 (28.0%)** | `20260731T224500Z` | **+22.75 h** |
| W3 Jun 1–15 | `20260615000000` | 42 | **1 (2.4%)** | `20260615T151500Z` | **+15.25 h** |
| W4 Aug 1–11 | `20260811235959` | 75 | 0 | `20260811T210000Z` | 0 (end ≈ now) |
| W5 Apr 2026 | `20260430235959` | 75 | **19 (25.3%)** | `20260501T233000Z` | **+23.5 h** |
| T1 mid-day | `20260715000000` | 75 | 2 (2.7%) | `20260715T010000Z` | +1 h |
| T1 mid-day | `20260715120000` | 78 | 0 | `20260715T061500Z` | 0 |

Real leaked rows from W1 (requested cutoff = end of 2026-07-31):

```
LEAK> 20260801T174500Z newsweek.com  El-Sayed Takes Lead Over Stevens Days Before Michigan Primary
LEAK> 20260801T110000Z vox.com       AIPAC test in Michigan Senate primary
LEAK> 20260801T104500Z slate.com     2026 Senate races: What the Michigan primary is about to reveal
```

Observations:
- Overshoot is **always forward, never backward** (`before_start` was 0 in all 5 windows) — so
  `startdatetime` behaves, `enddatetime` does not.
- Magnitude is bounded around **~24 h** but the *fraction* of contaminated rows varies wildly
  (0 %–28 %) with query volume and cutoff placement. It is 0 % only when the cutoff is "now".
- Cause is not documented anywhere. Do not model it; **defend against it**.

**Mandatory mitigations, in this order:**

1. Request `enddatetime = D` exactly (do *not* shift the window back — see 3).
2. **Hard client-side filter: drop every article with `seendate >= D`.** This is the actual
   guarantee. Treat GDELT's bound as a recall hint only.
3. Request `maxrecords=250`, not the default 75. Because `sort=datedesc` returns the *newest*
   records first, the out-of-window rows **consume result slots** and crowd out legitimate
   pre-`D` articles — in W2 that cost 28 % of the budget. Over-request, then filter.
4. Log the count of dropped rows per query into the audit log. A sudden spike is a signal that
   GDELT's behaviour changed.

### 1.3b 🔴 `seendate` itself is not trustworthy either

The natural assumption is that `seendate` (when GDELT *observed* the article) is ≥ the
publication time, so `seendate < D` ⟹ `published < D`. That holds most of the time — the Newsweek
article has `datePublished=2026-08-01T13:38:20Z` and `seendate=20260801T174500Z`, a ~4 h
observation lag in the safe direction.

**But it is not an invariant.** The §6 production run turned up a counterexample:

```
wkfr.com  gdelt_seendate 2026-07-24T19:15:00Z   datePublished 2026-07-27T11:18:41Z
          "Fake Michigan TikTok Accounts And The Stevens Senate Race"
```

GDELT claims it saw the article on 2026-07-24; the publisher stamps it 2026-07-27 — **three days
later**. Whichever date is wrong, the article passed the `seendate < D` filter while carrying a
post-`D` publication date. Causes are plausibly re-publication under the same URL, CMS re-dating,
or a GDELT batch-attribution error; the cause does not matter.

**This is the single most important reason the §5.2 checker uses two independent dates.** A
pipeline that filters on `seendate` alone — the obvious implementation — leaks here, silently.

### 1.4 ✅ VERIFIED — raw GDELT files: unmetered fallback

The 15-minute raw feed has **no rate limit** and is a clean escape hatch from §1.2:

```
$ curl -s http://data.gdeltproject.org/gdeltv2/masterfilelist.txt -o mfl.txt
HTTP:200 bytes:126585787
$ tail -3 mfl.txt
65891   834c9a73... http://data.gdeltproject.org/gdeltv2/20260811211500.export.CSV.zip
102535  d862757a... http://data.gdeltproject.org/gdeltv2/20260811211500.mentions.CSV.zip
5230332 a194cfc6... http://data.gdeltproject.org/gdeltv2/20260811211500.gkg.csv.zip
```

Fetched and parsed `20260715120000.gkg.csv.zip` (5.03 MB, 27 tab-separated columns, 1 218 rows):

```
DATE 20260715120000 | src newkerala.com | url https://www.newkerala.com/news/a/india-uk-fta-...
DATE 20260715120000 | src wjla.com      | url https://wjla.com/weather/first-alert-weather-blog/...
```

The `DATE` column **is the 15-minute batch stamp** — it is structurally impossible for a file
named `20260715120000.gkg.csv.zip` to contain anything GDELT saw after 12:00 on 2026-07-15.
That makes the raw feed a **strictly stronger PIT guarantee than the DOC API**: the filename is
the cutoff.

Size budget (from measured file sizes): `gkg` ≈ 480 MB/day, `mentions` ≈ 12 MB/day,
`export` ≈ 6 MB/day. GKG is too big for full history against the 1–2 GB disk allowance in
`ORCHESTRATION.md`. Practical use: **stream** GKG for a small number of high-value days
(decision points near market close), extract `(DATE, SourceCommonName, DocumentIdentifier,
V2Themes, V2Persons, V2Organizations)` for URLs matching the market's entity set, and discard
the rest. ~18 MB/day for `export`+`mentions` only is affordable for a bounded window.

> ⚠️ `https://data.gdeltproject.org/...` fails from this container (HTTP 000); `http://` works
> and is explicitly allowed by the egress policy. Content is a signed zip; integrity is covered
> by the MD5 in the master file list, so plaintext transport is acceptable here — but record the
> MD5 in the audit log.

### 1.5 🔴 GDELT gives URLs, not text — and that is a leak vector

Since there is no snippet field, the body must be fetched. Fetching the URL **now** returns the
*current* version of the article, which may have been edited after `D`. Verified live:

```
HTTP 200  https://www.newsweek.com/el-sayed-lead-stevens-michigan-primary-poll-12273776
   datePublished=['2026-08-01T13:38:20.000Z']  dateModified=['2026-08-01T13:38:32.000Z']
HTTP 200  https://slate.com/news-and-politics/2026/08/michigan-primary-...
   datePublished=['2026-08-01T09:45:00+00:00'] dateModified=[]  og:published=['2026-08-01T09:45:00+00:00']
HTTP 200  https://www.vox.com/politics/497612/aipac-michigan-senate-el-sayed-stevens
   datePublished=['2026-08-01T10:00:00+00:00'] dateModified=['2026-08-01T10:00:00+00:00']
HTTP 200  https://www.motherjones.com/politics/2026/07/how-michigan-became-...
   datePublished=[] dateModified=[] og=[]     ← NO METADATA: must be quarantined
```

3 of 4 outlets expose machine-readable `datePublished` **and** `dateModified` via schema.org
JSON-LD or `og:article:*`. That gives a per-source gate (§5.2). The 4th (Mother Jones) exposes
neither — such sources are **quarantined**, not silently accepted.

---

## 2. Wayback Machine — 🔴 CURRENTLY UNREACHABLE FROM THIS CONTAINER

**Intended design (correct, and what to use anywhere the host is reachable):**

```
# 1. find the snapshot closest to D, but not after it
GET https://web.archive.org/cdx/search/cdx
    ?url=<site>&from=<YYYYMMDD>&to=<YYYYMMDD>
    &output=json&collapse=timestamp:8&fl=timestamp,original,statuscode,digest&limit=N
# 2. fetch that snapshot's ORIGINAL bytes (the `id_` suffix strips Wayback's banner/rewrites)
GET https://web.archive.org/web/<timestamp>id_/<original-url>
# response carries `Memento-Datetime:` — the immutable capture time
```

Docs: <https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server> ·
<https://archive.org/help/wayback_api.php>

**Measured result (2026-08-11):**

| Host | Result |
|---|---|
| `web.archive.org` (CDX, snapshot fetch) | **TLS handshake reset**, 100 % of ~15 attempts over ~15 min |
| `archive.org/wayback/available` | HTTP 429 on ~90 % of attempts; **one HTTP 200** observed |
| `timetravel.mementoweb.org` | Egress-policy denial (proxy returned 502 to CONNECT) |
| `archive.ph` (archive.today) | HTTP 000 |

Diagnosis (`curl -v`) — this is **not** an egress-policy block:

```
* Establish HTTP proxy tunnel to web.archive.org:443
< HTTP/1.1 200 Connection Established        ← policy ALLOWS the host
* TLSv1.3 (OUT), TLS handshake, Client hello (1):
* Recv failure: Connection reset by peer     ← the REMOTE resets the handshake
```

The CONNECT tunnel is established, then Internet Archive resets the TLS handshake. That is
IA-side blocking of this datacenter IP range, consistent with the 429s from `archive.org`.
The `recentRelayFailures` list from `$HTTPS_PROXY/__agentproxy/status` contains **no** entry for
`web.archive.org` (only `timetravel.mementoweb.org`), confirming the proxy is not the blocker.

The one successful call was the *lookup* API, not the *fetch*:

```json
{"url": "nytimes.com", "archived_snapshots": {"closest": {"status": "200",
 "available": true, "url": "http://web.archive.org/web/2026...", ...}}}
```

**We can discover snapshot URLs but not retrieve their content.** That asymmetry makes Wayback
useless as a primary PIT source *from here*.

**Consequences and plan:**

- Do **not** put Wayback on the critical path of the backtest.
- Keep the CDX client written and unit-tested behind a feature flag; it is the right tool and
  may work from a different egress. Snapshot density for major news homepages is historically
  many-per-day, which would be ample.
- Where a Wayback snapshot would have been used (news homepage as of `D`, polling aggregator as
  of `D`), substitute: **Wikipedia revisions** (§3) for polling and reference material, and
  **CC-NEWS WARCs** (§4.1) for article bodies.
- If Wayback becomes reachable, retrofit it as the *verifier* rather than the retriever: for a
  sample of audit-log URLs, fetch the nearest snapshot ≤ `D` and diff against the live body to
  measure how much in-place editing the run actually absorbed.

---

## 3. Wikipedia revision history — ✅ VERIFIED, and the strongest PIT source we have

### 3.1 Find the revision in force at date `D`

```
GET https://en.wikipedia.org/w/api.php
  ?action=query&prop=revisions
  &titles=2026%20United%20States%20Senate%20election%20in%20Michigan
  &rvlimit=1&rvstart=2026-07-01T00:00:00Z&rvdir=older
  &rvprop=ids|timestamp|content&rvslots=main&format=json&formatversion=2
```

`rvstart` + `rvdir=older` + `rvlimit=1` = "the newest revision at or before `D`". Real response
(`HTTP 200`, 119 425 bytes):

```
revid 1361910696   timestamp 2026-06-30T20:20:10Z   content length 115818
```

The wikitext of that revision contains the **polling table as it stood on 2026-06-30**:

```
Polling===
'''Aggregate polls'''
{| class="wikitable sortable" ...
!Source of poll aggregation !Dates administered !Dates updated
! Abdul El-Sayed ! Mallory McMorrow ! Haley Stevens ! Undecided !Margin
|-
|[[270toWin]]<ref name="270toWin">{{cite web |title=2026 Polls: Michigan Senate
 |url=https://www.270towin.com/2026-senate-polls/michigan |access-date=June 27, 2026}}</ref>
|May 20 – June 14, 2026
|June 25, 2026
|'''31.7%'''  |9.3%  |29.3%  |29.7%
```

This is a **free, dated, machine-parseable poll aggregate for a live prediction market**, and it
is the single highest-value PIT source in this whole document. 538 is dead (§4.4), so Wikipedia
is now the practical free polling archive.

Docs: <https://www.mediawiki.org/wiki/API:Revisions>

### 3.2 Rendered HTML of a past revision

```
GET https://en.wikipedia.org/api/rest_v1/page/html/<Title>/<revid>
```

✅ Verified: `HTTP 200`, 1 156 208 bytes for revid `1361910696`, with
`about="//en.wikipedia.org/wiki/Special:Redirect/revision/1361910696"` in the root element.
Easier to feed to an LLM than wikitext.

### 3.3 ⚠️ Subtle risk: transclusion is rendered at *current* template state

Parsoid renders an old revision's wikitext but resolves `{{templates}}` against their **present**
content. If a page transcludes a live-updated data template, an "old" render can carry new facts.

Empirical check — two articles rendered at their 2026-05-31 revisions, scanned for date strings
later than the revision:

| Article | revid | ts | transclusions | post-revision dates found |
|---|---|---|---|---|
| 2026 United States Senate elections | 1357071010 | 2026-05-31T16:02Z | 862 | `August 5, 2026` (12), `August 4, 2026` (9), `November 3, 2026` (6), … |
| 2026 US Senate election in Michigan | 1357077523 | 2026-05-31T16:53Z | 95 | `November 3, 2026` (3), `August 4, 2026` (1) |

Every hit is a **scheduled future date** (primary date, general-election date) — a fact known on
2026-05-31, not a leak. Candidate mention counts in the May-31 render were balanced
(`El-Sayed` 84 / `Stevens` 92 / `McMorrow` 81), i.e. the render did **not** reveal the August
winner. So in practice these election pages render cleanly.

Still, mitigate:
- Prefer **wikitext** (`rvprop=content&rvslots=main`) over rendered HTML for anything
  fact-bearing; wikitext is byte-identical to what the editor saved at that timestamp.
- If HTML is needed, run the §5.2 date-scanner over the render and flag any date string > `D`
  that is not on an allow-list of known scheduled dates.
- Never use `action=parse` without `oldid`.

### 3.4 ✅ Wikimedia pageviews — clean daily salience signal

```
GET https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/
    all-access/user/Abdul_El-Sayed/daily/20260720/20260731
```

```json
{"items":[{"timestamp":"2026072000","views":23940},{"timestamp":"2026072100","views":15884},
          {"timestamp":"2026072200","views":11525},{"timestamp":"2026072300","views":...}]}
```

Daily buckets, never revised. PIT-safe by truncating `end` at `D − 1 day`. A useful non-price
attention feature for the price-only strategies too.

---

## 4. Other free archives and paid tiers

### 4.1 ✅ Common Crawl News (CC-NEWS) — best free full-text corpus, but bulk-only

```
$ curl https://data.commoncrawl.org/crawl-data/CC-NEWS/2026/07/warc.paths.gz
HTTP:200 bytes:2809   →  354 WARC files for July 2026
crawl-data/CC-NEWS/2026/07/CC-NEWS-20260701022501-08467.warc.gz
...
crawl-data/CC-NEWS/2026/07/CC-NEWS-20260731214950-00313.warc.gz
```

Verified a WARC is fetchable and parseable. `HEAD` on the last July file:
`content-length: 1072718749` (1.07 GB), `accept-ranges: bytes` — HTTP range requests work
(`HTTP:206` for `-r 0-3000000`). Decompressing the first 3 MB (WARC.gz is a concatenation of
independently-gzipped members, so `zlib.decompressobj(31)` walks it) yielded real records:

```
WARC-Date: 2026-07-31T21:49:50Z | WARC-Type: response | WARC-Target-URI: https://www.infobae.com/america/agencias/2026/07/31/captura-de-lider-...
WARC-Date: 2026-07-31T21:49:52Z | WARC-Type: response | WARC-Target-URI: https://www.dailyherald.com/20260731/education/with-start-of-school-...
```

**`WARC-Date` is the crawl fetch time and is immutable** — it captures the article body *as it
was* at that moment, which defeats the in-place-update leak (§1.5) outright.

Blocker: **there is no CDX index for CC-NEWS.** Verified by contrast:

```
data.commoncrawl.org/cc-index/collections/CC-NEWS-2026-07/indexes/cluster.idx  → HTTP 404
data.commoncrawl.org/cc-index/collections/CC-MAIN-2026-30/indexes/cluster.idx  → HTTP 200
```

CC-MAIN is indexed; CC-NEWS is not. So targeted lookup by URL is impossible — you must scan. At
354 files × ~1 GB ≈ **380 GB/month** that is out of reach for on-demand retrieval and blows the
1–2 GB disk allowance in `ORCHESTRATION.md`.

Verdict: **not for interactive retrieval.** Viable only as a one-off offline build: stream the
WARCs for a narrow date window, keep only records whose `WARC-Target-URI` matches a URL set
already discovered via GDELT, and store the extracted text locally. Cost is bandwidth, not disk,
if you stream and discard.

### 4.2 ⚠️ Common Crawl main index (CC-MAIN) — live but flaky and robots-limited

`https://index.commoncrawl.org/collinfo.json` → `HTTP 200`, **126 collections**, current through
`CC-MAIN-2026-30 | July 2026 Index`. But the query endpoint is unreliable from here:

```
$ curl "https://index.commoncrawl.org/CC-MAIN-2026-30-index?url=reuters.com%2F*&output=json&limit=3"
{"message": "No Captures found for: reuters.com/"}      # valid response, but empty
$ (repeat)  → curl exit 52/35 (connection reset) on ~75% of attempts
```

By the end of the session `index.commoncrawl.org` was returning `HTTP 000` on every attempt,
including `collinfo.json` which had worked an hour earlier.

Three problems: (a) intermittent-to-total connection resets from this container, (b) CC-MAIN
honours `robots.txt`, so Reuters, NYT and other major outlets are largely **absent**, (c) monthly
crawl granularity is far coarser than a decision-point cutoff. CC-NEWS (§4.1) exists precisely
because of (b). Not recommended.

### 4.3 Paid / keyed news APIs — what is actually reachable

| Service | Reachable? | Historical range | Cost | Verdict |
|---|---|---|---|---|
| **AskNews** | needs key | archive back to **2023**; `historical=True`, `start_timestamp`/`end_timestamp` | **No free tier.** PAYG $0/mo + usage; Pro $7.99/mo (500 credits); Spelunker $250/mo (20k); Analyst $1 000/mo (110k, incl. paywalled). Standard call = 1 credit, advanced endpoints 3–15 credits | Best *purpose-built* PIT news API. Its date filtering is a first-class product feature rather than an accident, unlike GDELT. If the LLM strategy graduates, this is the upgrade to buy. Not usable now (no key, no free tier). |
| **NewsAPI.org** | `HTTP 401` without key | **1 month** on most plans | Free tier: 25–100 req/day, **localhost only, production/commercial use prohibited** ; production from **$449/mo** | Useless — 1-month archive can't reach a 6-month backtest window |
| **GNews** | `HTTP 400` without key | Free: **30 days**. Paid: back to **2020** | Free €0; Essential €49.99/mo; Business €99.99/mo; Enterprise €249.99/mo | Free tier's 30-day archive is too short. Cheapest paid path to a multi-year archive. |
| Mediastack | `HTTP 401` | — | — | Keyed |
| Tavily | `HTTP 401` | — | — | Keyed, **and a search engine — banned anyway (§5.3)** |
| Currents | `HTTP 401` | — | — | Keyed |

Sources: <https://docs.asknews.app/en/news>, <https://docs.asknews.app/en/rate-limiting>,
<https://gnews.io/#pricing>, <https://newsapi.org/pricing>.

### 4.4 🔴 FiveThirtyEight's poll CSVs are gone

```
$ curl -L https://projects.fivethirtyeight.com/polls/data/senate_polls.csv
HTTP:200  final:https://abcnews.com/politics   bytes:307680   (an HTML page, not a CSV)
```

The historical go-to for programmatic polling data now 302s to an ABC News landing page. RCP
returns `HTTP 403` to non-browser clients; `racetothewh.com/senate/2026` → `HTTP 404`;
`270towin.com/2026-senate-polls/michigan` → `HTTP 200` (scrapeable, but **live-only — no
history**, so it leaks). **Wikipedia revisions (§3.1) are the replacement.**

### 4.5 ✅ Ground-truth data sources (verified)

- **BLS public API v1** — no key, `HTTP 200`:
  ```
  POST https://api.bls.gov/publicAPI/v1/timeseries/data/
  {"seriesid":["CUUR0000SA0"],"startyear":"2026","endyear":"2026"}
  → {"status":"REQUEST_SUCCEEDED", ... "2026 M06 June 333.952", "M05 335.123", "M04 333.020", ...}
  ```
  ⚠️ Returns **latest revised** values only, no vintages. Safe for CPI-U **NSA** (`CUUR*`, never
  revised); **unsafe** for seasonally-adjusted series (`CUSR*`, annually re-seasonalised) and for
  employment series, which are revised for months.
- **ALFRED / FRED** — `HTTP 400: Variable api_key is not set`. Needs a **free** key. This is the
  right tool for econ PIT: `realtime_start=realtime_end=D` returns the series *as it was
  published on D*, i.e. genuine vintages.
  <https://fred.stlouisfed.org/docs/api/fred/series_observations.html>
  `fredgraph.csv?id=CPIAUCSL` works keyless (`HTTP 200`) but returns the **latest vintage only** —
  a silent leak for any revised series. Do not use it.
- **FEC** — `api.open.fec.gov` with `DEMO_KEY` → `HTTP 429 OVER_RATE_LIMIT` (40 calls/hr). A free
  personal key from api.data.gov raises this to 1 000/hr. Filings carry `receipt_date`, so PIT
  filtering is exact.

**Action item:** register free keys for **FRED/ALFRED** and **api.data.gov (FEC)** before the
econ and election strategies are backtested. Both are free and both remove a real leak.

---

## 5. Leakage audit design

The rule from `SPEC.md` is "any leak invalidates the run". That is only enforceable if every
retrieved byte is attributable to a dated source. Design accordingly.

### 5.1 The retrieval ledger (the core mechanism)

Every retrieval writes one immutable row **before** the content reaches the model. No content may
enter an information pack that does not have a ledger row.

```python
@dataclass(frozen=True)
class RetrievalRecord:
    run_id: str                 # backtest run
    market_ticker: str
    decision_time: datetime     # D — the point-in-time cutoff
    source_kind: str            # gdelt_doc | gdelt_gkg | wikipedia_rev | wayback | ccnews | alfred | bls | fec
    query: str                  # exact query string / API params, verbatim
    request_url: str            # fully-resolved URL actually sent
    url: str                    # the retrieved document
    # --- the three dates that make or break the audit ---
    source_date: datetime       # authoritative: GDELT seendate | WARC-Date | revision timestamp | Memento-Datetime
    published_at: datetime|None # schema.org datePublished / og:article:published_time
    modified_at: datetime|None  # schema.org dateModified / og:article:modified_time
    content_sha256: str         # of the exact bytes handed to the model
    n_tokens: int               # feeds SPEC.md §4 inference_cost
    verdict: str                # PASS | QUARANTINE:<reasons> | FETCH_FAIL:<type>
```

Ledger goes to an append-only table alongside the trade log, keyed by `run_id`. A run's ledger is
part of its artifact — a backtest result without its ledger is not reviewable.

### 5.2 The automated checker (blocking, runs at end of every backtest)

```python
def audit_run(run, ledger, *, hard_fail=True):
    violations = []
    for r in ledger:
        D = r.decision_time
        if r.source_date >= D:                      # V1 primary date bound
            violations.append(("SOURCE_AFTER_D", r))
        if r.published_at and r.published_at >= D:   # V2 independent corroboration
            violations.append(("PUBLISHED_AFTER_D", r))
        if r.modified_at and r.modified_at >= D:     # V3 in-place edit after the cutoff
            violations.append(("MODIFIED_AFTER_D", r))
        if r.published_at is None and r.source_kind == "live_fetch":
            violations.append(("UNDATED_SOURCE", r))       # quarantine, not silent accept
        if r.source_kind in BANNED_KINDS:            # V4 see §5.3
            violations.append(("BANNED_SOURCE", r))
        if r.published_at and r.source_date and r.published_at > r.source_date + timedelta(hours=1):
            violations.append(("DATE_INCONSISTENT", r))    # V5 metadata disagreement
    # V6 aggregate: contamination for LLM strategies
    if any(m.resolution_time <= date(2026,2,1) for m in run.markets_scored):
        violations.append(("PRE_CUTOFF_MARKET_SCORED", None))
    if hard_fail and violations:
        raise LeakageError(violations)   # the run does not produce a P&L number
    return violations
```

Design points that matter:

- **V1 and V2 are deliberately redundant, and the redundancy is load-bearing.** V1 trusts the
  index (GDELT `seendate`); V2 trusts the publisher (`datePublished`). §1.3 proves the index's
  *bound* lies and §1.3b proves its *per-article date* can lie, so a second independent date is not
  paranoia — in the §6.1 run it caught 3 documents that V1 waved through.
- **V5 is the tripwire for §1.3b.** When the publisher's date is more than an hour later than the
  index's, one of the two is wrong and the document is not safe to use at any `D` near that date.
- **`hard_fail=True` by default.** A leak must delete the P&L number, not annotate it. If leaks
  merely warn, they will be tolerated.
- **Undated ⇒ quarantined, never included.** Mother Jones (§1.5) exposes no dates; a pipeline that
  silently keeps such pages has an unbounded leak surface.
- The checker must run on the **ledger**, not on the retrieval code, so that a refactor of the
  retrieval layer cannot quietly disable it.

### 5.3 🔴 Subtle leak: search-engine ranking encodes the future

A live search API (Google, Bing, Brave, Tavily, Exa, Perplexity, SerpAPI, or an LLM's built-in
web-search tool) computes its ranking **at call time**. Even with a `before:2026-07-25` filter:

- Which pre-`D` documents rank highest is determined by post-`D` link graphs, click data, and
  freshness/authority signals. The *selection* is contaminated even when every *document* is
  clean, and no date filter can undo that.
- Query autocompletion, "related searches", spelling correction and entity disambiguation all
  reflect present-day salience.
- Result snippets are regenerated from the current page.
- Many engines silently ignore or soften date operators.

**Rule: no live search engine may appear in a backtest retrieval path. Ever.** Add
`BANNED_KINDS = {"web_search", "tavily", "exa", "brave", "serpapi", "perplexity",
"llm_builtin_search"}` and make V4 in §5.2 enforce it. This also means the forecaster's LLM must
be called **with web-search tools disabled** — pair that with the FutureSearch prompt line already
recorded in `research/futuresearch.md`: *"Do NOT do any additional web research. Only use the
information already provided."*

GDELT is acceptable precisely because it is not a ranker: `sort=datedesc` is a deterministic
ordering by a stored timestamp, reproducible from the cache. If you use `sort=hybridrel`
(relevance), you reintroduce a present-day ranking signal — **so pin `sort=datedesc` and never
use relevance ordering in a backtest.**

### 5.4 Other subtle leaks, ranked by how likely they are to bite us

1. **In-place article updates.** §1.5. A story published 2026-07-30 and rewritten 2026-08-05 with
   the result reads as a pre-`D` source. Mitigations: the `dateModified` gate (V3); prefer
   immutable captures (CC-NEWS `WARC-Date`, Wayback `Memento-Datetime`) where available; hash the
   body so the same URL retrieved twice is detectably different.
2. **Redirect and canonicalisation drift.** A 2026-07 URL that now 301s to an updated hub page.
   Mitigation: log both `request_url` and the final `r.url`; flag any cross-path redirect.
3. **Market metadata edited after listing.** Kalshi rules text, title, or category can be amended
   after a market opens; a title amended post-event ("… after El-Sayed's win") is a direct leak.
   `SPEC.md` §1 already limits the strategy to "static data whose visibility predates `t`" — the
   harness must therefore snapshot market metadata **at listing time** and refuse to serve later
   revisions, and the ledger should record the metadata hash used.
4. **Question phrasing as a prior.** Even with perfect retrieval, a market titled *"Will El-Sayed
   win the Michigan Democratic Senate primary?"* tells the model which three names matter and that
   the race was competitive enough to list. This is **residual and unfixable**, and is exactly the
   risk `ORCHESTRATION.md` #2's post-Feb-2026 rule is *not* able to cover. Quantify rather than
   pretend: run a **no-retrieval ablation** — same model, same prompt, market title only, empty
   information pack — and report its Brier score. That number is the floor. If the full pipeline
   only beats the ablation by a hair, the "forecasting" is mostly prior recall and the strategy
   should not graduate.
5. **Model priors on post-cutoff events.** The Jan-2026 cutoff plus the post-Feb-2026 market
   filter handles the bulk. Residual: (a) models are periodically refreshed — **pin an exact model
   ID and record it in the ledger**, and re-audit if it changes; (b) an event with a long
   pre-history (a candidate famous since 2024) leaks background, though not the outcome;
   (c) retrieval-augmented *system* prompts or tool descriptions that mention current dates.
6. **Cache poisoning across runs.** If the retrieval cache is keyed only by query and not by `D`,
   a later run at `D' > D` can serve a wider result set to an earlier decision point. **Key every
   cache entry by `(source_kind, query, D)` and store the retrieval wall-clock time.**
7. **Resolution-source contamination.** The market's own resolution source (an official results
   page) is often the top hit for the market's keywords. Maintain a per-market
   **resolution-source domain blocklist** applied at retrieval time for any `D` before resolution.

### 5.5 Adversarial spot-checks (human/LLM-in-the-loop, per run)

Automated checks catch date violations; they cannot catch "the pack tells you the answer in prose
without a violating date". Add three cheap adversarial passes:

1. **Winner-word scan.** For each resolved market, take a small vocabulary of outcome-revealing
   tokens (`won`, `wins`, `victory`, `defeated`, `conceded`, `certified`, `resigned`, plus the
   winning entity's name in a past-tense construction) and grep the assembled pack. Flag hits for
   review. Cheap, and it would fire immediately on the W4 sample above
   (`"With El-Sayed Victory Comes a Wave of Islamophobia"`).
2. **Blind-judge test.** Hand the assembled pack (no market title, no date) to a cheap model and
   ask: *"What is the latest date any of this content could have been written? Does it state the
   outcome of any contest?"* If the estimated date ≥ `D`, or it names an outcome, quarantine the
   pack. This catches leaks that carry no metadata at all.
3. **Placebo / future-shift test.** Re-run the identical pipeline at `D + 14 days` for a sample of
   markets. Brier should improve *markedly* (more information, less time to resolution). If the
   `D` and `D+14` runs score the same, the `D` run was already seeing the future. Conversely, run
   at `D − 60 days`; scores should degrade. **A pipeline whose accuracy is flat in `D` is
   leaking** — this is the strongest single diagnostic in this document because it needs no
   metadata and no trust in any provider.

Additionally: sample ~20 random ledger rows per run, fetch each URL by hand, and confirm the
content genuinely predates `D`. Record the sample in the run's README (this is the manual
counterpart to `SPEC.md` §8's "thesis-level reasoning documented per hit").

---

## 6. ✅ WORKING PROTOTYPE — verified end-to-end

Run from this container against the Michigan Senate primary market, `asof = 2026-07-25T00:00Z`
(10 days before the 2026-08-04 primary). Full script in the scratchpad; the shape is what the
backtester should implement.

```python
ASOF = datetime(2026, 7, 25, tzinfo=timezone.utc)          # D — the decision point

def gdelt_artlist(query, asof, lookback_days=21):
    params = dict(
        query=query, mode="artlist", format="json",
        maxrecords=250,                                    # over-request: leaked rows steal slots (§1.3)
        startdatetime=(asof - timedelta(days=lookback_days)).strftime("%Y%m%d%H%M%S"),
        enddatetime=asof.strftime("%Y%m%d%H%M%S"),         # requested bound — NOT trusted
        sort="datedesc",                                   # never hybridrel: relevance = today's ranking (§5.3)
    )
    r = httpx.get("https://api.gdeltproject.org/api/v2/doc/doc", params=params, timeout=120)
    return r.json()["articles"], str(r.url)

arts, url = gdelt_artlist('"Michigan" ("Senate primary" OR "El-Sayed" OR "Haley Stevens")', ASOF)

# GATE 1 — the server's date bound is advisory; this filter is the guarantee (§1.3)
kept = [a for a in arts if parse_seendate(a["seendate"]) < ASOF]

# GATE 2 — independent corroboration from the publisher's own metadata (§1.3b, §1.5, §5.2)
for a in kept:
    body = httpx.get(a["url"], follow_redirects=True).text
    pub, mod = extract_schema_dates(body)      # schema.org JSON-LD, then og:article:*
    reasons = [r for r in (
        "NO_PUBDATE"          if pub is None            else None,
        "PUB_AFTER_ASOF"      if pub and pub >= ASOF    else None,
        "MODIFIED_AFTER_ASOF" if mod and mod >= ASOF    else None,
    ) if r]
    ledger.append(RetrievalRecord(
        url=a["url"], source_date=parse_seendate(a["seendate"]),
        published_at=pub, modified_at=mod, decision_time=ASOF,
        content_sha256=sha256(body.encode()).hexdigest(),
        verdict="PASS" if not reasons else "QUARANTINE:" + ",".join(reasons)))

pack = [r for r in ledger if r.verdict == "PASS"]           # only PASS reaches the model
```

### 6.1 ✅ Live run results — every gate fired on real data

```
QUERY URL: https://api.gdeltproject.org/api/v2/doc/doc?query=%22Michigan%22+%28%22Senate+primary%22
           +OR+%22El-Sayed%22+OR+%22Haley+Stevens%22%29&mode=artlist&maxrecords=250&format=json
           &startdatetime=20260704000000&enddatetime=20260725000000&sort=datedesc

raw articles returned: 250
GATE1 seendate<asof : kept=225 rejected=25          ← 10% of GDELT's own results were post-cutoff
  newest kept: 2026-07-24T23:15:00+00:00 | wxyz.com

SUMMARY asof=2026-07-25T00:00:00+00:00: 9/12 PASS, 3 quarantined, 0 fetch-fail
```

Real ledger rows (verbatim JSON, truncated to the interesting fields):

```json
{"domain":"wxyz.com","title":"Gov. Gretchen Whitmer endorses Haley Stevens in Democratic US Senate",
 "gdelt_seendate":"2026-07-24T23:15:00+00:00","asof":"2026-07-25T00:00:00+00:00","http":200,
 "sha256_body":"d8656e35d6523bba","datePublished":"2026-07-24T17:25:56-04:00",
 "dateModified":"2026-07-24T18:10:06-04:00","verdict":"PASS"}

{"domain":"kasu.org","title":"Michigan high-stakes primaries put both parties to the test",
 "gdelt_seendate":"2026-07-24T20:00:00+00:00","datePublished":"2026-07-24T12:07:47+00:00",
 "dateModified":"2026-07-24T14:12:18+00:00","verdict":"PASS"}
```

The three quarantines — **one instance of each distinct leak class, all from a single 12-URL
sample**:

| Domain | `seendate` | `datePublished` | `dateModified` | Verdict | Leak class |
|---|---|---|---|---|---|
| `news-gazette.com` | 2026-07-24T22:15Z | `null` | `null` | `QUARANTINE:NO_PUBDATE` | undated source — unbounded risk |
| `wdbo.com` | 2026-07-24T20:30Z | 2026-07-24T20:01Z | **2026-07-25T01:03Z** | `QUARANTINE:MODIFIED_AFTER_ASOF` | **in-place edit 1 h after the cutoff** (§5.4 #1) |
| `wkfr.com` | 2026-07-24T19:15Z | **2026-07-27T11:18Z** | 2026-07-27T14:40Z | `QUARANTINE:PUB_AFTER_ASOF,MODIFIED_AFTER_ASOF` | **`seendate` wrong by 3 days** (§1.3b) |

Read that table as the justification for the whole design:

- The `wxyz.com` row is what a *correct* retrieval looks like: published 17:25 EDT on 2026-07-24,
  edited 45 min later, both comfortably before the cutoff — a genuine Whitmer-endorses-Stevens
  story that a forecaster on 2026-07-25 would legitimately have seen.
- The `wdbo.com` row is the same story from a different outlet, and it would have been **silently
  accepted** by any pipeline that checks only publication date. Its body was rewritten after `D`.
- The `wkfr.com` row would have been silently accepted by any pipeline that trusts GDELT's index.

**Nothing in the raw 250-article response is safe to use without both gates.** A naive
implementation (trust `enddatetime`, fetch the URL, feed it to the model) would have leaked on
at least 25 + 3 = 28 of the sampled documents.

---

## 7. Concrete recommendation for the backtester

### 7.1 Per-category stack

**Politics / elections** (Kalshi's largest category, and where the Feb-2026 rule bites hardest)
1. *Primary:* **Wikipedia revision ≤ `D`** of the race article — polls, candidates, endorsements,
   scheduled dates. Free, exact timestamps, parseable tables (§3.1).
2. *Secondary:* **GDELT DOC** `artlist`, 21-day lookback, `sort=datedesc`, `maxrecords=250`, hard
   `seendate < D` filter, entity-name query built from the market's ticker/title.
3. *Tertiary:* **FEC** filings ≤ `D` (needs free key) for money-race signal;
   **Wikimedia pageviews** ≤ `D − 1d` for attention.
4. *Blocked:* the market's own resolution source; 270toWin/RCP live pages (no history).

**Economics / macro** (CPI, Fed, jobs — clean, quantitative, and the easiest category to get PIT-correct)
1. *Primary:* **ALFRED vintages** — `realtime_start = realtime_end = D`. This is genuinely exact:
   it returns the number as published on `D`, revisions and all. Needs a free FRED key.
2. *Secondary:* **BLS v1** for non-revised series only (`CUUR*` CPI-U NSA). Never `CUSR*`.
3. *Tertiary:* **GDELT DOC** with `theme:ECON_*` operators for Fed commentary/expectations.
4. *Blocked:* `fredgraph.csv` (latest vintage only — silent leak).

**World events / geopolitics**
1. *Primary:* **GDELT DOC**, `sourcecountry`/`sourcelang` filtered, hard `seendate` filter.
2. *Secondary:* **GDELT GKG raw 15-min files** — filename *is* the cutoff, strongest available
   guarantee, unmetered (§1.4).
3. *Tertiary:* **Wikipedia revision ≤ `D`** for background on the entity/conflict.
4. *If Wayback becomes reachable:* homepage snapshot ≤ `D` as a "what was actually on the front
   page" prior.

**Universal**
- Body text for any GDELT-discovered URL: live fetch + `datePublished`/`dateModified` gate. Where
  a CC-NEWS corpus has been pre-built for the window, prefer the WARC record (immutable body).
- Everything cached to disk keyed by `(source_kind, query, D)`; backtest re-runs must be
  cache-only and network-free.

### 7.2 Build order

1. `bot/forecaster/retrieval/ledger.py` — `RetrievalRecord` + append-only store. **First**, so no
   retrieval code can be written that bypasses it.
2. `bot/forecaster/retrieval/audit.py` — the §5.2 checker, wired as a hard gate in the backtest
   runner's teardown.
3. `bot/forecaster/retrieval/gdelt.py` — token-bucket client (1 req/5 s global), disk cache,
   429 backoff, **hard `seendate < D` filter**, `sort=datedesc` pinned.
4. `bot/forecaster/retrieval/wikipedia.py` — `rev_at(title, D)` → wikitext + revid; poll-table
   parser.
5. `bot/forecaster/retrieval/fetch.py` — URL fetch + schema.org date extraction + quarantine.
6. `bot/forecaster/retrieval/alfred.py`, `fec.py` — after free keys are registered.
7. `bot/forecaster/retrieval/wayback.py` — written, unit-tested, **feature-flagged off** (§2).
8. The §5.5 placebo test as a standing test in the strategy's CI, not a one-off.

### 7.3 Cost and time budget

- GDELT at ~15 s/query with retries: **build the cache offline and up front.** 250 markets ×
  20 decision points ≈ 21 h of wall-clock. This must start early or it becomes the critical path.
- Article body fetches: parallelisable, ~1 s each, negligible.
- Wikipedia: no meaningful rate limit at our volume; 1 call per market per decision point.
- Disk: GDELT JSON cache ≈ tens of MB; Wikipedia revisions ≈ 100 KB–1 MB each (store wikitext,
  gzip); avoid GKG bulk except for targeted days. Comfortably inside the 1–2 GB allowance.

---

## 8. Open items

- [ ] Register free **FRED/ALFRED** key and free **api.data.gov** key (FEC). Both remove real leaks.
- [ ] Re-test `web.archive.org` from a different egress; keep the client feature-flagged.
- [ ] Decide whether to pay for **AskNews Pro ($7.99/mo, 500 credits)** if the LLM strategy
      graduates — its `historical=True` + `start/end_timestamp` is the only *designed-for-PIT*
      news API found, versus GDELT's demonstrably leaky bound.
- [ ] Implement and report the **no-retrieval ablation** Brier (§5.4 item 4) as a standing column
      next to every LLM-strategy result. Without it we cannot separate forecasting from prior recall.
- [ ] Re-run the §1.3 leak measurement periodically. It is undocumented behaviour and could change
      in either direction; the ledger's `rejected` count per query is the standing monitor.
- [ ] Build a per-outlet date-extraction table. 3 of 4 outlets sampled expose schema.org dates and
      1 exposes none; the `NO_PUBDATE` quarantine rate is a direct tax on recall, so a handful of
      per-domain extractors (byline parsing, URL-path dates like `/2026/07/31/`) is worth writing
      for the top ~30 domains that actually appear in the ledger.

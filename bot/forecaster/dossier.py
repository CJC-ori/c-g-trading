"""Tier 2 — evidence assembly (PIT retrieval) + the compressed 6-section
per-EVENT dossier (research/cost-architecture.md §3.3: FutureSearch's
six-agent decomposition run as ONE Sonnet call with six sections).

Evidence flow (every byte ledgered before it reaches a model):

    plan_retrieval (Haiku, cached)  ->  GDELT artlist (GATE 1)
        -> fetch top-k articles (GATE 2, PASS only)
        -> Wikipedia revision <= D
        -> structured ground truth (VoteHub / FEC / ALFRED) where applicable
    -> assemble_evidence_pack (deterministic text)
    -> build_dossier (Sonnet compresses into 6 sections)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from bot.forecaster.groundtruth import GroundTruthClient
from bot.forecaster.llm import LLMClient, LLMRequest, LLMResponse
from bot.forecaster.retrieval import (
    ArticleFetcher,
    GdeltClient,
    RetrievalLedger,
    RetrievalRecord,
    WikipediaClient,
    _iso,
)

MAX_ARTICLES_FETCHED = 6
MAX_ARTICLE_CHARS = 1200
MAX_HEADLINES = 20

PLAN_SYSTEM = """\
You are a retrieval planner for a point-in-time forecasting system. You never
use tools. Answer ONLY with the requested JSON object — no prose.
"""

PLAN_PROMPT = """\
For the forecasting question below, produce retrieval parameters as JSON:

{{"gdelt_query": "<a GDELT DOC API query: 1-3 quoted phrases and/or OR groups,
  e.g. '\\"Nagorno-Karabakh\\" (\\"peace deal\\" OR treaty)'. Use the most
  distinctive entities; keep under 90 chars>",
 "wikipedia_titles": ["<up to 2 English Wikipedia article titles most likely
  to contain background/base-rate material for this question>"],
 "category": "<one of: us_election, non_us_election, econ_indicator,
  central_bank, geopolitics, policy_legal, company, science_tech,
  public_health, culture, other>",
 "entities": ["<up to 3 named people/organizations central to the question>"]}}

Question: {question}
Background: {background}
"""


@dataclass
class EvidencePack:
    question_key: str
    asof: str
    headlines: list[dict[str, str]] = field(default_factory=list)
    articles: list[dict[str, Any]] = field(default_factory=list)   # PASS only
    wikipedia: list[dict[str, Any]] = field(default_factory=list)
    structured: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    n_quarantined: int = 0
    n_gate1_dropped: int = 0

    def n_sources(self) -> int:
        return len(self.headlines) + len(self.articles) + len(self.wikipedia) \
            + sum(len(v) if isinstance(v, list) else 1 for v in self.structured.values())


def plan_retrieval(
    client: LLMClient, question: str, background: str,
) -> tuple[dict[str, Any], LLMResponse]:
    req = LLMRequest(
        model="haiku",
        system=PLAN_SYSTEM,
        prompt=PLAN_PROMPT.format(
            question=question.strip(), background=(background or "")[:800]
        ),
        params={"stage": "plan", "effort": "low"},
    )
    resp = client.complete(req)
    plan: dict[str, Any] = {}
    m = re.search(r"\{.*\}", resp.text, re.S)
    if m:
        try:
            plan = json.loads(m.group(0))
        except ValueError:
            plan = {}
    if not plan.get("gdelt_query"):
        # deterministic fallback: quoted capitalized entities from the title
        ents = re.findall(r"[A-Z][a-zA-Z0-9'.-]+(?: [A-Z][a-zA-Z0-9'.-]+)*",
                          question)
        ents = [e for e in ents if len(e) > 3][:2] or [question[:40]]
        plan["gdelt_query"] = " ".join(f'"{e}"' for e in ents)
        plan.setdefault("fallback", True)
    plan.setdefault("wikipedia_titles", [])
    plan.setdefault("category", "other")
    plan.setdefault("entities", [])
    return plan, resp


def gather_evidence(
    question_key: str,
    question: str,
    background: str,
    asof: datetime,
    ledger: RetrievalLedger,
    run_id: str,
    plan: dict[str, Any],
    gdelt: GdeltClient | None = None,
    fetcher: ArticleFetcher | None = None,
    wiki: WikipediaClient | None = None,
    gt: GroundTruthClient | None = None,
    fetch_bodies: bool = True,
    use_fec: bool = False,
) -> EvidencePack:
    """Run the full PIT retrieval for one question. Only ledger-PASS content
    enters the pack."""
    gdelt = gdelt or GdeltClient()
    fetcher = fetcher or ArticleFetcher()
    wiki = wiki or WikipediaClient()
    gt = gt or GroundTruthClient()
    pack = EvidencePack(question_key=question_key, asof=_iso(asof), plan=plan)

    # --- GDELT (GATE 1) ---
    try:
        arts, req_url, dropped = gdelt.artlist(str(plan["gdelt_query"]), asof)
    except RuntimeError:
        arts, req_url, dropped = [], "unavailable", 0
    pack.n_gate1_dropped = dropped
    for a in arts[:MAX_HEADLINES]:
        ledger.append(RetrievalRecord(
            run_id=run_id, question_key=question_key, decision_time=_iso(asof),
            source_kind="gdelt_doc", query=str(plan["gdelt_query"]),
            request_url=req_url, url=a.url, source_date=a.seendate,
            published_at=None, modified_at=None, content_sha256="",
            n_chars=len(a.title), verdict="PASS:headline_only",
        ))
        pack.headlines.append(
            {"seendate": a.seendate, "domain": a.domain, "title": a.title})

    # --- article bodies (GATE 2) ---
    if fetch_bodies:
        for a in arts[:MAX_ARTICLES_FETCHED]:
            f = fetcher.fetch(a.url, asof)
            rec = RetrievalRecord(
                run_id=run_id, question_key=question_key,
                decision_time=_iso(asof), source_kind="article_fetch",
                query=str(plan["gdelt_query"]), request_url=a.url,
                url=f.get("final_url", a.url), source_date=a.seendate,
                published_at=f.get("published_at"),
                modified_at=f.get("modified_at"),
                content_sha256=f.get("sha256", ""),
                n_chars=len(f.get("text", "")), verdict=f["verdict"],
            )
            ledger.append(rec)
            if f["verdict"] == "PASS" and f.get("text"):
                pack.articles.append({
                    "domain": a.domain, "title": a.title,
                    "published_at": f.get("published_at"),
                    "text": f["text"][:MAX_ARTICLE_CHARS],
                })
            elif f["verdict"].startswith("QUARANTINE"):
                pack.n_quarantined += 1

    # --- Wikipedia revision <= D ---
    for title in list(plan.get("wikipedia_titles") or [])[:2]:
        rev = wiki.rev_at(str(title), asof)
        if rev is None:
            continue
        ledger.append(RetrievalRecord(
            run_id=run_id, question_key=question_key, decision_time=_iso(asof),
            source_kind="wikipedia_rev", query=str(title),
            request_url=f"enwiki:revid={rev.get('revid')}",
            url=f"https://en.wikipedia.org/w/index.php?oldid={rev.get('revid')}",
            source_date=str(rev.get("timestamp")), published_at=None,
            modified_at=None, content_sha256="", n_chars=len(rev.get("content", "")),
            verdict="PASS",
        ))
        pack.wikipedia.append({
            "title": rev.get("title"), "revid": rev.get("revid"),
            "timestamp": rev.get("timestamp"),
            "extract": rev.get("content", "")[:6000],
        })

    # --- structured ground truth (the differentiator) ---
    cat = str(plan.get("category", "other"))
    if cat in ("us_election",):
        polls = gt.votehub_polls(asof, subject_terms=plan.get("entities") or None)
        if polls:
            ledger.append(RetrievalRecord(
                run_id=run_id, question_key=question_key,
                decision_time=_iso(asof), source_kind="votehub",
                query=json.dumps(plan.get("entities") or []),
                request_url="https://api.votehub.com/polls", url="votehub:polls",
                source_date=None, published_at=None, modified_at=None,
                content_sha256="", n_chars=len(json.dumps(polls)),
                verdict="PASS:date_gated_client_side",
            ))
            pack.structured["votehub_polls"] = [
                _slim_poll(p) for p in polls[:10]
            ]
        if use_fec:
            for ent in (plan.get("entities") or [])[:1]:
                rows = gt.fec_schedule_e(str(ent), asof)
                if rows:
                    ledger.append(RetrievalRecord(
                        run_id=run_id, question_key=question_key,
                        decision_time=_iso(asof), source_kind="fec",
                        query=str(ent),
                        request_url="https://api.open.fec.gov/v1/schedules/schedule_e/",
                        url="fec:schedule_e", source_date=None,
                        published_at=None, modified_at=None, content_sha256="",
                        n_chars=len(json.dumps(rows)),
                        verdict="PASS:filing_date_gated",
                    ))
                    pack.structured["fec_schedule_e"] = rows
    elif cat in ("econ_indicator", "central_bank"):
        obs = gt.alfred_vintage("CPIAUCSL", asof)
        if obs:
            pack.structured["alfred_cpiaucsl_vintage"] = obs[:12]
            ledger.append(RetrievalRecord(
                run_id=run_id, question_key=question_key,
                decision_time=_iso(asof), source_kind="alfred",
                query="CPIAUCSL", request_url="fred:series/observations",
                url="alfred:CPIAUCSL", source_date=None, published_at=None,
                modified_at=None, content_sha256="",
                n_chars=len(json.dumps(obs[:12])), verdict="PASS:vintage",
            ))
        else:
            pack.structured["alfred_note"] = (
                "no FRED_API_KEY registered — vintage data unavailable; "
                "latest-vintage sources are banned (leak)"
            )
    return pack


def _slim_poll(p: dict[str, Any]) -> dict[str, Any]:
    keep = {}
    for k in ("poll_type", "pollster", "created_at", "start_date", "end_date",
              "subject", "state", "sample_size", "answers", "candidates",
              "results", "approve", "disapprove"):
        if k in p and p[k] is not None:
            keep[k] = p[k]
    return keep or p


# ---------------------------------------------------------------------------
# The dossier call (Sonnet)
# ---------------------------------------------------------------------------

DOSSIER_SYSTEM = """\
You are a research analyst compressing point-in-time evidence into a dossier
for a forecasting desk. HARD RULES:
- Use ONLY the evidence provided in the message. Do NOT use tools. Do NOT add
  facts from memory beyond widely-known, pre-cutoff background.
- Treat TODAY as the AS-OF date given. You know nothing after it.
- Do not trust any evidence item that appears to describe events after the
  AS-OF date; flag it in one line and ignore it.
- Report honest negatives ("no polling found", "no direct evidence on X").
- Keep the dossier under 700 words. No preamble.
"""

DOSSIER_PROMPT = """\
AS-OF DATE: {asof}
QUESTION: {question}
RESOLUTION CRITERIA: {criteria}
BACKGROUND: {background}

EVIDENCE (retrieved under point-in-time discipline, all dated before AS-OF):

{evidence}

Write a 6-section dossier, exactly these headers:

## 1. Current state
## 2. Base rates
(name a reference class and, if possible, count it; say so if you cannot)
## 3. Key factors
## 4. Expert and market opinion
(include structured data — polls, filings, vintages — verbatim numbers here)
## 5. Strongest YES thesis
## 6. Strongest NO thesis
"""


def render_evidence(pack: EvidencePack) -> str:
    parts: list[str] = []
    if pack.structured:
        parts.append("### Structured ground-truth data (dated feeds)\n"
                     + json.dumps(pack.structured, indent=1)[:4000])
    if pack.wikipedia:
        for w in pack.wikipedia:
            parts.append(
                f"### Wikipedia: {w['title']} (revision {w['revid']} as of "
                f"{w['timestamp']})\n{w['extract'][:5000]}"
            )
    if pack.articles:
        for a in pack.articles:
            parts.append(
                f"### Article [{a['domain']}] {a['title']} "
                f"(published {a['published_at']})\n{a['text']}"
            )
    if pack.headlines:
        lines = [f"- {h['seendate'][:10]} [{h['domain']}] {h['title']}"
                 for h in pack.headlines]
        parts.append("### News headlines (GDELT, seendate-gated)\n"
                     + "\n".join(lines))
    if not parts:
        parts.append("(No point-in-time evidence could be retrieved for this "
                     "question. Say so in every section and rely on base "
                     "rates and background only.)")
    return "\n\n".join(parts)


def build_dossier(
    client: LLMClient,
    pack: EvidencePack,
    question: str,
    criteria: str,
    background: str,
    model: str = "sonnet",
) -> tuple[str, LLMResponse]:
    req = LLMRequest(
        model=model,
        system=DOSSIER_SYSTEM,
        prompt=DOSSIER_PROMPT.format(
            asof=pack.asof, question=question.strip(),
            criteria=(criteria or "(as titled)").strip()[:1200],
            background=(background or "(none)").strip()[:1200],
            evidence=render_evidence(pack),
        ),
        params={"stage": "dossier", "effort": "medium"},
    )
    resp = client.complete(req)
    return resp.text, resp

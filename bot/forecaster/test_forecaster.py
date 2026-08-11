"""Tests for the deterministic components of bot/forecaster: prompt
assembly, parsing, replay cache, retrieval date gates, aggregation."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bot.backtest.costs import CostLedger
from bot.forecaster import dossier as dz
from bot.forecaster import judge as jj
from bot.forecaster import prefilter as pf
from bot.forecaster import screen as sc
from bot.forecaster.llm import (
    CacheMiss,
    LLMClient,
    LLMRequest,
    LLMResponse,
    ReplayCache,
    ReplayCacheClient,
)
from bot.forecaster.pipeline import ForecastPipeline
from bot.forecaster.retrieval import (
    GdeltClient,
    LeakageError,
    RetrievalLedger,
    RetrievalRecord,
    _DiskCache,
    audit_ledger,
    extract_dates,
    parse_dt,
)

UTC = timezone.utc
D = datetime(2026, 3, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# llm.py
# ---------------------------------------------------------------------------

def test_cache_key_is_stable_and_param_sensitive():
    a = LLMRequest(model="haiku", system="s", prompt="p", params={"x": 1, "y": 2})
    b = LLMRequest(model="haiku", system="s", prompt="p", params={"y": 2, "x": 1})
    c = LLMRequest(model="haiku", system="s", prompt="p", params={"x": 1, "y": 3})
    d = LLMRequest(model="sonnet", system="s", prompt="p", params={"x": 1, "y": 2})
    assert a.cache_key() == b.cache_key()          # order-insensitive params
    assert a.cache_key() != c.cache_key()          # params matter
    assert a.cache_key() != d.cache_key()          # model matters


def test_replay_cache_roundtrip_and_miss(tmp_path: Path):
    cache = ReplayCache(tmp_path)
    req = LLMRequest(model="haiku", system="s", prompt="p")
    key = req.cache_key()
    cache.put(key, {"text": "hello", "model": "haiku", "usage": {"input_tokens": 3}})
    client = ReplayCacheClient(cache)
    resp = client.complete(req)
    assert resp.text == "hello" and resp.from_cache
    with pytest.raises(CacheMiss):
        client.complete(LLMRequest(model="haiku", system="s", prompt="other"))


def test_replay_cache_export_jsonl(tmp_path: Path):
    cache = ReplayCache(tmp_path / "c")
    req = LLMRequest(model="haiku", system="s", prompt="p")
    cache.put(req.cache_key(), {"text": "t", "usage": {}})
    out = tmp_path / "export.jsonl"
    n = cache.export_jsonl([req.cache_key(), "missing" * 8], out)
    assert n == 1
    row = json.loads(out.read_text().strip())
    assert row["key"] == req.cache_key() and row["text"] == "t"


# ---------------------------------------------------------------------------
# retrieval.py — date parsing, gates, audit
# ---------------------------------------------------------------------------

def test_parse_dt_formats():
    assert parse_dt("20260301T120000Z") == datetime(2026, 3, 1, 12, tzinfo=UTC)
    assert parse_dt("20260301120000") == datetime(2026, 3, 1, 12, tzinfo=UTC)
    assert parse_dt("2026-03-01T12:00:00Z") == datetime(2026, 3, 1, 12, tzinfo=UTC)
    assert parse_dt("2026-03-01T12:00:00+00:00") == datetime(2026, 3, 1, 12, tzinfo=UTC)
    assert parse_dt("2026-03-01") == datetime(2026, 3, 1, tzinfo=UTC)
    assert parse_dt("garbage") is None
    assert parse_dt(None) is None


def test_extract_dates_json_ld_and_meta():
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type": "NewsArticle", "datePublished": "2026-02-20T10:00:00Z",
     "dateModified": "2026-02-20T11:00:00Z"}
    </script></head><body></body></html>"""
    pub, mod = extract_dates(html)
    assert pub == "2026-02-20T10:00:00Z" and mod == "2026-02-20T11:00:00Z"

    html2 = """<meta property="article:published_time" content="2026-02-21T09:00:00Z">
    <meta property="article:modified_time" content="2026-02-22T09:00:00Z">"""
    pub2, mod2 = extract_dates(html2)
    assert pub2 == "2026-02-21T09:00:00Z" and mod2 == "2026-02-22T09:00:00Z"

    assert extract_dates("<html>no dates here</html>") == (None, None)


def _rec(**kw):
    base = dict(
        run_id="r", question_key="q", decision_time="2026-03-01T00:00:00Z",
        source_kind="article_fetch", query="q", request_url="u", url="u",
        source_date="2026-02-20T00:00:00Z", published_at="2026-02-19T00:00:00Z",
        modified_at=None, content_sha256="x", n_chars=10, verdict="PASS",
    )
    base.update(kw)
    return RetrievalRecord(**base)


def test_audit_passes_clean_ledger():
    assert audit_ledger([_rec()], hard_fail=True) == []


@pytest.mark.parametrize(
    "kw,violation",
    [
        (dict(source_date="2026-03-02T00:00:00Z"), "SOURCE_AFTER_D"),
        (dict(published_at="2026-03-01T00:00:00Z"), "PUBLISHED_AFTER_D"),
        (dict(modified_at="2026-03-05T00:00:00Z"), "MODIFIED_AFTER_D"),
        (dict(published_at=None), "UNDATED_SOURCE"),
        (dict(published_at="2026-02-25T00:00:00Z",
              source_date="2026-02-20T00:00:00Z"), "DATE_INCONSISTENT"),
        (dict(source_kind="web_search"), "BANNED_SOURCE"),
    ],
)
def test_audit_catches_each_violation(kw, violation):
    violations = audit_ledger([_rec(**kw)], hard_fail=False)
    assert violation in {v for v, _ in violations}
    with pytest.raises(LeakageError):
        audit_ledger([_rec(**kw)], hard_fail=True)


def test_audit_ignores_quarantined_rows():
    # a correctly-quarantined leaky row is NOT a violation — it never
    # reached the model
    r = _rec(published_at="2026-03-05T00:00:00Z",
             verdict="QUARANTINE:PUB_AFTER_ASOF")
    assert audit_ledger([r], hard_fail=True) == []


def test_gdelt_gate1_seendate_filter(tmp_path: Path):
    """GATE 1: articles with seendate >= asof are dropped client-side even
    when the server returned them inside the requested window."""
    cache = _DiskCache(tmp_path)
    asof = datetime(2026, 3, 1, tzinfo=UTC)
    params_key = json.dumps(
        ['"X"', (asof.replace(day=8) if False else asof).strftime("%Y%m%d%H%M%S"), ""],
    )
    # inject the cache entry the client would have written
    client = GdeltClient(cache=cache)
    start = "20260208000000"
    key = json.dumps(['"X"', start, "20260301000000"], sort_keys=True)
    cache.put("gdelt", key, {
        "request_url": "http://cached",
        "articles": [
            {"url": "u1", "title": "ok", "seendate": "20260228T230000Z",
             "domain": "a.com", "language": "English"},
            {"url": "u2", "title": "leak", "seendate": "20260301T003000Z",
             "domain": "b.com", "language": "English"},  # >= asof: dropped
            {"url": "u3", "title": "no date", "seendate": "",
             "domain": "c.com", "language": "English"},  # unparseable: dropped
        ],
    })
    kept, url, dropped = client.artlist('"X"', asof, lookback_days=21)
    assert [a.url for a in kept] == ["u1"]
    assert dropped == 2
    assert url == "http://cached"


def test_ledger_appends_and_reloads(tmp_path: Path):
    path = tmp_path / "ledger.jsonl"
    led = RetrievalLedger(path)
    led.append(_rec())
    led.append(_rec(question_key="q2"))
    rows = RetrievalLedger.load(path)
    assert len(rows) == 2 and rows[1].question_key == "q2"


# ---------------------------------------------------------------------------
# prefilter.py
# ---------------------------------------------------------------------------

class _Row:
    def __init__(self, q, p, freeze="2026-01-22T00:00:00+00:00",
                 close="2026-03-30T00:00:00+00:00"):
        self.question, self.market_p, self.freeze, self.close = q, p, freeze, close


def test_prefilter_forecastbench():
    rows = [
        _Row("Will X win the election?", 0.5),
        _Row("Will Bitcoin hit $200k?", 0.5),
        _Row("Will the Lakers win the NBA title?", 0.5),
        _Row("Extreme longshot", 0.01),
        _Row("Closes early", 0.5, close="2026-01-25T00:00:00+00:00"),
    ]
    res = pf.prefilter_forecastbench(rows)
    assert [r.question for r in res.kept] == ["Will X win the election?"]
    assert res.reject_counts == {
        "category:crypto": 1, "category:sports": 1,
        "price_band": 1, "too_close_to_close": 1,
    }


# ---------------------------------------------------------------------------
# screen.py
# ---------------------------------------------------------------------------

def test_screen_prompt_and_parse():
    prompt = sc.build_screen_prompt(["Q one?", "Q two?"])
    assert "1. Q one?" in prompt and "2. Q two?" in prompt
    text = '[{"i": 1, "pass": true, "reject_code": null}, {"i": 2, "pass": false, "reject_code": "sports"}]'
    v = sc.parse_screen_response(text, 2)
    assert v[0].passed and not v[1].passed and v[1].reject_code == "sports"


def test_screen_parse_failure_fails_closed():
    v = sc.parse_screen_response("I think they are all fine!", 3)
    assert all(not x.passed and x.parse_failed for x in v)
    # partial array: missing elements rejected
    v2 = sc.parse_screen_response('[{"i": 1, "pass": true}]', 2)
    assert v2[0].passed and not v2[1].passed and v2[1].parse_failed


# ---------------------------------------------------------------------------
# judge.py
# ---------------------------------------------------------------------------

def test_judge_parse_valid_integer_percent():
    text = 'reasoning...\n{"p_yes": 42, "key_uncertainties": ["a", "b"], "premortem": "x"}'
    p = jj.parse_judge_response(text, "s")
    assert p.p_yes == pytest.approx(0.42) and not p.parse_failed
    assert p.key_uncertainties == ("a", "b")


def test_judge_parse_clamps_to_rails():
    p = jj.parse_judge_response('{"p_yes": 99, "key_uncertainties": []}', "s")
    assert p.p_yes == pytest.approx(0.97)
    p2 = jj.parse_judge_response('{"p_yes": 1, "key_uncertainties": []}', "s")
    assert p2.p_yes == pytest.approx(0.03)


def test_judge_parse_float_probability():
    p = jj.parse_judge_response('{"p_yes": 0.25}', "s")
    assert p.p_yes == pytest.approx(0.25)


def test_judge_parse_failure_means_no_trade():
    for bad in ("no json at all", '{"p_yes": "forty"}', '{"p_yes": 150}',
                '{"other": 1}'):
        p = jj.parse_judge_response(bad, "s")
        assert p.p_yes is None and p.parse_failed, bad


def test_judge_parse_last_json_wins():
    text = ('draft {"p_yes": 10, "key_uncertainties": []} ... final: '
            '{"p_yes": 60, "key_uncertainties": ["u"]}')
    assert jj.parse_judge_response(text, "s").p_yes == pytest.approx(0.60)


def test_geometric_mean_of_odds():
    assert jj.geometric_mean_of_odds([0.5, 0.5]) == pytest.approx(0.5)
    # gmean of odds 1:3 and 3:1 -> odds 1 -> 0.5
    assert jj.geometric_mean_of_odds([0.25, 0.75]) == pytest.approx(0.5)
    # single pass passes through
    assert jj.geometric_mean_of_odds([0.2]) == pytest.approx(0.2)
    # pulls toward the extreme less than arithmetic mean
    g = jj.geometric_mean_of_odds([0.05, 0.5])
    assert 0.05 < g < 0.275


# ---------------------------------------------------------------------------
# dossier.py + pipeline.py with a fake client
# ---------------------------------------------------------------------------

class FakeClient(LLMClient):
    """Returns canned responses per stage; records requests."""

    def __init__(self):
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        stage = request.params.get("stage")
        if stage == "judge":
            stance = request.params.get("stance")
            p = 40 if stance == "outside_view_first" else 60
            text = f'{{"p_yes": {p}, "key_uncertainties": ["u1", "u2"], "premortem": "pm"}}'
        elif stage == "plan":
            text = json.dumps({"gdelt_query": '"Test"', "wikipedia_titles": [],
                               "category": "other", "entities": []})
        else:
            text = "## 1. Current state\n..."
        return LLMResponse(
            text=text, model=request.model, model_id="fake",
            usage={"input_tokens": 100, "output_tokens": 50},
            cache_key=request.cache_key(),
        )


def test_pipeline_ablation_mode(tmp_path: Path):
    led = RetrievalLedger(tmp_path / "ledger.jsonl")
    costs = CostLedger()
    fc = FakeClient()
    pipe = ForecastPipeline(fc, led, costs, run_id="t", judge_model="sonnet")
    res = pipe.forecast(
        question_key="k1", question="Will it rain?", background="",
        criteria="", asof="2026-03-01T00:00:00Z", close="2026-04-01",
        market_p=0.5, mode="ablation",
    )
    # gmean of odds of 40% and 60% = 50%
    assert res.p_yes == pytest.approx(0.5, abs=1e-9)
    assert res.mode == "ablation"
    assert res.cost_cents > 0                      # judge calls were charged
    assert len([r for r in fc.requests if r.params.get("stage") == "judge"]) == 2
    # ablation must not touch retrieval
    assert led.records == []
    # both stances got the market price line
    assert all("CURRENT MARKET PRICE: 50%" in r.prompt
               for r in fc.requests if r.params.get("stage") == "judge")


def test_pipeline_independent_passes_do_not_share_numbers(tmp_path: Path):
    fc = FakeClient()
    pipe = ForecastPipeline(fc, RetrievalLedger(tmp_path / "l.jsonl"),
                            CostLedger(), run_id="t")
    pipe.forecast(
        question_key="k", question="Q?", background="", criteria="",
        asof="2026-03-01T00:00:00Z", close="", market_p=None, mode="ablation",
    )
    judge_prompts = [r.prompt for r in fc.requests
                     if r.params.get("stage") == "judge"]
    assert len(judge_prompts) == 2
    # neither judge prompt contains the other's stance number or any p_yes
    assert all('"p_yes"' not in p for p in judge_prompts)


def test_render_evidence_empty_pack_says_so():
    pack = dz.EvidencePack(question_key="k", asof="2026-03-01T00:00:00Z")
    text = dz.render_evidence(pack)
    assert "No point-in-time evidence" in text


def test_dossier_prompt_assembly():
    pack = dz.EvidencePack(
        question_key="k", asof="2026-03-01T00:00:00Z",
        headlines=[{"seendate": "2026-02-25T00:00:00Z", "domain": "x.com",
                    "title": "headline A"}],
        structured={"votehub_polls": [{"pollster": "P", "created_at": "2026-02-20"}]},
    )
    fc = FakeClient()
    text, resp = dz.build_dossier(fc, pack, "Q?", "criteria", "bg")
    prompt = fc.requests[-1].prompt
    assert "headline A" in prompt
    assert "votehub_polls" in prompt
    assert "## 1. Current state" in prompt         # section skeleton requested
    assert "AS-OF DATE: 2026-03-01T00:00:00Z" in prompt


# ---------------------------------------------------------------------------
# strategy.py (rung 3)
# ---------------------------------------------------------------------------

def _view(t: int, bid: int, ask: int, ticker: str = "T-1"):
    from bot.backtest.types import Candle, MarketInfo, MarketView

    cand = Candle(ticker=ticker, start_ts=t - 3600, period_s=3600,
                  open=bid, high=ask, low=bid, close=(bid + ask) // 2,
                  volume=100, yes_bid_close=bid, yes_ask_close=ask)
    return MarketView(
        market=MarketInfo(ticker=ticker, close_ts=t + 20 * 86400),
        t=t, candles_by_period={3600: (cand,)},
    )


def test_forecaster_strategy_gate_and_sides():
    from bot.backtest.types import Portfolio
    from bot.forecaster.strategy import ForecasterStrategy, PrecomputedForecast

    fc = {"T-1": PrecomputedForecast("T-1", p_yes=0.60, asof_ts=1000,
                                     cost_cents=25)}
    pf_ = Portfolio(cash_cents=1_000_000)

    # before asof: nothing (forecast not usable yet), no charge
    s = ForecasterStrategy(fc, gate_cents=4)
    assert s.on_decision_point(_view(500, 50, 52), pf_) == []
    assert s.last_inference_cost_cents == 0

    # after asof, mid=51, p=60 -> edge +9 >= 4: rest YES at best bid; charge once
    intents = s.on_decision_point(_view(2000, 50, 52), pf_)
    assert s.last_inference_cost_cents == 25
    assert len(intents) == 1
    i = intents[0]
    assert i.side == "yes" and i.action == "buy" and i.tif == "rest"
    assert i.limit_price_cents == 50 and i.p_hat == 0.60

    # second decision: already quoted -> no new intent, no double charge
    assert s.on_decision_point(_view(3000, 50, 52), pf_) == []
    assert s.last_inference_cost_cents == 0


def test_forecaster_strategy_no_side():
    from bot.backtest.types import Portfolio
    from bot.forecaster.strategy import ForecasterStrategy, PrecomputedForecast

    fc = {"T-1": PrecomputedForecast("T-1", p_yes=0.30, asof_ts=1000,
                                     cost_cents=10)}
    s = ForecasterStrategy(fc, gate_cents=4)
    intents = s.on_decision_point(_view(2000, 50, 52), Portfolio(cash_cents=10**6))
    # mid=51, p=30 -> YES overpriced: buy NO at 100-ask=48
    assert intents[0].side == "no" and intents[0].limit_price_cents == 48


def test_forecaster_strategy_inside_gate_no_trade():
    from bot.backtest.types import Portfolio
    from bot.forecaster.strategy import ForecasterStrategy, PrecomputedForecast

    fc = {"T-1": PrecomputedForecast("T-1", p_yes=0.53, asof_ts=1000,
                                     cost_cents=10)}
    s = ForecasterStrategy(fc, gate_cents=4)
    # mid=51, p=53 -> edge 2 < 4: no trade (but cost is still charged)
    assert s.on_decision_point(_view(2000, 50, 52), Portfolio(cash_cents=10**6)) == []
    assert s.last_inference_cost_cents == 10

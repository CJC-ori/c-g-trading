# Forecasting benchmarks as eval infrastructure

*Research agent output, 2026-08-11. Audience: engineering agents building the bot in the next
few hours. Every load-bearing claim cites the URL or the local file it was verified from.
Anything I could not verify from a primary source is marked **[UNVERIFIED]**.*

**Scope of this doc:** which forecasting-eval datasets and scoring code we can run **locally,
today, with no API keys**, to benchmark a forecaster *before* we backtest on Kalshi market data;
and what "beating the market baseline" means numerically.

---

## 0. TL;DR — the decision table

| Asset | Runnable locally today? | What it gives us | Verdict |
|---|---|---|---|
| **ForecastBench datasets** ([repo](https://github.com/forecastingresearch/forecastbench-datasets)) | **YES.** 139 MB shallow clone, no keys, CC BY-SA 4.0 | 36 question sets + 33 resolution sets; 250 market + 250 dataset questions per set; market price at freeze time is a field; **1,216 resolved market questions with `forecast_due_date ≥ 2026-02-01`** (post-cutoff, contamination-clean) | **Use as the primary pre-backtest eval.** Already cloned & verified below. |
| **ForecastBench leaderboards (CSV)** ([dir](https://github.com/forecastingresearch/forecastbench-datasets/tree/main/leaderboards/csv)) | **YES**, 152 KB | Published Brier scores for ~124 baseline models / 273 tournament entries, **including explicit market-price baselines** | **Use for the "what does good look like" numbers.** |
| **ForecastBench scoring code** ([repo](https://github.com/forecastingresearch/forecastbench)) | Partly — scoring math is reproducible in ~40 lines; the full pipeline needs GCP | Exact definitions of Brier Index, peer score, BSS, imputation | **Reimplement the 40 lines; do not run their pipeline.** |
| **Metaculus AIB / FutureEval** | **NO** — `www.metaculus.com/api/...` returns **HTTP 403** from this container (verified) and needs a `METACULUS_TOKEN` | Live tournament, spot-peer scoring against pros | Blocked today. Needs a free token + a bot account. |
| **`forecasting-tools` `Benchmarker`** ([repo](https://github.com/Metaculus/forecasting-tools)) | Code yes, data no (needs Metaculus API) | Scores a bot against the *community prediction*, not against resolution | Useful library; **its default metric structurally cannot reward beating the crowd** — see §2.3. |
| **PredictionMarketBench** ([repo](https://github.com/Oddpool/PredictionMarketBench)) | **YES.** 64 MB clone, MIT, 4 real Kalshi episodes with orderbook + trade tape + settlement | A working Kalshi replay harness with maker/taker queue sim and the Oct-2025 fee schedule | **Read it before writing our own harness (P3).** Episodes are sports/BTC/weather — wrong markets for us, right code. |
| **PolyBench** ([repo](https://github.com/PolyBench/PolyBench)) | Code yes (2.8 MB clone); **dataset is a OneDrive link** — not verified downloadable here | 38,666 Polymarket binary markets + CLOB + news, Feb 6–12 2026 | Code is a useful reference; treat the data as **[UNVERIFIED]** until someone pulls the OneDrive. |
| **KalshiBench** ([HF](https://huggingface.co/datasets/2084Collective/kalshibench-v2)) | YES, 200 KB parquet, downloaded and inspected | 1,531 Kalshi questions with yes/no ground truth | **Skip.** `market_probability` column is **100% null** (0/1531 non-null) and `close_time` maxes out at 2025-11-16 → no market baseline *and* pre-cutoff. |

**The one-line answer to the task:** clone
`forecastingresearch/forecastbench-datasets`, join `datasets/question_sets/*-llm.json` to
`datasets/resolution_sets/*_resolution_set.json`, filter to
`source ∈ {polymarket, metaculus, manifold, infer}` and `forecast_due_date ≥ 2026-02-01`, and
you have **1,216 resolved, post-training-cutoff, market-priced questions** whose market baseline
Brier is **0.1172 (95% CI [0.107, 0.127])**. That is the number to beat, and with n=1,216 the
smallest Brier improvement you can prove at p<0.05 is about **0.004** (empirically measured
paired-difference SD, §4.3).

---

## 1. ForecastBench

Paper: [ForecastBench: A Dynamic Benchmark of AI Forecasting Capabilities](https://arxiv.org/abs/2409.19839)
(Karger, Bastani, Chen, Jacobs, Halawi, Zhang, Tetlock; ICLR 2025). Abstract, verbatim from
arXiv: *"expert forecasters outperform the top-performing LLM (p-value <0.001)."*

Two repos:
- Code: <https://github.com/forecastingresearch/forecastbench>
- Data, **updated nightly**: <https://github.com/forecastingresearch/forecastbench-datasets>
  (`README.md` states the datasets are CC BY-SA 4.0)

### 1.1 How to download (verified working from this container)

```bash
# 139 MB, ~90s, no auth. github.com API is blocked by our proxy; git clone and
# raw.githubusercontent.com both work.
GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 \
  https://github.com/forecastingresearch/forecastbench-datasets \
  /workspace/forecastingresearch/forecastbench-datasets
```

Already cloned in this session at
`/workspace/forecastingresearch/forecastbench-datasets`.

Single-file pulls also work if you want to stay small (verified HTTP 200):

```
https://raw.githubusercontent.com/forecastingresearch/forecastbench-datasets/main/datasets/question_sets/2026-08-02-llm.json   # 1.37 MB
https://raw.githubusercontent.com/forecastingresearch/forecastbench-datasets/main/datasets/resolution_sets/2026-07-19_resolution_set.json  # 114 KB
https://raw.githubusercontent.com/forecastingresearch/forecastbench-datasets/main/leaderboards/csv/leaderboard_tournament.csv  # 52 KB
```

Tree (verified, 82 tracked files):

```
datasets/question_sets/YYYY-MM-DD-llm.json      36 files, 88 MB total   (500 questions each in 2026)
datasets/question_sets/2024-07-21-human.json    1 file  (200 q, the human-comparison set)
datasets/resolution_sets/YYYY-MM-DD_resolution_set.json  33 files, 38 MB
datasets/forecast_sets/2024-07-21/*.json        only the 2024 human forecasts are published
leaderboards/csv/{baseline,tournament,dataset,preliminary}.csv
leaderboards/html/…
```

Note: **individual LLM/bot forecast files are not published** — only the 2024-07-21 human
forecast sets. So we cannot re-score other people's bots locally; we can only score *ours* and
compare to the published leaderboard aggregates.

### 1.2 Question-set format (verified against `2026-03-01-llm.json`)

```json
{
  "forecast_due_date": "2026-03-01",
  "question_set": "2026-03-01-llm.json",
  "questions": [
    {
      "id": "SEIqqlqg8L",
      "source": "manifold",
      "question": "Will Polyoptions volume hit $100 million in 2026?",
      "resolution_criteria": "Resolves to the outcome of the question found at https://…",
      "background": "…long text…",
      "market_info_open_datetime": "2025-12-11T22:21:38+00:00",
      "market_info_close_datetime": "2026-12-31T23:59:00+00:00",
      "market_info_resolution_criteria": "N/A",
      "url": "https://manifold.markets/FlipPidot/…",
      "freeze_datetime": "2026-02-19T00:00:00+00:00",
      "freeze_datetime_value": "0.28297104550941504",
      "freeze_datetime_value_explanation": "The market value.",
      "source_intro": "We would like you to predict the outcome of a prediction market…",
      "resolution_dates": "N/A"
    }
  ]
}
```

Key facts, all verified from the files:

- **`freeze_datetime_value` on a market question IS the market price** (the explanation string is
  literally `"The market value."`). `freeze_datetime` is 10 days before `forecast_due_date`
  (e.g. 2026-02-19 for the 2026-03-01 set). **This is our free, offline market-consensus
  baseline.**
- For **dataset** questions (`acled`, `fred`, `yfinance`, `wikipedia`, `dbnomics`),
  `freeze_datetime_value` is a *reference statistic*, not a probability, and `resolution_dates`
  is a list of up to 8 horizons (verified example: `["2026-03-08","2026-03-31","2026-05-30",
  "2026-08-28","2027-03-01","2029-02-28","2031-02-28","2036-02-27"]`).
- **Source mix in the 2026 sets** (verified, `2026-03-01`): manifold 72, metaculus 72,
  polymarket 71, infer 35 → **250 market**; acled/dbnomics/fred/wikipedia/yfinance 50 each →
  **250 dataset**. The [submission wiki](https://github.com/forecastingresearch/forecastbench/wiki/How-to-submit-to-ForecastBench)
  confirms "500 questions: 250 market and 250 dataset questions".
- **No Kalshi.** The market sources are Manifold, Metaculus, Polymarket, INFER. For us,
  **Polymarket is the directly transferable one** (79 questions in the current 2026-08-02 set).
- **Combination questions are gone.** The 2024-07-21 LLM set had 499/1000 combination questions
  (paired `id` lists with `direction` ∈ {[1,1],[1,-1],[-1,1],[-1,-1]}); the 2026 sets have
  **zero** (verified: `combos = 0` for 2026-03-01). Don't write combo-handling code.

### 1.3 Resolution-set format (verified)

```json
{
  "forecast_due_date": "2026-03-01",
  "question_set": "…",
  "resolutions": [
    {"id": "e274f8a2…", "source": "acled", "direction": null,
     "resolution_date": "2026-03-08", "resolved_to": 0.0, "resolved": true}
  ]
}
```

- Join key is **`(str(id), source, resolution_date)`**. Market questions get exactly one row
  (`resolution_date` = the market's close date); dataset questions get one row per horizon that
  has come due.
- `resolved_to` for a **resolved market question is 0.0 or 1.0** (verified: 2,439 zeros / 709
  ones across all resolved market rows). For an **unresolved** market question `resolved_to`
  holds the *current market value* and `resolved` is `false` — **you must filter on
  `resolved == true` or you will score against prices, not outcomes.** This is the single
  easiest way to silently corrupt the eval.
- Files are nightly-refreshed, so counts grow. As of this clone: 2026-03-01 set had 851/929 rows
  resolved.

### 1.4 The scoring math (read from `src/leaderboard/main.py`, so it's exact)

Everything below is from the cloned code at
`/workspace/forecastingresearch/forecastbench/src/leaderboard/main.py` and
`src/resolve/_impute.py`.

**Brier.** `df["brier_score"] = (forecast - resolved_to) ** 2`. Plain, unweighted.

**Difficulty adjustment (`two_way_fixed_effects`).** Fit `brier_score ~ 1 | question_pk + model_pk`
(pyfixest OLS with two-way fixed effects), take the question fixed effect `b_j`, and report
`brier_ij − b_j`. Then rescale all columns by `+ (0.25 − score_of_Always_0.5)`.

> **Algebraic consequence worth knowing:** the rescale exactly undoes the difficulty adjustment
> for any model that answered the common question set — `mean(B) − mean(b) + mean(b) = mean(B)`.
> The FE step exists only to make models with *different question coverage* comparable. I
> verified this numerically: for 60 leaderboard rows × 3 columns, **0 mismatches** against
> `Index = (1 − sqrt(Brier)) × 100`.

**Brier Index (the 0–100 number in the "Dataset"/"Market"/"Overall" columns):**

```
Brier Index = (1 − sqrt(difficulty_adjusted_brier)) × 100
# 100 = perfect, 50 = always 0.5, 0 = maximally wrong. Higher is better.
```

Quick conversions you will want: Brier 0.05 → 77.6; 0.077 → 72.2; 0.10 → 68.4;
0.117 → 65.8; 0.15 → 61.3; 0.25 → 50.0.

**Peer score:** `question_avg_brier − brier` (mean over models on that question minus yours;
positive = better than average).

**BSS:** `ref_brier − brier` where the reference model is the **Naive Forecaster**. Note this is
the *absolute difference* form, not the usual `1 − B/B_ref` ratio.

**The market baselines — this is the important part.** From `src/resolve/_impute.py`:

```python
"""Fill in np.nan forecast values with context-appropriate forecasts.
- Default imputation: 0.5
- Imputed Forecaster: market_value_on_due_date
- Naive Forecaster: market_value_on_due_date_minus_one
"""
```

The **Naive Forecaster** and **Imputed Forecaster** submit *nothing* on market questions, so
every market question is imputed with the market price — they **are** the market-consensus
baseline. (On dataset questions the Naive Forecaster runs a Prophet time-series extrapolation,
`src/base_eval/naive_and_dummy_forecasters/main.py`; the Imputed Forecaster just plays 0.5.)

Also relevant: for the **tournament leaderboard** the market-question difficulty adjustment is
`MarketQuestionAdjustment.MARKET_BRIER`, where the question fixed effect is *literally the
market's own Brier score*. So the tournament "Market" column is, per question,
`your_brier − market_brier`, rescaled. **ForecastBench's market column already is a
beat-the-market metric.**

Finally: **models with >5% imputed forecasts are dropped from the leaderboard**
(`IMPUTED_CUTOFF_PCT = 5`), and the submission wiki requires ≥95% coverage of both question types.

### 1.5 The published baseline numbers (from `leaderboards/csv/`, nightly as of this clone)

`leaderboard_baseline.csv` — the "how do LLMs stack up" board, 124 rows:

| Rank | Model | Dataset | Market | Overall | N | Brier Dataset | **Brier Market** | Brier Overall |
|---|---|---|---|---|---|---|---|---|
| 1 | **Superforecaster median forecast** | 63.9 | 73.0 | 68.1 | 578 | 0.131 | **0.073** | 0.102 |
| 2 | **Public median forecast** | 59.4 | 66.8 | 62.9 | 578 | 0.165 | **0.110** | 0.137 |
| 3 | o3-2025-04-16-scratchpad | 60.5 | 63.6 | 62.0 | 1560 | 0.156 | 0.132 | 0.144 |
| 4 | gpt-5.5-2026-04-23 | 60.6 | 61.9 | 61.3 | 1753 | 0.155 | 0.145 | 0.150 |
| 5 | claude-opus-4-1-20250805 | 60.3 | 62.1 | 61.2 | 12836 | 0.157 | 0.143 | 0.150 |
| **37** | **Imputed Forecaster** *(= market price on due date)* | 50.0 | **72.2** | 59.6 | 35148 | 0.250 | **0.077** | 0.164 |
| **59** | **Naive Forecaster** *(= market price, due date − 1)* | 48.2 | **72.1** | 58.4 | 35148 | 0.269 | **0.078** | 0.173 |
| 98 | LLM Crowd (gpt-4o + claude-3.5-sonnet + gemini-1.5-pro, geo-mean, with news) | 50.9 | 60.6 | 55.5 | 1508 | 0.242 | 0.155 | 0.198 |
| 118 | Always 0.5 | 50.0 | 50.0 | 50.0 | 35150 | 0.250 | 0.250 | 0.250 |
| 122 | Random Uniform | 42.2 | 42.0 | 42.1 | 35150 | 0.335 | 0.337 | 0.336 |

**Read this table carefully — it is the most important result in this document.**

> **On ForecastBench's baseline leaderboard, no LLM beats the market price on market questions.**
> The best LLM market Brier is ~0.132 (o3-scratchpad); the market price itself scores **0.077**.
> That is a gap of **0.055 Brier**, i.e. the market is *massively* better than an unaided
> frontier LLM at pricing its own questions. Only superforecasters (0.073) edge it out.

The **tournament** leaderboard (`leaderboard_tournament.csv`, 273 rows) is the one where
purpose-built bots appear, and there the picture is different:

| Rank | Team / Model | Market Index | N market | **Brier Market** | Overall |
|---|---|---|---|---|---|
| 1 | Torchcast AI `rice-demon` | 73.0 | 103 | **0.072** | 69.1 |
| 1 | **Superforecaster median** | 75.6 | 57 | **0.060** | 69.1 |
| 5 | Cassi-AI `Cassi-2026-05-10` | 76.6 | 102 | **0.055** | 68.7 |
| 5 | Google DeepMind `fire hedgehog` | 73.3 | 103 | 0.070 | 68.7 |
| 20 | FutureSearch `fb_forecaster_v2` | 70.7 | 103 | 0.086 | 66.9 |
| 25 | Artificial Judgement `aj-v1` | 73.5 | 504 | 0.065 | 66.8 |
| 111 | Public median forecast | 68.9 | 57 | 0.097 | 63.7 |
| 195 | **Imputed Forecaster (market price)** | 72.3 | 35148 | **0.077** | 59.6 |
| 212 | **Naive Forecaster (market price)** | 72.1 | 35148 | **0.078** | 58.4 |

So several 2026 bots (Cassi 0.055, Torchcast 0.072, Artificial Judgement 0.065) *do* post market
Briers below the market baseline's 0.077 — but on **n = 55–500 questions vs the baseline's
35,148**, over different (recent) question sets. **Do not read these as a clean head-to-head.**
The apples-to-apples statement that survives is: *beating the market on market questions is
possible in 2026, by a few tenths of a Brier point, with a purpose-built pipeline, and it was
not possible for a bare frontier model.*

### 1.6 What I actually computed locally (reproducible, no keys)

I joined every question set to its resolution set and scored the market's own freeze-time price.
**Market questions only, `resolved == true`.**

Market-price (freeze-time, i.e. T−10d) Brier by forecast-due-date cohort:

| due date | n resolved | market Brier | | due date | n resolved | market Brier |
|---|---|---|---|---|---|---|
| 2024-07-21 | 124 | 0.1017 | | 2026-02-01 | 57 | 0.0613 |
| 2025-06-08 | 95 | 0.0758 | | 2026-02-15 | 61 | 0.0575 |
| 2025-08-31 | 89 | 0.0584 | | 2026-03-01 | 130 | 0.1156 |
| 2025-11-09 | 108 | 0.0340 | | 2026-03-29 | 127 | 0.1506 |
| 2025-12-21 | 107 | 0.0176 | | 2026-04-26 | 105 | 0.1522 |
| 2026-01-04 | 63 | 0.0776 | | 2026-06-07 | 103 | 0.0995 |
| 2026-01-18 | 62 | 0.0641 | | 2026-07-19 | 40 | 0.1148 |

**Pooled, `forecast_due_date ≥ 2026-02-01` (our contamination-safe window):**

```
n = 1,216 resolved market questions
market-price Brier = 0.1172   sd = 0.1785   se = 0.0051   95% CI [0.1072, 0.1273]
```

By source:

| source | n | base rate | market Brier | market log-loss |
|---|---|---|---|---|
| infer | 102 | 0.206 | **0.0651** | 0.2171 |
| manifold | 357 | 0.401 | 0.0963 | 0.3110 |
| polymarket | 534 | 0.382 | **0.1253** | 0.3878 |
| metaculus | 223 | 0.305 | 0.1554 | 0.4765 |

> Note this pooled 0.1172 is **higher (worse) than the leaderboard's 0.077**. Two reasons:
> (a) the leaderboard baseline uses the market value on the *forecast due date*, 10 days later
> and better-informed than the `freeze_datetime_value` we have offline; (b) our subset is only
> questions that have already resolved in the 2026 window, which skews short-horizon and
> excludes the long-dated near-certainties that pad the baseline's 35k rows. **Use 0.1172 as
> *our* internal bar** — it is the number our own code produces on our own subset, and it is the
> apples-to-apples comparison for any forecaster we score the same way.

**Calibration of the market price at T−10d** (2026-02-01+, all 1,216):

| price bucket | n | mean price | realized freq |
|---|---|---|---|
| [0.00, 0.05) | 230 | 0.022 | 0.009 |
| [0.05, 0.15) | 173 | 0.100 | **0.035** |
| [0.15, 0.35) | 247 | 0.239 | **0.186** |
| [0.35, 0.65) | 239 | 0.495 | 0.435 |
| [0.65, 0.85) | 166 | 0.739 | 0.759 |
| [0.85, 0.95) | 79 | 0.898 | 0.911 |
| [0.95, 1.01) | 82 | 0.972 | 0.976 |

That is a textbook **longshot bias** in the 5–35¢ band (10¢ prices resolve YES 3.5% of the time;
24¢ prices resolve YES 18.6%). **But see §1.7 — it does not survive out-of-sample.**

Loss concentration: **the top 5% of questions carry 29% of the total Brier loss; the top 20%
carry 72%.** Whatever we build, its measured skill will be dominated by a handful of surprises.
Plan the variance analysis accordingly.

### 1.7 A negative result you should not have to rediscover

I fit a one-parameter-pair logistic recalibration of the market price on the pre-2026 data
(train n=1,932) and tested it on the 2026-02-01+ window (test n=1,216):

```
fitted:  p_adj = sigmoid(-0.5189 + 1.1466 * logit(p))     # i.e. "shade longshots down, sharpen"
TRAIN  market 0.0633 -> recalibrated 0.0606   (delta +0.0027, helps)
TEST   market 0.1172 -> recalibrated 0.1178   (delta -0.0005, hurts)
paired t = -0.26   (paired-difference sd = 0.0698)
```

**Recalibrating the aggregate market price for longshot bias does not transfer out of sample.**
The in-sample gain is real and the out-of-sample gain is zero. Any price-only strategy built on
"markets overprice longshots" must be validated on a *time-forward* split before anyone believes
it. (Caveat: this is Manifold/Metaculus/Polymarket/INFER at a 10-day horizon, not Kalshi at a
1-day horizon. It does not refute a Kalshi-specific longshot effect — it does refute the lazy
version of it.)

That paired-difference SD of **0.0698** is the most useful number here; see §4.3.

### 1.8 Runnable recipe (this exact code produced every number above)

```python
import json, glob, os, math, statistics
BASE = "/workspace/forecastingresearch/forecastbench-datasets"
MARKET = {"manifold", "metaculus", "polymarket", "infer"}

def load(min_due="2026-02-01", market_only=True, resolved_only=True):
    rows = []
    for qf in sorted(glob.glob(f"{BASE}/datasets/question_sets/*-llm.json")):
        due = os.path.basename(qf).replace("-llm.json", "")
        if due < min_due:
            continue
        rf = f"{BASE}/datasets/resolution_sets/{due}_resolution_set.json"
        if not os.path.exists(rf):
            continue
        Q = {(str(q["id"]), q["source"]): q
             for q in json.load(open(qf))["questions"]
             if not isinstance(q["id"], list)}          # skip legacy combo questions
        for r in json.load(open(rf))["resolutions"]:
            k = (str(r["id"]), r["source"])
            if k not in Q:                              continue
            if market_only and r["source"] not in MARKET: continue
            if resolved_only and not r["resolved"]:     continue   # <-- MUST filter
            try:
                p = float(Q[k]["freeze_datetime_value"])           # the market price at T-10d
            except (TypeError, ValueError):
                continue
            rows.append(dict(due=due, src=r["source"], qid=k[0], q=Q[k]["question"],
                             background=Q[k]["background"], criteria=Q[k]["resolution_criteria"],
                             url=Q[k]["url"], freeze=Q[k]["freeze_datetime"],
                             close=Q[k]["market_info_close_datetime"],
                             market_p=p, y=float(r["resolved_to"]),
                             resolution_date=r["resolution_date"]))
    return rows

def brier(rows, key):        return statistics.mean((r[key] - r["y"]) ** 2 for r in rows)
def brier_index(b):          return (1 - math.sqrt(b)) * 100          # ForecastBench 0-100 scale

def paired_vs_market(rows, key="my_p"):
    d = [(r["market_p"] - r["y"])**2 - (r[key] - r["y"])**2 for r in rows]   # >0 = we beat market
    m, s = statistics.mean(d), statistics.stdev(d)
    se = s / math.sqrt(len(d))
    return dict(n=len(d), delta=m, se=se, t=m/se, ci=(m-1.96*se, m+1.96*se))
```

To feed our forecaster: each row already carries `question`, `background`,
`resolution_criteria`, `url`, and `close`. **Point-in-time discipline:** the forecaster may use
information up to `freeze` (`freeze_datetime`, = due date − 10 days) and nothing after. Retrieval
must be date-bounded to `freeze`, or the eval is worthless.

### 1.9 Contamination discipline (maps to ORCHESTRATION.md constraint #2)

Training cutoff Jan 2026 → use **`forecast_due_date ≥ 2026-02-01`**. That gives 14 question sets
(2026-02-01 … 2026-08-02), of which 13 have resolutions today. Even so:

- `freeze_datetime` for the 2026-02-01 set is 2026-01-22 — *before* the cutoff. If you want a
  hard margin, start at **2026-03-01** (freeze 2026-02-19); you lose 118 questions and keep
  ~1,098.
- The 2026-08-02 set (freeze 2026-07-23) is the **currently open** set: 250 market questions
  including 79 Polymarket with close dates 2026-08-04 → 2027-07-01. That is a *live, honest,
  zero-cost* forward test we can start today: forecast them now, score them as they resolve, no
  API keys required.

### 1.10 Limitations to state out loud in any writeup

1. **No Kalshi.** Transfer from Manifold/Metaculus/INFER to Kalshi is an assumption, not a
   result. Polymarket (534 of 1,216 resolved) is the closest proxy.
2. **Manifold is play money and Metaculus is not a market.** Their "prices" are weaker than a
   real-money orderbook mid. Our per-source table shows exactly that (INFER 0.065 vs Metaculus
   0.155 — though base rates and question difficulty differ too).
3. **Resolved-only selection.** Long-dated questions are excluded by construction, which biases
   toward fast-resolving events. Report `n` and the due-date cohort every time.
4. **Brier ≠ P&L.** ForecastBench has no prices-over-time, no orderbook, no fees. It tells you
   whether the forecaster is any good; it cannot tell you whether the strategy makes money. That
   is what P2/P3 are for.

---

## 2. Metaculus AI Benchmark / FutureEval

### 2.1 What it is

Metaculus runs a $50,000 bot tournament three times a year (Fall/Spring/Summer), rebranded
**FutureEval** in Feb 2026. The
[Summer 2026 announcement](https://forum.effectivealtruism.org/posts/ZfLAN557rGWACKtmc/announcing-metaculus-summer-2026-futureeval-bot-tournament)
gives: starts **May 18 2026**, questions stop opening a few weeks before **Sept 1 2026**,
**$50,000** prize pool, **300–500 questions**, newcomers join any time and "start at the middle
of the leaderboard with 0 points".

### 2.2 Scoring

Metaculus's own definitions (from [scores FAQ](https://www.metaculus.com/help/scores-faq/);
the page 403s to our fetcher, so these formulas are from search-result extraction of that page —
**[UNVERIFIED against the live page]**, though they match the `forecasting-tools` source code
in §2.3):

- **Baseline score:** `MS = 100 · log₂(p_o / 0.5) = 100 · (log₂ p_o + 1)`, where `p_o = p` if the
  outcome was YES and `1−p` if NO. Perfect = +100, always-50% = 0.
- **Peer score:** `MPS = 100 · (ln p − ln GM(q_i))` — 100× the mean difference between your log
  score and every other forecaster's on that question. Zero-sum across participants;
  difficulty-adjusted by construction.
- **Spot** variants score only the prediction at one specified instant, ignoring how long you
  held it. **The bot tournaments use spot peer score** — bots forecast once per question.
- **Head-to-head score** between two teams:
  `100 × ln(team_A_prediction / team_B_prediction)`, per the
  [Q2 results writeup](https://forum.effectivealtruism.org/posts/F2stjK9wHSy3HPEC9/q2-ai-benchmark-results-pros-maintain-clear-lead).

**Can we run their scoring locally?** The formulas, yes — they're four lines. The *data*, no:
peer score is defined relative to every other participant's forecast on the same question, and
those forecasts live behind the Metaculus API.

### 2.3 `forecasting-tools` — the local benchmarker, and its trap

`Metaculus/forecasting-tools` (cloned to `/workspace/Metaculus/forecasting-tools`) ships
`forecasting_tools/cp_benchmarking/benchmarker.py`. Its own docstring:

> *"This class is used to benchmark a list of forecast bots by comparing their predictions to the
> community prediction on a set of questions. … TLDR: 100-200 questions is a decent starting
> point, but 500+ would be ideal. Lower than 100 can differentiate between bots of large skill
> differences, but not between bots of small skill differences. But even with 100 there is ~30%
> of the 'worse bot' winning if there are not large skill differences."*

The metric, from `forecasting_tools/data_models/binary_report.py`:

```python
@property
def expected_baseline_score(self) -> float | None:
    c = self.community_prediction
    p = self.prediction
    return 100.0 * (c * (np.log2(p) + 1.0) + (1.0 - c) * (np.log2(1.0 - p) + 1.0))
```

**This is the Metaculus baseline score in expectation *under the assumption that the community
prediction is the truth*.** It is maximized at `p = c`. **A bot that beats the crowd scores
*worse* on this metric than a bot that copies it.**

> **Directive for the forecaster team:** `forecasting-tools`' `Benchmarker` is fine for
> regression-testing "did my prompt change break the pipeline" and for cheap iteration without
> waiting for resolutions. It is **structurally incapable** of measuring the thing we care about
> (edge vs. the market). Do not adopt `average_expected_baseline_score` as our gate. Use
> ForecastBench §1 for the real gate. The same file also exposes `deviation_points`
> (`|p − c|`) which is at least an honest measure of *how much* we disagree with the crowd —
> useful as a diagnostic, since §4.1 shows expected edge scales with the square of that.

The library's genuinely useful parts: `MetaculusApi.get_benchmark_questions(n)`, cost tracking
(`MonetaryCostManager` — matches ORCHESTRATION constraint #3), report/aggregation plumbing, and
a `ForecastBot` base class we can subclass.

### 2.4 `metac-bot-template` mechanics

<https://github.com/Metaculus/metac-bot-template> (cloned to `/workspace/Metaculus/metac-bot-template`).
Two files: `main.py` (uses `forecasting-tools`) and `main_with_no_framework.py` (minimal deps —
**read this one**, it is the clearest short reference for "call an LLM, get a probability, post
it"). Mechanics per its README:

- Fork → set repo secrets `METACULUS_TOKEN` + `OPENROUTER_API_KEY` → enable Actions. Workflows:
  `test_bot.yaml` (manual, against the bot-testing-area tournament),
  `run_bot_on_tournament.yaml` (**every 20 minutes** on live AIB + MiniBench),
  `run_bot_on_metaculus_cup.yaml` (every 2 days).
- Dry-run: `publish_reports_to_metaculus=False` in `main.py`, or `SUBMIT_PREDICTION = False` in
  the no-framework file.
- Local: `poetry install`, `poetry run python main.py --mode test_questions`.

### 2.5 Environment blocker (verified today)

```
GET https://www.metaculus.com/api/posts/?limit=1   -> 403
GET https://www.metaculus.com/api2/questions/?limit=1 -> 403
GET https://www.metaculus.com/aib/  (WebFetch)     -> 403
```

The agent proxy is healthy (`__agentproxy/status` shows no relay failures) — Metaculus itself is
blocking unauthenticated/bot traffic. **We cannot pull Metaculus questions or community
predictions from this container without a `METACULUS_TOKEN`.** Tokens are free at
<https://www.metaculus.com/futureeval/participate/>. Treat this as a 5-minute human task for
Chris, not a blocker for tonight's build.

### 2.6 Where bots actually stand (this contradicts our README)

- Q2 2025 tournament: bot team vs pro team head-to-head **−20.03 [95% CI −28.63, −11.41]**,
  p = 0.00001, on 93 overlapping questions of 348 total. By type: binary −14.8 (55 q), numeric
  −23.2 (21 q), multiple-choice −32.9 (17 q). Winner: Panshul42 (sum peer 5,899).
  ([source](https://forum.effectivealtruism.org/posts/F2stjK9wHSy3HPEC9/q2-ai-benchmark-results-pros-maintain-clear-lead))
- Trend of bot-vs-pro head-to-head: Q3 2024 −11.3 → Q4 2024 −8.9 → Q1 2025 −17.7 → Q2 2025 −20.0.
  **Not converging.**
- As of 2026: *"head-to-head spot peer scores put[ting] Pros in the lead each quarter by a large
  margin"* and *"as of May 2026, Pros beat individual bots, though bots are making consistent
  forward progress."* In the Spring 2026 Cup, `metac-claude-4-5-sonnet-high-32k+asknews` placed
  **33rd of 1,130 humans** (top ~3%). *(These are from search-result extraction of
  metaculus.com/aib/2026/spring/ and an EA Forum roundup that both 403'd our fetcher —
  **[UNVERIFIED against primary source]**.)*

> **Correction to `README.md`:** the repo currently says *"Best AIs and top human forecasters are
> now in a statistical dead heat on Metaculus."* Every 2026 source I could reach says the
> opposite — **pros still lead bots by a large margin on Metaculus**, and the gap has been flat
> or widening since Q4 2024. The "dead heat" framing should be struck or heavily qualified. The
> honest version is: *top bots are ~top-3% of the public crowd, and still clearly behind pros.*
> Useful survey finding from the same writeup, if we want cheap wins: aggregating multiple
> forecasts was worth **+1,799 points** on average, testing on custom questions **+2,216**, and
> manual review of outputs **+1,041**; wall-clock time correlated only 0.20 with score and number
> of LLM calls 0.40.

---

## 3. Other public forecasting eval suites

### 3.1 PredictionMarketBench — the most immediately useful non-ForecastBench asset

<https://github.com/Oddpool/PredictionMarketBench> — MIT, 64 MB, cloned to
`/workspace/Oddpool/PredictionMarketBench`. **A Kalshi replay backtester that already exists.**

- 4 episodes with `metadata.json`, `orderbook.parquet`, `trades.parquet`, `settlement.json`:
  `KXBTCD-26JAN2017` (23 tickers), `KXHIGHNY-26JAN20` (6), `KXNFLGAME-26JAN11BUFJAC` (2),
  `KXNCAAF-26` (2). Wrong market types for our thesis, right data shape.
- Agent API: `act(ctx)` with `get_markets/get_orderbook/get_positions/get_cash/place_order/
  get_resting_orders/cancel_order`. Order types MARKET, LIMIT+IOC, LIMIT+GTC, **POST_ONLY**.
- Two execution modes: **taker-only** (cross the displayed book or reject) and **maker-taker**
  (rest in the book, fills when historical trades match your price, *"queue position is simulated
  based on displayed size and pro-rata fill allocation"*). This is exactly the honesty ORCHESTRATION
  constraint #4 demands, and it's the hardest part of P3 to get right.
- Outputs: PnL, max drawdown, Sharpe, contracts traded, fees paid, maker vs taker fill counts.
- Config knobs: `agent_call_cadence_seconds`, `equity_sample_interval_seconds`,
  `max_tool_calls_per_step`.

**Fee model (`src/oddpool_bench/fees.py`, class `KalshiOct2025FeeModel`):**

- taker = **7%** of `contracts · P · (1−P)`, **rounded UP** to the cent
- maker = **1.75%** of `contracts · P · (1−P)`, rounded up
- worked examples from the README: 1 contract at 50¢ taker = `ceil(0.07·0.5·0.5·100)` = **2¢**;
  at 50¢ maker = **1¢**; at 10¢ taker = `ceil(0.07·0.1·0.9·100)` = **1¢**

> **Flag for the fees/data agent:** `ORCHESTRATION.md` says taker ≈ `round(0.07·P·(1−P), 2)`.
> This third-party implementation uses **`ceil`, not `round`**, and adds a maker tier at 1.75%.
> At small sizes ceil-vs-round is a ~1¢/contract difference — which is a large fraction of the
> whole edge we're chasing. **Somebody must confirm against Kalshi's official fee schedule
> before any P&L number is believed.** (This repo is not authoritative; it is a third-party
> reimplementation.)

**Recommendation:** don't fork it (its episodes are useless to us and its data pipeline is
Kalshi-websocket-shaped), but **read `execution.py`, `maker_queue.py`, `fees.py`, and
`portfolio.py` before writing ours**, and consider adopting its `Agent`/`AgentContext` interface
so we could in principle run against its episodes as a smoke test.

### 3.2 Prophet Arena — the closest thing to "LLM vs prediction market", scored

- Live benchmark, LLMs predict on **Kalshi** markets; primary metric Brier, plus a CRRA-utility
  "Averaged Return (AVER)" that uses market-implied probabilities, plus IRT and Bradley-Terry
  ratings. Methodology writeup:
  <https://ai-prophet.github.io/pm_ranking/blogpost/ranking_llm_250727.html>
- Headline claim: **GPT-5 Brier 0.184 vs Market Baseline 0.187** — a *genuine but tiny* edge over
  market consensus. **[UNVERIFIED — this is from a search-result snippet; prophetarena.co 403s
  our fetcher.]** If real, note the shape of it: **+0.003 Brier**, which by §4.1 corresponds to an
  RMS disagreement with the market of only ~5.5¢ *if perfectly calibrated*. That is the
  realistic magnitude of an LLM edge over a real-money market.
- **The scoring code is pip-installable and open:** `pip install pm-rank` (v0.3.1,
  <https://pypi.org/project/pm-rank/>, source <https://github.com/listar2000/pm_rank>). Data
  model: `ForecastEvent` (problem_id, username, timestamp, probs, **odds / no_odds = market
  odds**) → `ForecastProblem` → streaming scorers. It supports market-odds-aware scoring and
  average-return metrics out of the box. **This is a ready-made library for exactly our
  "forecast vs market odds" scoring**, and it installs with no keys.
- Paper: *LLM-as-a-Prophet: Understanding Predictive Intelligence with Prophet Arena*.
- I could not find a public dump of Prophet Arena's resolved question/price data. **Open
  question:** does `pm_rank` ship or fetch any dataset? Worth 10 minutes.

### 3.3 PolyBench

- arXiv <https://arxiv.org/abs/2604.14199>; code <https://github.com/PolyBench/PolyBench>
  (cloned, 2.8 MB, no data).
- **38,666 binary Polymarket markets across 4,997 events**, point-in-time snapshots coupled to
  **CLOB state** and a **pre-fetched Google News stream**, collected **Feb 6–12 2026** (one week).
- Metrics: directional accuracy, **Confidence-Weighted Return (CWR)**, APY, Sharpe, via
  order-book execution simulation.
- Result: of 7 LLMs, **only 2 made money** — MiMo-V2-Flash **+17.6% CWR**, Gemini-3-Flash
  **+6.2% CWR**; the other five lost money "despite uniformly high stated confidence".
- **Data is behind a OneDrive link** (`https://1drv.ms/u/c/4d62feca782041b1/...`) — **[UNVERIFIED:
  I did not attempt the download; OneDrive share links usually need a browser redirect dance and
  the file is probably large.]** Code includes `evaluation/evaluate_mimo.py` (CWR/APY/Sharpe) and
  `core/market_data.py` (Polymarket Gamma + CLOB), both worth reading for P2/P4 even without the
  data.
- Caveat: one week of collection, Feb 2026, so it is post-cutoff but very short and
  regime-specific.

### 3.4 KalshiBench — checked, and we should skip it

<https://arxiv.org/abs/2512.16030>, data at
<https://huggingface.co/datasets/2084Collective/kalshibench-v2>. Downloaded and inspected the
parquet (203 KB) in this session:

```
shape (1531, 9)
columns: id, question, description, category, close_time, ground_truth,
         market_probability, series_ticker, source
ground_truth: {'no': 883, 'yes': 648}
market_probability non-null: 0        # <-- entirely empty
close_time range: 2021-09-01 .. 2025-11-16
```

**Two disqualifying problems:** the market price column is 100% null (so there is no market
baseline to beat), and every question closed before our Jan 2026 training cutoff (so it's
contaminated for LLM evaluation). Its finding — all five frontier models tested are
systematically overconfident — is worth knowing as a prior, not as an eval.

### 3.5 Also noted, not investigated

- `jon-becker/prediction-market-analysis` — described as *"the largest publicly available dataset
  of Polymarket and Kalshi market and trade data"*. **[UNVERIFIED]** — this is a P2 data-layer
  lead, not a benchmark; hand it to whoever owns the data puller.
- FutureX, TimeSeek, QuantSightBench, LLM-SoccerArena, LATTICE — surfaced in search, none
  market-baseline-oriented for binary event markets. Not pursued.
- Halawi et al. "approaching human-level forecasting" (the retrieval+ensemble recipe that the
  ForecastBench "LLM Crowd" rows descend from) — its published crowd rows score **0.155 market
  Brier**, i.e. far behind the market. Useful as a *ceiling on naive ensembling*.

---

## 4. What "beating the market baseline" looks like numerically

### 4.1 The identity every strategy discussion should start from

For a market price `p`, our forecast `q`, and outcome `y ∈ {0,1}`:

```
Brier_market − Brier_us = (p−y)² − (q−y)² = (p−q)·(p+q−2y)
```

Take the expectation under the true probability π:

- **If we are right (`q = π`):**  `E[ΔBrier] = +(p−q)²`
- **If the market is right (`p = π`):** `E[ΔBrier] = −(p−q)²`

**Our Brier advantage equals the mean squared distance between our forecast and the market
price — with the sign decided by who is actually right.** Deviating from the market is a
symmetric quadratic bet. The equivalent for log score: if we're calibrated, our mean log-score
advantage is exactly `KL(q ‖ p)`.

Practical conversion table (assuming we are the calibrated one):

| RMS disagreement with market | ΔBrier | ΔBrier Index |
|---|---|---|
| 2¢ | 0.0004 | +0.06 |
| 5¢ | 0.0025 | +0.37 |
| **6.3¢** | **0.0040** | **+0.59** ← our detection floor, §4.3 |
| 10¢ | 0.0100 | +1.5 |
| 15¢ | 0.0225 | +3.4 |
| 20¢ | 0.0400 | +6.4 |

Sanity check against reality: superforecasters beat the market baseline by
`0.077 − 0.073 = 0.004` on the ForecastBench baseline board — i.e. **the best humans on earth
beat market consensus by the equivalent of a 6¢ RMS disagreement.** The 2026 tournament bots that
lead (Cassi 0.055 vs 0.077) claim ~0.022, ≈15¢ RMS — on 102 questions, which is exactly the n
where §4.3 says you cannot yet distinguish that from luck at the tighter end.

### 4.2 The two different bars: Brier-beating vs profitability

These are **not the same test** and conflating them will waste a night.

- **Brier-beating** is pooled over *all* questions, including the ~80% where we'll agree with the
  market. A great forecaster with a 15¢ edge on 10% of questions and 0 elsewhere posts a pooled
  ΔBrier of only `0.10 × 0.0225 = 0.00225` — statistically invisible at n=1,216.
- **Profitability** only cares about the traded subset. Per contract held to resolution, expected
  gross profit is `|q − p|` dollars = `100·|q−p|` cents, against a Kalshi taker fee of
  `ceil(7 · p · (1−p))` cents ≈ **2¢ at mid, 1¢ at 10/90¢**. So the minimum *tradeable* edge is
  ~2 points at mid — and with fills at ~43% (FutureSearch's simulated rate, per our README) and
  adverse selection, the practical threshold is more like **6–10 points**.

**Therefore the benchmark spec should report three numbers, always together:**

1. **Pooled ΔBrier vs market**, with the paired CI (the calibration sanity check — proves we're
   not *worse*).
2. **ΔBrier restricted to the traded subset** (`|q−p| > threshold`) — the real skill claim, and
   the one that should be large.
3. **Simulated P&L after fees and fills** on those trades — the only number that pays rent.

A forecaster that is pooled-neutral but subset-positive is exactly what a profitable niche
strategy looks like. A forecaster that is pooled-positive but subset-negative is a
calibration-shrinkage artifact and will lose money.

### 4.3 How much data we need — measured, not assumed

The paired-difference SD I measured on real ForecastBench 2026 data (market price vs a
recalibrated variant of it) was **0.0698**. Using that:

| n | min detectable ΔBrier (p<0.05, two-sided, paired) | equivalent RMS disagreement |
|---|---|---|
| 100 | 0.0137 | 11.7¢ |
| 200 | 0.0097 | 9.9¢ |
| 500 | 0.0061 | 7.8¢ |
| **1,216** (our 2026 set) | **0.0039** | **6.3¢** |
| 2,000 | 0.0031 | 5.6¢ |

Two comments. First, a forecaster whose disagreements with the market are *larger* than a mere
recalibration will have a bigger paired SD (0.10–0.15 is plausible), pushing the floor up to
~0.006–0.008 at n=1,216 — so budget for that. Second, our README's MVP gate — *"beat the
market's Brier score over ≥100 resolved markets"* — is **statistically unsound at that n**: at
n=100 you can only detect a ~0.014 Brier edge, which is bigger than the edge superforecasters
have over markets. **Recommend raising the gate to ≥500 resolved markets, and always reporting
the paired CI rather than a point estimate.** This agrees with `forecasting-tools`' own
docstring ("100-200 is a decent starting point, but 500+ would be ideal … even with 100 there is
~30% [chance] of the 'worse bot' winning").

### 4.4 Concrete targets to put in the benchmark spec

| Tier | Pooled ΔBrier vs market (n≥500) | Interpretation |
|---|---|---|
| **Fail** | ≤ 0, or CI spanning 0 with a negative point estimate | We are not better than the price. Don't trade. |
| **Parity** | CI spans 0, point estimate ≥ 0 | Acceptable *if* the traded subset is positive — this is the realistic case for a niche strategy. |
| **Superforecaster-class** | +0.004 | Matches the human expert edge over market consensus (0.077 → 0.073). |
| **Top-2026-bot-class** | +0.02 | Matches Cassi/Torchcast's claimed market Brier (0.077 → 0.055). Be suspicious of this in our own results. |
| **Implausible** | > +0.05 | You have a leak. Check the point-in-time discipline and the `resolved == true` filter. |

---

## 5. Recommended benchmark ladder for this repo

Ordered, each rung gated on the previous. Rungs 0–2 need **no API keys and no market data** and
can start immediately.

0. **Wire the ForecastBench loader** (§1.8, ~40 lines) and reproduce `market Brier = 0.1172,
   n = 1,216`. This is the harness self-test — if you don't get that number, the join or the
   `resolved` filter is wrong.
1. **Score the dumb baselines** on the same subset: Always-0.5 (0.25), the market price (0.1172),
   and the out-of-sample recalibrated market (0.1178). Fixes the scale.
2. **Run our `forecasting/` engine on the 2026-03-01+ market questions with retrieval bounded to
   `freeze_datetime`.** Report the three numbers from §4.2 plus per-question inference cost
   (ORCHESTRATION constraint #3). Start with ~200 questions to get a cost estimate, then decide
   whether the full 1,098 is affordable.
3. **Ablate**: no-retrieval vs retrieval; single-model vs ensemble; with-market-price-in-prompt
   vs without. That last one matters enormously — ForecastBench's dataset leaderboard shows
   "(zero shot **with crowd forecast**)" variants clustering at 60–61 while the same models
   without it sit lower, and the tournament board shows `_crowdadj` ensemble variants
   outperforming. **Anchoring on the price is a known large effect; measure it rather than
   assume it.**
4. **Only then** move to the Kalshi backtest (P2/P3), where fills and fees enter and where the
   §4.2-item-3 number is the one that counts.
5. **In parallel, free:** forecast the currently-open 2026-08-02 ForecastBench set (79 Polymarket
   + 79 Metaculus + 80 Manifold questions, freeze 2026-07-23) and let it resolve. Zero cost, real
   forward test, no contamination risk whatsoever.
6. **Optional, needs a human 5 minutes:** get a `METACULUS_TOKEN` and enter the FutureEval Summer
   2026 tournament with the template bot. Gives an external, adversarial, third-party score of
   our forecaster — and a public leaderboard placement is the cheapest credibility we can buy.

---

## 6. Open questions / things I could not verify

1. **Kalshi's official fee schedule** — `ceil` vs `round`, and whether the maker tier is really
   1.75%. Third-party code says ceil + 1.75% maker; ORCHESTRATION.md says round. Must be
   resolved from Kalshi's own schedule before any P&L is trusted.
2. **Prophet Arena's 0.184 vs 0.187** (GPT-5 vs market baseline) — from a search snippet only;
   prophetarena.co 403s. Also unknown: does `pm_rank` ship any data, or only scorers?
3. **PolyBench's OneDrive dataset** — size and downloadability from this container untested.
4. **Metaculus 2026 bot-vs-pro numbers** — the metaculus.com pages 403 our fetcher, so §2.6's
   2026 claims rest on search extraction. The Q2-2025 numbers are solid (EA Forum fetched
   directly).
5. **Whether ForecastBench market questions transfer to Kalshi.** Untested and untestable from
   this data alone. The Polymarket subset (534 resolved in 2026) is the best available proxy;
   an obvious next step is to check overlap between ForecastBench Polymarket questions and Kalshi
   series on the same underlying events, which would give a genuine cross-venue calibration
   comparison.
6. **`jon-becker/prediction-market-analysis`** — claimed largest public Kalshi+Polymarket trade
   dataset; not inspected.

---

### Appendix: local paths from this session

```
/workspace/forecastingresearch/forecastbench-datasets   # 139 MB, THE dataset
/workspace/forecastingresearch/forecastbench            #  29 MB, scoring code (src/leaderboard/main.py, src/resolve/_impute.py)
/workspace/Oddpool/PredictionMarketBench                #  64 MB, Kalshi replay harness + 4 episodes
/workspace/PolyBench/PolyBench                          # 2.8 MB, code only
/workspace/Metaculus/forecasting-tools                  # library + Benchmarker
/workspace/Metaculus/metac-bot-template                 # minimal bot reference
```

These live outside the repo (they're third-party clones) — do not commit them. Re-clone with the
commands in §1.1 if the workspace is reset.

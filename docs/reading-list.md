# Annotated Reading List: AI Forecasting Bots on Prediction Markets

For Griffin. Everything below was fetched and read on 2026-08-11; annotations describe what the sources actually say, not folklore. The central question this list is ordered around: **can an AI agent profitably trade prediction markets?** Short answer from the sources: yes, it has been done (Preseen, on Kalshi), but the academic evidence says beating *market consensus* is much harder than matching *human forecasters*, and the edge lives in market selection and aggregation discipline, not in any single clever prompt.

---

## Start here — top 5

### 1. Scott Alexander, "The AI Superforecasters Are Here" (Astral Codex Ten)
<https://www.astralcodexten.com/p/the-ai-superforecasters-are-here>

The anchor piece: a mid-2026 state-of-play arguing AIs and top human forecasters are now in "a statistical dead heat" — AIs slightly better in finance, humans slightly better everywhere else — with AI improving ~0.9 Metaculus Elo points/month, putting bots on track to pass the best humans within a year or two. It names the two companies that matter for this project: **Preseen** (turned $35 into ~$2M on Kalshi in seven months; first bot ever to win a human tournament, Market Pulse) and **FutureSearch** (claims ~25% market outperformance with a market-neutral portfolio; live positions on Kalshi/Polymarket). Also notes best AIs score ~31 on the Metaculus scale vs ~36 for top humans, that scaffolding is worth ~9 months of base-model progress, and flags the Kapoor & Narayanan counterargument that forecasting may have an irreducible-error ceiling. Read this first — it is the existence proof and the map of who's already doing what we want to do.

### 2. FutureSearch — Kalshi Forecaster case study
<https://futuresearch.ai/blog/kalshi-forecaster-case-study/>

The single most directly on-point architecture writeup: an AI pipeline that scans ~3,500 open Kalshi markets and forecasts the tradeable subset. Five stages: (1) discovery — rank by volume, filter to 3–97% price, drop markets resolving <10 days out; (2) two AI screening filters rejecting insider-info and specialist-expertise markets (sports, crypto, pre-taped TV); (3) six parallel research agents per market (current state, base rates, key factors, expert opinion, YES/NO theses); (4) three independent LLM forecasters (Gemini + 2× Claude Opus) each given the dossier; (5) take the median, sort by disagreement with market price. Cost: ~$0.60/question, 153 questions in one February 2026 run, 100+ markets in 45 minutes. Example edges found: SpaceX Feb launches (market 6% vs forecast 45%), xAI top model (50% vs 78%), Greenland (41% vs 10%). **Important honesty note: the post reports no realized returns** — profitable-portfolio tracking was still "planned" at publication. This is our template pipeline; the missing P&L is the gap our project fills.

### 3. AIA Forecaster: Technical Report (Bridgewater AIA Labs) — arXiv:2511.07678
<https://arxiv.org/pdf/2511.07678>

The paper Chris cites most, from Bridgewater's AIA Labs (Alur, Stadie, Kang, Sekhon, et al., Nov 2025). Method: agentic search over high-quality news sources + a supervisor agent that reconciles disparate forecasts of the same event + statistical calibration corrections for LLM behavioral biases. Two headline results, and the second is the one that should shape our strategy: (a) on ForecastBench it reaches **parity with human superforecasters** — they call it the first verifiable expert-level AI forecasting at scale; (b) on their own harder benchmark built from **liquid prediction markets, the forecaster alone *underperforms* market consensus** — but an ensemble of forecaster + market price beats the market price alone, i.e. the model adds information without being independently better. Translation for us: don't expect to beat liquid markets head-on; expect edge in *combining* model signal with price, and in less liquid / less attended markets.

### 4. Metaculus Q2 AI Benchmark results: "Pros maintain clear lead" (EA Forum)
<https://forum.effectivealtruism.org/posts/F2stjK9wHSy3HPEC9/q2-ai-benchmark-results-pros-maintain-clear-lead>

The sobering counterweight, from the Q2 2025 quarterly tournament (54 bot makers, 348 questions, 10 Pro forecasters on a 96-question overlap): bots scored **−20.03 head-to-head vs Pros** (95% CI [−28.63, −11.41], p = 0.00001), every one of the 10 Pros beat every individual bot, and the gap had *widened* from −11.3 in Q3 2024. Key practical findings: **base model choice matters more than scaffolding** (o3 dominated among standardized template bots); aggregating multiple forecasts, extensive manual testing, and writing custom test questions were the highest-value practices; bots were worst at multiple-choice (−32.9) and better at binary (−14.8); and top performers were hobbyists, not companies. Read alongside #1: this is 2025 data, ACX describes the 2026 catch-up — together they show how fast the frontier is moving and that the gap direction reversed within ~a year.

### 5. Preseen
<https://preseen.com/>

The existence proof that the whole thesis works: "the most decorated forecasting bot in the world." Self-reported record: **grew $35 into $1.94M on Kalshi (a ~50,000× return), ranked 6th all-time on the Kalshi leaderboard**; 1st bot / 3rd of 1,283 overall in Metaculus Cup Spring 2026; won FutureEval Fall 2025 (164 systems); and won Market Pulse 26Q2 outright against humans (1st of 112). Method described only at a high level: independent AI "scientists" analyze each question separately from primary sources, then synthesize into a calibrated probability. Sparse on detail, but it defines the bar and confirms Kalshi specifically is beatable by an AI-driven account. (Numbers are self-reported; the Kalshi leaderboard rank is the closest thing to external validation.)

---

## Full list

### AI forecasters in the wild

- **ACX, "The AI Superforecasters Are Here"** — see Top 5 #1. Companies named in the post: **FutureSearch**, **Preseen**, plus context players Metaculus (tournaments; bots manticAI and Laertes in the summer Cup top ten), Kalshi/Polymarket/Manifold (venues), Jane Street (building AI data-center capacity), Bridgewater (hiring superforecasters; their AIA Labs wrote #3 above), and Google DeepMind (used superforecasters). Also cites ForecastBench.org and the Kapoor & Narayanan (Knight Columbia) skeptical piece.
- **FutureSearch (home)** — <https://futuresearch.ai/> — Commercial AI forecasting shop: forecast API ($0.15–$2/question across probability/numeric/date/conditional types), multi-agent research teams, and live real-money positions on Kalshi, Polymarket, and S&P 500. Claims #1 of 197 on Metaculus FutureEval and #19 of 335 on ForecastBench, and publishes losses as well as wins. Our closest "competitor/model to copy."
- **FutureSearch Kalshi case study** — see Top 5 #2.
- **FutureSearch Evals** — <https://evals.futuresearch.ai/> — Their public benchmark suite: BTF-3 "pastcasting" (1,907 resolved questions — 1,515 binary Brier-scored, 392 numeric — researched against a frozen web corpus so there's no leakage), BTF-2 (1,417 hard questions), Deep Research Bench, plus live-tournament results. Current board: FutureSearch's ensemble at 0.116 Brier, marginally ahead of Claude Opus 5 at 0.118, with Claude and GPT-5.x variants dominating. Useful to us twice over: as a model-selection leaderboard, and as the pastcasting evaluation pattern we should copy before risking money.
- **Preseen** — see Top 5 #5.
- **Faint Signals, "Building an AI prediction bot" (Matthew Granade, Sept 2024)** — <https://faintsignals.substack.com/p/building-an-ai-prediction-bot> — A solo builder's writeup of "MWG," a Metaculus benchmark-tournament bot built in ~10 part-time days on Google Colab for <$1,000 total through ~500 questions. Pipeline: pull question → gather news via AskNews + Perplexity (GPT-4o writes the search queries) → 12 analytical prompts from different angles → run ≥3 times, re-run if answers diverge → GPT-4o meta-forecast over all runs → weighted final submission. Reached top-5 of ~35 bots. Lessons that transfer directly: LLMs are good at synthesis/ranking but almost never self-correct when prompted to adjust for bias; rigorous A/B testing of prompt variants is impractical at hobby budget; and — the memorable failure — given *no* news input the bot **fabricated plausible headlines**, caught only because they read suspiciously well. Ground every forecast in retrieved sources.
- **Metaculus Q2 AI Benchmark results** — see Top 5 #4.

### Academic evidence

- **AIA Forecaster (Bridgewater AIA Labs), arXiv:2511.07678** — see Top 5 #3. The one result to internalize before sizing any bet: superforecaster-parity ≠ beating liquid market consensus; the value is additive in ensemble with the price.
- **Halawi, Zhang, Yueh-Han, Steinhardt, "Approaching Human-Level Forecasting with Language Models" (arXiv:2402.18563, Feb 2024)** — <https://arxiv.org/abs/2402.18563> — The foundational academic recipe: retrieval-augmented LM system (search → generate forecasts → aggregate) evaluated on competition questions published after model knowledge cutoffs; it "nears the crowd aggregate of competitive forecasters, and in some settings surpasses it." Established the retrieve-reason-aggregate architecture that everything above (FutureSearch, AIA, MWG) elaborates.

### Forecasting craft

- **Tetlock's Ten Commandments for Aspiring Superforecasters** — <https://goodjudgment.com/philip-tetlocks-10-commandments-of-superforecasting/> — (Good Judgment page blocked fetches; list recovered via fs.blog mirror.) The empirically-validated habits from the Good Judgment Project: triage to questions in the difficulty sweet spot, Fermi-ize (break problems down), balance outside/inside views, update incrementally, weigh clashing arguments, use granular numeric probabilities, balance prudence vs decisiveness, do postmortems on both hits and misses. "Triage" is literally our market-selection stage.
- **FutureSearch team, "The rationale-shaped hole at the heart of forecasting" (EA Forum)** — <https://forum.effectivealtruism.org/posts/qMP7LcCBFBEtuA3kL/the-rationale-shaped-hole-at-the-heart-of-forecasting> — Argues platforms capture the number but throw away the facts, reasons, and models that generated it; prescribes publishing source-linked facts, adversarial-collaboration-style reasons, and explicit quantitative models with every forecast. This is the design philosophy behind FutureSearch's per-market dossiers — and the right spec for our bot's audit trail.
- **Samotsvety Forecasting (EA Forum topic)** — <https://forum.effectivealtruism.org/topics/samotsvety-forecasting> — Elite human forecasting group (Yagudin, Sempere, Lifland) with a published track record; known for nuclear-risk, AI-risk, and AGI-timeline forecasts. The human benchmark for "small team, aggregated independent judgments" — the structure our multi-model ensemble imitates.
- **Metaculus question-writing guidelines** — <https://www.metaculus.com/question-writing/> — (Metaculus blocks automated fetches; summarized from search-indexed excerpts of the guide and its companion checklist.) Resolution criteria must be clear, verifiable, unambiguous; title must match resolution conditions; avoid vague or linked criteria; a question needing >~15 min of admin effort to resolve gets rejected. For a trading bot this doubles as a *screening* rubric: ambiguous resolution language is settlement risk.

### Aggregation math

- **Jaime Sevilla, "When pooling forecasts, use the geometric mean of odds" (EA Forum, 2021)** — <https://forum.effectivealtruism.org/posts/sMjcjnnpoAQCcedL2/when-pooling-forecasts-use-the-geometric-mean-of-odds> — (Fetched via GreaterWrong mirror.) When combining multiple forecasters' probabilities, take the geometric mean of their *odds*, not the arithmetic mean of probabilities: it robustly outperformed arithmetic pooling in Satopää et al.'s 69-question/1,300-forecaster study (extremized version best by Brier score), it's externally Bayesian, and it actually uses information in extreme forecasts (arithmetic mean can't tell a 1-in-1,000 insider from 1-in-10,000). Caveats: differences are small in the 10–90% range; extremizing risks overfitting; arithmetic mean can be right for mutually exclusive scenarios. Directly applicable to our multi-model ensemble step — note FutureSearch's Kalshi pipeline used a median of 3, which is the cheap robust cousin.

### Tooling (one-liners — covered in depth elsewhere)

- **Metaculus/forecasting-tools** — <https://github.com/Metaculus/forecasting-tools> — Python framework for building Metaculus forecasting bots: prebuilt bots, Metaculus API wrapper, web search, multi-provider LLM interface, cost tracking; handles binary/MC/numeric/date questions.
- **Metaculus/metac-bot-template** — <https://github.com/Metaculus/metac-bot-template> — Fork-and-go starter template (GitHub Actions or local) claiming a working tournament bot in ~5 minutes; one variant on forecasting-tools, one minimal-dependency.

---

## Couldn't fetch (content recovered via mirrors/search — noted inline above)

- `goodjudgment.com/philip-tetlocks-10-commandments-of-superforecasting/` — 403 on direct fetch and archive.org unavailable in this environment; the ten-commandments list itself was recovered from the fs.blog republication, so the annotation is grounded, just not in the Good Judgment page's own framing.
- `metaculus.com/question-writing/` — 403 on direct fetch (all metaculus.com properties and the czea mirror blocked); annotation is grounded only in search-engine excerpts of the guide and its question-approval checklist, not the full page text. Treat as a skim-level summary and open it in a browser for the full guidance.

Everything else in this document was fetched directly on 2026-08-11.

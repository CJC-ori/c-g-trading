"""bot.forecaster — the P-4 cost-engineered LLM forecaster.

Tiered funnel (research/cost-architecture.md §3, SYNTHESIS §1 P-4):

    prefilter (free) -> screen (Haiku) -> dossier (Sonnet, per EVENT)
        -> judge (2 independent stances, geometric mean of odds)

with point-in-time retrieval (retrieval.py, per
research/point-in-time-retrieval.md), structured ground-truth injection
(groundtruth.py), and every LLM call charged to bot.backtest.costs.CostLedger.

No module here may call a live search engine, ever (banned in backtests).
"""

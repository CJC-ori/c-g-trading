"""Eval ladder: external validation of the harness and the baselines to beat.

See ``bot/evals/README.md`` for the rung structure. Every module here is
runnable as ``python -m bot.evals.<module>`` and writes JSON + Markdown to
``reports/evals/``.

Nothing in this package may be imported by a live-money decision path: rung 2
is built on ForecastBench data (CC BY-SA 4.0) and the ladder references BTF
(CC BY-NC). See README §Licensing.
"""

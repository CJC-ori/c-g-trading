# Eval ladder — full run

*Generated 2026-08-11T22:47:30+00:00 — `python -m bot.evals.ladder`*

**Verdict: PASS**

## Rungs

| rung | eval | ok | verdict / detail |
|---|---|---|---|
| 0 | harness self-tests | PASS | 345 passed in 8.88s |
| 1 | fs_trade_replay | PASS | PASS — harness economics reproduce the external book |
| 2 | forecastbench | PASS | SELF-TEST PASS — baseline established, no forecaster scored |
| 2a | fs_replication | PASS | UNDERPOWERED — n=61 resolved of 153 (gate is n≥500); numbers are directional only |
| 3 | Kalshi tournament replay | — | NOT IMPLEMENTED HERE — see README rung 3 |

Each rung is gated on the one below it. A rung 2 Brier computed on a harness that failed rung 1 is not a measurement.

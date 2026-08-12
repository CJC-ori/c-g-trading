# Tournament universe — frozen record (Round 1, 2026-08-12)

Command that generated the Round 1 universe + split (defaults of
`bot/backtest/universe.py` / `run_flb` except where flagged):

```
python -m bot.strategies.run_flb \
    --db data/kalshi.db \
    --out reports/tournament/round1 \
    --close-after 2021-07-01 \
    --variants r5_endgame_6h,r5_endgame_12h,r5_endgame_24h,baseline_hold_favorite
```

- Selection: settled outcomes only (`result` in yes/no/scalar), categories
  excluded `Sports`,`Crypto`, parlay/HF-ladder series prefixes excluded
  (`DEFAULT_EXCLUDE_PREFIXES`, starts KXMVE), volume >= 1000 contracts,
  hourly candles present locally, `close_time > 2021-07-01T00:00:00Z`.
- Universe: **55,350 markets / 12,954 events**.
- Settlement span: t0 = 1626667260 (2021-07-19 04:01Z) .. t1 = 1786483329
  (2026-08-11 21:22Z).
- **Train/test boundary (60% of the full settled window by time, binding
  for ALL later rounds): split_ts = 1722556901 = 2024-08-02T00:01:41Z.**
- TRAIN = 7,103 markets settling <= split_ts. HELD-OUT = 48,247 markets
  settling after it — untouched until Round 4. The count asymmetry is
  expected: Kalshi's listing rate exploded in 2025-26 (weather ladders),
  so 60% of the *time span* holds ~13% of the *markets*.
- 2022 midterms are category=Politics (not Elections) in this DB; both are
  in-universe (only Sports/Crypto excluded), so no correction needed.
- Frozen train ticker list: `reports/tournament/round1/universe.json`
  (`train_tickers`). Later rounds MUST pin via
  `--universe-file reports/tournament/round1/universe.json`.
- DB state caveat: a 2025 trades backfill (hist-trades, closed 2024-11 ..
  2026-01) was still appending (WAL) during the Round 1 run. Those trades
  postdate split_ts, so they sit almost entirely in the held-out window;
  train-side fills are unaffected except for markets closing 2024-08..-11
  that settled pre-boundary (none — backfill starts at closed-after
  2024-11-01, which is after the boundary).
- Harness settings: exact per-series FeeSchedule (`from_store(data/kalshi.db)`,
  same centicent model as `load_default()`, resolved against the full live
  series table), maker queue ON depth_windows=1.0, cancel/replace ON,
  $10k bankroll, quarter-Kelly, fee stress x1.5.

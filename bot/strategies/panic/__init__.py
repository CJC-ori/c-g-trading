"""P-3: event-night panic-wick resting ladders (research/SYNTHESIS.md §1 P-3).

- episodes.py     — historical episode discovery + taxonomy (the
                    adverse-selection measurement SYNTHESIS flags as the
                    main risk).
- strategy.py     — R4 dip-side YES bid ladder and R4b cheap-NO convexity,
                    parameters frozen before results (rationale in
                    strategy.PARAMS_RATIONALE).
- provider.py     — per-episode HistoryProvider (real 1-min candles,
                    trade-synthesized 1-min candles, or hourly fallback;
                    trades exposed only where they honestly cover the
                    event window).
- run_backtest.py — per-episode engine runs through bot/backtest with
                    exact fees; train/test split by episode time.
"""

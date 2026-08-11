"""P-5 price-only structure scanner (SYNTHESIS §1 P-5).

Three sub-signals, one shared plumbing layer, all price-only (zero
inference cost, no contamination window):

- ``overround``  — R3: multi-outcome NO-set / YES-set mispricing scan.
- ``consistency`` — cross-instrument coherence (monotone strike ladders and
  binary-control vs seat-ladder pairs).
- ``divergence`` — R7: Kalshi vs Polymarket divergence, on a hand-curated
  STRUCTURALLY verified pair map (``pairs.py``).

Everything this package needs lives inside it, including its own Kalshi
candle puller (``kalshi_candles``) and Polymarket client (``polymarket``);
it only *reads* ``bot/backtest`` (types, fees, engine) and ``data/kalshi.db``.
"""

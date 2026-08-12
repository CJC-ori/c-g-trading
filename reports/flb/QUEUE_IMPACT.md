# Maker-fill realism: queue model + cancel/replace — impact on the FLB train suite

Generated 2026-08-12. Companion to `reports/flb/ANALYSIS.md` (which now
carries the AFTER numbers). Everything here is the **frozen train
universe** (`reports/flb/universe.json`: 132 train markets, split
2026-07-11 08:43Z), current `data/kalshi.db`, exact per-series centicent
fees. The held-out tail was never run.

## What changed in the engine (2026-08-12)

1. **Maker queue position** (`fills.MakerQueueConfig`, default ON). A
   resting order no longer participates in every qualifying print/candle.
   It joins BEHIND an estimate of the displayed depth at its level —
   `depth_windows × recent per-window traded volume` (mean of the last 3
   visible candles, the same estimator the taker depth clamp uses) — and
   only qualifying volume in excess of that queue reaches it, still capped
   at 25% of the excess. The store carries no book sizes (candlesticks
   have bid/ask price OHLC only), so the queue-ahead is a volume proxy,
   not real depth. Where no volume proxy exists at placement, each
   qualifying event reaches the order with fixed probability 0.45
   (mid-band), via a deterministic per-(order, event) hash.
2. **Cancel/replace** (`OrderIntent.replace`). A replace intent cancels
   the strategy's still-resting same-direction orders in that market
   (freeing their committed premium), then places the new order with a
   fresh queue position at the back. `replace=True, size=0` is a pure
   cancel. R2-maker and R5-endgame now use this for "reprice hourly"
   instead of the old stale ladder ("new order when the quote moves; old
   orders stay live until fill or close").
3. **Depth pre-clamp is taker-only.** The risk layer's 3-candle depth
   pre-clamp used to zero resting orders placed in quiet hours even though
   per-print/per-candle caps (and now the queue) already bound maker fills
   at fill time — a double count. It now applies to taker intents only.

## Calibration of `depth_windows` (fill rate, never P&L)

**Anchor** (SPEC §3): FutureSearch's measured live maker fill rate —
positions table ~50.1%, headline 43% → **40–50% sanity band**; >60% means
the fill model is lying. The legacy model put R2-maker at **69%** (order
level) / 57% (contract level), tripping the flag.

**Metric choice.** "Fill rate" per order is dominated by order lifecycle,
not by fill physics: under hourly cancel/replace each order lives ~1h, so
the order-level rate collapses to ≤31% *even with the queue disabled*,
while multi-day stale-ladder orders eventually outlast any finite
displayed-depth estimate, so their order-level rate stays ≥61% *at any*
`depth_windows`. The quantity the queue actually controls is the
**contract-level fill rate** (fraction of resting size that fills). We
therefore calibrate the contract-level rate **on the configuration in
which the 69% violation was measured** (R2, stale-ladder lifecycle), and
report both metrics everywhere.

**Sweep** (R2-maker, frozen train universe, replace OFF):

| depth_windows | order fill rate | **contract fill rate** | net P&L |
|---|---|---|---|
| 0.25 | 66.4% | 53.2% | $+161.71 |
| 0.5  | 65.3% | 51.1% | $+128.92 |
| 0.75 | 65.3% | 50.1% | $+91.12 |
| **1.0** | **64.1%** | **48.9%** | **$+68.02** |
| 1.5  | 64.1% | 50.0% | $+211.26 |
| 2.0  | 61.7% | 48.9% | $+226.20 |
| 3.0  | 61.1% | 48.2% | $+160.78 |

Any value in [0.75, 3] lands 48–50% — the aggregate under-identifies the
pessimism factor. **Chosen default: 1.0**, because (a) it is mid-band
(48.9%); (b) it is the natural unit — "you join behind one window's worth
of recent traded volume", the same per-window estimator the risk layer
already uses; (c) it distorts the R5 variants, whose legacy fill rates
(33–49%) were already anchor-consistent, the least (at 3.0 their rates
drop to 14–22%, implying books 3× hourly volume deep — implausible for
these thin markets). Note the chosen value has nearly the *worst* P&L in
the swept set — this calibration hurts the strategy; it is not P&L
tuning. The no-proxy fallback probability 0.45 is the middle of the same
band, applied directly.

For completeness, the shipped lifecycle (replace ON) across the sweep:
order rate 31.3%→17.6% and contract rate 33.3%→10.7% as depth_windows
goes 0→3; the band is unreachable there at any pessimism ≥0, which is
why calibrating on that lifecycle would be meaningless.

## Before / after (frozen train universe, exact fees)

BEFORE = committed engine (verified: re-running HEAD on the current DB
reproduces `ANALYSIS.md`'s numbers exactly, so the DB growth to 16,992
tickers did not touch the frozen train tickers' data). AFTER = queue
(dw=1.0) + cancel/replace + taker-only pre-clamp — the new defaults.

| variant | fill rate before (order / contract) | fill rate after | net P&L before | net P&L after | mean ep. return before → after | episodes |
|---|---|---|---|---|---|---|
| r2_maker | 69% / 57% | 23% / 19% | **$+262.74** | **$−161.79** | +3.37% → +0.90% | 82 → 80 |
| r5_endgame_6h | 33% / 32% | 30% / 24% | $+81.75 | $+100.58 | +6.24% → +6.28% | 13 → 13 |
| r5_endgame_12h | 40% / 50% | 19% / 16% | $+197.45 | $+141.41 | +5.68% → +6.10% | 22 → 17 |
| r5_endgame_24h | 49% / 52% | 24% / 17% | $+297.67 | $+160.21 | +6.16% → +5.40% | 35 → 33 |
| r1_taker (control) | — | — | $−16.92 | $−16.92 | −3.12% (unchanged) | 75 |
| baseline_hold_favorite (control) | — | — | $−3.67 | $−3.67 | −12.82% (unchanged) | 32 |

Taker-only variants are bit-identical, as they should be — the changes
touch only resting-order mechanics.

### Decomposition (R2-maker, cumulative left to right)

| step | order rate | contract rate | contracts | net P&L | mean ep. return |
|---|---|---|---|---|---|
| BEFORE (HEAD) | 68.6% | 57.0% | 5,178 | $+262.74 | +3.37% |
| + pre-clamp fix only | 66.4% | 54.7% | 8,984 | $+87.01 | +3.15% |
| + queue (dw=1.0) | 64.1% | 48.9% | 8,739 | $+68.02 | +1.94% |
| + cancel/replace (= AFTER) | 23.3% | 19.0% | 4,552 | $−161.79 | +0.90% |

Reading: **most of R2's simulated edge was fill-model artifact.** The
pre-clamp fix roughly doubled deployed contracts (quiet-hour quotes are no
longer zeroed) and the marginal contracts lost money; the queue shaved the
generous fills; and cancel/replace removed the stale-ladder fills — orders
resting for days at below-market prices that a real repricing strategy
would have moved — which were carrying the P&L. R2's kill-criterion (a) on
train moves from BORDERLINE (+3.37%, CI spans 0) to mean +0.90%,
dollar-weighted −4.03%, net negative.

### R5-endgame

R5 survives realism: per-episode means stay +5.4–6.3% (GWU benchmark
+2.6%) and net P&L stays +$100–160 per window, though 12/24h totals drop
~30–45% on fewer/smaller fills. Fill rates are now *below* the 40–50%
band (16–30%) — conservative rather than inflated: the shipped lifecycle
reprices, and the queue makes near-close books pessimistically deep. All
episodes remain winners, so the zero-loss caveat from ANALYSIS.md still
applies (the mean is an upper bound until the sample contains losses).

## Interface changes (tournament runner)

All additive / backward-compatible:

- `EngineConfig.maker_queue: fills.MakerQueueConfig` — new field,
  default-enabled (that IS the honest default);
  `MakerQueueConfig(enabled=False)` reproduces legacy runs. Fields:
  `depth_windows=1.0`, `min_depth_contracts=0`,
  `unknown_depth_fill_prob=0.45`, `seed=7` (deterministic hash gate; no
  RNG state).
- `OrderIntent.replace: bool = False` — cancel/replace (size=0 = pure
  cancel). Old strategies are unaffected.
- Order log rows gain `queue_ahead_at_placement` and `replace`; event log
  gains `cancel` events with `reason="replaced"` and `queue_ahead` on
  `order_placed`.
- `R2Maker`/`R5Endgame` gain `use_replace: bool = True`.
- `run_flb` gains `--universe-file` (pin a frozen universe + split),
  `--no-maker-queue`, `--no-replace`, `--depth-windows` (comparison /
  calibration runs only).
- Risk layer: the depth pre-clamp is applied only to taker intents (the
  engine passes `observed_window_volume=None` for resting intents);
  `clamp_intent_size`'s signature is unchanged.
- `test_realdata.py` universe-shape bound relaxed to structural checks
  (≥100, deduplicated, sorted) — the universe is a property of the DB and
  grew past the old ≤5000 pin with the full-history pull.

## Honesty caveats

1. **The queue-ahead is a volume proxy, not book data.** Candlesticks
   carry no displayed sizes; `depth_windows × recent volume` is a modeling
   assumption whose scale is under-identified by the calibration target
   (any 0.75–3 lands in-band). The chosen 1.0 is principled but not
   measured; live paper-trading fill rates are the real calibration.
2. **The anchor is one firm's number.** FutureSearch's 43–50% comes from
   their bots' lifecycles, which we cannot observe; order-level fill
   rates are not comparable across lifecycles (see metric discussion).
3. **Queue does not refill.** Once drained, the estimated queue stays
   drained for the order's remaining life; new joiners behind us are
   ignored (both directions of error exist; net effect untested).
4. **No adverse-selection model beyond the queue.** Fills still happen at
   our limit whenever enough volume prints through; in reality makers get
   filled preferentially when they are wrong.
5. **R2's train verdict flipped on realism** — that is the point of the
   exercise, but it also means earlier R2 conclusions (ANALYSIS.md of
   2026-08-11, git history) are superseded.
6. **Same small 2026-only sample** as ANALYSIS.md: 78-day span, R5 has
   zero losing episodes, maker fee coefficient 0.0175 still unverified,
   fee schedule is a present-day snapshot. The full-history rerun
   (16,992-ticker universe) remains the binding test and was NOT run here
   (it would confound fill-model impact with dataset change; the frozen
   universe isolates the model).

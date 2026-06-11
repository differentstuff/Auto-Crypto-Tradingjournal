# Time Travel — Fast-Forward Daemon

Replay the daemon's scoring logic on historical OHLCV data. Simulate entries at multiple thresholds, walk forward to find exits, and write results to `trade_learning` — exactly as if the daemon had run live during that period.

**Purpose:** Populate `trade_learning` with trades the daemon *would have taken* (and *missed*) at various `entry_threshold` values. This gives Karpathy and Hyperopt the data they need to discover optimal thresholds — killing Karpathy v1's "blocked but shouldn't be" blind spot.

**80:20 approach:** Backtest fills `trade_learning` fast (80%). Live trading finetunes from there (20%). Backtest trades have no slippage or latency — that's acceptable because the learning engine re-scores from `signals_at_entry_json`, not from raw P&L.

---

## Quick Start

```bash
# Backtest BTCUSDT from January 2025 (uses strategy config symbols by default)
python scripts/time_travel.py --start 2025-01-01

# Multiple symbols, custom date range
python scripts/time_travel.py --start 2025-06-01 --end 2025-12-01 \
    --symbols BTCUSDT ETHUSDT SOLUSDT

# Custom threshold sweep
python scripts/time_travel.py --start 2025-01-01 --thresholds 2.5,3.5,4.5,6.5

# Dry run — preview without writing to DB
python scripts/time_travel.py --start 2025-01-01 --symbols BTCUSDT --dry-run

# Verbose logging
python scripts/time_travel.py --start 2025-01-01 --log-level DEBUG
```

---

## How It Works

```
For each bar in the historical range:

  1. FETCH OHLCV for primary TF (1h) and confirmation TF (4h)
  2. COMPUTE INDICATORS using indicators/registry.py (same as live daemon)
  3. SCORE CONFLUENCE using the same formula as ScoreConfluence enzyme
  4. CHECK THRESHOLDS — for each threshold in the sweep:
     if |score| >= threshold AND cooldown OK:
       → SIMULATE EXIT (walk forward: SL, TP, trailing stop)
       → BUILD signals_at_entry_json (same extractors as record_trade_outcome.py)
       → INSERT INTO trade_learning

Result: trade_learning filled with backtest trades
  → Karpathy re-scores them → discovers optimal threshold
  → Hyperopt searches weight space → finds better weights
  → weight_adjuster adjusts → ScoreConfluence uses them live
```

### Scoring Parity

The script extracts the scoring logic from `enzymes/score_confluence.py` into standalone functions. Same weights, same formula constants, same momentum cap/dampening, same cross-timeframe alignment check. A bar that scores 6.3 in the live daemon scores 6.3 in time travel.

### Signal Format

`signals_at_entry_json` uses the same extractors as `enzymes/record_trade_outcome.py`. Karpathy's `_compute_score_from_signals()` reads `signal` keys — they must match exactly. They do.

### Exit Simulation

Matches the `exit_rules` in strategy config:

| Rule | Source | Default |
|---|---|---|
| Hard stop loss | `exit_rules.hard_stop.width_atr_multiplier` | 1.5× ATR |
| Take profit | `scoring.rr_minimum` × SL distance | 2.0 RR |
| Trailing stop | `exit_rules.trailing_stop.*` | Activates at 0.5% profit, trails at 1.0× ATR |
| Walk-forward cap | Hardcoded | 200 bars |

### Confirmation Timeframe

Fetches both primary TF (1h) and confirmation TF (4h). For each 1h bar, finds the last closed 4h candle and uses its indicators for the alignment check. When timeframes disagree, the score is neutralized — same as the live daemon.

### Trade Deduplication

A 3-bar cooldown per symbol/threshold prevents entering the same signal on consecutive bars. After entering, re-entry is blocked until:
- The confluence score drops below the threshold (signal fades)
- The direction reverses
- 3 bars pass (configurable via `--cooldown`)

---

## CLI Reference

| Argument | Default | Description |
|---|---|---|
| `--start` | *required* | Start date (ISO format, e.g. `2025-01-01`) |
| `--end` | `now` | End date (ISO format or `now`) |
| `--symbols` | from strategy config | Symbols to backtest (e.g. `BTCUSDT ETHUSDT`) |
| `--thresholds` | derived from config | Comma-separated entry thresholds to sweep (default: 50%/65%/80%/100% of `entry_threshold`) |
| `--strategy` | `momentum_rising` | Strategy name (loads corresponding YAML) |
| `--cooldown` | `3` | Bars to wait before re-entering same signal |
| `--batch-size` | `500` | OHLCV bars per API call (pagination) |
| `--dry-run` | `false` | Preview trades without writing to DB |
| `--log-level` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

---

## What Happens After

Once `trade_learning` has backtest data, the learning pipeline runs automatically on the next daemon cycle:

1. **Karpathy** re-scores trades at different weights → discovers "threshold 4.0 captures 47 extra trades with PF 1.8" → pushes to CandidateQueue
2. **Hyperopt** runs Optuna TPE → searches weight space → finds better weight combinations → pushes to CandidateQueue
3. **weight_adjuster** reads `signal_accuracy` (populated from `trade_learning`) → adjusts weights → `ScoreConfluence` uses them next cycle
4. **Challenger** paper-trades candidates before they reach production

No manual intervention needed. The learning modules read from `trade_learning` — they don't distinguish between backtest trades and live trades.

---

## Architecture

```
scripts/time_travel.py
  ├── indicators/registry.py        → compute_indicator()
  ├── enzymes/score_confluence.py   → scoring formula (extracted as standalone)
  ├── enzymes/record_trade_outcome.py → signal extractors (for signals_at_entry_json)
  ├── core/exchange.py              → fetch_ohlcv() (paginated historical data)
  ├── core/config_loader.py         → ConfigLoader (strategy + defaults)
  └── core/database.py              → trade_learning writes (same schema as live)
```

The script does **not** build a full `Substrate` per bar (too heavy). Instead, it extracts the scoring logic into standalone functions that mirror `ScoreConfluence` exactly.

---

## Threshold Sweep Strategy

The default sweep is derived from `scoring.entry_threshold` in the strategy config at fractions [50%, 65%, 80%, 100%]. For the default `entry_threshold: 6.5`, this produces `[3.3, 4.2, 5.2, 6.5]`:

| Threshold | Fraction | Captures | Risk |
|---|---|---|---|
| 3.3 | 50% | Almost any directional signal | Many false positives, low PF |
| 4.2 | 65% | Moderate confluence | Balanced discovery zone |
| 5.2 | 80% | Strong confluence | Fewer trades, higher quality |
| 6.5 | 100% | Production threshold | Baseline — matches live daemon |

If `entry_threshold` changes in the strategy config, the sweep adapts automatically. Override with `--thresholds` for manual control.

Karpathy evaluates: "If I lower the threshold from 6.5 to 4.0, I capture N extra trades with a combined PF of X. Is X > baseline PF?" If yes → push to CandidateQueue → Challenger validates.

---

## Performance & Progress

The script uses a **sliding window** for indicator computation instead of recomputing on a growing slice. This changes complexity from O(n²) to O(n × window), giving a ~10× speedup for long date ranges.

Progress is logged every 15 seconds during scoring:

```
21:17:53 [INFO] time_travel:   Pre-computing BTCUSDT indicators (1h)...
21:18:47 [INFO] time_travel:   1h Pre-computed indicators: 5688/5688 bars ok, 0 failed
21:18:47 [INFO] time_travel:   1h indicators done in 54.2s (105 bars/s)
21:18:47 [INFO] time_travel:   Pre-computing BTCUSDT indicators (4h)...
21:19:12 [INFO] time_travel:   4h Pre-computed indicators: 1922/1922 bars ok, 0 failed
21:19:12 [INFO] time_travel:   4h indicators done in 25.1s
21:19:12 [INFO] time_travel:   Scoring 5688 bars...
21:19:27 [INFO] time_travel:   [BTCUSDT] 1410/5688 bars (24.8%) | 3 entries | 1 trades | 94 bars/s | ETA: <1m
21:19:42 [INFO] time_travel:   [BTCUSDT] 2820/5688 bars (49.6%) | 7 entries | 3 trades | 94 bars/s | ETA: <1m
21:19:57 [INFO] time_travel:   [BTCUSDT] 4230/5688 bars (74.4%) | 12 entries | 5 trades | 94 bars/s | ETA: <1m
21:20:12 [INFO] time_travel:   [BTCUSDT] 5687/5688 bars (100.0%) | 18 entries | 8 trades | 94 bars/s | ETA: <1m
21:20:12 [INFO] time_travel:   BTCUSDT: 8 trades generated (18 entries evaluated) in 60.3s
```

The output shows:
- **bars processed / total** — how far through the date range
- **entries** — how many times a threshold was met (before cooldown filtering)
- **trades** — how many simulated trades were written to DB
- **bars/s** — processing speed
- **ETA** — estimated time remaining

## Limitations

- **No slippage or latency.** Entries use the bar's close price. Exits use the bar's high/low. Real trading has execution costs.
- **No position limits.** The script evaluates every entry opportunity independently, ignoring `max_positions`. This is intentional — we want to see *all* opportunities.
- **No LLM validation.** The live daemon can send borderline candidates to an LLM for review. Time travel skips this.
- **No soft penalties.** Noise, confluence, and trajectory penalties from the live daemon are not applied. `effective_score` equals `confluence_score_at_entry`. This is conservative — it means backtest trades are scored slightly higher than they would be live.
- **Walk-forward cap.** Trades still open after 200 bars are discarded. For 1h bars, that's ~8.3 days — reasonable for momentum strategies.
- **Sliding window approximation.** Indicators are computed on the last 400 bars, not the full history. EMA(200) converges within this window, but very long-period indicators may have slight differences from the live daemon (which uses all available data).

---

## Example Output

```
======================================================================
TIME TRAVEL — Fast-forward daemon
  Strategy: momentum_rising (uid: e600dffb-...)
  Symbols: ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
  Period: 2025-01-01 → now
  Thresholds: [3.3, 4.2, 5.2, 6.5]
  Primary TF: 1h, Confirmation TF: 4h
  Weights: {'rsi': 0.25, 'macd': 0.25, 'ema_stack': 0.3, 'adx': 0.2}
  Dry run: False
======================================================================
--------------------------------------------------
Processing BTCUSDT
  Fetching BTCUSDT OHLCV (1h)...
  Fetching BTCUSDT OHLCV (4h)...
  Primary bars: 4380, Confirmation bars: 1095
  BTCUSDT: 23 trades generated
--------------------------------------------------
Processing ETHUSDT
  ...
  ETHUSDT: 18 trades generated
--------------------------------------------------
Processing SOLUSDT
  ...
  SOLUSDT: 11 trades generated
======================================================================
TIME TRAVEL COMPLETE
  Total trades: 52
  Wins: 31, Losses: 19, Win rate: 59.6%
  By threshold: {3.3: 28, 4.2: 15, 5.2: 7, 6.5: 2}
  By symbol: {'BTCUSDT': 23, 'ETHUSDT': 18, 'SOLUSDT': 11}
======================================================================
```

The `By threshold` breakdown is the key insight: threshold 3.3 captured 28 trades, threshold 6.5 only 2. Karpathy can now evaluate whether those 26 extra trades at lower thresholds were profitable enough to justify loosening the threshold.

---

## DB Schema

Time travel writes to `trade_learning` using the **exact same columns** as the production daemon (`record_trade_outcome.py`). No schema changes needed.

| Column | Source |
|---|---|
| `strategy_name`, `strategy_uid` | From strategy config |
| `symbol`, `direction` | From confluence score sign |
| `entry_time`, `exit_time` | Bar timestamps |
| `outcome` | `win` / `loss` / `breakeven` |
| `pnl_pct` | Exit simulation |
| `duration_minutes` | Bars from entry to exit × TF minutes |
| `confluence_score_at_entry` | Normalized confluence score (0–10 scale) |
| `signals_at_entry_json` | Same format as live daemon + `_` prefixed metadata |
| `exit_reason` | `hard_stop` / `trailing_stop` / `take_profit` |
| `sl_hit`, `trailing_stop_hit` | Exit reason tracking |
| `max_favorable_excursion_pct` | MFE from walk-forward |
| `max_adverse_excursion_pct` | MAE from walk-forward |

### Metadata in `signals_at_entry_json`

Data that production doesn't store in separate columns (entry_price, exit_price, etc.) is embedded in `signals_at_entry_json` with a `_` prefix:

```json
{
  "rsi": {"signal": "bullish", "value": 32.1},
  "macd": {"signal": "bullish", "bias": "bullish_growing"},
  "ema_stack": {"signal": "bullish", "alignment": "bullish"},
  "_entry_price": 94250.0,
  "_exit_price": 96100.0,
  "_effective_score": 5.2,
  "_indicators_aligned": 3,
  "_threshold_used": 4.0,
  "_source": "time_travel"
}
```

Karpathy and Hyperopt read `signals_at_entry_json` by iterating over known indicator names (`rsi`, `macd`, `ema_stack`, `adx`, ...). Keys starting with `_` are ignored by the learning engine — they're metadata for analysis only.
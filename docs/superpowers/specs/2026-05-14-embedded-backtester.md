# Spec B (revised): Embedded Backtester + Optuna Optimizer — Design Spec
*Date: 2026-05-14 · Status: Approved · Covers: F4, F5*
*Replaces: freqtrade-backtesting.md (Freqtrade not used — runs embedded on Pi)*

---

## Overview

Two new Python modules added to the journal running on the Pi:
- **F4** — `backtest_engine.py`: vectorized backtester using the journal's own indicator stack. Exposed via `POST /api/backtest/run`. Results displayed in a new UI card.
- **F5** — `backtest_optimizer.py`: Optuna Bayesian optimizer that finds the best RSI/WaveTrend/confluence thresholds by maximising Sharpe from the backtester. Results written to the `settings` DB table for apply-on-demand.

No Freqtrade. No Mac. All runs on Pi alongside the journal. `pip install optuna` added to `requirements.txt`.

**Freqtrade code borrowed (GPL → GPL):** metric formulas for Sharpe ratio, max drawdown, profit factor adapted from `freqtrade/optimize/optimize_reports.py`. Logic pattern for walk-forward trade simulation adapted from `freqtrade/optimize/backtesting.py`.

---

## Architecture

```
backtest_engine.py (new)
  ├── BacktestParams  — dataclass: sl_pct, tp1_pct, tp2_pct, min_confluence, wt_oversold, rsi_max, adx_min
  ├── BacktestTrade   — dataclass: entry_price, exit_price, outcome, pnl_pct, entry_time, exit_time
  ├── BacktestResult  — dataclass: trades, sharpe, sortino, max_drawdown, profit_factor, win_rate, total_trades
  ├── _fetch_ohlcv(symbol, timeframe, limit) → pd.DataFrame  [calls bitget_client, paginates]
  ├── _compute_signals(df) → pd.DataFrame  [vectorized: RSI/EMA/WT/ADX/MFI/CVD on full df]
  ├── _simulate_trades(df, params) → list[BacktestTrade]  [walk-forward: SL/TP hit detection]
  ├── _compute_metrics(trades) → dict  [Sharpe, Sortino, drawdown, profit factor, win rate]
  └── run_backtest(symbol, timeframe, days, params) → BacktestResult

backtest_metrics.py (new)
  ├── sharpe_ratio(returns: list[float], periods_per_year: int) → float
  ├── sortino_ratio(returns: list[float], periods_per_year: int) → float
  ├── max_drawdown(equity_curve: list[float]) → float
  └── profit_factor(pnls: list[float]) → float

backtest_optimizer.py (new)
  ├── _objective(trial, symbol, timeframe, days) → float  [Optuna trial → Sharpe score]
  └── run_optimizer(symbol, timeframe, days, n_trials) → dict  [best params]

routes/backtest.py (new Flask blueprint)
  ├── POST /api/backtest/run   — {symbol, timeframe, days} → starts backtest, returns result
  └── GET  /api/backtest/optimize — {symbol, n_trials} → runs Optuna, returns best params

app.py — register backtest blueprint
static/js/09-analysis.js — add backtest result card (symbol input + Run Backtest button + results)
templates/index.html — bump 09-analysis.js version
```

---

## F4 — Backtest Engine

### `_fetch_ohlcv(symbol, timeframe, limit)`

Uses `bitget_client._get("/api/v2/mix/market/candles", ...)` with pagination (2 calls for 1000 candles = ~6 months of 4H). Returns `pd.DataFrame` with columns: `timestamp, open, high, low, close, volume`. Already available via `chart_context.get_candles()` — reuse it.

```python
from chart_context import get_candles
df = get_candles(symbol, timeframe, limit=limit)  # existing function, already cached
```

### `_compute_signals(df)` — vectorized indicators

Adds columns to the full OHLCV dataframe in one pass (no per-row loops):

| New column | Computation |
|-----------|-------------|
| `rsi` | RSI-14 (pandas_ta) |
| `ema_20`, `ema_50`, `ema_200` | EMA via ewm |
| `macd_hist` | MACD histogram (pandas_ta) |
| `adx` | ADX-14 (pandas_ta) |
| `wt1`, `wt2` | WaveTrend (same formula as `chart_indicators.compute_wavetrend`) |
| `mfi` | `(ta.rsi(hlc3*volume, 60) - 50) * 2` |
| `cvd` | `((close-open)/(high-low+1e-9)*volume).cumsum()` → trend = `cvd > cvd.shift(20)` |
| `vol_ratio` | `volume / volume.rolling(20).mean()` |
| `wt_buy` | `(wt1 < params.wt_oversold) & (wt1 > wt1.shift(1))` |
| `confluence` | Weighted sum of signal booleans (mirrors `chart_context.py` weights) |
| `entry_signal` | `wt_buy & (rsi < params.rsi_max) & (adx >= params.adx_min) & (confluence >= params.min_confluence)` |

### `_simulate_trades(df, params)` — walk-forward

```python
trades = []
i = 200  # skip warmup
while i < len(df) - 1:
    if not df['entry_signal'].iloc[i]:
        i += 1
        continue
    entry = df['close'].iloc[i]
    sl    = entry * (1 - params.sl_pct)
    tp1   = entry * (1 + params.tp1_pct)
    tp2   = entry * (1 + params.tp2_pct)
    # Walk forward candle by candle to find first hit
    for j in range(i + 1, min(i + 200, len(df))):
        lo, hi = df['low'].iloc[j], df['high'].iloc[j]
        if lo <= sl:
            trades.append(BacktestTrade(..., outcome='sl',  exit_price=sl))
            i = j; break
        elif hi >= tp2:
            trades.append(BacktestTrade(..., outcome='tp2', exit_price=tp2))
            i = j; break
        elif hi >= tp1:
            trades.append(BacktestTrade(..., outcome='tp1', exit_price=tp1))
            i = j; break
    else:
        i += 1
return trades
```

### Default `BacktestParams`

```python
@dataclass
class BacktestParams:
    sl_pct:          float = 0.10   # 10% stop loss
    tp1_pct:         float = 0.05   # 5% TP1
    tp2_pct:         float = 0.10   # 10% TP2
    min_confluence:  float = 0.33   # minimum confluence fraction
    wt_oversold:     float = -53.0  # WaveTrend oversold threshold
    rsi_max:         float = 65.0   # max RSI at entry
    adx_min:         float = 15.0   # min ADX at entry
```

---

## `backtest_metrics.py` — adapted from Freqtrade

```python
def sharpe_ratio(returns: list, periods_per_year: int = 1460) -> float:
    """Annualised Sharpe. periods_per_year=1460 for 4H candles (6/day × 365 × ~2/3 active)."""
    import numpy as np
    r = np.array(returns)
    if len(r) < 2 or r.std() == 0:
        return 0.0
    return float(r.mean() / r.std() * (periods_per_year ** 0.5))

def max_drawdown(equity_curve: list) -> float:
    """Maximum peak-to-trough drawdown as a positive fraction."""
    import numpy as np
    eq = np.array(equity_curve)
    peak = np.maximum.accumulate(eq)
    dd   = (eq - peak) / peak
    return float(abs(dd.min())) if len(dd) else 0.0

def profit_factor(pnls: list) -> float:
    wins  = sum(p for p in pnls if p > 0)
    loses = sum(abs(p) for p in pnls if p < 0)
    return round(wins / loses, 2) if loses else 0.0
```

---

## F5 — Optuna Optimizer

```python
import optuna
optuna.logging.set_verbosity(optuna.logging.WARNING)

def _objective(trial, symbol: str, timeframe: str, days: int) -> float:
    params = BacktestParams(
        wt_oversold    = trial.suggest_float('wt_oversold',    -80, -40),
        rsi_max        = trial.suggest_float('rsi_max',         45,  70),
        adx_min        = trial.suggest_float('adx_min',         10,  25),
        min_confluence = trial.suggest_float('min_confluence', 0.25, 0.55),
        sl_pct         = trial.suggest_float('sl_pct',         0.05, 0.20),
        tp1_pct        = trial.suggest_float('tp1_pct',        0.03, 0.10),
        tp2_pct        = trial.suggest_float('tp2_pct',        0.08, 0.20),
    )
    result = run_backtest(symbol, timeframe, days, params)
    if result.total_trades < 10:
        return -999.0   # penalise strategies that barely trade
    return result.sharpe

def run_optimizer(symbol: str = "BTCUSDT", timeframe: str = "4H",
                  days: int = 180, n_trials: int = 100) -> dict:
    study = optuna.create_study(direction='maximize')
    study.optimize(lambda t: _objective(t, symbol, timeframe, days),
                   n_trials=n_trials, n_jobs=1)
    return study.best_params
```

---

## API endpoints

**`POST /api/backtest/run`**
Body: `{"symbol": "BTCUSDT", "timeframe": "4H", "days": 180}`
Returns:
```json
{
  "ok": true,
  "data": {
    "symbol": "BTCUSDT",
    "total_trades": 34,
    "win_rate": 62.5,
    "profit_factor": 1.84,
    "sharpe": 1.21,
    "max_drawdown": 0.12,
    "days": 180
  }
}
```

**`GET /api/backtest/optimize`**
Query: `?symbol=BTCUSDT&n_trials=50`
Returns: `{"ok": true, "data": {"wt_oversold": -61.2, "rsi_max": 58.0, ...}}`
Note: Long-running (minutes). Client polls via existing poller pattern.

---

## UI — Backtest Card in `09-analysis.js`

New card below the accuracy progress widget on the Calls tab:

```
📈 Backtest (6M · 4H)
[BTCUSDT ▾] [▸ Run]
──────────────────────────────
Trades: 34 · Win rate: 62.5%
Profit factor: 1.84 · Sharpe: 1.21
Max drawdown: 12.0%
```

Single `loadBacktest(symbol)` function — `POST /api/backtest/run`, renders result card. No live-updating — user manually triggers.

---

## Files Changed / Created

| File | Change |
|------|--------|
| `requirements.txt` | Add `optuna>=3.0.0` (ccxt already added from Spec A) |
| `backtest_engine.py` | **New** — fetch + compute + simulate + metrics |
| `backtest_metrics.py` | **New** — Sharpe, Sortino, drawdown, profit factor |
| `backtest_optimizer.py` | **New** — Optuna objective + run_optimizer |
| `routes/backtest.py` | **New** — Flask blueprint: /api/backtest/run, /api/backtest/optimize |
| `app.py` | Register backtest blueprint |
| `static/js/09-analysis.js` | Add backtest card UI |
| `templates/index.html` | Bump 09-analysis.js version |

No DB migrations. No Pi deployment change (systemd picks up new modules automatically).

---

## Testing

- `tests/test_backtest_engine.py` — unit tests with synthetic OHLCV data:
  - `test_run_backtest_returns_result_shape()` — result has all required fields
  - `test_sl_hit_detected()` — candle low < SL → outcome = 'sl'
  - `test_tp1_hit_detected()` — candle high > TP1 → outcome = 'tp1'
  - `test_sharpe_positive_on_winning_trades()` — Sharpe > 0 when all trades win
  - `test_max_drawdown_zero_on_monotonic_equity()` — no drawdown if equity only rises

- `tests/test_backtest_metrics.py` — pure unit tests (no DB/API):
  - `test_profit_factor_2x()` — 2 wins of 10%, 1 loss of 10% → PF = 2.0
  - `test_max_drawdown_50pct()` — equity [100,150,75] → drawdown = 0.50
  - `test_sharpe_zero_std()` — all returns identical → returns 0.0

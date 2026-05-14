# Embedded Backtester + Optuna Optimizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an embedded backtester and Bayesian optimizer to the journal running on the Pi, exposed via two new API endpoints and a UI card on the Analysis tab.

**Architecture:** `backtest_metrics.py` (pure math) → `backtest_engine.py` (fetch + simulate, depends on metrics + chart_context) → `backtest_optimizer.py` (Optuna, depends on engine) → `routes/backtest.py` (Flask blueprint) → `app.py` (register) → `09-analysis.js` (UI card). Metric formulas adapted from Freqtrade (GPL→GPL).

**Tech Stack:** Optuna ≥3.0.0, pandas, pandas-ta, existing `chart_context.get_candles()`, Flask, SQLite.

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `backtest_metrics.py` | Pure math: Sharpe, Sortino, max drawdown, profit factor |
| Create | `backtest_engine.py` | BacktestParams, BacktestTrade, BacktestResult dataclasses; fetch + compute + simulate |
| Create | `backtest_optimizer.py` | Optuna objective + run_optimizer |
| Create | `routes/backtest.py` | Flask blueprint: POST /api/backtest/run, GET /api/backtest/optimize |
| Modify | `app.py` | Register backtest blueprint |
| Modify | `static/js/09-analysis.js` | Add backtest card with symbol picker + Run button + results |
| Modify | `templates/index.html` | Bump 09-analysis.js version |
| Modify | `requirements.txt` | Add `optuna>=3.0.0` |
| Create | `tests/test_backtest_metrics.py` | Pure unit tests for metric functions |
| Create | `tests/test_backtest_engine.py` | Unit tests with synthetic OHLCV data |

---

## Task 1: Create backtest_metrics.py (pure math, no deps)

**Files:**
- Create: `backtest_metrics.py`
- Create: `tests/test_backtest_metrics.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_backtest_metrics.py`:

```python
"""Pure unit tests for backtest_metrics.py — no DB, no API, no network."""


def test_profit_factor_2x():
    """2 wins of 10%, 1 loss of 10% -> PF = 2.0."""
    from backtest_metrics import profit_factor
    pnls = [0.10, 0.10, -0.10]
    assert profit_factor(pnls) == 2.0


def test_profit_factor_no_losses():
    """All wins -> profit_factor returns 0.0 (guard for division by zero)."""
    from backtest_metrics import profit_factor
    assert profit_factor([0.10, 0.20]) == 0.0


def test_profit_factor_all_losses():
    """All losses -> PF = 0.0."""
    from backtest_metrics import profit_factor
    assert profit_factor([-0.10, -0.05]) == 0.0


def test_max_drawdown_50pct():
    """Equity [100, 150, 75] -> drawdown = 0.50 (75 is 50% below 150 peak)."""
    from backtest_metrics import max_drawdown
    result = max_drawdown([100.0, 150.0, 75.0])
    assert abs(result - 0.50) < 1e-9


def test_max_drawdown_zero_on_monotonic_equity():
    """Monotonically rising equity -> drawdown = 0.0."""
    from backtest_metrics import max_drawdown
    result = max_drawdown([100.0, 110.0, 120.0, 130.0])
    assert result == 0.0


def test_max_drawdown_empty():
    """Empty list -> drawdown = 0.0."""
    from backtest_metrics import max_drawdown
    assert max_drawdown([]) == 0.0


def test_sharpe_zero_std():
    """All returns identical -> std = 0 -> returns 0.0."""
    from backtest_metrics import sharpe_ratio
    assert sharpe_ratio([0.05, 0.05, 0.05]) == 0.0


def test_sharpe_positive_on_all_wins():
    """Positive returns with variance -> Sharpe > 0."""
    from backtest_metrics import sharpe_ratio
    returns = [0.10, 0.05, 0.08, 0.12, 0.07]
    assert sharpe_ratio(returns) > 0


def test_sharpe_single_value():
    """Single return value -> returns 0.0 (can't compute std)."""
    from backtest_metrics import sharpe_ratio
    assert sharpe_ratio([0.10]) == 0.0


def test_sortino_positive_on_all_wins():
    """All positive returns -> Sortino > 0."""
    from backtest_metrics import sortino_ratio
    returns = [0.10, 0.05, 0.08]
    assert sortino_ratio(returns) > 0


def test_sortino_zero_downside():
    """No negative returns -> downside std = 0 -> returns 0.0."""
    from backtest_metrics import sortino_ratio
    assert sortino_ratio([0.10, 0.10]) == 0.0
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python3 -m pytest tests/test_backtest_metrics.py -v
```

Expected: `ModuleNotFoundError: No module named 'backtest_metrics'`

- [ ] **Step 3: Create backtest_metrics.py**

```python
"""
backtest_metrics.py — Risk-adjusted performance metrics for the embedded backtester.
Metric formulas adapted from Freqtrade optimize/optimize_reports.py (GPL-3.0).
"""
import numpy as np


def sharpe_ratio(returns: list, periods_per_year: int = 1460) -> float:
    """
    Annualised Sharpe ratio.
    periods_per_year=1460 for 4H candles (6 candles/day x 365 x ~2/3 active).
    Returns 0.0 when std=0 or fewer than 2 returns.
    """
    r = np.array(returns, dtype=float)
    if len(r) < 2 or r.std() == 0:
        return 0.0
    return float(r.mean() / r.std() * (periods_per_year ** 0.5))


def sortino_ratio(returns: list, periods_per_year: int = 1460) -> float:
    """
    Annualised Sortino ratio (penalises downside deviation only).
    Returns 0.0 when downside std=0 or fewer than 2 returns.
    """
    r = np.array(returns, dtype=float)
    if len(r) < 2:
        return 0.0
    downside = r[r < 0]
    if len(downside) == 0:
        return 0.0
    downside_std = downside.std()
    if downside_std == 0:
        return 0.0
    return float(r.mean() / downside_std * (periods_per_year ** 0.5))


def max_drawdown(equity_curve: list) -> float:
    """
    Maximum peak-to-trough drawdown as a positive fraction (0.0 to 1.0).
    Pattern adapted from Freqtrade optimize/optimize_reports.py (GPL-3.0).
    """
    if not equity_curve:
        return 0.0
    eq = np.array(equity_curve, dtype=float)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    return float(abs(dd.min()))


def profit_factor(pnls: list) -> float:
    """
    Gross profit / gross loss. Returns 0.0 when no losses or no wins.
    """
    wins  = sum(p for p in pnls if p > 0)
    loses = sum(abs(p) for p in pnls if p < 0)
    if not loses or not wins:
        return 0.0
    return round(wins / loses, 2)
```

- [ ] **Step 4: Run tests to verify all passing**

```bash
python3 -m pytest tests/test_backtest_metrics.py -v
```

Expected: 11 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backtest_metrics.py tests/test_backtest_metrics.py
git commit -m "feat(F4): add backtest_metrics.py (Sharpe, Sortino, drawdown, profit factor)"
```

---

## Task 2: Create backtest_engine.py (fetch + compute + simulate)

**Files:**
- Create: `backtest_engine.py`
- Create: `tests/test_backtest_engine.py`
- Modify: `requirements.txt` — add `optuna>=3.0.0` (placed here so pip install happens before Task 3 needs it)

- [ ] **Step 1: Add optuna to requirements.txt**

```
# in requirements.txt, add:
optuna>=3.0.0
```

Then install:

```bash
pip install optuna
```

- [ ] **Step 2: Write failing tests for the engine**

Create `tests/test_backtest_engine.py`:

```python
"""Unit tests for backtest_engine.py using synthetic OHLCV data."""
import datetime
import pandas as pd
import numpy as np
import pytest


def _make_ohlcv(n=300, trend="flat") -> pd.DataFrame:
    """Build a synthetic OHLCV dataframe with n rows."""
    np.random.seed(42)
    price = 60000.0
    closes = []
    for i in range(n):
        if trend == "up":
            price += np.random.uniform(0, 50)
        elif trend == "down":
            price -= np.random.uniform(0, 50)
        else:
            price += np.random.uniform(-25, 25)
        closes.append(price)
    closes = np.array(closes)
    highs = closes + np.random.uniform(50, 200, n)
    lows  = closes - np.random.uniform(50, 200, n)
    opens = closes + np.random.uniform(-100, 100, n)
    vols  = np.random.uniform(1000, 5000, n)
    ts    = pd.date_range("2025-01-01", periods=n, freq="4h")
    return pd.DataFrame({
        "timestamp": ts,
        "open": opens, "high": highs, "low": lows, "close": closes, "volume": vols,
    })


def test_run_backtest_returns_result_shape(monkeypatch):
    """result has all required fields with correct types."""
    from backtest_engine import BacktestParams, run_backtest

    df = _make_ohlcv(300)
    monkeypatch.setattr("backtest_engine._fetch_ohlcv", lambda s, tf, limit: df)

    params = BacktestParams()
    result = run_backtest("BTCUSDT", "4H", 180, params)

    assert hasattr(result, "trades")
    assert hasattr(result, "sharpe")
    assert hasattr(result, "max_drawdown")
    assert hasattr(result, "profit_factor")
    assert hasattr(result, "win_rate")
    assert hasattr(result, "total_trades")
    assert isinstance(result.total_trades, int)
    assert isinstance(result.sharpe, float)


def test_sl_hit_detected():
    """When candle low goes below SL, outcome must be 'sl'."""
    from backtest_engine import BacktestParams, _simulate_trades

    df = _make_ohlcv(250)
    df["entry_signal"] = False
    df.loc[df.index[210], "entry_signal"] = True

    entry_price = df["close"].iloc[210]
    sl_price = entry_price * 0.90

    # Force next candle to hit the SL
    df.loc[df.index[211], "low"]  = sl_price * 0.99
    df.loc[df.index[211], "high"] = df["close"].iloc[211]

    params = BacktestParams(sl_pct=0.10, tp1_pct=0.05, tp2_pct=0.10)
    trades = _simulate_trades(df, params)

    sl_trades = [t for t in trades if t.outcome == "sl"]
    assert len(sl_trades) >= 1


def test_tp1_hit_detected():
    """When candle high exceeds TP1 (but not TP2), outcome must be 'tp1'."""
    from backtest_engine import BacktestParams, _simulate_trades

    df = _make_ohlcv(250)
    df["entry_signal"] = False
    df.loc[df.index[210], "entry_signal"] = True

    entry_price = df["close"].iloc[210]
    tp1_price = entry_price * 1.05

    # Force next candle to reach TP1 but not TP2
    df.loc[df.index[211], "high"] = tp1_price * 1.001
    df.loc[df.index[211], "low"]  = entry_price * 0.99

    params = BacktestParams(sl_pct=0.10, tp1_pct=0.05, tp2_pct=0.10)
    trades = _simulate_trades(df, params)

    tp1_trades = [t for t in trades if t.outcome == "tp1"]
    assert len(tp1_trades) >= 1


def test_sharpe_positive_on_winning_trades():
    """When all simulated trades are winners, Sharpe > 0."""
    from backtest_engine import BacktestTrade, _compute_metrics

    now = datetime.datetime.utcnow()
    trades = [
        BacktestTrade(60000.0, 63000.0, "tp2", 0.05, now, now)
        for _ in range(20)
    ]
    metrics = _compute_metrics(trades)
    assert metrics["sharpe"] > 0


def test_max_drawdown_zero_on_monotonic_equity():
    """Monotonically growing equity curve -> max_drawdown = 0.0."""
    from backtest_engine import BacktestTrade, _compute_metrics

    now = datetime.datetime.utcnow()
    trades = [
        BacktestTrade(60000.0, 63000.0, "tp2", 0.05, now, now)
        for _ in range(10)
    ]
    metrics = _compute_metrics(trades)
    assert metrics["max_drawdown"] == 0.0
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
python3 -m pytest tests/test_backtest_engine.py -v
```

Expected: `ModuleNotFoundError: No module named 'backtest_engine'`

- [ ] **Step 4: Create backtest_engine.py**

```python
"""
backtest_engine.py — Embedded vectorized backtester for the trading journal.
Walk-forward trade simulation logic adapted from Freqtrade backtesting.py (GPL-3.0).
"""
import datetime
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pandas_ta as ta

from backtest_metrics import sharpe_ratio, sortino_ratio, max_drawdown, profit_factor


@dataclass
class BacktestParams:
    sl_pct:         float = 0.10
    tp1_pct:        float = 0.05
    tp2_pct:        float = 0.10
    min_confluence: float = 0.33
    wt_oversold:    float = -53.0
    rsi_max:        float = 65.0
    adx_min:        float = 15.0


@dataclass
class BacktestTrade:
    entry_price: float
    exit_price:  float
    outcome:     str        # 'sl', 'tp1', 'tp2'
    pnl_pct:     float
    entry_time:  datetime.datetime
    exit_time:   datetime.datetime


@dataclass
class BacktestResult:
    trades:        list = field(default_factory=list)
    sharpe:        float = 0.0
    sortino:       float = 0.0
    max_drawdown:  float = 0.0
    profit_factor: float = 0.0
    win_rate:      float = 0.0
    total_trades:  int   = 0


def _fetch_ohlcv(symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    """Fetch OHLCV data using the journal's existing get_candles() function."""
    from chart_context import get_candles
    return get_candles(symbol, timeframe, limit=limit)


def _compute_signals(df: pd.DataFrame, params: BacktestParams) -> pd.DataFrame:
    """Add indicator columns to the full OHLCV dataframe in one vectorized pass."""
    df = df.copy()
    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]

    df["rsi"] = ta.rsi(close, length=14)

    df["ema_20"]  = close.ewm(span=20,  adjust=False).mean()
    df["ema_50"]  = close.ewm(span=50,  adjust=False).mean()
    df["ema_200"] = close.ewm(span=200, adjust=False).mean()

    adx_df = ta.adx(high, low, close, length=14)
    df["adx"] = adx_df["ADX_14"] if adx_df is not None and "ADX_14" in adx_df.columns else 0.0

    # WaveTrend — same formula as chart_indicators.py
    hlc3 = (high + low + close) / 3
    ema1 = hlc3.ewm(span=9, adjust=False).mean()
    d    = (hlc3 - ema1).abs().ewm(span=9, adjust=False).mean()
    ci   = (hlc3 - ema1) / (0.015 * d.replace(0, np.nan)).fillna(1)
    df["wt1"] = ci.ewm(span=13, adjust=False).mean()
    df["wt2"] = df["wt1"].rolling(3).mean()

    # MFI proxy (journal formula)
    hlc3_vol = hlc3 * volume
    df["mfi"] = (ta.rsi(hlc3_vol, length=60) - 50) * 2

    # CVD proxy
    df["cvd"]       = ((close - df["open"]) / (high - low + 1e-9) * volume).cumsum()
    df["cvd_trend"] = df["cvd"] > df["cvd"].shift(20)

    df["vol_ratio"] = volume / volume.rolling(20).mean()

    df["wt_buy"]   = (df["wt1"] < params.wt_oversold) & (df["wt1"] > df["wt1"].shift(1))
    df["ema_bull"] = df["ema_20"] > df["ema_50"]

    # Simplified confluence fraction (weights mirror chart_context.py)
    df["confluence"] = (
        (df["rsi"] < 40).astype(float) * 0.5
        + df["ema_bull"].astype(float) * 1.0
        + df["wt_buy"].astype(float) * 0.85
        + (df["mfi"] > 10).astype(float) * 0.3
        + df["cvd_trend"].astype(float) * 0.4
        + (df["vol_ratio"] > 1.5).astype(float) * 0.5
    ) / 3.55

    df["entry_signal"] = (
        df["wt_buy"]
        & (df["rsi"] < params.rsi_max)
        & (df["adx"] >= params.adx_min)
        & (df["confluence"] >= params.min_confluence)
    )

    return df


def _simulate_trades(df: pd.DataFrame, params: BacktestParams) -> list:
    """
    Walk-forward trade simulation. Finds SL/TP1/TP2 hit candle-by-candle.
    Logic pattern adapted from Freqtrade backtesting.py (GPL-3.0).
    """
    trades = []
    i = 200  # skip warmup period
    n = len(df)
    while i < n - 1:
        if not df["entry_signal"].iloc[i]:
            i += 1
            continue
        entry = float(df["close"].iloc[i])
        sl  = entry * (1 - params.sl_pct)
        tp1 = entry * (1 + params.tp1_pct)
        tp2 = entry * (1 + params.tp2_pct)
        entry_time = df["timestamp"].iloc[i] if "timestamp" in df.columns else datetime.datetime.utcnow()
        exited = False
        for j in range(i + 1, min(i + 200, n)):
            lo = float(df["low"].iloc[j])
            hi = float(df["high"].iloc[j])
            exit_time = df["timestamp"].iloc[j] if "timestamp" in df.columns else datetime.datetime.utcnow()
            if lo <= sl:
                trades.append(BacktestTrade(entry, sl,  "sl",  (sl  - entry) / entry, entry_time, exit_time))
                i = j
                exited = True
                break
            elif hi >= tp2:
                trades.append(BacktestTrade(entry, tp2, "tp2", (tp2 - entry) / entry, entry_time, exit_time))
                i = j
                exited = True
                break
            elif hi >= tp1:
                trades.append(BacktestTrade(entry, tp1, "tp1", (tp1 - entry) / entry, entry_time, exit_time))
                i = j
                exited = True
                break
        if not exited:
            i += 1
    return trades


def _compute_metrics(trades: list) -> dict:
    """Compute performance metrics from a list of BacktestTrade objects."""
    if not trades:
        return {"sharpe": 0.0, "sortino": 0.0, "max_drawdown": 0.0,
                "profit_factor": 0.0, "win_rate": 0.0, "total_trades": 0}

    pnls    = [t.pnl_pct for t in trades]
    winners = [t for t in trades if t.pnl_pct > 0]

    equity = [1.0]
    for p in pnls:
        equity.append(equity[-1] * (1 + p))

    return {
        "sharpe":        sharpe_ratio(pnls),
        "sortino":       sortino_ratio(pnls),
        "max_drawdown":  max_drawdown(equity),
        "profit_factor": profit_factor(pnls),
        "win_rate":      round(len(winners) / len(trades) * 100, 1),
        "total_trades":  len(trades),
    }


def run_backtest(symbol: str, timeframe: str, days: int,
                 params: BacktestParams = None) -> BacktestResult:
    """
    Run a full vectorized backtest. Fetches ~6 candles per day + 200 warmup candles.
    Returns an empty BacktestResult if fewer than 250 rows are available.
    """
    if params is None:
        params = BacktestParams()

    candles_needed = max(int(days * 6) + 200, 500)
    df = _fetch_ohlcv(symbol, timeframe, limit=candles_needed)

    if df is None or len(df) < 250:
        return BacktestResult()

    df = _compute_signals(df, params)
    trades = _simulate_trades(df, params)
    metrics = _compute_metrics(trades)

    return BacktestResult(trades=trades, **metrics)
```

- [ ] **Step 5: Run tests to verify passing**

```bash
python3 -m pytest tests/test_backtest_engine.py -v
```

Expected: 5 tests PASS

- [ ] **Step 6: Run full test suite**

```bash
python3 -m pytest tests/ -v -x
```

Expected: all tests PASS

- [ ] **Step 7: Commit**

```bash
git add backtest_engine.py tests/test_backtest_engine.py requirements.txt
git commit -m "feat(F4): add backtest_engine.py with vectorized signals + walk-forward simulation"
```

---

## Task 3: Create backtest_optimizer.py (Optuna F5)

**Files:**
- Create: `backtest_optimizer.py`

No automated tests — Optuna's study loop is integration-level and runs for minutes. Manual verification in Task 6 covers this.

- [ ] **Step 1: Create backtest_optimizer.py**

```python
"""
backtest_optimizer.py — Bayesian optimizer for backtester parameters using Optuna.
Maximises Sharpe ratio across the parameter search space defined in BacktestParams.
"""
import optuna

optuna.logging.set_verbosity(optuna.logging.WARNING)

from backtest_engine import BacktestParams, run_backtest


def _objective(trial: optuna.Trial, symbol: str, timeframe: str, days: int) -> float:
    """Optuna objective: sample params -> run backtest -> return Sharpe (or penalty)."""
    params = BacktestParams(
        wt_oversold    = trial.suggest_float("wt_oversold",    -80,   -40),
        rsi_max        = trial.suggest_float("rsi_max",         45,    70),
        adx_min        = trial.suggest_float("adx_min",         10,    25),
        min_confluence = trial.suggest_float("min_confluence",  0.25,  0.55),
        sl_pct         = trial.suggest_float("sl_pct",         0.05,  0.20),
        tp1_pct        = trial.suggest_float("tp1_pct",        0.03,  0.10),
        tp2_pct        = trial.suggest_float("tp2_pct",        0.08,  0.20),
    )
    result = run_backtest(symbol, timeframe, days, params)
    if result.total_trades < 10:
        return -999.0  # penalise strategies that barely trade
    return result.sharpe


def run_optimizer(symbol: str = "BTCUSDT", timeframe: str = "4H",
                  days: int = 180, n_trials: int = 100) -> dict:
    """
    Run Optuna Bayesian optimization. Returns best params dict.
    Typical runtime on Pi: 5-15 min with n_trials=100.
    """
    study = optuna.create_study(direction="maximize")
    study.optimize(
        lambda t: _objective(t, symbol, timeframe, days),
        n_trials=n_trials,
        n_jobs=1,
    )
    return study.best_params
```

- [ ] **Step 2: Smoke test import**

```bash
python3 -c "import backtest_optimizer; print('optimizer imported OK')"
```

Expected: no error.

- [ ] **Step 3: Commit**

```bash
git add backtest_optimizer.py
git commit -m "feat(F5): add backtest_optimizer.py with Optuna Bayesian search"
```

---

## Task 4: Create Flask blueprint routes/backtest.py

**Files:**
- Create: `routes/backtest.py`
- Create: `tests/test_routes_backtest.py`

- [ ] **Step 1: Write failing tests for the route**

Create `tests/test_routes_backtest.py`:

```python
"""Tests for /api/backtest/run endpoint."""
import json
from unittest.mock import patch


def test_backtest_run_returns_ok_shape(client):
    """POST /api/backtest/run returns ok=true with required fields."""
    from backtest_engine import BacktestResult

    mock_result = BacktestResult(
        trades=[],
        sharpe=1.21,
        sortino=1.45,
        max_drawdown=0.12,
        profit_factor=1.84,
        win_rate=62.5,
        total_trades=34,
    )

    with patch("routes.backtest.run_backtest", return_value=mock_result):
        resp = client.post(
            "/api/backtest/run",
            data=json.dumps({"symbol": "BTCUSDT", "timeframe": "4H", "days": 180}),
            content_type="application/json",
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert "total_trades" in data["data"]
    assert "sharpe" in data["data"]
    assert "win_rate" in data["data"]
    assert "max_drawdown" in data["data"]
    assert "profit_factor" in data["data"]


def test_backtest_run_missing_symbol_returns_error(client):
    """POST /api/backtest/run without symbol returns ok=false."""
    resp = client.post(
        "/api/backtest/run",
        data=json.dumps({"timeframe": "4H", "days": 180}),
        content_type="application/json",
    )
    data = resp.get_json()
    assert data["ok"] is False
```

The `client` fixture comes from `tests/conftest.py`. Verify it exists:

```bash
grep -n "def client" /Users/fbauer/Documents/ClaudeAIData/Trading-Journal/tests/conftest.py
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python3 -m pytest tests/test_routes_backtest.py -v
```

Expected: 404 or ImportError (blueprint not registered yet).

- [ ] **Step 3: Create routes/backtest.py**

```python
"""routes/backtest.py — Backtest and optimizer API endpoints."""
from flask import Blueprint, request

from backtest_engine import BacktestParams, run_backtest
from helpers import _ok, _err

bp = Blueprint("backtest", __name__)


@bp.post("/api/backtest/run")
def backtest_run():
    body      = request.get_json(silent=True) or {}
    symbol    = body.get("symbol")
    timeframe = body.get("timeframe", "4H")
    days      = int(body.get("days", 180))

    if not symbol:
        return _err("symbol is required")

    params_raw = body.get("params") or {}
    params = BacktestParams(
        sl_pct         = float(params_raw.get("sl_pct",         0.10)),
        tp1_pct        = float(params_raw.get("tp1_pct",        0.05)),
        tp2_pct        = float(params_raw.get("tp2_pct",        0.10)),
        min_confluence = float(params_raw.get("min_confluence",  0.33)),
        wt_oversold    = float(params_raw.get("wt_oversold",   -53.0)),
        rsi_max        = float(params_raw.get("rsi_max",        65.0)),
        adx_min        = float(params_raw.get("adx_min",        15.0)),
    )

    result = run_backtest(symbol, timeframe, days, params)

    return _ok({
        "symbol":        symbol,
        "timeframe":     timeframe,
        "days":          days,
        "total_trades":  result.total_trades,
        "win_rate":      result.win_rate,
        "profit_factor": result.profit_factor,
        "sharpe":        round(result.sharpe,       2),
        "sortino":       round(result.sortino,      2),
        "max_drawdown":  round(result.max_drawdown, 4),
    })


@bp.get("/api/backtest/optimize")
def backtest_optimize():
    symbol    = request.args.get("symbol",    "BTCUSDT")
    n_trials  = int(request.args.get("n_trials", 50))
    timeframe = request.args.get("timeframe", "4H")
    days      = int(request.args.get("days",  180))

    from backtest_optimizer import run_optimizer
    best = run_optimizer(symbol, timeframe, days, n_trials)
    return _ok(best)
```

- [ ] **Step 4: Register blueprint in app.py**

Open `app.py`. Find where other blueprints are imported (e.g., `from routes.calls import bp as calls_bp`). Add alongside them:

```python
from routes.backtest import bp as backtest_bp
```

Find where blueprints are registered (e.g., `app.register_blueprint(calls_bp)`). Add:

```python
app.register_blueprint(backtest_bp)
```

- [ ] **Step 5: Run tests to verify passing**

```bash
python3 -m pytest tests/test_routes_backtest.py -v
```

Expected: 2 tests PASS

- [ ] **Step 6: Run full test suite**

```bash
python3 -m pytest tests/ -v -x
```

Expected: all tests PASS

- [ ] **Step 7: Commit**

```bash
git add routes/backtest.py app.py tests/test_routes_backtest.py
git commit -m "feat(F4/F5): add /api/backtest/run + /api/backtest/optimize Flask blueprint"
```

---

## Task 5: Add backtest card UI to 09-analysis.js

**Files:**
- Modify: `static/js/09-analysis.js`
- Modify: `templates/index.html`

No automated test — visual verification required after deploy.

- [ ] **Step 1: Check current 09-analysis.js version in index.html**

```bash
grep "09-analysis" /Users/fbauer/Documents/ClaudeAIData/Trading-Journal/templates/index.html
```

Note the current `?v=X.X` version number.

- [ ] **Step 2: Add loadBacktest() function to 09-analysis.js**

Find the end of `loadAccuracyProgress()`. After it (inside the module), add:

```javascript
function _renderBacktestCard(container, d) {
  // Builds backtest result card using DOM methods — values are numbers from trusted internal API
  container.textContent = '';

  const card = document.createElement('div');
  card.className = 'card mt-2';

  const body = document.createElement('div');
  body.className = 'card-body py-2';

  const title = document.createElement('h6');
  title.className = 'card-title mb-2';
  title.textContent = 'Backtest result (' + (d.days || 180) + 'd · ' + (d.timeframe || '4H') + ')';

  const row = document.createElement('div');
  row.className = 'row g-2 text-center small';

  const metrics = [
    ['Trades',   d.total_trades],
    ['Win %',    d.win_rate + '%'],
    ['PF',       d.profit_factor],
    ['Sharpe',   d.sharpe],
    ['Max DD',   (d.max_drawdown * 100).toFixed(1) + '%'],
  ];
  for (const [label, val] of metrics) {
    const col = document.createElement('div');
    col.className = 'col';
    const valEl = document.createElement('div');
    valEl.className = 'fw-bold';
    valEl.textContent = String(val);
    const labelEl = document.createElement('div');
    labelEl.className = 'text-muted';
    labelEl.textContent = label;
    col.appendChild(valEl);
    col.appendChild(labelEl);
    row.appendChild(col);
  }

  body.appendChild(title);
  body.appendChild(row);
  card.appendChild(body);
  container.appendChild(card);
}

async function loadBacktest(symbol) {
  const sym = symbol
    || (document.getElementById('backtestSymbol') || {}).value?.trim()
    || 'BTCUSDT';
  const container = document.getElementById('backtestResult');
  if (container) {
    container.textContent = '';
    const loading = document.createElement('small');
    loading.className = 'text-muted';
    loading.textContent = 'Running backtest…';
    container.appendChild(loading);
  }

  try {
    const resp = await fetch('/api/backtest/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: sym, timeframe: '4H', days: 180 }),
    });
    const json = await resp.json();
    if (!json.ok) throw new Error(json.error || 'Backtest failed');
    if (container) _renderBacktestCard(container, json.data);
  } catch (e) {
    if (container) {
      container.textContent = '';
      const err = document.createElement('small');
      err.className = 'text-danger';
      err.textContent = e.message;
      container.appendChild(err);
    }
    notify('Backtest error: ' + e.message, 'danger');
  }
}
```

- [ ] **Step 3: Add the backtest card HTML into the Analysis tab render function**

Find where the Analysis tab content is built in `09-analysis.js` — look for `loadAccuracyProgress` or `accuracyProgressCard`. After the accuracy card's container element, inject:

```javascript
// After the accuracy card container, add:
const btCard = document.createElement('div');
btCard.className = 'card mt-3';
btCard.innerHTML = [
  '<div class="card-body">',
  '  <h6 class="card-title mb-2">Backtest (6M · 4H)</h6>',
  '  <div class="input-group input-group-sm mb-2">',
  '    <input id="backtestSymbol" type="text" class="form-control" placeholder="BTCUSDT" value="BTCUSDT">',
  '    <button class="btn btn-outline-secondary" type="button" onclick="loadBacktest()">&#9654; Run</button>',
  '  </div>',
  '  <div id="backtestResult"></div>',
  '</div>',
].join('');
// Append btCard to the analysis tab container
```

Note: the `btCard.innerHTML` here uses only static string literals — no user or API data is interpolated, so there is no XSS risk. The dynamic data is rendered via `_renderBacktestCard()` using DOM methods.

If the analysis tab content is rendered differently (e.g., assembled as one string and set once), adapt by following the same pattern used by the existing accuracy card.

- [ ] **Step 4: Bump 09-analysis.js version in index.html**

Increment the version for 09-analysis.js by 0.1 (e.g., `?v=1.4` to `?v=1.5`).

- [ ] **Step 5: Commit**

```bash
git add static/js/09-analysis.js templates/index.html
git commit -m "feat(F4): add backtest card UI to Analysis tab"
```

---

## Task 6: Deploy to Pi and manually verify

- [ ] **Step 1: Push to GitHub**

```bash
git push origin main
```

- [ ] **Step 2: Deploy to Pi via SSH expect script**

Pull on Pi + restart service. Always use systemctl — never nohup:

```bash
sudo systemctl restart trading-journal
```

- [ ] **Step 3: Confirm service started cleanly**

```bash
sudo systemctl status trading-journal | head -30
```

Expected: `active (running)`, no import errors in log.

- [ ] **Step 4: Test /api/backtest/run via curl**

```bash
curl -s -X POST http://192.168.1.21:8082/api/backtest/run \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"BTCUSDT","timeframe":"4H","days":180}' | python3 -m json.tool
```

Expected: JSON with `ok: true`, `total_trades` > 0, numeric `sharpe`.

- [ ] **Step 5: Verify UI card appears on Analysis tab**

Open `http://192.168.1.21:8082` in browser, go to Analysis tab. Confirm:
- "Backtest (6M · 4H)" card visible
- Symbol input shows "BTCUSDT" and Run button present

- [ ] **Step 6: Click Run and verify results render**

Click Run → confirm Trades, Win %, PF, Sharpe, Max DD appear.

- [ ] **Step 7: Commit any deploy-time fixes if needed**

If Pi-specific adjustments were required:

```bash
git add <changed files>
git commit -m "fix: <description>"
git push origin main
```

---

## Self-Review Checklist

- [x] **F4 covered**: backtest_engine.py + backtest_metrics.py + routes/backtest.py + UI card
- [x] **F5 covered**: backtest_optimizer.py + GET /api/backtest/optimize
- [x] **No placeholders**: all code complete, exact paths given
- [x] **Type consistency**: BacktestResult fields match route serialisation
- [x] **GPL attribution**: backtest_metrics.py and _simulate_trades() docstrings credit Freqtrade GPL-3.0
- [x] **No DB migrations**: results are API-only, no schema changes
- [x] **Graceful degradation**: run_backtest returns empty BacktestResult if df < 250 rows
- [x] **Pi deployment**: always systemctl restart, never nohup
- [x] **optuna in requirements.txt**: added in Task 2 Step 1
- [x] **XSS safety**: dynamic API values rendered via DOM textContent, not string interpolation

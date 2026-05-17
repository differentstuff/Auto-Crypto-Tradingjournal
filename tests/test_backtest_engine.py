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
    monkeypatch.setattr("backtest_engine._fetch_ohlcv", lambda s, tf, limit, end_ms=None: df)

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

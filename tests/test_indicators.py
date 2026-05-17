import numpy as np
import pandas as pd
import pytest

def _make_df(n=100, seed=42):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high  = close + rng.uniform(0, 2, n)
    low   = close - rng.uniform(0, 2, n)
    vol   = rng.uniform(1000, 5000, n)
    return pd.DataFrame({"open": close, "high": high, "low": low,
                         "close": close, "volume": vol})

def test_rsi_series_length():
    import indicators
    df = _make_df(100)
    rsi = indicators.rsi_series(df["close"])
    assert len(rsi) == 100

def test_rsi_series_range():
    import indicators
    df = _make_df(100)
    rsi = indicators.rsi_series(df["close"]).dropna()
    assert (rsi >= 0).all() and (rsi <= 100).all()

def test_wavetrend_returns_two_series():
    import indicators
    df = _make_df(100)
    wt1, wt2 = indicators.wavetrend_series(df["high"], df["low"], df["close"])
    assert len(wt1) == len(wt2) == 100

def test_adx_series_nonnegative():
    import indicators
    df = _make_df(100)
    adx = indicators.adx_series(df["high"], df["low"], df["close"]).dropna()
    assert (adx >= 0).all()

def test_rsi_downtrend_below_50():
    """RSI on a steadily falling series should be below 50 (only losses, no gains)."""
    import indicators
    close = pd.Series([100.0 - i * 0.5 for i in range(60)])
    rsi = indicators.rsi_series(close, length=14).dropna()
    assert len(rsi) > 0
    assert (rsi < 50).all()

def test_rsi_uptrend_above_50():
    """RSI on a steadily rising series should be above 50."""
    import indicators
    close = pd.Series([100.0 + i for i in range(60)])
    rsi = indicators.rsi_series(close, length=14).dropna()
    assert (rsi > 50).all()

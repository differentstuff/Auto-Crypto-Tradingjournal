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

def test_rsi_matches_pandas_ta():
    """RSI from indicators.py must match pandas_ta directly within 0.5."""
    import indicators
    import pandas_ta as ta
    df = _make_df(200)
    our_rsi = indicators.rsi_series(df["close"], length=14).iloc[-1]
    pta_rsi = ta.rsi(df["close"], length=14).iloc[-1]
    assert abs(our_rsi - pta_rsi) < 0.5

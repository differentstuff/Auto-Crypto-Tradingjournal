"""
indicators.py — Canonical indicator series functions.

Single source of truth for raw indicator math used by both backtest_engine.py
and chart_indicators.py. Returns pd.Series — callers decide how to slice/label.
"""
import numpy as np
import pandas as pd
import pandas_ta as ta


def rsi_series(close: pd.Series, length: int = 14) -> pd.Series:
    """RSI via pandas_ta — matches chart_indicators.compute_rsi()."""
    result = ta.rsi(close, length=length)
    if result is None:
        return pd.Series(np.nan, index=close.index)
    return result


def wavetrend_series(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    n1: int = 10,
    n2: int = 21,
    roll: int = 4,
) -> tuple[pd.Series, pd.Series]:
    """
    WaveTrend oscillator (VMC Cipher A/B).
    n1=10, n2=21, roll=4 — must match chart_indicators.py constants.
    Returns (wt1, wt2).
    """
    hlc3 = (high + low + close) / 3
    ema1 = hlc3.ewm(span=n1, adjust=False).mean()
    d    = (hlc3 - ema1).abs().ewm(span=n1, adjust=False).mean()
    ci   = (hlc3 - ema1) / (0.015 * d.replace(0, float("nan"))).fillna(1e-9)
    wt1  = ci.ewm(span=n2, adjust=False).mean()
    wt2  = wt1.rolling(roll, min_periods=1).mean()
    return wt1, wt2


def adx_series(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    length: int = 14,
) -> pd.Series:
    """
    Average Directional Index via pandas_ta.
    Returns the ADX column only (not DI+/DI-).
    """
    result = ta.adx(high, low, close, length=length)
    if result is None or result.empty:
        return pd.Series(np.nan, index=close.index)
    adx_col = [c for c in result.columns if c.startswith("ADX_")]
    if not adx_col:
        return pd.Series(np.nan, index=close.index)
    return result[adx_col[0]]

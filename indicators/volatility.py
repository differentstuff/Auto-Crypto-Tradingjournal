"""
indicators/volatility.py -- Volatility indicator computations.

Pure functions: accept a DataFrame (open, high, low, close, volume),
return structured dicts. No API calls, no caching, no side effects.
Uses only numpy/pandas — no pandas_ta dependency.

Public API (used by registry and enzymes):
  compute_atr, compute_bollinger
"""

from __future__ import annotations

import pandas as pd
import numpy as np


def compute_atr(df: pd.DataFrame, period: int = 14) -> dict | None:
    """
    ATR(period) using Wilder smoothing.
    Returns {"value","pct","comment"} or None if < 30 bars.
    """
    if df is None or len(df) < 30:
        return None
    try:
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        close = df["close"].astype(float)
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        # Wilder smoothing (EMA with alpha = 1/period)
        atr_s = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        atr_val = atr_s.iloc[-1]
        if pd.isna(atr_val):
            return None
        atr_val = round(float(atr_val), 4)
        cur_price = float(close.iloc[-1])
        atr_pct = round(atr_val / cur_price * 100, 2) if cur_price > 0 else 0.0
        return {
            "value": atr_val,
            "pct": atr_pct,
            "comment": f"typical candle range {atr_pct}% of price — useful for SL sizing",
        }
    except Exception:
        return None


def compute_bollinger(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> dict | None:
    """
    Bollinger Bands(20, 2).
    Returns {"upper","mid","lower","position_pct","band_width","signal"} or None.
    """
    if df is None or len(df) < 30:
        return None
    try:
        close = df["close"].astype(float)
        mid = close.rolling(period).mean()
        std = close.rolling(period).std(ddof=0)
        upper = mid + std_dev * std
        lower = mid - std_dev * std
        bwidth = (upper - lower) / mid.replace(0, 1e-9) * 100  # band width %

        u = upper.iloc[-1]
        m = mid.iloc[-1]
        lo = lower.iloc[-1]
        bw = bwidth.iloc[-1]

        if any(pd.isna(v) for v in (u, m, lo)):
            return None

        price = float(close.iloc[-1])
        band_range = float(u) - float(lo)
        position_pct = round((price - float(lo)) / band_range * 100, 1) if band_range > 0 else 50.0

        return {
            "upper": round(float(u), 4),
            "mid": round(float(m), 4),
            "lower": round(float(lo), 4),
            "position_pct": position_pct,
            "band_width": round(float(bw), 4) if not pd.isna(bw) else None,
            "signal": (
                "near upper band (overbought zone)" if position_pct > 80 else
                "near lower band (oversold zone)" if position_pct < 20 else
                "mid-band area"
            ),
        }
    except Exception:
        return None
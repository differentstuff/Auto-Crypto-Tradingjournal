
"""
indicators/trend.py -- Trend indicator computations.

Pure functions: accept a DataFrame (open, high, low, close, volume),
return structured dicts. No API calls, no caching, no side effects.
Uses only numpy/pandas — no pandas_ta dependency.

Public API (used by registry and enzymes):
  compute_ema_alignment, compute_adx, compute_recent_candles
"""

from __future__ import annotations

import logging

import pandas as pd
import numpy as np

_log = logging.getLogger(__name__)


def compute_ema_alignment(df: pd.DataFrame, periods: list[int] | None = None) -> dict:
    """
    EMA alignment for configurable periods.
    Default periods: [20, 50, 200] (backward compatible).
    Config overrides via indicators[].params.periods (e.g. [21, 55, 200]).
    Returns {"ema21","ema55","ema200","current_price","alignment","stack"}.
    """
    if periods is None:
        _log.warning(
            "compute_ema_alignment: no periods passed from config — "
            "using hardcoded [20, 50, 200]. "
            "Set indicators[].params.periods in YAML to override."
        )
        periods = [20, 50, 200]

    # Build default dict with dynamic keys
    default = {f"ema{p}": 0.0 for p in periods}
    default["current_price"] = 0.0
    default["alignment"] = "neutral"
    default["stack"] = "mixed"

    if df is None or df.empty or len(df) < 30:
        return default
    try:
        close = df["close"].astype(float)
        emas: dict[str, float] = {}
        for length in periods:
            if len(df) >= length:
                s = close.ewm(span=length, adjust=False).mean()
                val = s.iloc[-1]
                if not pd.isna(val):
                    emas[f"ema{length}"] = round(float(val), 4)

        if not emas:
            return default

        cur = round(float(close.iloc[-1]), 4)
        above = [k for k, v in emas.items() if cur > v > 0]
        below = [k for k, v in emas.items() if cur < v > 0]
        total = len(emas)

        if len(above) == total:        alignment = "bullish"
        elif len(below) == total:      alignment = "bearish"
        elif len(above) > len(below):  alignment = "mixed-bullish"
        elif len(below) > len(above):  alignment = "mixed-bearish"
        else:                          alignment = "neutral"

        # Stack: check if EMAs are ordered (shortest > middle > longest = bullish)
        sorted_emas = [v for _, v in sorted(emas.items(), key=lambda x: x[0])]
        if len(sorted_emas) >= 3:
            # Check bullish stack (descending order: shortest period > longest)
            bullish_stack = all(sorted_emas[i] > sorted_emas[i + 1] for i in range(len(sorted_emas) - 1))
            bearish_stack = all(sorted_emas[i] < sorted_emas[i + 1] for i in range(len(sorted_emas) - 1))
            if bullish_stack:
                stack = "bullish"
            elif bearish_stack:
                stack = "bearish"
            else:
                stack = "mixed"
        else:
            stack = "mixed"

        return {**default, **emas, "current_price": cur, "alignment": alignment, "stack": stack}
    except Exception:
        return default


def compute_adx(df: pd.DataFrame, period: int = 14) -> dict:
    """
    ADX trend strength and direction using Wilder smoothing.
    Returns {"value","trend_strength","direction"}.
    """
    default = {"value": 0.0, "trend_strength": "weak", "direction": "undetermined"}
    if df is None or df.empty or len(df) < 30:
        return default
    try:
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        close = df["close"].astype(float)

        # True Range
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)

        # Directional movement
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

        # Wilder smoothing
        atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        plus_di = 100 * plus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr.replace(0, 1e-9)
        minus_di = 100 * minus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr.replace(0, 1e-9)

        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-9)
        adx = dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

        av = adx.iloc[-1]
        if pd.isna(av):
            return default
        av = round(float(av), 1)

        strength = "strong" if av > 25 else "trending" if av > 20 else "weak"

        dp = plus_di.iloc[-1]
        dn = minus_di.iloc[-1]
        direction = "undetermined"
        if not pd.isna(dp) and not pd.isna(dn):
            direction = "bullish" if float(dp) > float(dn) else "bearish"

        return {"value": av, "trend_strength": strength, "direction": direction}
    except Exception:
        return default


def compute_recent_candles(df: pd.DataFrame) -> list[str] | None:
    """
    Last 3 candle body descriptions.
    Returns list of 3 strings or None if < 3 bars.
    """
    if df is None or len(df) < 3:
        return None
    try:
        candles = []
        for i in range(-3, 0):
            row = df.iloc[i]
            o = float(row["open"])
            c = float(row["close"])
            h = float(row["high"])
            lo = float(row["low"])
            body = abs(c - o)
            full_range = h - lo
            body_pct = round(body / full_range * 100, 0) if full_range > 0 else 0
            candle_type = "doji" if body_pct < 20 else ("bullish" if c > o else "bearish")
            candles.append(f"{candle_type} (body {body_pct:.0f}% of range)")
        return candles
    except Exception:
        return None

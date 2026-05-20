
"""
indicators/momentum.py -- Momentum indicator computations.

Pure functions: accept a DataFrame (open, high, low, close, volume),
return structured dicts. No API calls, no caching, no side effects.
Uses only numpy/pandas — no pandas_ta dependency.

Public API (used by registry and enzymes):
  compute_rsi, compute_macd, compute_stochrsi, compute_wavetrend,
  compute_cvd, compute_order_flow_delta
"""

from __future__ import annotations

import pandas as pd
import numpy as np


def compute_rsi(df: pd.DataFrame, period: int = 14) -> dict:
    """
    RSI(period) using Wilder smoothing.
    Returns {"value": float, "level": "overbought"|"oversold"|"neutral"}.
    Returns default {"value": 50.0, "level": "neutral"} if < 30 bars.
    """
    if df is None or len(df) < 30:
        return {"value": 50.0, "level": "neutral"}
    try:
        close = df["close"].astype(float)
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        # Wilder smoothing (EMA with alpha = 1/period)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-9)
        rsi_s = 100 - (100 / (1 + rs))
        val = rsi_s.iloc[-1]
        if pd.isna(val):
            return {"value": 50.0, "level": "neutral"}
        val = round(float(val), 1)
        level = "overbought" if val > 70 else "oversold" if val < 30 else "neutral"
        return {"value": val, "level": level}
    except Exception:
        return {"value": 50.0, "level": "neutral"}


def compute_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """
    MACD with crossover detection using EMA.
    Returns {"macd","signal","histogram","bias","histogram_growing","crossover","crossunder"}.
    """
    default = {
        "macd": 0.0, "signal": 0.0, "histogram": 0.0,
        "bias": "bearish", "histogram_growing": False,
        "crossover": False, "crossunder": False,
    }
    if df is None or len(df) < 30:
        return default
    try:
        close = df["close"].astype(float)
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line

        mv = round(float(macd_line.iloc[-1]), 4)
        sv = round(float(signal_line.iloc[-1]), 4)
        hv = round(float(histogram.iloc[-1]), 4)
        hp = float(histogram.iloc[-2]) if len(histogram) > 1 else hv
        mp = float(macd_line.iloc[-2]) if len(macd_line) > 1 else mv
        sp = float(signal_line.iloc[-2]) if len(signal_line) > 1 else sv

        if any(pd.isna(v) for v in (mv, sv, hv)):
            return default

        return {
            "macd": mv, "signal": sv, "histogram": hv,
            "bias": "bullish" if mv > sv else "bearish",
            "histogram_growing": hv > hp,
            "crossover": (mv > sv) and (mp <= sp),
            "crossunder": (mv < sv) and (mp >= sp),
        }
    except Exception:
        return default


def compute_stochrsi(df: pd.DataFrame, period: int = 14, k: int = 3, d: int = 3) -> dict | None:
    """
    Stochastic RSI(14). Returns {"k","d","signal"} or None.
    """
    if df is None or len(df) < 30:
        return None
    try:
        close = df["close"].astype(float)
        # Compute RSI first
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-9)
        rsi_s = 100 - (100 / (1 + rs))

        # Stochastic of RSI
        rsi_min = rsi_s.rolling(period).min()
        rsi_max = rsi_s.rolling(period).max()
        rsi_range = rsi_max - rsi_min
        stoch = (rsi_s - rsi_min) / rsi_range.replace(0, 1e-9) * 100

        k_line = stoch.rolling(k).mean()
        d_line = k_line.rolling(d).mean()

        k_val = k_line.iloc[-1]
        d_val = d_line.iloc[-1]
        if pd.isna(k_val) or pd.isna(d_val):
            return None

        k_v = round(float(k_val), 1)
        d_v = round(float(d_val), 1)
        return {
            "k": k_v, "d": d_v,
            "signal": (
                "overbought (K>80)" if k_v > 80 else
                "oversold (K<20)" if k_v < 20 else
                "neutral"
            ),
        }
    except Exception:
        return None


def compute_wavetrend(
    df: pd.DataFrame,
    n1: int = 10, n2: int = 21,
    ob: float = 53, os_: float = -53,
    mfi_period: int = 60,
) -> pd.DataFrame:
    """
    WaveTrend (VMC Cipher A/B) using pure numpy/pandas.
    Returns DataFrame with columns: wt1, wt2, histogram, mfi, cross_bull, cross_bear, signal.
    """
    try:
        hlc3 = (df["high"].astype(float) + df["low"].astype(float) + df["close"].astype(float)) / 3.0
        esa = hlc3.ewm(span=n1, adjust=False).mean()
        d = (hlc3 - esa).abs().ewm(span=n1, adjust=False).mean()
        ci = (hlc3 - esa) / (0.015 * d.replace(0, float("nan"))).fillna(1e-9)
        wt1 = ci.ewm(span=n2, adjust=False).mean()
        wt2 = wt1.rolling(4, min_periods=1).mean()
        hist = wt1 - wt2

        # MFI approximation using RSI of (hlc3 * volume)
        mfi_src = hlc3 * df["volume"].astype(float)
        mfi_src_delta = mfi_src.diff()
        mfi_gain = mfi_src_delta.clip(lower=0)
        mfi_loss = (-mfi_src_delta).clip(lower=0)
        mfi_avg_gain = mfi_gain.ewm(alpha=1 / mfi_period, min_periods=mfi_period, adjust=False).mean()
        mfi_avg_loss = mfi_loss.ewm(alpha=1 / mfi_period, min_periods=mfi_period, adjust=False).mean()
        mfi_rs = mfi_avg_gain / mfi_avg_loss.replace(0, 1e-9)
        mfi_rsi = 100 - (100 / (1 + mfi_rs))
        mfi = (mfi_rsi - 50.0) * 2.0

        cross_bull = (wt1 > wt2) & (wt1.shift(1) <= wt2.shift(1))
        cross_bear = (wt1 < wt2) & (wt1.shift(1) >= wt2.shift(1))

        signal = pd.Series(None, index=df.index, dtype=object)
        gold_mask = cross_bull & (wt2 < -80)
        buy_mask = cross_bull & (wt2 < os_) & ~gold_mask
        sell_mask = cross_bear & (wt2 > ob)
        signal[gold_mask] = "gold_buy"
        signal[buy_mask] = "buy"
        signal[sell_mask] = "sell"

        return pd.DataFrame({
            "wt1": wt1.round(2),
            "wt2": wt2.round(2),
            "histogram": hist.round(2),
            "mfi": mfi.round(2),
            "cross_bull": cross_bull,
            "cross_bear": cross_bear,
            "signal": signal,
        }, index=df.index)
    except Exception:
        # Return empty DataFrame with correct columns on error
        cols = ["wt1", "wt2", "histogram", "mfi", "cross_bull", "cross_bear", "signal"]
        return pd.DataFrame(columns=cols)


def compute_cvd(df: pd.DataFrame) -> dict | None:
    """
    Cumulative Volume Delta (Money Flow Multiplier approximation).
    Returns {"value","trend","signal"} or None.
    """
    if df is None or len(df) < 4:
        return None
    try:
        h = df["high"].values.astype(float)
        lo = df["low"].values.astype(float)
        c = df["close"].values.astype(float)
        v = df["volume"].values.astype(float)
        running = 0.0
        cvd_series = []
        for i in range(len(h)):
            denom = h[i] - lo[i]
            delta = v[i] * (2 * c[i] - lo[i] - h[i]) / denom if denom > 0 else 0.0
            running += delta
            cvd_series.append(running)
        cvd_now = cvd_series[-1]
        cvd_prev = cvd_series[-4] if len(cvd_series) >= 4 else cvd_series[0]
        trend = (
            "rising" if cvd_now > cvd_prev * 1.001 else
            "falling" if cvd_now < cvd_prev * 0.999 else
            "flat"
        )
        return {
            "value": round(cvd_now, 2),
            "trend": trend,
            "signal": (
                "bullish (net buy pressure)" if trend == "rising" else
                "bearish (net sell pressure)" if trend == "falling" else
                "neutral"
            ),
        }
    except Exception:
        return None


def compute_order_flow_delta(df: pd.DataFrame) -> dict | None:
    """
    Tick-rule proxy for per-candle aggressor delta.
    Returns {"delta","cumulative_delta","signal","divergence"} or None.
    """
    if df is None or len(df) < 3:
        return None
    try:
        body = df["close"].astype(float) - df["open"].astype(float)
        body_abs = body.abs()
        ratio = (body_abs / (body_abs + 1e-9)).clip(0.10, 0.90)
        buy_vol = df["volume"].astype(float) * ratio.where(body >= 0, 1 - ratio)
        sell_vol = df["volume"].astype(float) - buy_vol
        delta_bar = buy_vol - sell_vol

        delta = float(delta_bar.iloc[-1])
        cum_delta = float(delta_bar.sum())

        price_high = df["close"].iloc[-1] > df["close"].iloc[-5:-1].max()
        prior_avg = float(delta_bar.iloc[-5:-1].mean()) if len(delta_bar) >= 5 else 0.0
        divergence = bool(price_high and delta < prior_avg)

        signal = (
            "buying_pressure" if delta > 0 else
            "selling_pressure" if delta < 0 else
            "neutral"
        )
        return {
            "delta": delta,
            "cumulative_delta": cum_delta,
            "signal": signal,
            "divergence": divergence,
        }
    except Exception:
        return None

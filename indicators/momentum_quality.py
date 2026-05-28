"""
indicators/momentum_quality.py -- Momentum quality ranking indicator.

Pure function: accept a DataFrame (open, high, low, close, volume),
return structured dict. No API calls, no caching, no side effects.
Uses only numpy/pandas — no scipy dependency.

Computes momentum_quality = slope × R² on log-price series via OLS
linear regression. R² acts as a quality floor: symbols below
min_r_squared are excluded from dynamic ranking. Lookback adapts
based on R² quality (shorter when R² is high, longer when low).

Public API (used by registry and enzymes):
  compute_momentum_quality
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

_log = logging.getLogger(__name__)


def compute_momentum_quality(
    df: pd.DataFrame,
    ranking_window: int = 90,
    min_r_squared: float = 0.15,
    lookback_short: int = 30,
    lookback_long: int = 90,
    r_squared_high: float = 0.5,
    r_squared_low: float = 0.3,
) -> dict | None:
    """
    Compute momentum quality as slope × R² on log-price series.

    Linear regression on log(close) over the ranking_window gives:
      - slope: direction and magnitude of the trend
      - R²: confidence/quality of the fit (0 to 1)

    momentum_quality = slope × R² combines both: high slope with low R²
    (noisy trend) scores low; high slope with high R² (clean trend)
    scores high.

    R² floor: symbols with R² < min_r_squared are filtered (score=None,
    filtered=True). These have no discernible trend — no point in
    trading them.

    Adaptive lookback: when R² is high (clear trend), use shorter
    lookback; when low (noisy), use longer lookback. Interpolates
    linearly between lookback_short and lookback_long.

    Args:
        df: OHLCV DataFrame with 'close' column.
        ranking_window: Number of candles for regression (default 90).
        min_r_squared: R² floor — symbols below this are filtered (default 0.15).
        lookback_short: Lookback when R² is high (default 30).
        lookback_long: Lookback when R² is low (default 90).
        r_squared_high: R² threshold for "high quality" (default 0.5).
        r_squared_low: R² threshold for "low quality" (default 0.3).

    Returns:
        dict with keys: score, slope, r_squared, adaptive_lookback,
        filtered, direction. Returns None if insufficient data.
    """
    if df is None or len(df) < 30:
        return None

    try:
        close = df["close"].astype(float).values
    except (KeyError, ValueError):
        return None

    # Guard: prices must be positive for log transform
    if np.any(close <= 0):
        _log.warning("Non-positive prices detected — skipping momentum_quality")
        return None

    # Use the last ranking_window candles (or all if fewer)
    window = min(ranking_window, len(close))
    prices = close[-window:]

    # Log-price transform
    log_p = np.log(prices)

    # OLS linear regression: y = slope * x + intercept
    # x = 0, 1, 2, ..., n-1 (bar indices)
    n = len(log_p)
    x = np.arange(n, dtype=float)

    # Manual OLS formulas (no scipy needed)
    sum_x = x.sum()
    sum_y = log_p.sum()
    sum_xy = (x * log_p).sum()
    sum_x2 = (x * x).sum()

    denom = n * sum_x2 - sum_x * sum_x
    if denom == 0:
        return None

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n

    # R² = 1 - SS_res / SS_tot
    y_pred = slope * x + intercept
    ss_res = ((log_p - y_pred) ** 2).sum()
    y_mean = log_p.mean()
    ss_tot = ((log_p - y_mean) ** 2).sum()

    if ss_tot == 0:
        # All prices identical — no trend, R² undefined
        r_squared = 0.0
    else:
        r_squared = 1.0 - ss_res / ss_tot

    # Clamp R² to [0, 1] for float precision safety
    r_squared = max(0.0, min(1.0, r_squared))

    # Adaptive lookback based on R² quality
    adaptive_lookback = _compute_adaptive_lookback(
        r_squared, r_squared_high, r_squared_low,
        lookback_short, lookback_long,
    )

    # R² floor filter
    filtered = r_squared < min_r_squared

    # Score: slope × R² (None if filtered)
    score = slope * r_squared if not filtered else None

    # Direction from slope sign (with tolerance for float precision)
    # A slope of ~1e-17 on flat prices is not a real trend
    if slope > 1e-10:
        direction = "bullish"
    elif slope < -1e-10:
        direction = "bearish"
    else:
        direction = "neutral"

    return {
        "score": round(score, 6) if score is not None else None,
        "slope": round(slope, 6),
        "r_squared": round(r_squared, 4),
        "adaptive_lookback": adaptive_lookback,
        "filtered": filtered,
        "direction": direction,
    }


def _compute_adaptive_lookback(
    r_squared: float,
    r_squared_high: float,
    r_squared_low: float,
    lookback_short: int,
    lookback_long: int,
) -> int:
    """
    Compute adaptive lookback based on R² quality.

    When R² is high (clear trend), use shorter lookback — the trend
    is obvious and we want responsiveness. When R² is low (noisy),
    use longer lookback — we need more data to find the signal.

    Linear interpolation between the two thresholds.

    Args:
        r_squared: Computed R² value.
        r_squared_high: Threshold for "high quality" (use short lookback).
        r_squared_low: Threshold for "low quality" (use long lookback).
        lookback_short: Lookback at high R².
        lookback_long: Lookback at low R².

    Returns:
        Adaptive lookback as integer.
    """
    if r_squared >= r_squared_high:
        return lookback_short
    if r_squared <= r_squared_low:
        return lookback_long

    # Linear interpolation between low and high thresholds
    # frac = 0 at r_squared_high, frac = 1 at r_squared_low
    frac = (r_squared_high - r_squared) / (r_squared_high - r_squared_low)
    return round(lookback_short + frac * (lookback_long - lookback_short))

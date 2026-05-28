"""
indicators/registry.py -- Indicator name -> function lookup.

Maps indicator names from config YAML (e.g. "rsi", "macd", "ema_stack")
to their compute functions. The strategy YAML specifies which indicators
to enable; the registry resolves the name to the right function.

Usage:
    from indicators.registry import compute_indicator, list_available

    result = compute_indicator("rsi", df, period=14)
    available = list_available()
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

import pandas as pd

_log = logging.getLogger(__name__)

# Type alias for indicator functions
IndicatorFn = Callable[..., dict | list | None]


def _rsi_fn(df: pd.DataFrame, **params) -> dict:
    from indicators.momentum import compute_rsi
    return compute_rsi(df, period=params.get("period", 14))


def _macd_fn(df: pd.DataFrame, **params) -> dict:
    from indicators.momentum import compute_macd
    return compute_macd(
        df,
        fast=params.get("fast", 12),
        slow=params.get("slow", 26),
        signal=params.get("signal", 9),
    )


def _stochrsi_fn(df: pd.DataFrame, **params) -> dict | None:
    from indicators.momentum import compute_stochrsi
    return compute_stochrsi(df)


def _wavetrend_fn(df: pd.DataFrame, **params) -> dict:
    from indicators.momentum import compute_wavetrend
    wt_df = compute_wavetrend(
        df,
        n1=params.get("n1", 10),
        n2=params.get("n2", 21),
        ob=params.get("ob", 53),
        os_=params.get("os", -53),
        mfi_period=params.get("mfi_period", 60),
    )
    if wt_df is None or wt_df.empty:
        return {}
    wt1_last = float(wt_df["wt1"].iloc[-1])
    wt2_last = float(wt_df["wt2"].iloc[-1])
    mfi_last = float(wt_df["mfi"].iloc[-1])
    sig_last = wt_df["signal"].iloc[-1]
    cb_last = bool(wt_df["cross_bull"].iloc[-1])
    cs_last = bool(wt_df["cross_bear"].iloc[-1])
    ob = params.get("ob", 53)
    os_ = params.get("os", -53)
    return {
        "wt1": round(wt1_last, 2),
        "wt2": round(wt2_last, 2),
        "histogram": round(wt1_last - wt2_last, 2),
        "mfi": round(mfi_last, 2),
        "cross": "bullish" if cb_last else ("bearish" if cs_last else None),
        "zone": "overbought" if wt1_last > ob else "oversold" if wt1_last < os_ else "neutral",
        "signal": sig_last,
    }


def _cvd_fn(df: pd.DataFrame, **params) -> dict | None:
    from indicators.momentum import compute_cvd
    return compute_cvd(df)


def _order_flow_fn(df: pd.DataFrame, **params) -> dict | None:
    from indicators.momentum import compute_order_flow_delta
    return compute_order_flow_delta(df)


def _ema_stack_fn(df: pd.DataFrame, **params) -> dict:
    from indicators.trend import compute_ema_alignment
    periods = params.get("periods")
    return compute_ema_alignment(df, periods=periods)


def _adx_fn(df: pd.DataFrame, **params) -> dict:
    from indicators.trend import compute_adx
    return compute_adx(df, period=params.get("period", 14))


def _recent_candles_fn(df: pd.DataFrame, **params) -> list[str] | None:
    from indicators.trend import compute_recent_candles
    return compute_recent_candles(df)


def _atr_fn(df: pd.DataFrame, **params) -> dict | None:
    from indicators.volatility import compute_atr
    return compute_atr(df, period=params.get("period", 14))


def _bollinger_fn(df: pd.DataFrame, **params) -> dict | None:
    from indicators.volatility import compute_bollinger
    return compute_bollinger(
        df,
        period=params.get("period", 20),
        std_dev=params.get("std_dev", 2.0),
    )


def _volume_fn(df: pd.DataFrame, **params) -> dict | None:
    from indicators.volume import compute_volume
    return compute_volume(df)


def _sr_levels_fn(df: pd.DataFrame, **params) -> list[dict]:
    from indicators.structure import detect_sr_levels
    return detect_sr_levels(
        df,
        window=params.get("window", 5),
        max_levels=params.get("max_levels", 8),
        tolerance=params.get("tolerance"),
        min_touches=params.get("min_touches", 2),
    )


def _trendlines_fn(df: pd.DataFrame, **params) -> list:
    from indicators.structure import detect_trendlines
    return detect_trendlines(
        df,
        n_swing=params.get("n_swing", 5),
        max_lines=params.get("max_lines", 4),
    )


def _fibonacci_fn(df: pd.DataFrame, **params) -> dict | None:
    from indicators.structure import detect_fibonacci
    return detect_fibonacci(df, n_swing=params.get("n_swing", 10))


# ── Registry: name -> function ──────────────────────────────────────────────

_REGISTRY: dict[str, IndicatorFn] = {
    "rsi": _rsi_fn,
    "macd": _macd_fn,
    "stoch_rsi": _stochrsi_fn,
    "wavetrend": _wavetrend_fn,
    "cvd": _cvd_fn,
    "order_flow": _order_flow_fn,
    "ema_stack": _ema_stack_fn,
    "adx": _adx_fn,
    "recent_candles": _recent_candles_fn,
    "atr": _atr_fn,
    "bollinger": _bollinger_fn,
    "volume": _volume_fn,
    "sr_levels": _sr_levels_fn,
    "trendlines": _trendlines_fn,
    "fibonacci": _fibonacci_fn,
}


def compute_indicator(name: str, df: pd.DataFrame, **params) -> Any:
    """
    Compute an indicator by name.

    Args:
        name: indicator name from config YAML (e.g. "rsi", "macd")
        df: OHLCV DataFrame
        **params: indicator-specific parameters (from config YAML params section)

    Returns:
        Indicator result (dict, list, or None depending on indicator)

    Raises:
        ValueError: if indicator name is not registered
    """
    fn = _REGISTRY.get(name)
    if fn is None:
        raise ValueError(f"Unknown indicator: {name}. Available: {list_available()}")
    try:
        return fn(df, **params)
    except Exception as e:
        _log.warning("Indicator %s computation failed: %s", name, e)
        return None


def get_indicator_fn(name: str) -> Optional[IndicatorFn]:
    """Look up an indicator function by name. Returns None if not found."""
    return _REGISTRY.get(name)


def list_available() -> list[str]:
    """List all registered indicator names."""
    return sorted(_REGISTRY.keys())


def is_registered(name: str) -> bool:
    """Check if an indicator name is registered."""
    return name in _REGISTRY
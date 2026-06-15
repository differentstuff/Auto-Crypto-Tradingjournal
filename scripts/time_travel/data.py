"""
scripts/time_travel/data.py -- OHLCV fetching, indicator pre-computation, helpers.

Extracted from time_travel.py for modularity.
"""

from __future__ import annotations

import logging
import time as _time
from datetime import datetime, timezone
from typing import List, Optional

import pandas as pd

from core.exchange import Exchange
from indicators.registry import compute_indicator

_log = logging.getLogger("time_travel.data")

# Sliding window size for indicator computation.
_INDICATOR_WINDOW = 400


def precompute_indicators(
    df: pd.DataFrame,
    compute_configs: List[dict],
    min_bars: int = 30,
    window: int = _INDICATOR_WINDOW,
    label: str = "",
) -> List[Optional[dict]]:
    """Pre-compute indicators for every bar using a sliding window.

    Returns a list of dicts (one per bar). Bars before min_bars are None.
    """
    n = len(df)
    results: List[Optional[dict]] = [None] * n
    failed_bars = 0

    for bar_idx in range(min_bars, n):
        start_idx = max(0, bar_idx - window)
        df_slice = df.iloc[start_idx:bar_idx + 1]

        tf_indicators = {"ok": True, "candles_used": len(df_slice)}
        for ind_cfg in compute_configs:
            ind_name = ind_cfg.get("name", "")
            ind_params = ind_cfg.get("params", {})
            try:
                result = compute_indicator(ind_name, df_slice, **ind_params)
                if result is not None:
                    tf_indicators[ind_name] = result
            except Exception:
                pass

        if len(tf_indicators) > 2:
            results[bar_idx] = tf_indicators
        else:
            failed_bars += 1

    _log.info("  %sPre-computed indicators: %d/%d bars ok, %d failed",
              f"{label} " if label else "", n - min_bars - failed_bars, n - min_bars, failed_bars)
    return results


def fetch_historical_ohlcv(
    exchange: Exchange,
    symbol: str,
    timeframe: str,
    start_dt: datetime,
    end_dt: datetime,
    batch_size: int = 500,
) -> Optional[pd.DataFrame]:
    """Fetch historical OHLCV data, paginating if needed."""
    all_bars = []
    since_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    ccxt_symbol = symbol.replace("USDT", "/USDT:USDT")

    while since_ms < end_ms:
        try:
            exchange_obj = exchange._get_data_exchange()
            bars = exchange_obj.fetch_ohlcv(
                ccxt_symbol,
                timeframe=timeframe,
                since=since_ms,
                limit=batch_size,
            )
        except Exception as e:
            _log.error("Failed to fetch %s %s since %s: %s", symbol, timeframe, since_ms, e)
            break

        if not bars:
            break

        all_bars.extend(bars)

        last_ts = bars[-1][0]
        if last_ts <= since_ms:
            break
        since_ms = last_ts + 1

        if last_ts >= end_ms:
            break

        _time.sleep(0.2)

    if not all_bars:
        return None

    df = pd.DataFrame(all_bars, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp")

    df = df[df.index >= start_dt]
    df = df[df.index <= end_dt]
    df = df[~df.index.duplicated(keep="first")]
    df = df.sort_index()

    return df


def find_confirmation_bar(
    df_confirm: pd.DataFrame,
    primary_bar_time,
    primary_bar_idx: int,
) -> Optional[int]:
    """Find the last closed confirmation bar before a primary bar."""
    if df_confirm is None or df_confirm.empty:
        return None

    mask = df_confirm.index <= primary_bar_time
    if not mask.any():
        return None

    last_matching = df_confirm.index[mask][-1]
    return df_confirm.index.get_loc(last_matching)


def tf_to_minutes(timeframe: str) -> int:
    """Convert timeframe string to minutes."""
    tf = timeframe.strip().upper()
    if tf.endswith("H"):
        return int(tf[:-1]) * 60
    if tf.endswith("M"):
        return int(tf[:-1])
    if tf.endswith("D"):
        return int(tf[:-1]) * 1440
    return 60


def bar_to_iso(bar_time) -> str:
    """Convert a bar timestamp to ISO format string."""
    if hasattr(bar_time, 'isoformat'):
        return bar_time.isoformat()
    return str(bar_time)
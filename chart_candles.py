"""
chart_candles.py — OHLCV candle fetch with cache.
Single responsibility: get a DataFrame of candles from Bitget.
Extracted from chart_context.py.
"""
import threading
import time

import pandas as pd

import bitget_client
from constants import CHART_CACHE_TTL

# ── Cache ──────────────────────────────────────────────────────────────────────

_cache: dict = {}
_cache_lock  = threading.Lock()


def _cached(key: str, fn, ttl: int = CHART_CACHE_TTL):
    # Fast path: check without lock (GIL makes dict.get atomic in CPython)
    now   = time.time()
    entry = _cache.get(key)
    if entry and (now - entry[0]) < ttl:
        return entry[1]

    with _cache_lock:
        # Second check under lock: another thread may have populated cache
        now   = time.time()
        entry = _cache.get(key)
        if entry and (now - entry[0]) < ttl:
            return entry[1]
        result = fn()
        _cache[key] = (time.time(), result)
        return result


# ── Candle fetch ───────────────────────────────────────────────────────────────

def get_candles(symbol: str, timeframe: str = "4H", limit: int = 200) -> pd.DataFrame:
    """
    Fetch OHLCV candles from Bitget and return as a DataFrame.
    Columns: timestamp, open, high, low, close, volume (base), quote_volume
    """
    sym = symbol.upper()
    if not sym.endswith("USDT"):
        sym += "USDT"

    def _fetch():
        try:
            raw = bitget_client._get("/api/v2/mix/market/candles", {
                "symbol":      sym,
                "productType": "USDT-FUTURES",
                "granularity": timeframe,
                "limit":       str(limit),
            })
            if not raw or not isinstance(raw, list):
                return pd.DataFrame()

            df = pd.DataFrame(raw, columns=[
                "timestamp", "open", "high", "low", "close", "volume", "quote_volume"
            ])
            for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df["timestamp"] = pd.to_numeric(df["timestamp"])
            df = df.sort_values("timestamp").reset_index(drop=True)
            return df
        except Exception:
            return pd.DataFrame()

    return _cached(f"candles_{sym}_{timeframe}_{limit}", _fetch)


def get_candles_at_time(symbol: str, timeframe: str, end_time_ms: int,
                        limit: int = 200) -> pd.DataFrame:
    """
    Fetch historical candles ending at end_time_ms (Unix ms). NOT cached —
    each call returns the snapshot visible at that specific point in time.
    """
    sym = symbol.upper()
    if not sym.endswith("USDT"):
        sym += "USDT"
    try:
        raw = bitget_client._get("/api/v2/mix/market/candles", {
            "symbol":      sym,
            "productType": "USDT-FUTURES",
            "granularity": timeframe,
            "limit":       str(limit),
            "endTime":     str(end_time_ms),
        })
        if not raw or not isinstance(raw, list):
            return pd.DataFrame()
        df = pd.DataFrame(raw, columns=[
            "timestamp", "open", "high", "low", "close", "volume", "quote_volume"
        ])
        for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["timestamp"] = pd.to_numeric(df["timestamp"])
        return df.sort_values("timestamp").reset_index(drop=True)
    except Exception:
        return pd.DataFrame()

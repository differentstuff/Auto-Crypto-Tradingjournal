"""
coinalyze_client.py — Coinalyze aggregated derivatives data.

Provides multi-exchange aggregated OI, liquidations, CVD, funding rate,
and long/short ratio. All data aggregated across Binance, Bybit, OKX,
Bitget, Deribit, and other major exchanges.

API docs: https://api.coinalyze.net/v1/doc/
Rate limit: 40 requests/minute (free tier)

Confirmed API response shapes (tested 2026-05-15):
  open-interest:
    [{"symbol": "BTCUSDT_PERP.A", "value": 102168.237, "update": 1778869122575}]
    → "value" is OI in coin units (NOT USD). No 24h change field on current endpoint.

  funding-rate:
    [{"symbol": "...", "value": <rate_float>, "update": <ms_timestamp>}]
    → "value" is the funding rate (e.g. 0.0001 = 0.01%)

  long-short-ratio:
    [{"symbol": "...", "value": <ratio_float>, "update": <ms_timestamp>}]
    → "value" is long/short ratio (>1 means more longs)

  liquidation-history:
    [{"symbol": "...", "t": <ms>, "l": <long_liq_usd>, "s": <short_liq_usd>}, ...]
    → OHLCV-style array; "l" = long liquidations USD, "s" = short liquidations USD
    → Requires "from" and "to" ms timestamps + valid interval
    → Valid intervals: 1min, 3min, 5min, 15min, 30min, 1hour, 2hour, 4hour, 6hour, 12hour, daily, weekly
"""
import os
import time
import urllib.request
import json
import logging
import threading
from typing import Optional

_log = logging.getLogger(__name__)

_BASE = "https://api.coinalyze.net/v1"
_API_KEY = os.environ.get("COINALYZE_API_KEY", "")
_TIMEOUT = 10


def _symbol(trading_pair: str) -> str:
    """Convert 'BTCUSDT' → 'BTCUSDT_PERP.A' (aggregated perp across all exchanges)."""
    pair = trading_pair.upper()
    if pair.endswith("_PERP.A"):
        return pair
    if not pair.endswith("USDT"):
        pair = pair + "USDT"
    return pair + "_PERP.A"


def _get(path: str, params: dict) -> dict | list | None:
    """Single authenticated GET. Returns parsed JSON or None on error."""
    if not _API_KEY:
        _log.warning("COINALYZE_API_KEY not set")
        return None
    params["api_key"] = _API_KEY
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{_BASE}/{path}?{qs}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TradingJournal/1.0"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return json.loads(r.read())
    except Exception as e:
        _log.debug("Coinalyze %s failed: %s", path, e)
        return None


def get_open_interest(symbol: str) -> dict:
    """
    Current aggregated open interest across all exchanges.

    API returns OI in coin units (e.g. BTC). Multiply by spot price for USD.
    Response shape: [{"symbol": "...", "value": <oi_coins>, "update": <ms>}]

    Returns {"oi_coins": float, "oi_symbol": str} or {}.
    Note: USD conversion not done here to avoid an extra price fetch.
    """
    data = _get("open-interest", {"symbols": _symbol(symbol)})
    try:
        if not data:
            return {}
        record = data[0] if isinstance(data, list) else data
        # Confirmed field: "value" = OI in coin units
        oi_coins = float(record.get("value") or 0)
        if oi_coins == 0:
            return {}
        return {
            "oi_coins": round(oi_coins, 3),
            "oi_symbol": record.get("symbol", _symbol(symbol)),
        }
    except Exception:
        return {}


def get_liquidations(symbol: str, lookback_hours: int = 1) -> dict:
    """
    Recent liquidation volume over the last lookback_hours.

    Response shape: [{"symbol": "...", "t": <ms>, "l": <long_liq_usd>, "s": <short_liq_usd>}, ...]
    "l" = long liquidations USD (longs got liquidated — price fell)
    "s" = short liquidations USD (shorts got liquidated — price rose)

    Returns {"liq_long_usd": float, "liq_short_usd": float, "liq_total_usd": float} or {}.
    """
    now_ms = int(time.time() * 1000)
    from_ms = now_ms - (lookback_hours * 3600 * 1000)
    data = _get("liquidation-history", {
        "symbols": _symbol(symbol),
        "interval": "1hour",
        "from": from_ms,
        "to": now_ms,
    })
    try:
        if not data:
            return {}
        records = data if isinstance(data, list) else [data]
        if not records:
            return {}
        # Sum all records in the window (typically just 1 for 1h lookback)
        long_liq = sum(float(r.get("l") or r.get("longLiquidationUsd") or 0) for r in records)
        short_liq = sum(float(r.get("s") or r.get("shortLiquidationUsd") or 0) for r in records)
        if long_liq == 0 and short_liq == 0:
            return {}
        return {
            "liq_long_usd":  round(long_liq, 0),
            "liq_short_usd": round(short_liq, 0),
            "liq_total_usd": round(long_liq + short_liq, 0),
        }
    except Exception:
        return {}


def get_funding_rate(symbol: str) -> dict:
    """
    Current aggregated funding rate across all exchanges.

    Response shape: [{"symbol": "...", "value": <rate>, "update": <ms>}]
    "value" is the funding rate float (e.g. 0.0001 = 0.01% per 8h)

    Returns {"rate": float, "annualized_pct": float, "sentiment": str} or {}.
    """
    data = _get("funding-rate", {"symbols": _symbol(symbol)})
    try:
        if not data:
            return {}
        record = data[0] if isinstance(data, list) else data
        # Confirmed field: "value" = funding rate float
        rate = float(record.get("value") or record.get("fundingRate") or record.get("r") or 0)
        ann = round(rate * 3 * 365 * 100, 2)  # 8h payments × 3/day × 365 days
        if rate > 0.0005:
            sentiment = "longs_paying_heavily"
        elif rate > 0.0001:
            sentiment = "longs_paying"
        elif rate < -0.0001:
            sentiment = "shorts_paying"
        else:
            sentiment = "neutral"
        return {"rate": rate, "annualized_pct": ann, "sentiment": sentiment}
    except Exception:
        return {}


def get_long_short_ratio(symbol: str) -> dict:
    """
    Aggregated long/short account ratio across exchanges.

    Response shape: [{"symbol": "...", "value": <ratio>, "update": <ms>}]
    "value" > 1 means more long accounts than short accounts.

    Returns {"ratio": float, "longs_pct": float, "shorts_pct": float} or {}.
    """
    data = _get("long-short-ratio", {"symbols": _symbol(symbol)})
    try:
        if not data:
            return {}
        record = data[0] if isinstance(data, list) else data
        # Confirmed field: "value" = long/short ratio
        ratio = float(record.get("value") or record.get("longShortRatio") or record.get("r") or 0)
        if ratio <= 0:
            return {}
        longs_pct = round(ratio / (1 + ratio) * 100, 1)
        return {
            "ratio":      round(ratio, 3),
            "longs_pct":  longs_pct,
            "shorts_pct": round(100 - longs_pct, 1),
        }
    except Exception:
        return {}


def get_all(symbol: str) -> dict:
    """
    Fetch OI, liquidations, funding rate, and L/S ratio in parallel.

    All sources degrade gracefully — returns {} sub-dicts on individual failures.
    Use this in the agent pipeline for a single symbol.

    Returns:
        {
            "oi":           {"oi_coins": float, "oi_symbol": str} or {},
            "liquidations": {"liq_long_usd": float, "liq_short_usd": float, "liq_total_usd": float} or {},
            "funding":      {"rate": float, "annualized_pct": float, "sentiment": str} or {},
            "long_short":   {"ratio": float, "longs_pct": float, "shorts_pct": float} or {},
        }
    """
    results: dict = {}

    def _fetch(name, fn, *args):
        try:
            results[name] = fn(*args)
        except Exception:
            results[name] = {}

    threads = [
        threading.Thread(target=_fetch, args=("oi",          get_open_interest,   symbol)),
        threading.Thread(target=_fetch, args=("liquidations", get_liquidations,    symbol)),
        threading.Thread(target=_fetch, args=("funding",      get_funding_rate,    symbol)),
        threading.Thread(target=_fetch, args=("long_short",   get_long_short_ratio, symbol)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=12)

    return {
        "oi":           results.get("oi", {}),
        "liquidations": results.get("liquidations", {}),
        "funding":      results.get("funding", {}),
        "long_short":   results.get("long_short", {}),
    }

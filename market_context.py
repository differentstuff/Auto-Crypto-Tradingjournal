"""
market_context.py — Real-time market context from three free sources.

Sources:
  1. Fear & Greed Index  — alternative.me (no auth)
  2. Bitget funding rate — per symbol, authenticated via bitget_client
  3. Bitget long/short   — per symbol, authenticated via bitget_client

All results are cached for 5 minutes to avoid rate-limiting.
"""

import json
import time
import urllib.request
from typing import Optional

import bitget_client

_cache: dict = {}
CACHE_TTL    = 300   # 5 minutes


def _cached(key: str, fn):
    now = time.time()
    if key in _cache:
        ts, data = _cache[key]
        if now - ts < CACHE_TTL:
            return data
    data = _cache[key] = (now, fn())
    return data[1]


# ── Fear & Greed ───────────────────────────────────────────────────────────────

def get_fear_greed() -> dict:
    def _fetch():
        try:
            req = urllib.request.Request(
                "https://api.alternative.me/fng/?limit=1",
                headers={"User-Agent": "TradingJournal/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read())
            item = d["data"][0]
            val  = int(item["value"])
            return {
                "value":          val,
                "classification": item["value_classification"],
                "color": (
                    "var(--red)"     if val <= 25 else
                    "var(--yellow)"  if val <= 45 else
                    "var(--muted)"   if val <= 55 else
                    "var(--yellow)"  if val <= 75 else
                    "var(--red)"
                ),
                "ok": True,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return _cached("fear_greed", _fetch)


# ── Bitget market data ─────────────────────────────────────────────────────────

def get_funding_rate(symbol: str) -> dict:
    sym = symbol.upper()
    def _fetch():
        try:
            data = bitget_client._get(
                "/api/v2/mix/market/current-fund-rate",
                {"symbol": sym, "productType": "USDT-FUTURES"},
            )
            item = data[0] if isinstance(data, list) and data else data
            rate = float(item.get("fundingRate", 0))
            return {
                "symbol":    sym,
                "rate":      rate,
                "rate_pct":  round(rate * 100, 4),
                "direction": "longs paying" if rate > 0 else "shorts paying",
                "high":      abs(rate) >= 0.0005,   # flag if ≥ 0.05%
                "ok":        True,
            }
        except Exception as e:
            return {"symbol": sym, "ok": False, "error": str(e)}
    return _cached(f"funding_{sym}", _fetch)


def get_long_short_ratio(symbol: str) -> dict:
    sym = symbol.upper()
    def _fetch():
        try:
            data = bitget_client._get(
                "/api/v2/mix/market/account-long-short",
                {"symbol": sym, "productType": "USDT-FUTURES", "period": "1H"},
            )
            item = data[0] if isinstance(data, list) and data else data
            lp   = round(float(item.get("longAccountRatio",  0)) * 100, 1)
            sp   = round(float(item.get("shortAccountRatio", 0)) * 100, 1)
            return {
                "symbol":    sym,
                "long_pct":  lp,
                "short_pct": sp,
                "bias":      "crowded long" if lp > 65 else "crowded short" if sp > 65 else "balanced",
                "ok":        True,
            }
        except Exception as e:
            return {"symbol": sym, "ok": False, "error": str(e)}
    return _cached(f"ls_{sym}", _fetch)


# ── Combined ───────────────────────────────────────────────────────────────────

def get_market_context(symbols: Optional[list] = None) -> dict:
    """Fear & Greed + per-symbol funding rate + long/short ratio."""
    result = {"fear_greed": get_fear_greed(), "symbols": {}}
    if symbols:
        for sym in list(dict.fromkeys(symbols))[:6]:   # dedupe, cap at 6
            result["symbols"][sym] = {
                "funding":    get_funding_rate(sym),
                "long_short": get_long_short_ratio(sym),
            }
    return result


def format_for_prompt(ctx: dict) -> str:
    """Concise text block for Claude prompts."""
    lines = []
    fg = ctx.get("fear_greed", {})
    if fg.get("ok"):
        lines.append(f"Fear & Greed Index: {fg['value']}/100 — {fg['classification']}")
    for sym, d in ctx.get("symbols", {}).items():
        parts = []
        fr = d.get("funding", {})
        if fr.get("ok"):
            flag = " ⚠ HIGH" if fr.get("high") else ""
            parts.append(f"funding {fr['rate_pct']:+.4f}% ({fr['direction']}){flag}")
        ls = d.get("long_short", {})
        if ls.get("ok"):
            parts.append(f"retail {ls['long_pct']}% long / {ls['short_pct']}% short ({ls['bias']})")
        if parts:
            lines.append(f"{sym}: {' · '.join(parts)}")
    return "\n".join(lines)

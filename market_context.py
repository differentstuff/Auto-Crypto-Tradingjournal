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
CACHE_TTL    = 300   # 5 minutes default


def _cached(key: str, fn, ttl: int = CACHE_TTL):
    now = time.time()
    if key in _cache:
        ts, data = _cache[key]
        if now - ts < ttl:
            return data
    result = fn()
    _cache[key] = (now, result)
    return result


def get_market_str(symbols: list, fallback: str = "") -> str:
    """Fetch market context and format it as a prompt string. Returns fallback on error."""
    try:
        return format_for_prompt(get_market_context(symbols))
    except Exception:
        return fallback


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


# ── BTC Dominance ─────────────────────────────────────────────────────────────

def get_btc_dominance() -> dict:
    """Fetch BTC market dominance from CoinGecko (no auth)."""
    def _fetch():
        try:
            req = urllib.request.Request(
                "https://api.coingecko.com/api/v3/global",
                headers={"User-Agent": "TradingJournal/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read())
            data = d.get("data", {})
            dom  = round(float(data.get("market_cap_percentage", {}).get("btc", 0)), 2)
            chg  = round(float(data.get("market_cap_change_percentage_24h_usd", 0)), 2)
            return {"btc_dominance": dom, "change_24h": chg, "ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return _cached("btc_dominance", _fetch, ttl=900)   # 15-min cache


# ── Economic Calendar ──────────────────────────────────────────────────────────

def get_economic_calendar() -> list:
    """
    Fetch this week's high-impact USD events from ForexFactory community mirror.
    Returns events for today and tomorrow only.
    """
    def _fetch():
        try:
            req = urllib.request.Request(
                "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
                headers={"User-Agent": "TradingJournal/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                events = json.loads(r.read())

            from datetime import datetime, timezone, timedelta
            today    = datetime.now(timezone.utc).date()
            tomorrow = today + timedelta(days=1)

            result = []
            for e in events:
                if e.get("impact") != "High":
                    continue
                country = e.get("country", e.get("currency", ""))
                if country != "USD":
                    continue
                raw = e.get("date", "")
                try:
                    ev_date = datetime.strptime(raw, "%m-%d-%Y").date()
                except ValueError:
                    continue
                if ev_date not in (today, tomorrow):
                    continue
                result.append({
                    "title":    e.get("title", ""),
                    "time":     e.get("time", ""),
                    "forecast": e.get("forecast", ""),
                    "previous": e.get("previous", ""),
                    "when":     "today" if ev_date == today else "tomorrow",
                })
            return result
        except Exception:
            return []
    return _cached("eco_calendar", _fetch, ttl=3600)   # 1-hour cache


# ── Combined ───────────────────────────────────────────────────────────────────

def get_market_context(symbols: Optional[list] = None) -> dict:
    """Fear & Greed + BTC dominance + per-symbol funding rate + long/short ratio."""
    result = {
        "fear_greed":    get_fear_greed(),
        "btc_dominance": get_btc_dominance(),
        "symbols":       {},
    }
    if symbols:
        for sym in list(dict.fromkeys(symbols))[:6]:
            result["symbols"][sym] = {
                "funding":    get_funding_rate(sym),
                "long_short": get_long_short_ratio(sym),
            }
    return result


def get_btc_regime(as_of_ts: str = None) -> str:
    """
    Determine BTC market regime ('bull', 'bear', or 'range') at a given ISO timestamp.
    Uses 50-day EMA vs 200-day EMA cross: bull if EMA50 > EMA200, bear otherwise.
    Falls back to 'range' on any error.

    as_of_ts — ISO datetime string; if None, uses current market data.
    """
    try:
        import chart_context as _cc
        tfs  = ["1D"]
        ctx  = _cc.get_chart_context("BTCUSDT", tfs)
        inds = ctx.get("1D", {}).get("indicators", {})
        ema  = inds.get("ema", {}) or {}
        # ema contains: ema20, ema50, ema200, current_price
        ema50  = ema.get("ema50",  0) or 0
        ema200 = ema.get("ema200", 0) or 0
        price  = ema.get("current_price", 0) or 0
        if ema50 and ema200:
            spread = (ema50 - ema200) / ema200
            if spread > 0.03:
                return "bull"
            elif spread < -0.03:
                return "bear"
            else:
                return "range"
        return "range"
    except Exception:
        return "range"


def format_for_prompt(ctx: dict) -> str:
    """Concise text block for Claude prompts."""
    lines = []
    fg = ctx.get("fear_greed", {})
    if fg.get("ok"):
        lines.append(f"Fear & Greed Index: {fg['value']}/100 — {fg['classification']}")
    bd = ctx.get("btc_dominance", {})
    if bd.get("ok"):
        arrow = "↑" if bd["change_24h"] >= 0 else "↓"
        lines.append(f"BTC Dominance: {bd['btc_dominance']}% ({arrow}{abs(bd['change_24h'])}% 24h)")
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

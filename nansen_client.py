"""
nansen_client.py — Nansen.ai smart money intelligence.

Strategy:
  - Fetch the full token screener ONCE per scan cycle (1 API credit).
  - Cache for 30 minutes (same as scanner TTL).
  - Look up individual symbols from the in-memory cache — zero extra calls.
  - Only surface a signal when 5+ smart money wallets are active (Q2: B).
  - Only called for scanner finalists and call analysis (Q1: C).

Configuration:
  NANSEN_API_KEY in .env  (free account at nansen.ai)
"""

import json
import os
from constants import NANSEN_CACHE_TTL
import threading
import time
import urllib.request
from typing import Optional

NANSEN_API_KEY = os.environ.get("NANSEN_API_KEY", "")
NANSEN_BASE    = "https://api.nansen.ai/api/v1"
MIN_TRADERS    = 5     # minimum smart money wallets to surface a signal
CHAINS         = ["ethereum", "solana", "base"]

_cache_lock  = threading.Lock()
_cache_ts: float = 0.0
_cache_data: list = []   # raw screener rows


def is_configured() -> bool:
    return bool(NANSEN_API_KEY)


# ── Internal HTTP ──────────────────────────────────────────────────────────────

def _post(path: str, body: dict, timeout: int = 15) -> dict:
    if not NANSEN_API_KEY:
        return {"error": "NANSEN_API_KEY not set"}
    url  = NANSEN_BASE + path
    data = json.dumps(body).encode()
    req  = urllib.request.Request(url, data=data, method="POST")
    # Cloudflare on api.nansen.ai blocks Python-urllib User-Agent (error 1010).
    # Must send browser-like headers including Origin/Referer to pass the check.
    req.add_header("Content-Type",   "application/json")
    req.add_header("apiKey",         NANSEN_API_KEY)
    req.add_header("User-Agent",     "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                                     "Chrome/124.0.0.0 Safari/537.36")
    req.add_header("Accept",         "application/json, text/plain, */*")
    req.add_header("Accept-Language","en-US,en;q=0.9")
    req.add_header("Origin",         "https://app.nansen.ai")
    req.add_header("Referer",        "https://app.nansen.ai/")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


# ── Screener cache ─────────────────────────────────────────────────────────────

def _fetch_screener() -> list:
    """
    Fetch ALL tokens with smart money activity across major chains.
    Returns raw list of screener rows. Uses no market-cap filter so
    major coins like ETH/SOL are included even when smart money is
    using DEX rather than large on-chain transfers.
    """
    result = _post("/token-screener", {
        "chains":    CHAINS,
        "timeframe": "24h",
        "filters":   {"only_smart_money": True},
        "order_by":  [{"field": "netflow", "direction": "DESC"}],
        "pagination": {"page": 1, "per_page": 100},
    })
    return result.get("data", [])


def _get_cached_screener() -> list:
    """Return cached screener data, refreshing if stale. Thread-safe via double-checked locking."""
    global _cache_ts, _cache_data
    # Fast path: check without lock
    now = time.time()
    if _cache_data and (now - _cache_ts) < NANSEN_CACHE_TTL:
        return _cache_data

    with _cache_lock:
        # Second check under lock: another thread may have refreshed while we waited
        now = time.time()
        if _cache_data and (now - _cache_ts) < NANSEN_CACHE_TTL:
            return _cache_data
        rows = _fetch_screener()
        _cache_data = rows
        _cache_ts   = time.time()
    return rows


def refresh_cache():
    """Force-refresh the screener cache. Call once at scan start."""
    global _cache_ts, _cache_data
    rows = _fetch_screener()
    with _cache_lock:
        _cache_data = rows
        _cache_ts   = time.time()
    print(f"[Nansen] Screener refreshed — {len(rows)} tokens with smart money activity", flush=True)
    return rows


# ── Public API ─────────────────────────────────────────────────────────────────

def get_smart_money_signal(symbol: str) -> dict:
    """
    Return smart money signal for a single symbol (e.g. 'BTCUSDT' or 'BTC').

    Returns:
      ok=True  when 5+ smart money wallets are active — includes netflow,
               direction (accumulating/distributing), strength, trader count.
      ok=False when fewer than MIN_TRADERS wallets found, or not in screener.

    Uses cached screener data — no extra API call per symbol.
    """
    if not NANSEN_API_KEY:
        return {"ok": False, "reason": "Nansen not configured"}

    base = symbol.upper().replace("USDT", "").replace("PERP", "").replace("-", "")
    rows = _get_cached_screener()

    # Match by token_symbol (case-insensitive)
    match = next(
        (r for r in rows if r.get("token_symbol", "").upper() == base),
        None
    )

    if not match:
        return {"ok": False, "reason": "not in Nansen screener (no smart money activity)"}

    traders = match.get("nof_traders", 0) or 0
    if traders < MIN_TRADERS:
        return {
            "ok": False,
            "reason": f"only {traders} smart money trader(s) active (minimum {MIN_TRADERS})",
        }

    netflow   = match.get("netflow", 0) or 0
    buy_vol   = match.get("buy_volume", 0) or 0
    sell_vol  = match.get("sell_volume", 0) or 0
    px_change = match.get("price_change", 0) or 0
    mc        = match.get("market_cap_usd")

    direction = "accumulating" if netflow > 0 else "distributing"
    strength  = (
        "strong"   if abs(netflow) > 500_000 else
        "moderate" if abs(netflow) > 50_000  else
        "weak"
    )

    return {
        "ok":             True,
        "symbol":         base,
        "chain":          match.get("chain", ""),
        "netflow_usd":    round(netflow, 0),
        "buy_vol_usd":    round(buy_vol, 0),
        "sell_vol_usd":   round(sell_vol, 0),
        "nof_traders":    traders,
        "px_change_24h":  round(px_change * 100, 2),
        "market_cap_usd": mc,
        "direction":      direction,
        "strength":       strength,
        # Compact prompt line
        "prompt_line": (
            f"Nansen smart money ({traders} wallets): {direction} — "
            f"netflow ${netflow:+,.0f} "
            f"(buy ${buy_vol:,.0f} / sell ${sell_vol:,.0f}) [{strength}]"
        ),
    }


def get_signals_for_symbols(symbols: list) -> dict:
    """
    Bulk lookup for a list of symbols. Returns {symbol: signal_dict}.
    Refreshes cache once then does all lookups from memory.
    """
    if not NANSEN_API_KEY:
        return {}
    # One API call for all symbols
    _get_cached_screener()
    return {sym: get_smart_money_signal(sym) for sym in symbols}


def get_top_movers(n_accumulators: int = 10, n_distributors: int = 5,
                   min_traders: int = MIN_TRADERS,
                   min_market_cap: float = 10_000_000) -> dict:
    """
    Return top smart money accumulators and distributors.
    Used for the Smart Money widget on the scanner/dashboard page.
    """
    rows = _get_cached_screener()
    eligible = [
        r for r in rows
        if (r.get("nof_traders") or 0) >= min_traders
        and (r.get("market_cap_usd") or 0) >= min_market_cap
    ]
    accumulators = sorted(
        [r for r in eligible if (r.get("netflow") or 0) > 0],
        key=lambda x: -(x.get("netflow") or 0)
    )[:n_accumulators]
    distributors = sorted(
        [r for r in eligible if (r.get("netflow") or 0) < 0],
        key=lambda x: (x.get("netflow") or 0)
    )[:n_distributors]

    def _fmt(r):
        return {
            "symbol":      r.get("token_symbol"),
            "chain":       r.get("chain"),
            "netflow_usd": round(r.get("netflow", 0), 0),
            "nof_traders": r.get("nof_traders"),
            "px_change":   round((r.get("price_change") or 0) * 100, 2),
            "market_cap_usd": r.get("market_cap_usd"),
        }

    return {
        "accumulators": [_fmt(r) for r in accumulators],
        "distributors":  [_fmt(r) for r in distributors],
        "total_screened": len(rows),
        "eligible":       len(eligible),
        "cached_at":      time.strftime("%H:%M UTC", time.gmtime(_cache_ts)),
    }

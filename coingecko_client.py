"""
coingecko_client.py — CoinGecko keyless public API.

Provides BTC dominance, market cap, market cap rank, and 24h volume
for individual coins. No API key required.

Rate limit: 30 calls/minute (keyless public API)
Docs: https://docs.coingecko.com/docs/keyless-public-api

Confirmed response shapes (2026-05-15):
  /global → {"data": {"market_cap_percentage": {"btc": 52.3, ...},
    "total_market_cap": {"usd": 2.7e12}, "total_volume": {"usd": 80e9},
    "active_cryptocurrencies": 17405}}
  /coins/markets → [{"market_cap_rank": 1, "total_volume": 40e9,
    "price_change_percentage_24h": -3.1, "market_cap": 1.58e12, ...}]
"""
import urllib.request
import json
import logging
import threading
import time

_log = logging.getLogger(__name__)
_BASE = "https://api.coingecko.com/api/v3"
_TIMEOUT = 10

# Rate limiter: CoinGecko keyless public API allows 30 requests/minute.
_ratelimit_lock = threading.Lock()
_request_times: list[float] = []


def _rate_limit():
    """Block if 28+ requests have been made in the last 60 seconds (leaves 2 buffer)."""
    with _ratelimit_lock:
        now = time.time()
        # Drop timestamps older than 60s
        _request_times[:] = [t for t in _request_times if now - t < 60]
        if len(_request_times) >= 28:
            sleep_for = 60 - (now - _request_times[0])
            if sleep_for > 0:
                time.sleep(sleep_for)
        _request_times.append(time.time())

# Map CCXT/trading symbols to CoinGecko coin IDs
_COIN_IDS = {
    "BTCUSDT": "bitcoin",     "ETHUSDT": "ethereum",
    "SOLUSDT": "solana",      "BNBUSDT": "binancecoin",
    "XRPUSDT": "ripple",      "ADAUSDT": "cardano",
    "DOGEUSDT": "dogecoin",   "AVAXUSDT": "avalanche-2",
    "LINKUSDT": "chainlink",  "DOTUSDT": "polkadot",
    "MATICUSDT": "matic-network", "UNIUSDT": "uniswap",
    "AAVEUSDT": "aave",       "LTCUSDT": "litecoin",
    "ATOMUSDT": "cosmos",     "NEARUSDT": "near",
    "APTUSDT": "aptos",       "ARBUSDT": "arbitrum",
    "OPUSDT": "optimism",     "INJUSDT": "injective-protocol",
    "SUIUSDT": "sui",         "SEIUSDT": "sei-network",
    "TIAUSDT": "celestia",    "JUPUSDT": "jupiter-exchange-solana",
    "PENDLEUSDT": "pendle",   "GMXUSDT": "gmx",
    "EIGENUSDT": "eigenlayer",
}


def _get(path: str, params: dict = None) -> dict | list | None:
    qs = ""
    if params:
        qs = "?" + "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{_BASE}/{path}{qs}"
    try:
        _rate_limit()
        req = urllib.request.Request(url, headers={"User-Agent": "TradingJournal/1.0"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return json.loads(r.read())
    except Exception as e:
        _log.debug("CoinGecko %s failed: %s", path, e)
        return None


def get_global_market() -> dict:
    """
    Global crypto market stats: BTC dominance, total market cap, altcoin season.
    Returns {} on failure.
    """
    data = _get("global")
    try:
        d = data.get("data", {}) if isinstance(data, dict) else {}
        btc_dom = round(float(d.get("market_cap_percentage", {}).get("btc", 0)), 1)
        total_mcap = float((d.get("total_market_cap") or {}).get("usd", 0))
        total_vol  = float((d.get("total_volume") or {}).get("usd", 0))
        # Altcoin season: BTC dominance < 50% suggests alts outperforming
        if btc_dom < 45:
            regime = "altcoin_season"
        elif btc_dom < 55:
            regime = "mixed"
        else:
            regime = "btc_dominant"
        return {
            "btc_dominance_pct":    btc_dom,
            "total_market_cap_usd": round(total_mcap, 0),
            "total_volume_24h_usd": round(total_vol, 0),
            "market_regime":        regime,
            "active_coins":         d.get("active_cryptocurrencies", 0),
        }
    except Exception:
        return {}


def get_coin_market_data(symbol: str) -> dict:
    """
    Market cap rank, 24h volume, 24h price change for a specific coin.
    Returns {} for unknown symbols or on failure.
    """
    coin_id = _COIN_IDS.get(symbol.upper())
    if not coin_id:
        return {}
    data = _get("coins/markets", {
        "vs_currency": "usd",
        "ids": coin_id,
        "per_page": "1",
        "page": "1",
    })
    try:
        record = data[0] if data else {}
        rank = record.get("market_cap_rank")
        vol  = record.get("total_volume", 0) or 0
        chg  = record.get("price_change_percentage_24h", 0) or 0
        mcap = record.get("market_cap", 0) or 0
        # Cap risk: top-10 = large-cap, 11-50 = mid-cap, 50+ = small/micro
        if rank and rank <= 10:
            cap_tier = "large_cap"
        elif rank and rank <= 50:
            cap_tier = "mid_cap"
        elif rank:
            cap_tier = "small_cap"
        else:
            cap_tier = "micro_cap"
        return {
            "market_cap_rank":      rank,
            "market_cap_usd":       round(mcap, 0),
            "volume_24h_usd":       round(vol, 0),
            "price_change_24h_pct": round(chg, 2),
            "cap_tier":             cap_tier,
        }
    except Exception:
        return {}


def get_trending_coins() -> list:
    """
    Return list of trending coin symbols (top 10) from CoinGecko last 24h.
    Trending = already popular = momentum chase risk for new entries.
    Returns [] on failure.

    Confirmed response (2026-05-15):
      /search/trending -> {"coins": [{"item": {"symbol": "BTC", "market_cap_rank": 1}}, ...]}
    Note: top_gainers_losers requires paid CoinGecko plan - not available on keyless.
    """
    data = _get("search/trending")
    try:
        symbols = []
        for item in (data.get("coins") or []):
            coin = item.get("item") or {}
            sym = (coin.get("symbol") or "").upper().strip()
            if sym:
                symbols.append(sym)
        return symbols[:10]
    except Exception:
        return []

# liquidation_levels.py
"""Forced-liquidation cluster detection from Binance USDM via CCXT."""
import logging
import time
import ccxt

_log  = logging.getLogger(__name__)
_CACHE: dict[str, tuple[float, dict]] = {}
_TTL  = 900  # 15 min


def _fetch(symbol: str) -> dict:
    try:
        ex  = ccxt.binanceusdm({"options": {"defaultType": "future"}})
        liq = ex.fetch_liquidations(symbol, limit=500)
        if not liq:
            return {"ok": False, "reason": "empty"}
        prices  = [float(x["price"])            for x in liq if x.get("price")]
        sides   = [x.get("side", "")            for x in liq]
        volumes = [float(x.get("amount") or 0)  for x in liq]
        if not prices:
            return {"ok": False, "reason": "no prices"}
        lo, hi = min(prices), max(prices)
        if lo == hi:
            return {"ok": False, "reason": "no range"}
        N  = 20
        bw = (hi - lo) / N
        bins: dict[int, dict] = {}
        for i, p in enumerate(prices):
            b = min(int((p - lo) / bw), N - 1)
            if b not in bins:
                bins[b] = {"price": lo + (b + 0.5) * bw,
                           "long_vol": 0.0, "short_vol": 0.0}
            v = volumes[i] if i < len(volumes) else 0
            if sides[i] == "buy":
                bins[b]["long_vol"] += v
            else:
                bins[b]["short_vol"] += v
        clusters   = sorted(bins.values(),
                             key=lambda x: x["long_vol"] + x["short_vol"],
                             reverse=True)
        long_wall  = max(bins.values(), key=lambda x: x["long_vol"])["price"]
        short_wall = max(bins.values(), key=lambda x: x["short_vol"])["price"]
        return {"ok": True, "long_wall": long_wall,
                "short_wall": short_wall, "clusters": clusters[:5],
                "total": len(prices)}
    except Exception as exc:
        _log.warning("liquidation_levels %s: %s", symbol, exc)
        return {"ok": False, "reason": str(exc)}


def get_liquidation_clusters(symbol: str) -> dict:
    """Return liquidation cluster data for symbol, TTL-cached."""
    now = time.time()
    if symbol in _CACHE:
        ts, data = _CACHE[symbol]
        if now - ts < _TTL:
            return data
    result      = _fetch(symbol)
    _CACHE[symbol] = (now, result)
    return result

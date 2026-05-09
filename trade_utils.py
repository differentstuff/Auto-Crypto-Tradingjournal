"""
trade_utils.py — Shared trading utilities for AI analysis modules.

Centralises sector definitions and ATR-based SL quality check,
which were previously duplicated in ai_call.py and ai_limit.py.
"""

import chart_context

# Sector → USDT symbol list (synced with JS SECTORS in 08-live.js)
SECTORS = {
    "BTC":      ["BTCUSDT", "WBTCUSDT"],
    "ETH/L2":   ["ETHUSDT", "ARBUSDT", "OPUSDT", "MATICUSDT", "STRKUSDT", "ZKUSDT", "SCROLLUSDT"],
    "SOL/L1":   ["SOLUSDT", "AVAXUSDT", "SUIUSDT", "APTUSDT", "NEARUSDT", "SEIUSDT", "INJUSDT"],
    "Meme":     ["DOGEUSDT", "SHIBUSDT", "PEPEUSDT", "BOMEUSDT", "WIFUSDT", "BONKUSDT",
                 "FLOKIUSDT", "MOGUSDT", "POPCATUSDT"],
    "DeFi":     ["UNIUSDT", "AAVEUSDT", "CRVUSDT", "MKRUSDT", "SNXUSDT", "COMPUSDT", "DYDXUSDT"],
    "AI/Infra": ["FETUSDT", "RENDERUSDT", "WLDUSDT", "TAOUSDT", "AGIXUSDT", "GRTUSDT"],
}


def atr_sl_warning(symbol: str, entry: float, sl: float) -> str:
    """Return a warning string if SL distance is within 1H ATR noise range."""
    try:
        ctx     = chart_context.get_chart_context(symbol, ["1H"])
        inds    = ctx.get("1H", {}).get("indicators", {})
        atr     = inds.get("atr", {})
        if not atr or not atr.get("value"):
            return ""
        atr_val = atr["value"]
        sl_dist = abs(entry - sl)
        if sl_dist < atr_val * 0.5:
            return (f"SL distance {sl_dist:.4f} < 0.5× 1H ATR ({atr_val:.4f}) — "
                    "stop is inside noise, very high chance of premature trigger")
        if sl_dist < atr_val:
            return (f"SL distance {sl_dist:.4f} < 1× 1H ATR ({atr_val:.4f}) — "
                    "tight stop, moderate noise risk")
    except Exception:
        pass
    return ""


def normalize_symbol(s: str) -> str:
    """BTC/USDT, btc-usdt → BTCUSDT.""",
    return (s or '').upper().replace('/', '').replace('-', '').replace('_', '').strip()


def normalize_direction(s: str) -> str:
    """long/buy/open_long → Long; short/sell → Short.""",
    d = (s or '').strip().lower()
    if d in ('long', 'buy', 'open_long'):  return 'Long'
    if d in ('short', 'sell', 'open_short'): return 'Short'
    return s

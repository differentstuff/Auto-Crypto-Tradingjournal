"""ccxt_client.py — CCXT exchange factory. Provides initialized exchange instances."""
import os
import time

import ccxt

_binance_price_cache: dict = {}
BINANCE_PRICE_CACHE_TTL = 60  # seconds


def get_blofin_exchange() -> ccxt.Exchange:
    """Return an authenticated Blofin exchange instance. Reads credentials from env."""
    return ccxt.blofin({
        "apiKey":          os.environ.get("BLOFIN_API_KEY", ""),
        "secret":          os.environ.get("BLOFIN_SECRET_KEY", ""),
        "password":        os.environ.get("BLOFIN_PASSPHRASE", ""),
        "enableRateLimit": True,
    })


def get_binance_exchange() -> ccxt.Exchange:
    """Public-only Binance instance — no auth required for market data."""
    return ccxt.binance({"enableRateLimit": True})


def get_binance_price(symbol: str) -> float | None:
    """
    Fetch last price from Binance for SMT divergence check.
    symbol: 'BTCUSDT' -> maps to 'BTC/USDT:USDT' for Binance futures.
    Returns None on any error. 60-second cache.
    """
    now = time.time()
    cached = _binance_price_cache.get(symbol)
    if cached and (now - cached[1]) < BINANCE_PRICE_CACHE_TTL:
        return cached[0]
    try:
        exchange = get_binance_exchange()
        ccxt_sym = symbol.removesuffix("USDT") + "/USDT:USDT"
        ticker = exchange.fetch_ticker(ccxt_sym)
        price = ticker["last"]
        _binance_price_cache[symbol] = (price, now)
        return price
    except Exception:
        return None


def get_live_price(symbol: str) -> float | None:
    """
    Fetch current last price for any symbol — tries Binance first (cached),
    falls back to Bitget public ticker. Returns None only if both fail.
    Used for price-freshness checks before Telegram alerts.
    """
    price = get_binance_price(symbol)
    if price is not None:
        return price
    try:
        import ccxt as _ccxt
        bitget = _ccxt.bitget({"enableRateLimit": True})
        ccxt_sym = symbol.removesuffix("USDT") + "/USDT:USDT"
        ticker = bitget.fetch_ticker(ccxt_sym)
        return ticker.get("last")
    except Exception:
        return None


def get_binance_ticker_change(symbol: str) -> float | None:
    """
    Return the 24h percentage price change for a symbol on Binance.
    Returns None on error. Uses cached exchange instance.
    """
    try:
        exchange = get_binance_exchange()
        ccxt_sym = symbol.replace("USDT", "/USDT:USDT")
        ticker = exchange.fetch_ticker(ccxt_sym)
        return ticker.get("percentage")  # float, e.g. 2.3 for +2.3%
    except Exception:
        return None


def get_multi_exchange_ls_ratio(symbol: str) -> dict:
    """
    Fetch long/short ratio from Binance, Bybit, and OKX simultaneously.
    Returns {"binance": float|None, "bybit": float|None, "okx": float|None,
             "consensus": str}  # "longs_dominant"|"shorts_dominant"|"neutral"|"unknown"
    All public endpoints — no API key required.
    """
    import ccxt, threading

    ratios: dict = {}

    def _fetch(exchange_id: str, ccxt_symbol: str):
        try:
            ex = getattr(ccxt, exchange_id)({"enableRateLimit": True})
            ls = ex.fetch_long_short_ratio_history(ccxt_symbol, "1h", limit=1)
            if ls:
                ratios[exchange_id] = round(float(ls[-1].get("longShortRatio", 0) or 0), 3)
        except Exception:
            ratios[exchange_id] = None

    # Normalize symbol: BTCUSDT -> BTC/USDT:USDT for futures
    base = symbol.replace("USDT", "")
    ccxt_sym = f"{base}/USDT:USDT"

    threads = [
        threading.Thread(target=_fetch, args=("binance",  ccxt_sym)),
        threading.Thread(target=_fetch, args=("bybit",    ccxt_sym)),
        threading.Thread(target=_fetch, args=("okx",      ccxt_sym)),
    ]
    for t in threads: t.start()
    for t in threads: t.join(timeout=5)

    valid = [v for v in ratios.values() if v is not None and v > 0]
    if not valid:
        consensus = "unknown"
    else:
        avg = sum(valid) / len(valid)
        if avg > 1.5:
            consensus = "longs_dominant"
        elif avg < 0.75:
            consensus = "shorts_dominant"
        else:
            consensus = "neutral"

    return {
        "binance":   ratios.get("binance"),
        "bybit":     ratios.get("bybit"),
        "okx":       ratios.get("okx"),
        "consensus": consensus,
    }


def get_binance_futures_symbols(min_vol_usd: float = 50_000_000) -> list:
    """
    Return top USDT-M linear futures symbols from Binance filtered by 24h volume.
    Strips '/USDT:USDT' suffix to match journal symbol format (e.g. 'BTCUSDT').
    Returns empty list on any error.

    Uses defaultType='future' so fetch_tickers() hits the USDT-M perpetuals
    endpoint instead of the spot market (which uses BTC/USDT format).
    """
    try:
        import ccxt as _ccxt
        futures_ex = _ccxt.binance({
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })
        tickers = futures_ex.fetch_tickers()
        symbols = []
        for sym, t in tickers.items():
            if not sym.endswith("/USDT:USDT"):
                continue
            vol = t.get("quoteVolume") or 0
            if vol >= min_vol_usd:
                symbols.append(sym.replace("/USDT:USDT", "USDT"))
        return sorted(
            symbols,
            key=lambda s: tickers.get(s.removesuffix("USDT") + "/USDT:USDT", {}).get("quoteVolume", 0),
            reverse=True,
        )[:300]
    except Exception:
        return []


def get_binance_oi_map(symbols: list) -> dict:
    """
    Return {symbol: open_interest_usd} for a list of USDT-M symbols.
    Uses Binance futures fetch_tickers which includes openInterestValue.
    Returns empty dict on any error — OI filter is best-effort.
    symbol format: 'BTCUSDT' (not 'BTC/USDT:USDT').
    """
    try:
        import ccxt as _ccxt
        futures_ex = _ccxt.binance({
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })
        target = set(symbols)
        result = {}
        tickers = futures_ex.fetch_tickers()
        for sym, t in tickers.items():
            if not sym.endswith("/USDT:USDT"):
                continue
            journal_sym = sym.replace("/USDT:USDT", "USDT")
            if journal_sym not in target:
                continue
            oi = t.get("info", {}).get("openInterestValue") or t.get("openInterestValue")
            if oi is not None:
                try:
                    result[journal_sym] = float(oi)
                except (TypeError, ValueError):
                    pass
        return result
    except Exception:
        return {}

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


def get_binance_futures_symbols(min_vol_usd: float = 50_000_000) -> list:
    """
    Return top USDT-M linear futures symbols from Binance filtered by 24h volume.
    Strips '/USDT:USDT' suffix to match journal symbol format (e.g. 'BTCUSDT').
    Returns empty list on any error.
    """
    try:
        exchange = get_binance_exchange()
        tickers = exchange.fetch_tickers()
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
        )[:100]
    except Exception:
        return []

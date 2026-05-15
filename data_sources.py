"""
data_sources.py — Thin adapters over external data clients.

Each function wraps exactly one external call and returns the data shape
that CollectorResult expects. Failures return the zero/empty value for
that field — never raise.

Adding a new source: add one function here, then call it from
agent_data_collector.py. No other file needs to change.
"""
from __future__ import annotations


def fetch_candles(symbol: str, tf: str) -> object:
    """Single-timeframe OHLCV candle fetch. Raises on failure (pipeline requires candles)."""
    import chart_context
    return chart_context.get_candles(symbol, tf)


def fetch_funding_rate(symbol: str) -> dict:
    """Bitget funding rate for a symbol. Returns {} on failure."""
    try:
        import market_context
        return market_context.get_funding_rate(symbol)
    except Exception:
        return {}


def fetch_open_interest(symbol: str) -> dict:
    """Open Interest from Binance futures. Returns {} on failure."""
    try:
        import market_context
        return market_context.get_open_interest(symbol)
    except Exception:
        return {}


def fetch_long_short_ratio(symbol: str) -> dict:
    """Long/short account ratio from Bitget. Returns {} on failure."""
    try:
        import market_context
        return market_context.get_long_short_ratio(symbol)
    except Exception:
        return {}


def fetch_fear_greed() -> dict:
    """Fear & Greed index from alternative.me. Returns {} on failure."""
    try:
        import market_context
        return market_context.get_fear_greed()
    except Exception:
        return {}


def fetch_fred_macro() -> dict:
    """FRED macro indicators (Fed rate, CPI, M2, 10Y). Returns {} on failure."""
    try:
        import market_context
        return market_context.get_fred_macro()
    except Exception:
        return {}


def fetch_smart_money(symbol: str) -> dict:
    """Nansen smart-money wallet signals. Returns {} on failure."""
    try:
        import nansen_client
        return nansen_client.get_smart_money_signal(symbol)
    except Exception:
        return {}


def fetch_news(symbol: str, direction: str = "") -> dict:
    """Grok social intelligence / news context. Returns {} on failure."""
    try:
        import grok_client
        text, weight = grok_client.get_coin_context(symbol, direction)
        if not text:
            return {}
        return {"text": text, "weight": weight}
    except Exception:
        return {}

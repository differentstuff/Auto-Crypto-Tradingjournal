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


def fetch_macro_regime() -> dict:
    """VIX + DXY macro regime from yfinance."""
    try:
        from market_context import get_macro_regime
        return get_macro_regime()
    except Exception:
        return {"vix": None, "dxy": None, "regime": "unknown"}


def fetch_ls_consensus(symbol: str) -> dict:
    """Multi-exchange long/short ratio consensus."""
    try:
        from market_context import get_ls_consensus
        return get_ls_consensus(symbol)
    except Exception:
        return {}


def fetch_defi_tvl(symbol: str) -> dict:
    """DefiLlama TVL for DeFi protocol tokens. Empty dict for non-DeFi."""
    try:
        from market_context import get_defi_tvl
        return get_defi_tvl(symbol)
    except Exception:
        return {}


def fetch_btc_mempool() -> dict:
    """BTC mempool stats from blockchain.com."""
    try:
        from market_context import get_btc_mempool
        return get_btc_mempool()
    except Exception:
        return {"congestion": "unknown"}


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


def fetch_coinalyze(symbol: str) -> dict:
    """
    Aggregated derivatives data from Coinalyze (multi-exchange OI, liquidations,
    funding rate, L/S ratio). Returns {} if API key not configured or on error.
    """
    try:
        import coinalyze_client
        if not coinalyze_client._API_KEY:
            return {}
        return coinalyze_client.get_all(symbol)
    except Exception:
        return {}

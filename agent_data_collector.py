"""
agent_data_collector.py — DataCollector agent.

Single entry point for all external data. Runs all non-candle fetches
in parallel via ThreadPoolExecutor. Candle fetch is sequential and
blocking — if it fails the pipeline cannot continue.

All non-candle sources degrade gracefully to {} on failure.
"""
import time
from concurrent.futures import ThreadPoolExecutor

from data_sources import (
    fetch_candles,
    fetch_smart_money,
    fetch_news,
    fetch_macro_regime,
    fetch_ls_consensus,
    fetch_defi_tvl,
    fetch_btc_mempool,
    fetch_coinalyze,
    fetch_economic_events,
    fetch_global_market,
    fetch_coin_market_data,
    fetch_trending_coins,
)

from agent_types import CollectorInput, CollectorResult


def run(inp: CollectorInput) -> CollectorResult:
    symbol    = inp["symbol"]
    direction = inp["direction"]
    tfs       = inp["timeframes"]

    # Candles are blocking — raises on failure (downstream agents require them)
    candles = {tf: fetch_candles(symbol, tf) for tf in tfs}

    def _safe(fn):
        try:
            return fn()
        except Exception:
            return {}

    with ThreadPoolExecutor(max_workers=10) as ex:
        f_nansen      = ex.submit(_safe, lambda: fetch_smart_money(symbol))
        f_grok        = ex.submit(_safe, lambda: fetch_news(symbol, direction))
        f_macro       = ex.submit(_safe, fetch_macro_regime)
        f_ls_con      = ex.submit(_safe, lambda: fetch_ls_consensus(symbol))
        f_defi        = ex.submit(_safe, lambda: fetch_defi_tvl(symbol))
        f_mempool     = ex.submit(_safe, fetch_btc_mempool)
        f_coinalyze   = ex.submit(_safe, lambda: fetch_coinalyze(symbol))
        f_eco         = ex.submit(_safe, fetch_economic_events)
        f_global_mkt  = ex.submit(_safe, fetch_global_market)
        f_coin_mkt    = ex.submit(_safe, lambda: fetch_coin_market_data(symbol))
        f_trending    = ex.submit(_safe, fetch_trending_coins)

    return CollectorResult(
        symbol           = symbol,
        candles          = candles,
        nansen           = f_nansen.result(),
        grok             = f_grok.result(),
        macro_regime     = f_macro.result(),
        ls_consensus     = f_ls_con.result(),
        defi_tvl         = f_defi.result(),
        btc_mempool      = f_mempool.result(),
        coinalyze        = f_coinalyze.result(),
        economic_events  = f_eco.result(),
        global_market    = f_global_mkt.result(),
        coin_market_data = f_coin_mkt.result(),
        trending_coins   = f_trending.result() or [],
        fetched_at       = time.time(),
    )

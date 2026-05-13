"""
agent_data_collector.py — DataCollector agent.

Single entry point for all external data. Runs all non-candle fetches
in parallel via ThreadPoolExecutor. Candle fetch is sequential and
blocking — if it fails the pipeline cannot continue.

All non-candle sources degrade gracefully to {} on failure.
"""
import time
from concurrent.futures import ThreadPoolExecutor

import chart_context
import market_context
import nansen_client
import grok_client

from agent_types import CollectorInput, CollectorResult


def run(inp: CollectorInput) -> CollectorResult:
    symbol    = inp["symbol"]
    direction = inp["direction"]
    tfs       = inp["timeframes"]

    # Candles are blocking — raises on failure (downstream agents require them)
    candles = {tf: chart_context.get_candles(symbol, tf) for tf in tfs}

    def _safe(fn):
        try:
            return fn()
        except Exception:
            return {}

    with ThreadPoolExecutor(max_workers=7) as ex:
        f_funding = ex.submit(_safe, lambda: market_context.get_funding_rate(symbol))
        f_oi      = ex.submit(_safe, lambda: market_context.get_open_interest(symbol))
        f_ls      = ex.submit(_safe, lambda: market_context.get_long_short_ratio(symbol))
        f_fg      = ex.submit(_safe, market_context.get_fear_greed)
        f_fred    = ex.submit(_safe, market_context.get_fred_macro)
        f_nansen  = ex.submit(_safe, lambda: nansen_client.get_smart_money_signal(symbol))
        f_grok    = ex.submit(_safe, lambda: _grok(symbol, direction))

    return CollectorResult(
        symbol        = symbol,
        candles       = candles,
        funding_rate  = f_funding.result(),
        open_interest = f_oi.result(),
        long_short    = f_ls.result(),
        fear_greed    = f_fg.result(),
        fred_macro    = f_fred.result(),
        nansen        = f_nansen.result(),
        grok          = f_grok.result(),
        fetched_at    = time.time(),
    )


def _grok(symbol: str, direction: str) -> dict:
    text, weight = grok_client.get_coin_context(symbol, direction)
    if not text:
        return {}
    return {"text": text, "weight": weight}

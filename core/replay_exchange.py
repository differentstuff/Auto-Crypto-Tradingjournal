"""
core/replay_exchange.py -- Exchange wrapper for replay mode.

Wraps Exchange for replay mode:
  - fetch_ohlcv(): injects since=t_cursor_ms, caches close prices
  - fetch_tickers(): returns cached close prices (not live tickers)
  - fetch_positions(): returns [] (paper mode manages positions)
  - fetch_balance(): returns {} (paper mode manages equity)
  - All other methods delegate to the real Exchange
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from core.exchange import Exchange
from core.virtual_clock import VirtualClock

_log = logging.getLogger(__name__)


def _timeframe_to_ms(timeframe: str) -> int:
    """Convert a timeframe string (e.g. '4h', '1H', '15m') to milliseconds."""
    tf = timeframe.strip().upper()
    if tf.endswith("H"):
        return int(tf[:-1]) * 60 * 60 * 1000
    if tf.endswith("M"):
        return int(tf[:-1]) * 60 * 1000
    if tf.endswith("D"):
        return int(tf[:-1]) * 24 * 60 * 60 * 1000
    if tf.endswith("W"):
        return int(tf[:-1]) * 7 * 24 * 60 * 60 * 1000
    _log.warning("Unknown timeframe format '%s', defaulting to 4h", timeframe)
    return 4 * 60 * 60 * 1000


class ReplayExchange:
    """
    Wraps Exchange for replay mode.

    - fetch_ohlcv(): injects since=t_cursor_ms, caches close prices
    - fetch_tickers(): returns cached close prices (not live tickers)
    - fetch_ticker(): returns cached close price for single symbol
    - fetch_positions(): returns [] (paper mode manages positions)
    - fetch_balance(): returns {} (paper mode manages equity)
    - All other methods delegate to the real Exchange
    """

    def __init__(self, real_exchange: Exchange):
        self._exchange = real_exchange
        self._clock: Optional[VirtualClock] = None
        self._close_price_cache: Dict[str, float] = {}

    def set_clock(self, clock: VirtualClock) -> None:
        """Set the virtual clock for since= calculation."""
        self._clock = clock

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "4h",
        limit: int = 200,
        since: Optional[int] = None,
    ):
        """Fetch OHLCV with since= injection from virtual clock."""
        # Calculate since from virtual clock if not provided
        if since is None and self._clock is not None and self._clock.active:
            tf_ms = _timeframe_to_ms(timeframe)
            since = self._clock.now_ms() - (limit * tf_ms)

        result = self._exchange.fetch_ohlcv(symbol, timeframe, limit=limit, since=since)
        if result is not None and not result.empty:
            self._close_price_cache[symbol] = float(result.iloc[-1]["close"])
        return result

    def fetch_tickers(self, symbols: list) -> dict:
        """Return cached close prices instead of live tickers."""
        results = {}
        for symbol in symbols:
            if symbol in self._close_price_cache:
                price = self._close_price_cache[symbol]
                results[symbol] = {
                    "symbol": symbol,
                    "last": price,
                    "bid": price,
                    "ask": price,
                    "timestamp": self._clock.now_ms() if self._clock else 0,
                }
        return results

    def fetch_ticker(self, symbol: str) -> Optional[dict]:
        """Return cached close price for single symbol."""
        if symbol in self._close_price_cache:
            price = self._close_price_cache[symbol]
            return {
                "symbol": symbol,
                "last": price,
                "bid": price,
                "ask": price,
                "timestamp": self._clock.now_ms() if self._clock else 0,
            }
        return None

    def fetch_positions(self) -> list:
        """Return empty list — paper mode manages positions."""
        return []

    def fetch_balance(self) -> dict:
        """Return empty dict — paper mode manages equity."""
        return {}

    def fetch_usdt_perps(self) -> List[Dict]:
        """Delegate to real exchange — universe is cached at replay start."""
        return self._exchange.fetch_usdt_perps()

    # All other methods delegate to the real Exchange
    def __getattr__(self, name: str):
        """Delegate unknown attributes to the real Exchange."""
        return getattr(self._exchange, name)

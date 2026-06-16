"""
tests/test_replay_exchange.py -- Verify ReplayExchange since injection and cached tickers.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.replay_exchange import ReplayExchange, _timeframe_to_ms
from core.virtual_clock import VirtualClock


class TestTimeframeToMs:
    """Test timeframe-to-milliseconds conversion."""

    def test_hours(self):
        assert _timeframe_to_ms("4h") == 4 * 60 * 60 * 1000
        assert _timeframe_to_ms("1H") == 60 * 60 * 1000

    def test_minutes(self):
        assert _timeframe_to_ms("15m") == 15 * 60 * 1000
        assert _timeframe_to_ms("1M") == 60 * 1000

    def test_days(self):
        assert _timeframe_to_ms("1D") == 24 * 60 * 60 * 1000

    def test_unknown_defaults_to_4h(self):
        assert _timeframe_to_ms("xyz") == 4 * 60 * 60 * 1000


class TestReplayExchange:
    """Test ReplayExchange wrapper."""

    def _make_exchange_mock(self, ohlcv_data=None):
        """Create a mock Exchange with configurable OHLCV data."""
        mock = MagicMock()
        if ohlcv_data is not None:
            mock.fetch_ohlcv.return_value = ohlcv_data
        else:
            # Default: empty DataFrame
            import pandas as pd
            mock.fetch_ohlcv.return_value = pd.DataFrame({
                "ts": [1, 2, 3],
                "open": [100, 101, 102],
                "high": [105, 106, 107],
                "low": [95, 96, 97],
                "close": [103, 104, 105],
                "volume": [1000, 1100, 1200],
            })
        mock.fetch_usdt_perps.return_value = [
            {"symbol": "BTCUSDT", "volume_24h_usd": 1e9, "open_interest_usd": 1e8},
        ]
        return mock

    def test_fetch_ohlcv_injects_since(self):
        """fetch_ohlcv() injects since= from virtual clock when active."""
        import pandas as pd

        clock = VirtualClock()
        t = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        clock.activate(t)

        mock_exchange = self._make_exchange_mock()
        replay = ReplayExchange(mock_exchange)
        replay.set_clock(clock)

        replay.fetch_ohlcv("BTCUSDT", timeframe="4h", limit=200)

        # Verify since= was passed to the real exchange
        call_args = mock_exchange.fetch_ohlcv.call_args
        assert call_args is not None
        since_arg = call_args[1].get("since") or call_args[0][2] if len(call_args[0]) > 2 else call_args[1].get("since")
        # since should be t_ms - (200 * 4h_ms)
        expected_since = clock.now_ms() - (200 * _timeframe_to_ms("4h"))
        assert since_arg == expected_since

    def test_fetch_ohlcv_no_clock_no_since(self):
        """fetch_ohlcv() passes since=None when no clock is set."""
        mock_exchange = self._make_exchange_mock()
        replay = ReplayExchange(mock_exchange)

        replay.fetch_ohlcv("BTCUSDT", timeframe="4h", limit=200)

        call_args = mock_exchange.fetch_ohlcv.call_args
        since_arg = call_args[1].get("since")
        assert since_arg is None

    def test_fetch_ohlcv_caches_close_price(self):
        """fetch_ohlcv() caches the last close price."""
        import pandas as pd

        df = pd.DataFrame({
            "ts": [1, 2, 3],
            "open": [100, 101, 102],
            "high": [105, 106, 107],
            "low": [95, 96, 97],
            "close": [103, 104, 105.5],
            "volume": [1000, 1100, 1200],
        })

        mock_exchange = self._make_exchange_mock(ohlcv_data=df)
        replay = ReplayExchange(mock_exchange)

        replay.fetch_ohlcv("BTCUSDT", timeframe="4h", limit=200)
        assert replay._close_price_cache.get("BTCUSDT") == 105.5

    def test_fetch_tickers_returns_cached(self):
        """fetch_tickers() returns cached close prices."""
        mock_exchange = self._make_exchange_mock()
        replay = ReplayExchange(mock_exchange)
        replay._close_price_cache["BTCUSDT"] = 50000.0

        tickers = replay.fetch_tickers(["BTCUSDT", "ETHUSDT"])
        assert "BTCUSDT" in tickers
        assert tickers["BTCUSDT"]["last"] == 50000.0
        assert "ETHUSDT" not in tickers  # Not cached

    def test_fetch_ticker_returns_cached(self):
        """fetch_ticker() returns cached close price for single symbol."""
        mock_exchange = self._make_exchange_mock()
        replay = ReplayExchange(mock_exchange)
        replay._close_price_cache["BTCUSDT"] = 50000.0

        ticker = replay.fetch_ticker("BTCUSDT")
        assert ticker is not None
        assert ticker["last"] == 50000.0

        # Symbol not in cache
        assert replay.fetch_ticker("ETHUSDT") is None

    def test_fetch_positions_returns_empty(self):
        """fetch_positions() returns empty list."""
        mock_exchange = self._make_exchange_mock()
        replay = ReplayExchange(mock_exchange)
        assert replay.fetch_positions() == []

    def test_fetch_balance_returns_empty(self):
        """fetch_balance() returns empty dict."""
        mock_exchange = self._make_exchange_mock()
        replay = ReplayExchange(mock_exchange)
        assert replay.fetch_balance() == {}

    def test_fetch_usdt_perps_delegates(self):
        """fetch_usdt_perps() delegates to real exchange."""
        mock_exchange = self._make_exchange_mock()
        replay = ReplayExchange(mock_exchange)
        result = replay.fetch_usdt_perps()
        mock_exchange.fetch_usdt_perps.assert_called_once()
        assert len(result) == 1

    def test_explicit_since_overrides_clock(self):
        """Explicit since= parameter overrides clock calculation."""
        clock = VirtualClock()
        t = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        clock.activate(t)

        mock_exchange = self._make_exchange_mock()
        replay = ReplayExchange(mock_exchange)
        replay.set_clock(clock)

        explicit_since = 1700000000000
        replay.fetch_ohlcv("BTCUSDT", timeframe="4h", limit=200, since=explicit_since)

        call_args = mock_exchange.fetch_ohlcv.call_args
        since_arg = call_args[1].get("since")
        assert since_arg == explicit_since

    def test_getattr_delegates(self):
        """Unknown attributes delegate to real exchange."""
        mock_exchange = self._make_exchange_mock()
        mock_exchange.custom_method.return_value = "hello"
        replay = ReplayExchange(mock_exchange)
        assert replay.custom_method() == "hello"

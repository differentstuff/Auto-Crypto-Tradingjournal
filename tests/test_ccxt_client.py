"""Tests for ccxt_client.py factory functions."""
import time
from unittest.mock import MagicMock, patch

import pytest


def test_get_blofin_exchange_returns_ccxt_instance():
    """get_blofin_exchange() must return an object with fetch_balance method."""
    mock_exchange = MagicMock()
    mock_exchange.fetch_balance = MagicMock(return_value={})

    with patch("ccxt.blofin", return_value=mock_exchange):
        import importlib
        import ccxt_client
        importlib.reload(ccxt_client)
        result = ccxt_client.get_blofin_exchange()
        assert hasattr(result, "fetch_balance")


def test_get_binance_exchange_no_auth():
    """get_binance_exchange() must not set apiKey (public-only)."""
    mock_exchange = MagicMock()
    with patch("ccxt.binance", return_value=mock_exchange) as mock_cls:
        import importlib
        import ccxt_client
        importlib.reload(ccxt_client)
        ccxt_client.get_binance_exchange()
        call_kwargs = mock_cls.call_args[0][0]
        assert "apiKey" not in call_kwargs
        assert call_kwargs.get("enableRateLimit") is True


def test_get_binance_price_cache_hit():
    """get_binance_price() returns cached value within TTL without calling exchange."""
    import importlib
    import ccxt_client
    importlib.reload(ccxt_client)

    ccxt_client._binance_price_cache["BTCUSDT"] = (50000.0, time.time())
    with patch("ccxt_client.get_binance_exchange") as mock_ex:
        result = ccxt_client.get_binance_price("BTCUSDT")
    assert result == 50000.0
    mock_ex.assert_not_called()


def test_get_binance_price_returns_none_on_error():
    """get_binance_price() returns None when exchange raises."""
    import importlib
    import ccxt_client
    importlib.reload(ccxt_client)
    ccxt_client._binance_price_cache.clear()

    mock_exchange = MagicMock()
    mock_exchange.fetch_ticker.side_effect = Exception("network error")
    with patch("ccxt_client.get_binance_exchange", return_value=mock_exchange):
        result = ccxt_client.get_binance_price("BTCUSDT")
    assert result is None


def test_get_binance_futures_symbols_filters_usdt_pairs():
    """get_binance_futures_symbols() returns symbols ending in USDT, filtered by volume."""
    import importlib
    import ccxt_client
    importlib.reload(ccxt_client)

    mock_tickers = {
        "BTC/USDT:USDT": {"quoteVolume": 1_000_000_000},
        "ETH/USDT:USDT": {"quoteVolume": 500_000_000},
        "TINY/USDT:USDT": {"quoteVolume": 1_000},  # below threshold
        "BTC/USD:BTC": {"quoteVolume": 900_000_000},  # inverse, wrong format
    }
    mock_exchange = MagicMock()
    mock_exchange.fetch_tickers.return_value = mock_tickers

    with patch("ccxt_client.get_binance_exchange", return_value=mock_exchange):
        result = ccxt_client.get_binance_futures_symbols(min_vol_usd=50_000_000)

    assert "BTCUSDT" in result
    assert "ETHUSDT" in result
    assert "TINYUSDT" not in result
    assert "BTCUSD" not in result

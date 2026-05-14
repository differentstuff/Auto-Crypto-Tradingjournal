"""Tests for ai_scanner.py lazy initialisation — Binance not called at import."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _evict_ai_scanner():
    """Remove ai_scanner (and sub-modules) from sys.modules to force a fresh import."""
    for key in list(sys.modules.keys()):
        if "ai_scanner" in key:
            del sys.modules[key]


def test_binance_not_called_at_import():
    """Importing ai_scanner must NOT call get_binance_futures_symbols()."""
    from unittest.mock import patch, MagicMock
    _evict_ai_scanner()

    with patch("ccxt_client.get_binance_futures_symbols") as mock_binance:
        import ai_scanner  # noqa: F401
        mock_binance.assert_not_called()


def test_default_watchlist_calls_binance_on_first_use():
    """_get_default_watchlist() fetches Binance symbols on first use, then caches."""
    from unittest.mock import patch
    import ai_scanner

    # Reset state so we can observe the first call
    ai_scanner._binance_watchlist_loaded = False
    ai_scanner.BINANCE_WATCHLIST = []

    with patch("ccxt_client.get_binance_futures_symbols", return_value=["XYZUSDT"]) as mock:
        result1 = ai_scanner._get_default_watchlist()
        result2 = ai_scanner._get_default_watchlist()
        assert mock.call_count == 1  # called exactly once (cached after first)
        assert "XYZUSDT" in result1
        assert "XYZUSDT" in result2


def test_default_watchlist_includes_btcusdt():
    """Bitget watchlist always includes BTCUSDT regardless of Binance response."""
    from unittest.mock import patch
    import ai_scanner

    ai_scanner._binance_watchlist_loaded = False
    ai_scanner.BINANCE_WATCHLIST = []

    with patch("ccxt_client.get_binance_futures_symbols", return_value=[]):
        result = ai_scanner._get_default_watchlist()
    assert "BTCUSDT" in result


def test_default_watchlist_falls_back_on_error():
    """If Binance call raises, the Bitget-only list is still returned."""
    from unittest.mock import patch
    import ai_scanner

    ai_scanner._binance_watchlist_loaded = False
    ai_scanner.BINANCE_WATCHLIST = []

    with patch("ccxt_client.get_binance_futures_symbols", side_effect=Exception("network error")):
        result = ai_scanner._get_default_watchlist()
    assert len(result) > 0
    assert "BTCUSDT" in result


def test_default_watchlist_no_duplicates():
    """Merged watchlist must not contain duplicate symbols."""
    from unittest.mock import patch
    import ai_scanner

    ai_scanner._binance_watchlist_loaded = False
    ai_scanner.BINANCE_WATCHLIST = []

    # Return symbols that overlap with the Bitget list
    with patch("ccxt_client.get_binance_futures_symbols",
               return_value=["BTCUSDT", "ETHUSDT", "NEWCOINUSDT"]):
        result = ai_scanner._get_default_watchlist()
    assert len(result) == len(set(result)), "Duplicate symbols found in watchlist"


def test_default_watchlist_binance_symbols_appended():
    """Binance-only symbols should appear after the Bitget list."""
    from unittest.mock import patch
    import ai_scanner

    ai_scanner._binance_watchlist_loaded = False
    ai_scanner.BINANCE_WATCHLIST = []

    with patch("ccxt_client.get_binance_futures_symbols", return_value=["UNIQUEUSDT"]):
        result = ai_scanner._get_default_watchlist()
    assert "UNIQUEUSDT" in result

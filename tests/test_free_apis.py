"""Tests for free no-key API integrations (degrade gracefully on network failure)."""
import pytest
from unittest.mock import patch, MagicMock


def test_get_macro_regime_returns_dict():
    """get_macro_regime returns dict with required keys even if yfinance fails."""
    mock_yf = MagicMock()
    mock_yf.Ticker.return_value.history.return_value = MagicMock(empty=True)
    import sys
    with patch.dict(sys.modules, {"yfinance": mock_yf}):
        from market_context import get_macro_regime
        result = get_macro_regime()
    assert isinstance(result, dict)
    assert "vix" in result
    assert "regime" in result


def test_get_macro_regime_degrades():
    """get_macro_regime returns unknown regime on exception."""
    with patch("market_context.get_macro_regime", side_effect=Exception("network")):
        from data_sources import fetch_macro_regime
        result = fetch_macro_regime()
    assert result.get("regime") == "unknown"


def test_get_defi_tvl_unknown_token():
    """get_defi_tvl returns {} for non-DeFi tokens like BTCUSDT."""
    from market_context import get_defi_tvl
    result = get_defi_tvl("BTCUSDT")
    assert result == {}


def test_get_defi_tvl_degrades_on_network_error():
    """get_defi_tvl returns {} when DefiLlama is unreachable."""
    with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
        from market_context import get_defi_tvl
        result = get_defi_tvl("AAVEUSDT")
    assert result == {}


def test_get_btc_mempool_degrades():
    """get_btc_mempool returns unknown congestion on network error."""
    with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
        from market_context import get_btc_mempool
        result = get_btc_mempool()
    assert result.get("congestion") == "unknown"


def test_multi_exchange_ls_structure():
    """get_multi_exchange_ls_ratio returns dict with expected keys."""
    # Just test structure with mocked CCXT to avoid live calls
    with patch("ccxt.binance") as mb, patch("ccxt.bybit") as mby, patch("ccxt.okx") as mo:
        for m in (mb, mo, mby):
            m.return_value = MagicMock()
            m.return_value.fetch_long_short_ratio_history.return_value = [{"longShortRatio": 1.2}]
        from ccxt_client import get_multi_exchange_ls_ratio
        result = get_multi_exchange_ls_ratio("BTCUSDT")
    assert "consensus" in result
    assert "binance" in result


def test_fetch_defi_tvl_adapter():
    """fetch_defi_tvl adapter degrades to {} on any error."""
    with patch("market_context.get_defi_tvl", side_effect=Exception("error")):
        from data_sources import fetch_defi_tvl
        result = fetch_defi_tvl("AAVEUSDT")
    assert result == {}

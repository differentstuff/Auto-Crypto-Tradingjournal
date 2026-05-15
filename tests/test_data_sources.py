"""Tests for data_sources adapter — verifies each function is callable and degrades gracefully."""
import pytest
from unittest.mock import patch, MagicMock


def test_fetch_candles_returns_value_on_success():
    """fetch_candles returns whatever get_candles returns — never suppresses errors."""
    mock_df = MagicMock()
    with patch("chart_context.get_candles", return_value=mock_df):
        from data_sources import fetch_candles
        result = fetch_candles("BTCUSDT", "4H")
    assert result is mock_df


def test_fetch_funding_rate_degrades_gracefully():
    """fetch_funding_rate returns {} on any exception."""
    with patch("market_context.get_funding_rate", side_effect=Exception("network error")):
        from data_sources import fetch_funding_rate
        result = fetch_funding_rate("BTCUSDT")
    assert result == {}


def test_fetch_open_interest_degrades():
    """fetch_open_interest returns {} on failure."""
    with patch("market_context.get_open_interest", side_effect=Exception("timeout")):
        from data_sources import fetch_open_interest
        result = fetch_open_interest("BTCUSDT")
    assert result == {}


def test_fetch_long_short_ratio_degrades():
    """fetch_long_short_ratio returns {} on failure."""
    with patch("market_context.get_long_short_ratio", side_effect=Exception("API error")):
        from data_sources import fetch_long_short_ratio
        result = fetch_long_short_ratio("BTCUSDT")
    assert result == {}


def test_fetch_fear_greed_degrades():
    """fetch_fear_greed returns {} on failure."""
    with patch("market_context.get_fear_greed", side_effect=Exception("timeout")):
        from data_sources import fetch_fear_greed
        result = fetch_fear_greed()
    assert result == {}


def test_fetch_fred_macro_degrades():
    """fetch_fred_macro returns {} on failure."""
    with patch("market_context.get_fred_macro", side_effect=Exception("FRED error")):
        from data_sources import fetch_fred_macro
        result = fetch_fred_macro()
    assert result == {}


def test_fetch_smart_money_degrades():
    """fetch_smart_money returns {} on failure."""
    with patch("nansen_client.get_smart_money_signal", side_effect=Exception("API error")):
        from data_sources import fetch_smart_money
        result = fetch_smart_money("BTCUSDT")
    assert result == {}


def test_fetch_news_degrades():
    """fetch_news returns {} on failure."""
    with patch("grok_client.get_coin_context", side_effect=Exception("rate limited")):
        from data_sources import fetch_news
        result = fetch_news("BTCUSDT", "long")
    assert result == {}


def test_fetch_news_returns_empty_when_no_text():
    """fetch_news returns {} when grok returns empty text."""
    with patch("grok_client.get_coin_context", return_value=("", 0.0)):
        from data_sources import fetch_news
        result = fetch_news("BTCUSDT", "long")
    assert result == {}


def test_fetch_news_returns_dict_with_text_and_weight():
    """fetch_news returns {text, weight} when grok returns content."""
    with patch("grok_client.get_coin_context", return_value=("Some news text.", 0.4)):
        from data_sources import fetch_news
        result = fetch_news("BTCUSDT", "long")
    assert result == {"text": "Some news text.", "weight": 0.4}


def test_collector_imports_from_data_sources():
    """agent_data_collector must not import clients directly."""
    import ast, pathlib
    src = pathlib.Path(__file__).parent.parent.joinpath("agent_data_collector.py").read_text()
    tree = ast.parse(src)
    direct_imports = {"chart_context", "market_context", "nansen_client", "grok_client"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in direct_imports, \
                    f"agent_data_collector should import {alias.name} via data_sources, not directly"
        elif isinstance(node, ast.ImportFrom):
            if node.module in direct_imports:
                raise AssertionError(
                    f"agent_data_collector should import from data_sources, not from {node.module}")

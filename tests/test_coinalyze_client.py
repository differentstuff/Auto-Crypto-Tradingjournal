"""Tests for Coinalyze API client."""
import pytest
from unittest.mock import patch, MagicMock


def test_symbol_converter():
    """Symbol converter maps BTCUSDT → BTCUSDT_PERP.A"""
    from coinalyze_client import _symbol
    assert _symbol("BTCUSDT") == "BTCUSDT_PERP.A"
    assert _symbol("ETHUSDT") == "ETHUSDT_PERP.A"
    assert _symbol("SOLUSDT") == "SOLUSDT_PERP.A"


def test_symbol_converter_no_double_usdt():
    """Symbol converter does not add USDT if already present."""
    from coinalyze_client import _symbol
    assert _symbol("BTCUSDT") == "BTCUSDT_PERP.A"
    # Already-formatted symbol should pass through
    assert _symbol("BTCUSDT_PERP.A") == "BTCUSDT_PERP.A"


def test_symbol_converter_base_only():
    """Symbol converter handles base-only symbols by appending USDT."""
    from coinalyze_client import _symbol
    result = _symbol("BTC")
    assert result == "BTCUSDT_PERP.A"


def test_get_all_structure():
    """get_all returns dict with 4 expected keys."""
    with patch("coinalyze_client._get", return_value=None):
        from coinalyze_client import get_all
        result = get_all("BTCUSDT")
    assert "oi" in result
    assert "liquidations" in result
    assert "funding" in result
    assert "long_short" in result


def test_get_open_interest_degrades():
    """get_open_interest returns {} on API failure."""
    with patch("coinalyze_client._get", return_value=None):
        from coinalyze_client import get_open_interest
        assert get_open_interest("BTCUSDT") == {}


def test_get_open_interest_parses_value_field():
    """get_open_interest reads 'value' field (confirmed API shape)."""
    mock_response = [{"symbol": "BTCUSDT_PERP.A", "value": 98765.432, "update": 1778869122575}]
    with patch("coinalyze_client._get", return_value=mock_response):
        from coinalyze_client import get_open_interest
        result = get_open_interest("BTCUSDT")
    assert result["oi_coins"] == 98765.432
    assert result["oi_symbol"] == "BTCUSDT_PERP.A"


def test_get_funding_rate_parses_value_field():
    """get_funding_rate reads 'value' field (confirmed API shape)."""
    # Use 0.0002 (> 0.0001 threshold for longs_paying)
    mock_response = [{"symbol": "BTCUSDT_PERP.A", "value": 0.0002, "update": 1778869122575}]
    with patch("coinalyze_client._get", return_value=mock_response):
        from coinalyze_client import get_funding_rate
        result = get_funding_rate("BTCUSDT")
    assert result["rate"] == 0.0002
    assert result["sentiment"] == "longs_paying"
    # annualized: 0.0002 * 3 * 365 * 100 = 21.9
    assert abs(result["annualized_pct"] - 21.9) < 0.01


def test_get_funding_rate_sentiment_labels():
    """Sentiment labels are applied correctly for various rate values."""
    from coinalyze_client import get_funding_rate

    cases = [
        (0.001,   "longs_paying_heavily"),
        (0.0003,  "longs_paying"),
        (0.00005, "neutral"),
        (-0.0003, "shorts_paying"),
    ]
    for rate, expected_sentiment in cases:
        mock = [{"value": rate}]
        with patch("coinalyze_client._get", return_value=mock):
            result = get_funding_rate("BTCUSDT")
        assert result["sentiment"] == expected_sentiment, f"rate={rate}"


def test_get_long_short_ratio_parses_value_field():
    """get_long_short_ratio reads 'value' field and computes percentages."""
    mock_response = [{"symbol": "BTCUSDT_PERP.A", "value": 1.5, "update": 1778869122575}]
    with patch("coinalyze_client._get", return_value=mock_response):
        from coinalyze_client import get_long_short_ratio
        result = get_long_short_ratio("BTCUSDT")
    assert result["ratio"] == 1.5
    # longs_pct = 1.5 / 2.5 * 100 = 60.0
    assert result["longs_pct"] == 60.0
    assert result["shorts_pct"] == 40.0


def test_get_long_short_ratio_degrades_on_zero():
    """get_long_short_ratio returns {} when ratio is 0 (invalid)."""
    mock_response = [{"symbol": "BTCUSDT_PERP.A", "value": 0, "update": 1778869122575}]
    with patch("coinalyze_client._get", return_value=mock_response):
        from coinalyze_client import get_long_short_ratio
        assert get_long_short_ratio("BTCUSDT") == {}


def test_get_liquidations_parses_l_s_fields():
    """get_liquidations reads 'l' (long liq) and 's' (short liq) fields."""
    mock_response = [
        {"symbol": "BTCUSDT_PERP.A", "t": 1778869122575, "l": 1500000.0, "s": 800000.0}
    ]
    with patch("coinalyze_client._get", return_value=mock_response):
        from coinalyze_client import get_liquidations
        result = get_liquidations("BTCUSDT")
    assert result["liq_long_usd"] == 1500000.0
    assert result["liq_short_usd"] == 800000.0
    assert result["liq_total_usd"] == 2300000.0


def test_get_liquidations_degrades():
    """get_liquidations returns {} on API failure."""
    with patch("coinalyze_client._get", return_value=None):
        from coinalyze_client import get_liquidations
        assert get_liquidations("BTCUSDT") == {}


def test_get_all_aggregates_all_sources():
    """get_all combines results from all 4 sub-fetches."""
    oi_data     = [{"value": 50000.0, "symbol": "BTCUSDT_PERP.A", "update": 0}]
    funding_data = [{"value": 0.0002, "symbol": "BTCUSDT_PERP.A", "update": 0}]
    ls_data      = [{"value": 1.2, "symbol": "BTCUSDT_PERP.A", "update": 0}]
    liq_data     = [{"t": 0, "l": 500000.0, "s": 300000.0}]

    call_count = [0]
    def mock_get(path, params):
        call_count[0] += 1
        if "open-interest" in path:
            return oi_data
        if "funding-rate" in path:
            return funding_data
        if "long-short-ratio" in path:
            return ls_data
        if "liquidation-history" in path:
            return liq_data
        return None

    with patch("coinalyze_client._get", side_effect=mock_get):
        from coinalyze_client import get_all
        result = get_all("BTCUSDT")

    assert result["oi"]["oi_coins"] == 50000.0
    assert result["funding"]["sentiment"] == "longs_paying"
    assert result["long_short"]["longs_pct"] == pytest.approx(54.5, abs=0.5)
    assert result["liquidations"]["liq_total_usd"] == 800000.0


def test_get_all_partial_failure():
    """get_all returns {} for failed sub-fetches but succeeds for others."""
    def mock_get(path, params):
        if "open-interest" in path:
            return [{"value": 50000.0, "symbol": "BTCUSDT_PERP.A", "update": 0}]
        return None  # All other endpoints fail

    with patch("coinalyze_client._get", side_effect=mock_get):
        from coinalyze_client import get_all
        result = get_all("BTCUSDT")

    assert result["oi"]["oi_coins"] == 50000.0
    assert result["funding"] == {}
    assert result["long_short"] == {}
    assert result["liquidations"] == {}


def test_api_key_loaded():
    """COINALYZE_API_KEY must be set in environment when running on Pi."""
    import os
    key = os.environ.get("COINALYZE_API_KEY", "")
    if not key:
        pytest.skip("COINALYZE_API_KEY not in local environment")
    assert len(key) > 10


def test_fetch_coinalyze_adapter():
    """fetch_coinalyze adapter in data_sources returns dict."""
    mock_result = {
        "oi": {"oi_coins": 50000.0, "oi_symbol": "BTCUSDT_PERP.A"},
        "liquidations": {},
        "funding": {},
        "long_short": {},
    }
    with patch("coinalyze_client.get_all", return_value=mock_result):
        with patch("coinalyze_client._API_KEY", "test-key-12345"):
            from data_sources import fetch_coinalyze
            result = fetch_coinalyze("BTCUSDT")
    assert isinstance(result, dict)
    assert "oi" in result


def test_fetch_coinalyze_adapter_no_key():
    """fetch_coinalyze returns {} when API key is not set."""
    with patch("coinalyze_client._API_KEY", ""):
        from data_sources import fetch_coinalyze
        result = fetch_coinalyze("BTCUSDT")
    assert result == {}


def test_get_all_has_new_keys():
    """get_all() must return funding_by_exchange and liquidation_trend keys."""
    with patch("coinalyze_client._get", return_value=None):
        from coinalyze_client import get_all
        result = get_all("BTCUSDT")
    assert "funding_by_exchange" in result
    assert "liquidation_trend" in result


def test_get_funding_by_exchange_degrades():
    """get_funding_by_exchange returns {} on API failure."""
    with patch("coinalyze_client._get", return_value=None):
        from coinalyze_client import get_funding_by_exchange
        assert get_funding_by_exchange("BTCUSDT") == {}


def test_get_funding_by_exchange_parses_per_exchange():
    """get_funding_by_exchange reads per-exchange rates and computes spread."""
    mock_response = [
        {"symbol": "BTCUSDT_PERP.BINANCE", "value": 0.0001, "update": 0},
        {"symbol": "BTCUSDT_PERP.BYBIT",   "value": 0.00007, "update": 0},
        {"symbol": "BTCUSDT_PERP.OKX",     "value": 0.00013, "update": 0},
    ]
    with patch("coinalyze_client._get", return_value=mock_response):
        from coinalyze_client import get_funding_by_exchange
        result = get_funding_by_exchange("BTCUSDT")
    assert result["binance"] == 0.0001
    assert result["bybit"] == 0.00007
    assert result["okx"] == 0.00013
    # spread = (0.00013 - 0.00007) * 100 = 0.006
    assert abs(result["spread_pct"] - 0.006) < 0.0001


def test_get_liquidation_trend_degrades():
    """get_liquidation_trend returns {} on API failure."""
    with patch("coinalyze_client._get", return_value=None):
        from coinalyze_client import get_liquidation_trend
        assert get_liquidation_trend("BTCUSDT") == {}


def test_get_liquidation_trend_too_few_records():
    """get_liquidation_trend returns {} when fewer than 6 hourly records returned."""
    mock_response = [{"t": 0, "l": 100000.0, "s": 50000.0}]
    with patch("coinalyze_client._get", return_value=mock_response):
        from coinalyze_client import get_liquidation_trend
        assert get_liquidation_trend("BTCUSDT") == {}


def test_get_liquidation_trend_accelerating():
    """get_liquidation_trend detects accelerating when recent 6h >> older 18h."""
    # 18 older hours with low liq, 6 recent hours with high liq → accelerating
    old_records = [{"t": i, "l": 100.0, "s": 100.0} for i in range(18)]
    recent_records = [{"t": 18 + i, "l": 1_000_000.0, "s": 1_000_000.0} for i in range(6)]
    mock_response = old_records + recent_records
    with patch("coinalyze_client._get", return_value=mock_response):
        from coinalyze_client import get_liquidation_trend
        result = get_liquidation_trend("BTCUSDT")
    assert result["trend"] == "accelerating"
    assert result["total_24h_usd"] > 0
    assert result["recent_6h_usd"] > 0


def test_get_liquidation_trend_dominant_shorts():
    """get_liquidation_trend reports dominant_side=shorts when short liqs dominate."""
    records = [{"t": i, "l": 100.0, "s": 500_000.0} for i in range(24)]
    with patch("coinalyze_client._get", return_value=records):
        from coinalyze_client import get_liquidation_trend
        result = get_liquidation_trend("BTCUSDT")
    assert result["dominant_side"] == "shorts"

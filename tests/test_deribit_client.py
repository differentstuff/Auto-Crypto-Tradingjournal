"""Tests for Deribit options skew client."""
import pytest
from unittest.mock import patch


def test_deribit_unsupported_symbol():
    """get_options_skew returns {} for non-BTC/ETH symbols."""
    from deribit_client import get_options_skew
    assert get_options_skew("SOLUSDT") == {}
    assert get_options_skew("DOGEUSDT") == {}


def test_deribit_degrades_on_failure():
    """get_options_skew returns {} on API failure."""
    with patch("deribit_client._get", return_value=None):
        from deribit_client import get_options_skew
        assert get_options_skew("BTCUSDT") == {}


def test_deribit_bearish_signal():
    """PCR > 1.2 produces bearish_hedge sentiment."""
    fake_result = [
        {"instrument_name": f"BTC-27JUN25-{p}-P", "mark_iv": 70.0, "volume": 100}
        for p in [90000, 80000, 70000, 60000]
    ] + [
        {"instrument_name": f"BTC-27JUN25-{p}-C", "mark_iv": 60.0, "volume": 60}
        for p in [110000, 120000]
    ]
    with patch("deribit_client._get", return_value=fake_result):
        from deribit_client import get_options_skew
        result = get_options_skew("BTCUSDT")
    assert result.get("sentiment") == "bearish_hedge"
    assert result.get("put_call_ratio") > 1.2


def test_deribit_bullish_signal():
    """PCR < 0.8 produces bullish_positioning sentiment."""
    fake_result = [
        {"instrument_name": f"BTC-27JUN25-{p}-P", "mark_iv": 55.0, "volume": 30}
        for p in [90000, 80000]
    ] + [
        {"instrument_name": f"BTC-27JUN25-{p}-C", "mark_iv": 65.0, "volume": 100}
        for p in [110000, 120000, 130000, 140000]
    ]
    with patch("deribit_client._get", return_value=fake_result):
        from deribit_client import get_options_skew
        result = get_options_skew("BTCUSDT")
    assert result.get("sentiment") == "bullish_positioning"
    assert result.get("put_call_ratio") < 0.8


def test_deribit_neutral_signal():
    """Balanced put/call produces neutral sentiment."""
    fake_result = [
        {"instrument_name": f"BTC-27JUN25-{p}-P", "mark_iv": 60.0, "volume": 50}
        for p in [90000, 80000]
    ] + [
        {"instrument_name": f"BTC-27JUN25-{p}-C", "mark_iv": 60.0, "volume": 50}
        for p in [110000, 120000]
    ]
    with patch("deribit_client._get", return_value=fake_result):
        from deribit_client import get_options_skew
        result = get_options_skew("BTCUSDT")
    assert result.get("sentiment") == "neutral"
    assert result.get("put_call_ratio") == pytest.approx(1.0, abs=0.01)


def test_deribit_eth_supported():
    """ETH is a supported symbol."""
    fake_result = [
        {"instrument_name": "ETH-27JUN25-2000-P", "mark_iv": 68.0, "volume": 200},
        {"instrument_name": "ETH-27JUN25-3000-C", "mark_iv": 60.0, "volume": 100},
    ]
    with patch("deribit_client._get", return_value=fake_result):
        from deribit_client import get_options_skew
        result = get_options_skew("ETHUSDT")
    assert result.get("currency") == "ETH"
    assert "put_call_ratio" in result
    assert "iv_skew" in result
    assert "sentiment" in result
    assert "near_term_iv" in result


def test_deribit_empty_result():
    """Empty list from API returns {}."""
    with patch("deribit_client._get", return_value=[]):
        from deribit_client import get_options_skew
        assert get_options_skew("BTCUSDT") == {}


def test_collector_result_has_options_skew():
    """CollectorResult must include options_skew field."""
    from agent_types import CollectorResult
    assert "options_skew" in CollectorResult.__annotations__

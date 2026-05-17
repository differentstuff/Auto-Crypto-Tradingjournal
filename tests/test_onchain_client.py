# tests/test_onchain_client.py
import pytest
from unittest.mock import patch, MagicMock


_FAKE = {
    "data": [{
        "time": "2026-05-17",
        "mvrv_cur": "2.3",
        "sopr": "1.02",
        "FlowInExUSD": "120000000",
        "FlowOutExUSD": "150000000",
    }]
}


def test_get_btc_onchain_ok():
    with patch("onchain_client.requests.get") as mock_get:
        mock_get.return_value.ok = True
        mock_get.return_value.json.return_value = _FAKE
        import onchain_client
        result = onchain_client._fetch()
    assert result["ok"] is True
    assert result["mvrv"] == pytest.approx(2.3)
    assert result["sopr"] == pytest.approx(1.02)


def test_get_btc_onchain_regime_overvalued():
    with patch("onchain_client.requests.get") as mock_get:
        mock_get.return_value.ok = True
        mock_get.return_value.json.return_value = {"data": [{
            "time": "2026-05-17", "mvrv_cur": "4.1", "sopr": "1.06",
            "FlowInExUSD": "0", "FlowOutExUSD": "0",
        }]}
        import onchain_client
        result = onchain_client._fetch()
    assert result["regime"] == "overvalued"


def test_get_btc_onchain_regime_undervalued():
    with patch("onchain_client.requests.get") as mock_get:
        mock_get.return_value.ok = True
        mock_get.return_value.json.return_value = {"data": [{
            "time": "2026-05-17", "mvrv_cur": "0.8", "sopr": "0.96",
            "FlowInExUSD": "0", "FlowOutExUSD": "0",
        }]}
        import onchain_client
        result = onchain_client._fetch()
    assert result["regime"] == "undervalued"


def test_get_btc_onchain_error_not_ok():
    with patch("onchain_client.requests.get", side_effect=Exception("timeout")):
        import onchain_client
        result = onchain_client._fetch()
    assert result["ok"] is False

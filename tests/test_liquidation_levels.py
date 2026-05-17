# tests/test_liquidation_levels.py
import pytest
from unittest.mock import patch


def _fake_liquidations(symbol, limit=500):
    return [
        {"price": 100.0, "side": "sell", "amount": 50000},
        {"price": 100.1, "side": "sell", "amount": 20000},
        {"price": 98.0,  "side": "buy",  "amount": 80000},
        {"price": 97.9,  "side": "buy",  "amount": 30000},
    ]


def test_clusters_ok():
    with patch("liquidation_levels.ccxt.binanceusdm") as mock_ex:
        mock_ex.return_value.fetch_liquidations = _fake_liquidations
        import liquidation_levels
        result = liquidation_levels._fetch("BTCUSDT")
    assert result["ok"] is True
    assert result["short_wall"] is not None
    assert result["long_wall"] is not None


def test_clusters_empty_returns_not_ok():
    with patch("liquidation_levels.ccxt.binanceusdm") as mock_ex:
        mock_ex.return_value.fetch_liquidations = lambda *a, **k: []
        import liquidation_levels
        result = liquidation_levels._fetch("BTCUSDT")
    assert result["ok"] is False


def test_clusters_exception_returns_not_ok():
    with patch("liquidation_levels.ccxt.binanceusdm") as mock_ex:
        mock_ex.return_value.fetch_liquidations = lambda *a, **k: 1/0
        import liquidation_levels
        result = liquidation_levels._fetch("BTCUSDT")
    assert result["ok"] is False

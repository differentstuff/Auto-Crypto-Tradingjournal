"""Tests for blofin_client.py — verifies output shapes via mocked CCXT exchange."""
from unittest.mock import MagicMock, patch


def _make_mock_exchange(balance=None, positions=None, orders=None):
    ex = MagicMock()
    ex.fetch_balance.return_value = balance or {
        "USDT": {"total": 1000.0, "free": 800.0},
    }
    ex.fetch_positions.return_value = positions or [
        {
            "symbol": "BTC/USDT:USDT",
            "side": "long",
            "contracts": 0.01,
            "entryPrice": 60000.0,
            "unrealizedPnl": 50.0,
            "leverage": 10,
            "notional": 600.0,
        }
    ]
    ex.fetch_closed_orders.return_value = orders or []
    return ex


def test_is_configured_false_without_env(monkeypatch):
    monkeypatch.delenv("BLOFIN_API_KEY", raising=False)
    monkeypatch.delenv("BLOFIN_SECRET_KEY", raising=False)
    import importlib
    import blofin_client
    importlib.reload(blofin_client)
    assert blofin_client.is_configured() is False


def test_get_account_equity_returns_equity_and_available():
    mock_ex = _make_mock_exchange()
    with patch("blofin_client.get_blofin_exchange", return_value=mock_ex):
        import blofin_client
        result = blofin_client.get_account_equity()
    assert "equity" in result
    assert "available" in result
    assert result["equity"] == 1000.0
    assert result["available"] == 800.0


def test_get_account_equity_returns_zeros_on_error():
    mock_ex = MagicMock()
    mock_ex.fetch_balance.side_effect = Exception("auth error")
    with patch("blofin_client.get_blofin_exchange", return_value=mock_ex):
        import blofin_client
        result = blofin_client.get_account_equity()
    assert result == {"equity": 0.0, "available": 0.0}


def test_get_open_positions_returns_list_with_correct_shape():
    mock_ex = _make_mock_exchange()
    with patch("blofin_client.get_blofin_exchange", return_value=mock_ex):
        import blofin_client
        result = blofin_client.get_open_positions()
    assert isinstance(result, list)
    assert len(result) == 1
    pos = result[0]
    # Bitget-compatible shape — must expose direction, not raw side
    assert "symbol" in pos
    assert "direction" in pos
    assert pos["symbol"] == "BTCUSDT"
    assert pos["direction"] in ("Long", "Short")
    # Required normalised fields for the unified live-positions UI
    for key in ("margin_usdt", "size_usdt", "entry_price", "mark_price",
                "unrealized_pnl", "exchange"):
        assert key in pos, f"missing key {key}"
    assert pos["exchange"] == "blofin"


def test_get_open_positions_returns_empty_on_error():
    mock_ex = MagicMock()
    mock_ex.fetch_positions.side_effect = Exception("network")
    with patch("blofin_client.get_blofin_exchange", return_value=mock_ex):
        import blofin_client
        result = blofin_client.get_open_positions()
    assert result == []


def test_test_connection_returns_ok_true():
    mock_ex = _make_mock_exchange()
    with patch("blofin_client.get_blofin_exchange", return_value=mock_ex):
        import blofin_client
        result = blofin_client.test_connection()
    assert result.get("ok") is True


def test_test_connection_returns_ok_false_on_auth_error():
    import ccxt
    mock_ex = MagicMock()
    mock_ex.fetch_balance.side_effect = ccxt.AuthenticationError("bad key")
    with patch("blofin_client.get_blofin_exchange", return_value=mock_ex):
        import blofin_client
        result = blofin_client.test_connection()
    assert result.get("ok") is False

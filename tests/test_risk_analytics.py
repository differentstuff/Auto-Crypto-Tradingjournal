import sys, os, unittest.mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import numpy as np
import pandas as pd


@pytest.fixture
def mock_ohlcv(monkeypatch):
    def fake_fetch(symbol, tf="4H", limit=500):
        rng = np.random.default_rng(int(hash(symbol) % 10000))
        closes = 100 * np.cumprod(1 + rng.normal(0.001, 0.02, limit))
        idx = pd.date_range(end=pd.Timestamp.now(), periods=limit, freq="4h")
        return pd.DataFrame({"close": closes, "volume": np.ones(limit) * 1e6}, index=idx)
    monkeypatch.setattr("risk_analytics._fetch_ohlcv_df", fake_fetch)
    return fake_fetch


def _make_positions(*symbols):
    return [{"symbol": s, "direction": "Long", "size_usdt": 500, "margin_usdt": 50}
            for s in symbols]


def test_var_returns_required_keys(mock_ohlcv):
    from risk_analytics import compute_portfolio_var
    result = compute_portfolio_var(_make_positions("BTCUSDT", "ETHUSDT"), equity=10000.0)
    assert "var_95_usd" in result
    assert "var_99_usd" in result
    assert "var_95_pct" in result
    assert "horizon_days" in result


def test_var_99_gte_var_95(mock_ohlcv):
    from risk_analytics import compute_portfolio_var
    result = compute_portfolio_var(_make_positions("BTCUSDT", "SOLUSDT"), equity=10000.0)
    assert result["var_99_usd"] >= result["var_95_usd"]


def test_var_empty_positions():
    from risk_analytics import compute_portfolio_var
    result = compute_portfolio_var([], equity=10000.0)
    assert result["var_95_usd"] == 0.0
    assert result["available"] is False


def test_correlation_single_position_unavailable(mock_ohlcv):
    from risk_analytics import compute_correlation_matrix
    result = compute_correlation_matrix([{"symbol": "BTCUSDT", "direction": "Long", "size_usdt": 500}])
    assert result["available"] is False


def test_correlation_matrix_returns_pairs(mock_ohlcv):
    from risk_analytics import compute_correlation_matrix
    positions = _make_positions("BTCUSDT", "ETHUSDT", "SOLUSDT")
    result = compute_correlation_matrix(positions, lookback_days=30)
    if result["available"]:
        assert len(result["matrix"]) == 3
        for item in result["matrix"]:
            assert -1.0 <= item["correlation"] <= 1.0

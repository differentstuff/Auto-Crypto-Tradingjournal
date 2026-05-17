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


def test_kelly_caps_at_20_percent(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "kelly.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    for i in range(10):
        conn.execute("""
            INSERT INTO positions (symbol, base_asset, direction, realized_pnl,
                   setup_score, open_time, close_time, exchange)
            VALUES ('BTCUSDT','BTC','Long',100.0,9,'2026-01-01','2026-01-02','bitget')
        """)
    conn.commit()
    from risk_analytics import compute_kelly_by_bucket
    result = compute_kelly_by_bucket(conn)
    if result["available"]:
        for b in result["buckets"]:
            assert b["recommended_size_pct"] <= 20.0
    conn.close()


def test_alpha_decay_no_data(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "decay.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    from risk_analytics import compute_alpha_decay
    assert compute_alpha_decay(conn)["available"] is False
    conn.close()


def test_alpha_decay_with_data(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "decay2.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    for lag, pnl in [(10, 30), (15, 25), (60, 10), (90, 5), (300, -10), (400, -15)]:
        conn.execute("""
            INSERT INTO positions (symbol, base_asset, direction, realized_pnl,
                   execution_lag_minutes, open_time, close_time, exchange)
            VALUES ('BTCUSDT','BTC','Long',?,?,'2026-01-01','2026-01-02','bitget')
        """, (float(pnl), lag))
    conn.commit()
    from risk_analytics import compute_alpha_decay
    result = compute_alpha_decay(conn)
    assert result["available"] is True
    assert "correlation" in result
    assert len(result["lag_buckets"]) > 0
    conn.close()


def test_pnl_attribution_returns_keys(tmp_path, monkeypatch):
    import database as _db, unittest.mock, pandas as pd
    db_file = str(tmp_path / "attr.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    conn.execute("""
        INSERT INTO positions (symbol, base_asset, direction, realized_pnl, size_usdt,
               open_time, close_time, exchange)
        VALUES ('BTCUSDT','BTC','Long',50.0,300.0,'2026-01-01','2026-01-02','bitget')
    """)
    conn.commit()
    fake_btc = pd.DataFrame({"Close": [40000.0, 41000.0]},
                            index=pd.date_range("2026-01-01", periods=2))
    monkeypatch.setattr("risk_analytics.yf.download",
                        unittest.mock.MagicMock(return_value=fake_btc))
    from risk_analytics import compute_pnl_attribution
    result = compute_pnl_attribution(conn, lookback_days=90)
    assert "alpha_pnl" in result and "beta_pnl" in result and "total_pnl" in result
    conn.close()

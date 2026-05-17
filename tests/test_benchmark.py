# tests/test_benchmark.py
import sys, os, unittest.mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import pandas as pd


@pytest.fixture
def db_bench(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "bench.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    trades = [
        ("BTCUSDT", "Long",  50.0, "2026-01-10", "2026-01-11"),
        ("ETHUSDT", "Long", -20.0, "2026-01-15", "2026-01-16"),
        ("SOLUSDT", "Long",  80.0, "2026-02-01", "2026-02-02"),
    ]
    for sym, direction, pnl, ot, ct in trades:
        conn.execute("""
            INSERT INTO positions (symbol, base_asset, direction, realized_pnl,
                                   open_time, close_time, exchange)
            VALUES (?,?,?,?,?,?,'bitget')
        """, (sym, sym[:-4], direction, pnl, ot, ct))
    conn.commit()
    yield conn
    conn.close()


def test_benchmark_returns_required_keys(db_bench, monkeypatch):
    fake_btc = pd.DataFrame(
        {"Close": [40000.0, 41000.0, 42000.0, 43000.0, 44000.0]},
        index=pd.date_range("2026-01-10", periods=5),
    )
    monkeypatch.setattr("analytics.yf.download", unittest.mock.MagicMock(return_value=fake_btc))
    from analytics import get_benchmark_comparison
    result = get_benchmark_comparison(conn=db_bench)
    assert "trader_return_pct" in result
    assert "btc_return_pct" in result
    assert "alpha_pct" in result
    assert "period_days" in result


def test_alpha_is_trader_minus_btc(db_bench, monkeypatch):
    fake_btc = pd.DataFrame(
        {"Close": [40000.0, 44000.0]},
        index=pd.date_range("2026-01-10", periods=2),
    )
    monkeypatch.setattr("analytics.yf.download", unittest.mock.MagicMock(return_value=fake_btc))
    from analytics import get_benchmark_comparison
    result = get_benchmark_comparison(conn=db_bench)
    expected_alpha = round(result["trader_return_pct"] - result["btc_return_pct"], 2)
    assert result["alpha_pct"] == pytest.approx(expected_alpha, abs=0.1)


def test_benchmark_handles_no_trades(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "empty.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    from analytics import get_benchmark_comparison
    result = get_benchmark_comparison(conn=conn)
    assert result["trader_return_pct"] == 0.0
    assert result["btc_return_pct"] == 0.0
    conn.close()

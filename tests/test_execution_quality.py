import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest


@pytest.fixture
def db_exec(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "exec.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    yield conn
    conn.close()


def test_funding_pnl_column_exists(db_exec):
    cols = [r[1] for r in db_exec.execute("PRAGMA table_info(positions)").fetchall()]
    assert "funding_pnl" in cols


def test_signal_price_column_exists(db_exec):
    cols = [r[1] for r in db_exec.execute("PRAGMA table_info(positions)").fetchall()]
    assert "signal_price" in cols


def test_execution_lag_column_exists(db_exec):
    cols = [r[1] for r in db_exec.execute("PRAGMA table_info(positions)").fetchall()]
    assert "execution_lag_minutes" in cols


def test_dashboard_kpis_has_total_funding_pnl(db_exec):
    db_exec.execute("""
        INSERT INTO positions (symbol, base_asset, direction, realized_pnl,
               open_time, close_time, exchange, funding_pnl)
        VALUES ('BTCUSDT','BTC','Long',50.0,'2026-01-01','2026-01-02','bitget',-2.5)
    """)
    db_exec.commit()
    from analytics import get_dashboard_kpis
    kpis = get_dashboard_kpis(conn=db_exec)
    assert "total_funding_pnl" in kpis
    assert kpis["total_funding_pnl"] == pytest.approx(-2.5, abs=0.01)


def test_get_execution_quality_returns_stats(db_exec):
    db_exec.execute("""
        INSERT INTO positions
          (symbol, base_asset, direction, realized_pnl, exchange,
           open_time, close_time, execution_lag_minutes, signal_price, entry_price)
        VALUES ('BTCUSDT','BTC','Long',30.0,'bitget',
                '2026-01-01','2026-01-02', 45, 50000.0, 50200.0)
    """)
    db_exec.commit()
    from analytics import get_execution_quality
    result = get_execution_quality(conn=db_exec)
    assert "avg_lag_minutes" in result
    assert "avg_slippage_pct" in result
    assert result["sample_size"] >= 1

import sys, os, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest


@pytest.fixture
def db_ts(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "ts.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    balance = 1000.0
    base = dt.date(2026, 1, 1)
    for i in range(30):
        d = (base + dt.timedelta(days=i)).isoformat()
        balance += (5 if i % 3 != 0 else -8)
        conn.execute(
            "INSERT INTO wallet_snapshots (date, wallet_balance, symbol, type) VALUES (?,?,'USDT','trade')",
            (d + " 12:00:00", balance)
        )
    conn.commit()
    yield conn
    conn.close()


def test_tearsheet_metrics_has_required_keys(db_ts):
    from analytics import get_tearsheet_metrics
    result = get_tearsheet_metrics(conn=db_ts)
    for key in ("sharpe", "max_drawdown_pct", "cagr_pct", "volatility_pct", "available"):
        assert key in result


def test_tearsheet_available_with_enough_data(db_ts):
    from analytics import get_tearsheet_metrics
    assert get_tearsheet_metrics(conn=db_ts)["available"] is True


def test_tearsheet_unavailable_with_no_data(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "empty.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    from analytics import get_tearsheet_metrics
    assert get_tearsheet_metrics(conn=conn)["available"] is False
    conn.close()

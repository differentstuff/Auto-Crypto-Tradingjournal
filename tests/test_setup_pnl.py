import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest


@pytest.fixture
def db_setups(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "test_setup.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    rows = [
        ("BTCUSDT", "BTC", "Long", "Breakout",  100.0, "2026-01-01", "2026-01-02"),
        ("BTCUSDT", "BTC", "Long", "Breakout",  -40.0, "2026-01-03", "2026-01-04"),
        ("BTCUSDT", "BTC", "Long", "Breakout",   80.0, "2026-01-05", "2026-01-06"),
        ("ETHUSDT", "ETH", "Long", "Reversal",   50.0, "2026-01-07", "2026-01-08"),
        ("ETHUSDT", "ETH", "Long", "Reversal",  -60.0, "2026-01-09", "2026-01-10"),
    ]
    for sym, base, direction, setup_type, pnl, open_t, close_t in rows:
        conn.execute("""
            INSERT INTO positions
              (symbol, base_asset, direction, setup_type, realized_pnl, open_time, close_time, exchange)
            VALUES (?,?,?,?,?,?,?,'bitget')
        """, (sym, base, direction, setup_type, pnl, open_t, close_t))
    conn.commit()
    yield conn
    conn.close()


def test_returns_both_setup_types(db_setups):
    from analytics import get_setup_type_stats
    result = get_setup_type_stats(conn=db_setups)
    names = [r["setup_type"] for r in result]
    assert "Breakout" in names and "Reversal" in names


def test_breakout_win_rate(db_setups):
    from analytics import get_setup_type_stats
    result = get_setup_type_stats(conn=db_setups)
    b = next(r for r in result if r["setup_type"] == "Breakout")
    assert b["win_rate"] == pytest.approx(66.7, abs=0.5)


def test_profit_factor_present(db_setups):
    from analytics import get_setup_type_stats
    for r in get_setup_type_stats(conn=db_setups):
        assert "profit_factor" in r


def test_breakout_profit_factor(db_setups):
    from analytics import get_setup_type_stats
    b = next(r for r in get_setup_type_stats(conn=db_setups) if r["setup_type"] == "Breakout")
    # wins: 100+80=180, losses: 40 -> PF = 4.5
    assert b["profit_factor"] == pytest.approx(4.5, abs=0.1)

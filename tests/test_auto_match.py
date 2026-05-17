import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def db_auto(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "test_am.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    yield conn
    conn.close()


def _call(conn, symbol, direction, created_at, status="saved", entry=0.04, sl=0.038, tp1=0.045):
    conn.execute("""
        INSERT INTO analyzed_calls
          (symbol, direction, entry_price, sl_price, tp1_price, status, created_at)
        VALUES (?,?,?,?,?,?,?)
    """, (symbol, direction, entry, sl, tp1, status, created_at))
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _pos(conn, symbol, direction, open_time, close_time, pnl=10.0):
    conn.execute("""
        INSERT INTO positions
          (symbol, base_asset, direction, open_time, close_time,
           entry_price, close_price, realized_pnl, exchange)
        VALUES (?,?,?,?,?,0.041,0.046,?,'bitget')
    """, (symbol, symbol.replace("USDT", ""), direction, open_time, close_time, pnl))
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_saved_call_gets_matched(db_auto):
    from sync_base import auto_match_calls
    call_id = _call(db_auto, "CHZUSDT", "Long", "2026-05-01 10:00:00")
    pos_id  = _pos(db_auto, "CHZUSDT", "Long", "2026-05-01 10:30:00", "2026-05-01 14:00:00")
    # Make position appear recent
    db_auto.execute("UPDATE positions SET close_time=datetime('now','-1 days') WHERE id=?", (pos_id,))
    db_auto.execute("UPDATE positions SET open_time=datetime('now','-1 days','+1 hour') WHERE id=?", (pos_id,))
    db_auto.execute("UPDATE analyzed_calls SET created_at=datetime('now','-2 days') WHERE id=?", (call_id,))
    db_auto.commit()
    matched = auto_match_calls(db_auto, exchange="bitget")
    assert matched == 1
    call = db_auto.execute("SELECT status FROM analyzed_calls WHERE id=?", (call_id,)).fetchone()
    assert call[0] == "matched"


def test_already_matched_not_touched(db_auto):
    from sync_base import auto_match_calls
    _call(db_auto, "BTCUSDT", "Long", "2026-05-01 10:00:00", status="matched")
    pos_id = _pos(db_auto, "BTCUSDT", "Long", "2026-05-01 10:30:00", "2026-05-01 14:00:00")
    db_auto.execute("UPDATE positions SET close_time=datetime('now','-1 days') WHERE id=?", (pos_id,))
    db_auto.commit()
    # Position already has call_id=None but call is 'matched', not 'saved'
    matched = auto_match_calls(db_auto)
    assert matched == 0


def test_wrong_direction_not_matched(db_auto):
    from sync_base import auto_match_calls
    _call(db_auto, "ETHUSDT", "Short", "2026-05-01 10:00:00")
    pos_id = _pos(db_auto, "ETHUSDT", "Long", "2026-05-01 10:30:00", "2026-05-01 14:00:00")
    db_auto.execute("UPDATE positions SET close_time=datetime('now','-1 days') WHERE id=?", (pos_id,))
    db_auto.commit()
    assert auto_match_calls(db_auto) == 0

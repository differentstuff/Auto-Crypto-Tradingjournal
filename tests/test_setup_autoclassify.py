import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest


@pytest.fixture
def db_cls(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "cls.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    yield conn
    conn.close()


def _insert_call_with_type(conn, symbol, direction, trade_type, status="saved"):
    analysis = json.dumps({"trade_type": trade_type, "setup_score": 7})
    conn.execute("""
        INSERT INTO analyzed_calls
          (symbol, direction, entry_price, sl_price, tp1_price, status,
           created_at, analysis_json)
        VALUES (?,?,0.04,0.038,0.045,?,datetime('now','-2 hours'),?)
    """, (symbol, direction, status, analysis))
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_position(conn, symbol, direction, call_id=None):
    conn.execute("""
        INSERT INTO positions
          (symbol, base_asset, direction, realized_pnl, exchange,
           open_time, close_time, call_id)
        VALUES (?,?,?,10.0,'bitget','2026-05-01','2026-05-02',?)
    """, (symbol, symbol[:-4], direction, call_id))
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_setup_type_populated_from_call(db_cls):
    from sync_base import _populate_setup_type_from_call
    call_id = _insert_call_with_type(db_cls, "BTCUSDT", "Long", "Breakout")
    pos_id  = _insert_position(db_cls, "BTCUSDT", "Long", call_id=call_id)
    _populate_setup_type_from_call(db_cls, pos_id, call_id)
    row = db_cls.execute("SELECT setup_type FROM positions WHERE id=?", (pos_id,)).fetchone()
    assert row[0] == "Breakout"


def test_missing_analysis_json_does_not_crash(db_cls):
    from sync_base import _populate_setup_type_from_call
    db_cls.execute("""
        INSERT INTO analyzed_calls
          (symbol, direction, entry_price, sl_price, tp1_price, status, created_at)
        VALUES ('ETHUSDT','Long',0.04,0.038,0.045,'saved',datetime('now'))
    """)
    db_cls.commit()
    call_id = db_cls.execute("SELECT last_insert_rowid()").fetchone()[0]
    pos_id  = _insert_position(db_cls, "ETHUSDT", "Long", call_id=call_id)
    _populate_setup_type_from_call(db_cls, pos_id, call_id)
    row = db_cls.execute("SELECT setup_type FROM positions WHERE id=?", (pos_id,)).fetchone()
    assert row[0] is None or row[0] == ""

import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest


@pytest.fixture
def db_hs(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "test_hs.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    # 6 TP + 4 FP at score 7
    for i in range(10):
        conn.execute("""
            INSERT INTO positions
              (symbol, base_asset, direction, realized_pnl, open_time, close_time, exchange)
            VALUES ('BTCUSDT','BTC','Long',?,?,?,'bitget')
        """, (50.0 if i < 6 else -30.0, "2026-01-01", "2026-01-02"))
    conn.commit()
    positions = conn.execute("SELECT id FROM positions").fetchall()
    for idx, (pos_id,) in enumerate(positions):
        verdict = "TP" if idx < 6 else "FP"
        conn.execute("""
            INSERT INTO trade_hindsight (position_id, setup_score, would_enter, verdict, actual_pnl)
            VALUES (?,7,1,?,?)
        """, (pos_id, verdict, 50.0 if verdict == "TP" else -30.0))
    conn.commit()
    yield conn
    conn.close()


def test_compute_feedback_returns_buckets(db_hs):
    from ai_hindsight import compute_feedback
    result = compute_feedback(conn=db_hs)
    assert "buckets" in result
    assert "recommendation" in result
    assert len(result["buckets"]) > 0


def test_fp_rate_correct(db_hs):
    from ai_hindsight import compute_feedback
    result = compute_feedback(conn=db_hs)
    b = next((x for x in result["buckets"] if x["score_range"] == "7-8"), None)
    assert b is not None
    assert b["fp_rate"] == pytest.approx(40.0, abs=1.0)


def test_high_fp_triggers_raise(db_hs):
    from ai_hindsight import compute_feedback
    result = compute_feedback(conn=db_hs)
    assert result["recommendation"] == "raise_threshold"

"""Tests for optimizer run history persistence."""
import json
import pytest


def test_optimizer_runs_table_exists(db):
    """optimizer_runs table must exist after init_db."""
    tables = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    assert "optimizer_runs" in tables


def test_optimizer_runs_insert_and_retrieve(db):
    """Can insert and read back an optimizer run."""
    db.execute("""
        INSERT INTO optimizer_runs (symbol, timeframe, days, n_trials, best_sharpe, best_params, duration_sec)
        VALUES ('BTCUSDT', '4H', 30, 50, 1.23, '{"rsi_max": 60}', 45.2)
    """)
    db.commit()
    row = db.execute("SELECT * FROM optimizer_runs WHERE symbol='BTCUSDT'").fetchone()
    assert row is not None
    # columns: id(0) ts(1) symbol(2) timeframe(3) days(4) n_trials(5) best_sharpe(6) best_params(7) duration_sec(8)
    assert row[6] == pytest.approx(1.23)  # best_sharpe
    params = json.loads(row[7])           # best_params
    assert params["rsi_max"] == 60

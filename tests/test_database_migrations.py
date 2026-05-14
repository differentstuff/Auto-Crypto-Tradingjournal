"""Tests for database.py — init_db() idempotency and migration correctness."""
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_init_db_idempotent(tmp_path, monkeypatch):
    """Calling init_db() multiple times must not crash or duplicate schema_version rows."""
    import database as db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    db.init_db()  # second call must not crash or duplicate rows
    with db.db_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    assert count > 0  # migrations ran

    # calling again should not increase count
    db.init_db()
    with db.db_conn() as conn:
        count2 = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    assert count2 == count


def test_all_tables_exist(db):
    """All expected tables must be created by init_db()."""
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    for t in (
        "positions", "orders", "wallet_snapshots", "analyzed_calls",
        "pending_limits", "trader_rulebook", "trade_hindsight",
        "settings", "import_log", "token_usage", "schema_version",
    ):
        assert t in tables, f"Missing table: {t}"


def test_schema_version_rows_have_unique_versions(tmp_path, monkeypatch):
    """Each migration must be tracked with a unique version number."""
    import database as db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t2.db"))
    db.init_db()
    with db.db_conn() as conn:
        rows = conn.execute("SELECT version FROM schema_version").fetchall()
    versions = [r[0] for r in rows]
    assert len(versions) == len(set(versions)), "Duplicate migration versions found"


def test_positions_table_columns(db):
    """positions table must have core trading columns."""
    info = db.execute("PRAGMA table_info(positions)").fetchall()
    col_names = {row[1] for row in info}
    for col in ("symbol", "realized_pnl", "direction", "open_time", "close_time", "exchange"):
        assert col in col_names, f"Missing column: {col}"


def test_wallet_snapshots_table_exists(db):
    """wallet_snapshots must exist for equity curve queries."""
    info = db.execute("PRAGMA table_info(wallet_snapshots)").fetchall()
    assert len(info) > 0, "wallet_snapshots has no columns"


def test_token_usage_table_columns(db):
    """token_usage must track module, model, and token counts."""
    info = db.execute("PRAGMA table_info(token_usage)").fetchall()
    col_names = {row[1] for row in info}
    for col in ("module", "model", "input_tokens", "output_tokens"):
        assert col in col_names, f"Missing token_usage column: {col}"


def test_settings_table_accepts_key_value(db):
    """settings table should accept (key, value) pairs."""
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
               ("test_key", "test_value"))
    db.commit()
    row = db.execute("SELECT value FROM settings WHERE key='test_key'").fetchone()
    assert row is not None
    assert row[0] == "test_value"


def test_positions_insert_and_query(db):
    """Basic round-trip: insert a position and read it back."""
    db.execute(
        "INSERT INTO positions (symbol, base_asset, direction, realized_pnl, "
        "open_time, close_time, exchange) VALUES (?,?,?,?,?,?,?)",
        ("BTCUSDT", "BTC", "Long", 123.45, "2026-01-01T00:00:00",
         "2026-01-02T00:00:00", "bitget"),
    )
    db.commit()
    row = db.execute(
        "SELECT realized_pnl FROM positions WHERE symbol='BTCUSDT'"
    ).fetchone()
    assert row is not None
    assert abs(row[0] - 123.45) < 1e-6

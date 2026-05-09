import os
import sys
import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub env vars before any project imports
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("BITGET_API_KEY", "test")
os.environ.setdefault("BITGET_SECRET_KEY", "test")
os.environ.setdefault("BITGET_PASSPHRASE", "test")
os.environ.setdefault("NANSEN_API_KEY", "test")
os.environ.setdefault("FRED_API_KEY", "test")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")


@pytest.fixture
def db(tmp_path, monkeypatch):
    """In-memory SQLite DB with full schema, isolated per test."""
    import database as _db
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    yield conn
    conn.close()


@pytest.fixture
def sample_positions(db):
    """5 closed positions across BTCUSDT and ETHUSDT for history tests."""
    rows = [
        ("BTCUSDT", "Long",   100.0, "2026-01-01T00:00:00", "2026-01-02T00:00:00"),
        ("BTCUSDT", "Long",   -50.0, "2026-01-03T00:00:00", "2026-01-04T00:00:00"),
        ("BTCUSDT", "Long",    80.0, "2026-01-05T00:00:00", "2026-01-06T00:00:00"),
        ("ETHUSDT", "Long",    40.0, "2026-01-07T00:00:00", "2026-01-08T00:00:00"),
        ("BTCUSDT", "Short",  -20.0, "2026-01-09T00:00:00", "2026-01-10T00:00:00"),
    ]
    for sym, direction, pnl, open_t, close_t in rows:
        db.execute(
            "INSERT INTO positions (symbol, direction, realized_pnl, "
            "open_time, close_time, exchange) VALUES (?,?,?,?,?,'bitget')",
            (sym, direction, pnl, open_t, close_t),
        )
    db.commit()
    return db

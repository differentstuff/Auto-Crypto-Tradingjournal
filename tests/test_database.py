def test_schema_version_table_exists(db):
    tables = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    assert "schema_version" in tables

def test_schema_version_has_entries(db):
    rows = db.execute("SELECT version, name FROM schema_version ORDER BY version").fetchall()
    assert len(rows) > 0
    assert all(r[1] for r in rows)  # all have names

def test_init_db_is_idempotent(db, tmp_path, monkeypatch):
    """Running init_db() twice must not raise or duplicate schema_version rows."""
    import database as _db
    count_before = db.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    monkeypatch.setattr(_db, "DB_PATH", str(tmp_path / "test2.db"))
    _db.init_db()
    conn2 = _db.get_conn()
    count2 = conn2.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    conn2.close()
    assert count2 == count_before

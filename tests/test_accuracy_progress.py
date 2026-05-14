"""Tests for GET /api/calls/accuracy-progress."""
import sys, os
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Ensure real Flask is used (conftest may have stubbed it if it loaded first)
for _mod in list(sys.modules):
    if _mod == "flask" or _mod.startswith("flask."):
        del sys.modules[_mod]
import flask as _flask_real          # noqa: F401 — forces real Flask into sys.modules


def _insert_outcome(db, outcome):
    db.execute("""
        INSERT INTO analyzed_calls (symbol, direction, status, outcome, created_at)
        VALUES ('BTCUSDT', 'Long', 'closed', ?, datetime('now'))
    """, (outcome,))
    db.commit()


def _client(db, monkeypatch):
    import flask
    monkeypatch.setattr("database.DB_PATH", db.execute("PRAGMA database_list").fetchone()[2])
    import importlib, routes.calls as rc
    importlib.reload(rc)
    app = flask.Flask(__name__)
    app.register_blueprint(rc.bp)
    return app.test_client()


def test_empty(db, monkeypatch):
    c = _client(db, monkeypatch)
    data = c.get("/api/calls/accuracy-progress").get_json()
    assert data["ok"] is True
    assert data["data"]["recorded"]    == 0
    assert data["data"]["target"]      == 35
    assert data["data"]["win_rate"]    == 0.0
    assert data["data"]["remaining"]   == 35
    assert data["data"]["enough_data"] is False


def test_partial(db, monkeypatch):
    for _ in range(10):
        _insert_outcome(db, "won")
    for _ in range(4):
        _insert_outcome(db, "lost")
    c = _client(db, monkeypatch)
    data = c.get("/api/calls/accuracy-progress").get_json()
    assert data["data"]["recorded"]    == 14
    assert data["data"]["win_rate"]    == round(10 / 14 * 100, 1)
    assert data["data"]["remaining"]   == 21
    assert data["data"]["enough_data"] is False


def test_target_reached(db, monkeypatch):
    for _ in range(35):
        _insert_outcome(db, "won")
    c = _client(db, monkeypatch)
    data = c.get("/api/calls/accuracy-progress").get_json()
    assert data["data"]["enough_data"] is True
    assert data["data"]["remaining"]   == 0
    assert data["data"]["recorded"]    == 35

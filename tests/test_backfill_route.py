import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "test_bf.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()

    # Save stub modules so we can restore them after the test
    _saved = {k: v for k, v in sys.modules.items()
              if k == "flask" or k.startswith("flask.")}
    for mod in list(_saved):
        del sys.modules[mod]

    import flask, helpers, importlib
    importlib.reload(helpers)

    import routes.sync as rs
    importlib.reload(rs)
    app = flask.Flask(__name__)
    app.register_blueprint(rs.bp)
    yield app.test_client()

    # Restore Flask stub so subsequent tests that depend on it still work
    for k in [k for k in sys.modules if k == "flask" or k.startswith("flask.")]:
        del sys.modules[k]
    sys.modules.update(_saved)
    import helpers as _h
    importlib.reload(_h)


def test_backfill_returns_ok(client, monkeypatch):
    import bitget_sync
    monkeypatch.setattr(bitget_sync, "run_backfill", lambda **kw: {"inserted": 3, "pages": 50, "fetched": 10})
    resp = client.post("/api/sync/backfill")
    data = resp.get_json()
    assert data["ok"] is True
    assert data["data"]["inserted"] == 3


def test_backfill_error_returns_err(client, monkeypatch):
    import bitget_sync
    def _raise(**kw): raise RuntimeError("API timeout")
    monkeypatch.setattr(bitget_sync, "run_backfill", _raise)
    resp = client.post("/api/sync/backfill")
    data = resp.get_json()
    assert data["ok"] is False

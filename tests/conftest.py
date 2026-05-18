import os
import sys
import types
import unittest.mock
import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def pytest_addoption(parser):
    """Register --host option for live performance baseline tests."""
    parser.addoption("--host", action="store", default=None,
                     help="Host:port of live journal (e.g. 192.168.1.100:8082)")

# Stub heavy optional deps so tests work without full pip install
if "flask" not in sys.modules:
    class _FakeResponse:
        """Minimal Flask Response stub — supports both .get_json() and dict-style access."""
        def __init__(self, data): self._data = data; self.status_code = 200
        def get_json(self): return self._data
        def __getitem__(self, key): return self._data[key]
        def __contains__(self, key): return key in (self._data or {})
        def get(self, key, default=None): return (self._data or {}).get(key, default)
    _flask = types.ModuleType("flask")
    _flask.jsonify = lambda x: _FakeResponse(x)
    _flask.request = unittest.mock.MagicMock()
    sys.modules["flask"] = _flask

if "anthropic" not in sys.modules:
    _anthropic = types.ModuleType("anthropic")
    _anthropic.Anthropic = unittest.mock.MagicMock()
    # Minimal exception class so tests can raise/catch anthropic.APIError
    class _APIError(Exception):
        pass
    _anthropic.APIError = _APIError
    sys.modules["anthropic"] = _anthropic

if "pandas_ta" not in sys.modules:
    import numpy as _np
    import pandas as _pd

    def _pta_rsi(series, length=14):
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_g = gain.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
        avg_l = loss.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
        rs = avg_g / avg_l.replace(0, _np.nan)
        result = 100 - (100 / (1 + rs))
        result.name = f"RSI_{length}"
        return result

    def _pta_adx(high, low, close, length=14):
        prev_close = close.shift(1)
        tr = _pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        dm_plus = _np.where(
            (high - high.shift(1)) > (low.shift(1) - low),
            (high - high.shift(1)).clip(lower=0), 0)
        dm_minus = _np.where(
            (low.shift(1) - low) > (high - high.shift(1)),
            (low.shift(1) - low).clip(lower=0), 0)
        dm_plus = _pd.Series(dm_plus, index=high.index)
        dm_minus = _pd.Series(dm_minus, index=high.index)
        atr = tr.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
        di_p = 100 * dm_plus.ewm(alpha=1 / length, min_periods=length, adjust=False).mean() / atr.replace(0, _np.nan)
        di_m = 100 * dm_minus.ewm(alpha=1 / length, min_periods=length, adjust=False).mean() / atr.replace(0, _np.nan)
        dx = 100 * (di_p - di_m).abs() / (di_p + di_m).replace(0, _np.nan)
        adx_vals = dx.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
        col = f"ADX_{length}"
        return _pd.DataFrame({col: adx_vals, f"DMP_{length}": di_p, f"DMN_{length}": di_m})

    def _pta_ema(series, length=None, **kwargs):
        return series.ewm(span=length, adjust=False).mean()

    _pandas_ta = types.ModuleType("pandas_ta")
    _pandas_ta.rsi = _pta_rsi
    _pandas_ta.adx = _pta_adx
    _pandas_ta.ema = _pta_ema
    sys.modules["pandas_ta"] = _pandas_ta

if "chart_indicators" not in sys.modules:
    _chart_indicators = types.ModuleType("chart_indicators")
    _chart_indicators.compute_all_indicators = unittest.mock.MagicMock(return_value={})
    _chart_indicators.compute_wavetrend = unittest.mock.MagicMock(return_value={})
    sys.modules["chart_indicators"] = _chart_indicators

if "chart_sr" not in sys.modules:
    _chart_sr = types.ModuleType("chart_sr")
    _chart_sr.detect_support_resistance = unittest.mock.MagicMock(return_value=[])
    sys.modules["chart_sr"] = _chart_sr

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
    conn.row_factory = None  # plain tuples so tests can do row == ("a", 1, ...)
    yield conn
    conn.close()


@pytest.fixture
def sample_positions(db):
    """5 closed positions across BTCUSDT and ETHUSDT for history tests."""
    rows = [
        ("BTCUSDT", "BTC", "Long",   100.0, "2026-01-01T00:00:00", "2026-01-02T00:00:00"),
        ("BTCUSDT", "BTC", "Long",   -50.0, "2026-01-03T00:00:00", "2026-01-04T00:00:00"),
        ("BTCUSDT", "BTC", "Long",    80.0, "2026-01-05T00:00:00", "2026-01-06T00:00:00"),
        ("ETHUSDT", "ETH", "Long",    40.0, "2026-01-07T00:00:00", "2026-01-08T00:00:00"),
        ("BTCUSDT", "BTC", "Short",  -20.0, "2026-01-09T00:00:00", "2026-01-10T00:00:00"),
    ]
    for sym, base, direction, pnl, open_t, close_t in rows:
        db.execute(
            "INSERT INTO positions (symbol, base_asset, direction, realized_pnl, "
            "open_time, close_time, exchange) VALUES (?,?,?,?,?,?,'bitget')",
            (sym, base, direction, pnl, open_t, close_t),
        )
    db.commit()
    return db


@pytest.fixture
def client(db, monkeypatch):
    """Real Flask test client with in-memory DB, isolated per test."""
    import importlib
    import database as _db
    monkeypatch.setattr(_db, "DB_PATH", db.execute("PRAGMA database_list").fetchone()[2])

    # Save stub Flask entries so we can restore them after the test
    _saved_modules = {k: v for k, v in sys.modules.items()
                      if k == "flask" or k.startswith("flask.")}

    # Evict the Flask stub so that routes and helpers can import real Flask
    for _mod in list(sys.modules):
        if _mod == "flask" or _mod.startswith("flask."):
            del sys.modules[_mod]
    import flask

    # Reload helpers so its module-level `jsonify` binding points to real Flask
    import helpers
    importlib.reload(helpers)

    import routes.calls as rc
    importlib.reload(rc)
    app = flask.Flask(__name__)
    app.register_blueprint(rc.bp)
    import routes.backtest as rb
    importlib.reload(rb)
    app.register_blueprint(rb.bp)
    import routes.limits as rl
    importlib.reload(rl)
    app.register_blueprint(rl.bp)

    yield app.test_client()

    # Teardown: restore the stub Flask so later tests that depend on it still work
    for k in list(sys.modules):
        if k == "flask" or k.startswith("flask."):
            del sys.modules[k]
    sys.modules.update(_saved_modules)
    # Reload helpers to re-bind jsonify to the stub
    import helpers as _h
    importlib.reload(_h)

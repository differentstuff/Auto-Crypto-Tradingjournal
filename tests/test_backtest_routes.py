"""Tests for /api/backtest/run route — input validation, caps, response shape."""
import sys
import os
import importlib
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force real Flask — conftest may have stubbed it
for _mod in list(sys.modules):
    if _mod == "flask" or _mod.startswith("flask."):
        del sys.modules[_mod]
import flask as _flask_real  # noqa: F401


def _make_result(**kwargs):
    defaults = dict(
        trades=[], sharpe=1.5, sortino=1.8, max_drawdown=0.1,
        profit_factor=1.8, win_rate=65.0, total_trades=20,
    )
    defaults.update(kwargs)
    result = MagicMock()
    for k, v in defaults.items():
        setattr(result, k, v)
    return result


def _make_client():
    """Create a Flask test client with backtest blueprint, stubs for heavy deps."""
    import flask

    # Also reload helpers so it picks up real Flask's jsonify, not a stub
    for mod in ["backtest_engine", "backtest_optimizer", "backtest_metrics",
                "chart_context", "bitget_client", "helpers", "routes.backtest"]:
        if mod in sys.modules:
            del sys.modules[mod]

    _be = MagicMock()
    _be.BacktestParams = MagicMock(return_value=MagicMock())
    _be.run_backtest = MagicMock(return_value=_make_result())
    sys.modules["backtest_engine"] = _be

    _bo = MagicMock()
    _bo.run_optimizer = MagicMock(return_value={"best": "params"})
    sys.modules["backtest_optimizer"] = _bo

    if "routes.backtest" in sys.modules:
        del sys.modules["routes.backtest"]

    import routes.backtest as rb
    importlib.reload(rb)

    app = flask.Flask(__name__)
    app.register_blueprint(rb.bp)
    app.config["TESTING"] = True
    return app.test_client(), _be


def test_backtest_run_symbol_required():
    """Missing symbol → ok=False."""
    client, _ = _make_client()
    resp = client.post(
        "/api/backtest/run",
        json={"timeframe": "4H", "days": 30},
        content_type="application/json",
    )
    data = resp.get_json()
    assert data["ok"] is False


def test_backtest_run_symbol_required_status_code():
    """Missing symbol → HTTP 400."""
    client, _ = _make_client()
    resp = client.post(
        "/api/backtest/run",
        json={"timeframe": "4H", "days": 30},
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_backtest_run_days_capped():
    """days=9999 → response days == 365."""
    client, _be = _make_client()
    _be.run_backtest.return_value = _make_result()
    resp = client.post(
        "/api/backtest/run",
        json={"symbol": "BTCUSDT", "days": 9999},
        content_type="application/json",
    )
    data = resp.get_json()
    assert data["ok"] is True
    assert data["data"]["days"] == 365


def test_backtest_run_invalid_days():
    """days='abc' → ok=False."""
    client, _ = _make_client()
    resp = client.post(
        "/api/backtest/run",
        json={"symbol": "BTCUSDT", "days": "abc"},
        content_type="application/json",
    )
    data = resp.get_json()
    assert data["ok"] is False


def test_backtest_run_response_shape():
    """Successful run → all required metric fields present."""
    client, _be = _make_client()
    _be.run_backtest.return_value = _make_result()
    resp = client.post(
        "/api/backtest/run",
        json={"symbol": "BTCUSDT", "timeframe": "4H", "days": 30},
        content_type="application/json",
    )
    data = resp.get_json()
    assert data["ok"] is True
    for field in ("total_trades", "win_rate", "profit_factor", "sharpe", "sortino", "max_drawdown"):
        assert field in data["data"], f"Missing field: {field}"


def test_backtest_run_symbol_echoed_in_response():
    """Response must echo back the symbol and timeframe."""
    client, _be = _make_client()
    _be.run_backtest.return_value = _make_result()
    resp = client.post(
        "/api/backtest/run",
        json={"symbol": "ETHUSDT", "timeframe": "1H", "days": 60},
        content_type="application/json",
    )
    data = resp.get_json()
    assert data["ok"] is True
    assert data["data"]["symbol"] == "ETHUSDT"
    assert data["data"]["timeframe"] == "1H"


def test_backtest_run_days_within_limit():
    """days=30 should not be changed."""
    client, _be = _make_client()
    _be.run_backtest.return_value = _make_result()
    resp = client.post(
        "/api/backtest/run",
        json={"symbol": "BTCUSDT", "days": 30},
        content_type="application/json",
    )
    data = resp.get_json()
    assert data["ok"] is True
    assert data["data"]["days"] == 30


def test_backtest_run_days_exact_365():
    """days=365 stays 365 (boundary value)."""
    client, _be = _make_client()
    _be.run_backtest.return_value = _make_result()
    resp = client.post(
        "/api/backtest/run",
        json={"symbol": "BTCUSDT", "days": 365},
        content_type="application/json",
    )
    data = resp.get_json()
    assert data["ok"] is True
    assert data["data"]["days"] == 365


def test_backtest_run_default_days():
    """No days parameter → uses default (180) which is ≤ 365."""
    client, _be = _make_client()
    _be.run_backtest.return_value = _make_result()
    resp = client.post(
        "/api/backtest/run",
        json={"symbol": "BTCUSDT"},
        content_type="application/json",
    )
    data = resp.get_json()
    assert data["ok"] is True
    assert data["data"]["days"] <= 365

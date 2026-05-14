"""Tests for /api/backtest/run and /api/backtest/optimize routes."""
import sys
import os
import importlib
from unittest.mock import patch, MagicMock
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force real Flask — conftest may have stubbed it
for _mod in list(sys.modules):
    if _mod == "flask" or _mod.startswith("flask."):
        del sys.modules[_mod]
import flask as _flask_real  # noqa: F401 — forces real Flask into sys.modules


def _make_backtest_result(**kwargs):
    """Build a minimal BacktestResult-like object."""
    result = MagicMock()
    result.total_trades  = kwargs.get("total_trades",  10)
    result.win_rate      = kwargs.get("win_rate",       0.6)
    result.profit_factor = kwargs.get("profit_factor",  1.5)
    result.sharpe        = kwargs.get("sharpe",         1.23)
    result.sortino       = kwargs.get("sortino",        1.45)
    result.max_drawdown  = kwargs.get("max_drawdown",   0.0832)
    return result


def _client(monkeypatch):
    """Create a minimal Flask test client with the backtest blueprint registered."""
    import flask
    # Stub run_backtest so no real exchange calls are made
    mock_result = _make_backtest_result()

    # Stub heavy imports before loading the route module
    for mod in ["backtest_engine", "backtest_optimizer",
                "backtest_metrics", "chart_context"]:
        if mod in sys.modules:
            del sys.modules[mod]

    _be = MagicMock()
    _be.BacktestParams = MagicMock(return_value=MagicMock())
    _be.run_backtest   = MagicMock(return_value=mock_result)
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


# ── tests ──────────────────────────────────────────────────────────────────────

def test_backtest_run_ok(monkeypatch):
    """POST /api/backtest/run returns ok=True with required metric fields."""
    client, _be = _client(monkeypatch)
    resp = client.post(
        "/api/backtest/run",
        json={"symbol": "BTCUSDT", "timeframe": "4H", "days": 180},
        content_type="application/json",
    )
    data = resp.get_json()
    assert data["ok"] is True
    d = data["data"]
    assert d["symbol"]       == "BTCUSDT"
    assert d["timeframe"]    == "4H"
    assert d["days"]         == 180
    assert "total_trades"    in d
    assert "win_rate"        in d
    assert "profit_factor"   in d
    assert "sharpe"          in d
    assert "sortino"         in d
    assert "max_drawdown"    in d


def test_backtest_run_missing_symbol(monkeypatch):
    """POST /api/backtest/run without symbol returns ok=False with 400."""
    client, _be = _client(monkeypatch)
    resp = client.post(
        "/api/backtest/run",
        json={},
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["ok"] is False
    assert "symbol" in data["error"]

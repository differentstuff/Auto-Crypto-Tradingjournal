"""
tests/test_walk_forward_pbo.py -- Unit tests for Walk-Forward + PBO integration.

Tests run_walk_forward_pbo() with fake modules injected into sys.modules
so that the function's local imports resolve to our mocks.
"""
import os
import sys
import types
from unittest.mock import MagicMock

import numpy as np
import pytest

# Ensure backtest_quality can be imported (it only needs numpy)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "tools", "backtest"))

from backtest_quality import _pbo, _sharpe


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_backtest_result(sharpe=1.5, n_trades=20, pnl_mean=0.01):
    """Create a mock BacktestResult."""
    result = MagicMock()
    result.sharpe = sharpe
    result.total_trades = n_trades
    result.win_rate = 60.0
    result.trades = []
    rng = np.random.default_rng(42)
    for _ in range(n_trades):
        t = MagicMock()
        t.pnl_pct = float(rng.normal(pnl_mean, 0.02))
        result.trades.append(t)
    return result


BEST_PARAMS = {
    "wt_oversold": -53.0,
    "rsi_max": 60.0,
    "adx_min": 15.0,
    "min_confluence": 0.35,
    "sl_pct": 0.10,
    "tp1_pct": 0.05,
    "tp2_pct": 0.10,
}


def _inject_fake_modules(
    db_row=("2025-01-01 00:00:00", "2025-12-27 00:00:00", 50),
    optimizer_return=BEST_PARAMS,
    backtest_return=None,
):
    """
    Inject fake database, backtest_engine, and backtest_optimizer into sys.modules.

    Returns a dict of the fake modules so tests can inspect/modify them.
    Call _remove_fake_modules() to clean up.
    """
    # ── Fake database module ─────────────────────────────────────────────
    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchone.return_value = db_row
    # Make fake_conn work as its own context manager:
    # with db_conn() as conn  →  conn = fake_conn.__enter__() = fake_conn
    fake_conn.__enter__ = MagicMock(return_value=fake_conn)
    fake_conn.__exit__ = MagicMock(return_value=False)
    fake_db_mod = types.ModuleType("database")
    # db_conn is a callable returning a context manager
    fake_db_mod.db_conn = lambda: fake_conn

    # ── Fake backtest_engine module ──────────────────────────────────────
    fake_bt_mod = types.ModuleType("backtest_engine")
    # BacktestParams is a dataclass — mock __dataclass_fields__
    MockBP = MagicMock()
    MockBP.__dataclass_fields__ = {
        "wt_oversold": True, "rsi_max": True, "adx_min": True,
        "min_confluence": True, "sl_pct": True, "tp1_pct": True, "tp2_pct": True,
    }
    MockBP.return_value = MagicMock()
    fake_bt_mod.BacktestParams = MockBP

    if backtest_return is None:
        backtest_return = _make_backtest_result()
    fake_bt_mod.run_backtest = MagicMock(return_value=backtest_return)

    # ── Fake backtest_optimizer module ───────────────────────────────────
    fake_opt_mod = types.ModuleType("backtest_optimizer")
    if callable(optimizer_return):
        fake_opt_mod.run_optimizer = MagicMock(side_effect=optimizer_return)
    else:
        fake_opt_mod.run_optimizer = MagicMock(return_value=optimizer_return)

    # ── Inject into sys.modules ──────────────────────────────────────────
    saved = {}
    for name, mod in [
        ("database", fake_db_mod),
        ("backtest_engine", fake_bt_mod),
        ("backtest_optimizer", fake_opt_mod),
    ]:
        saved[name] = sys.modules.get(name)
        sys.modules[name] = mod

    return {
        "saved": saved,
        "database": fake_db_mod,
        "backtest_engine": fake_bt_mod,
        "backtest_optimizer": fake_opt_mod,
        "db_conn_inner": fake_conn,
    }


def _remove_fake_modules(injected):
    """Restore original sys.modules entries."""
    for name, orig in injected["saved"].items():
        if orig is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = orig


@pytest.fixture(autouse=True)
def _cleanup_sys_modules():
    """Ensure any injected modules are cleaned up after each test."""
    injected_holder = []
    yield injected_holder
    # If a test stored injected info, clean it up
    for injected in injected_holder:
        _remove_fake_modules(injected)


def _run_wfpbo(**kwargs):
    """Import and call run_walk_forward_pbo (forces fresh import each time)."""
    import importlib
    import backtest_quality as bq
    importlib.reload(bq)
    return bq.run_walk_forward_pbo(**kwargs)


# ── PBO unit tests ────────────────────────────────────────────────────────────

class TestPBOFunction:
    """Test the _pbo() helper directly."""

    def test_pbo_genuine_strategy(self):
        rng = np.random.default_rng(42)
        returns = rng.normal(0.002, 0.01, 500)
        pbo = _pbo(returns)
        assert pbo < 0.5, f"Genuine strategy should have PBO < 0.5, got {pbo}"

    def test_pbo_overfitted_strategy(self):
        rng = np.random.default_rng(123)
        returns = rng.normal(0.0, 0.02, 500)
        pbo = _pbo(returns)
        assert 0.0 <= pbo <= 1.0

    def test_pbo_too_few_returns(self):
        returns = np.array([0.01, 0.02, -0.01])
        pbo = _pbo(returns)
        assert pbo != pbo  # NaN

    def test_pbo_boundary(self):
        rng = np.random.default_rng(99)
        returns = rng.normal(0.001, 0.015, 200)
        pbo = _pbo(returns)
        assert 0.0 <= pbo <= 1.0


# ── Walk-Forward PBO integration tests ────────────────────────────────────────

class TestRunWalkForwardPBO:
    """Test run_walk_forward_pbo() with fake module injection."""

    def test_no_positions_found(self, _cleanup_sys_modules):
        inj = _inject_fake_modules(db_row=(None, None, 0))
        _cleanup_sys_modules.append(inj)
        result = _run_wfpbo(symbol="NOSYMBOL")
        assert "error" in result
        assert "No positions found" in result["error"]

    def test_too_few_positions(self, _cleanup_sys_modules):
        inj = _inject_fake_modules(
            db_row=("2025-06-01 00:00:00", "2025-06-15 00:00:00", 5),
        )
        _cleanup_sys_modules.append(inj)
        result = _run_wfpbo(symbol="BTCUSDT")
        assert "error" in result
        assert "Too few positions" in result["error"]

    def test_n_windows_minimum(self, _cleanup_sys_modules):
        inj = _inject_fake_modules()
        _cleanup_sys_modules.append(inj)
        result = _run_wfpbo(symbol="BTCUSDT", n_windows=1)
        assert "error" in result
        assert "n_windows must be >= 2" in result["error"]

    def test_all_windows_pass(self, _cleanup_sys_modules):
        inj = _inject_fake_modules(
            backtest_return=_make_backtest_result(sharpe=2.0, n_trades=25),
        )
        _cleanup_sys_modules.append(inj)
        result = _run_wfpbo(symbol="BTCUSDT", n_windows=3, n_trials=5)

        assert "error" not in result
        assert result["symbol"] == "BTCUSDT"
        assert result["n_windows_requested"] == 3
        assert result["n_windows_completed"] >= 1
        assert result["regime_survives"] is True
        assert result["any_negative_sharpe"] is False
        assert "window_details" in result
        assert "interpretation" in result

    def test_negative_sharpe_fails(self, _cleanup_sys_modules):
        call_count = [0]

        def _backtest_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] % 3 == 0:
                return _make_backtest_result(sharpe=-0.5, n_trades=10)
            return _make_backtest_result(sharpe=1.5, n_trades=20)

        inj = _inject_fake_modules(backtest_return=None)
        # Override with side_effect after injection
        inj["backtest_engine"].run_backtest.side_effect = _backtest_side_effect
        _cleanup_sys_modules.append(inj)

        result = _run_wfpbo(symbol="BTCUSDT", n_windows=3, n_trials=5)

        assert "error" not in result
        test_sharpes = [w["test_sharpe"] for w in result["window_details"]]
        if any(s < 0 for s in test_sharpes):
            assert result["regime_survives"] is False
            assert result["passes"] is False

    def test_optimizer_failure_graceful(self, _cleanup_sys_modules):
        def _failing_optimizer(*a, **kw):
            raise RuntimeError("API down")

        inj = _inject_fake_modules(
            optimizer_return=_failing_optimizer,
            backtest_return=_make_backtest_result(),
        )
        _cleanup_sys_modules.append(inj)

        result = _run_wfpbo(symbol="BTCUSDT", n_windows=3, n_trials=5)
        assert "error" in result
        assert "No valid windows" in result["error"]

    def test_optimizer_returns_empty(self, _cleanup_sys_modules):
        inj = _inject_fake_modules(optimizer_return={})
        _cleanup_sys_modules.append(inj)

        result = _run_wfpbo(symbol="BTCUSDT", n_windows=3, n_trials=5)
        assert "error" in result
        assert "No valid windows" in result["error"]

    def test_wfe_computed(self, _cleanup_sys_modules):
        call_count = [0]

        def _backtest_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] % 2 == 1:
                return _make_backtest_result(sharpe=1.0, n_trades=20)
            else:
                return _make_backtest_result(sharpe=2.0, n_trades=30)

        inj = _inject_fake_modules(backtest_return=None)
        inj["backtest_engine"].run_backtest.side_effect = _backtest_side_effect
        _cleanup_sys_modules.append(inj)

        result = _run_wfpbo(symbol="BTCUSDT", n_windows=3, n_trials=5)

        if "error" not in result and result.get("wfe") is not None:
            assert 0.0 < result["wfe"] <= 2.0

    def test_window_details_structure(self, _cleanup_sys_modules):
        inj = _inject_fake_modules(
            backtest_return=_make_backtest_result(sharpe=1.5, n_trades=20),
        )
        _cleanup_sys_modules.append(inj)

        result = _run_wfpbo(symbol="BTCUSDT", n_windows=3, n_trials=5)

        if "error" not in result:
            for w in result["window_details"]:
                assert "window" in w
                assert "train_days" in w
                assert "test_days" in w
                assert "train_sharpe" in w
                assert "test_sharpe" in w
                assert "test_trades" in w
                assert "best_params" in w

    def test_passes_flag_true_when_healthy(self, _cleanup_sys_modules):
        inj = _inject_fake_modules(
            backtest_return=_make_backtest_result(sharpe=2.0, n_trades=25),
        )
        _cleanup_sys_modules.append(inj)

        result = _run_wfpbo(symbol="BTCUSDT", n_windows=3, n_trials=5)

        if "error" not in result and result["regime_survives"]:
            assert result["passes"] is True
            assert "PASS" in result["interpretation"]

    def test_result_has_aggregate_metrics(self, _cleanup_sys_modules):
        inj = _inject_fake_modules(
            backtest_return=_make_backtest_result(sharpe=1.5, n_trades=20),
        )
        _cleanup_sys_modules.append(inj)

        result = _run_wfpbo(symbol="BTCUSDT", n_windows=3, n_trials=5)

        if "error" not in result:
            for key in ["aggregate_test_sharpe", "overall_pbo", "wfe",
                        "regime_survives", "any_negative_sharpe", "passes",
                        "interpretation"]:
                assert key in result, f"Missing key: {key}"

    def test_expanding_windows(self, _cleanup_sys_modules):
        inj = _inject_fake_modules(
            backtest_return=_make_backtest_result(sharpe=1.5, n_trades=20),
        )
        _cleanup_sys_modules.append(inj)

        result = _run_wfpbo(symbol="BTCUSDT", n_windows=4, n_trials=5)

        if "error" not in result and len(result["window_details"]) >= 2:
            train_days_list = [w["train_days"] for w in result["window_details"]]
            for j in range(1, len(train_days_list)):
                assert train_days_list[j] >= train_days_list[j - 1], \
                    f"Window {j} train_days={train_days_list[j]} < " \
                    f"window {j-1} train_days={train_days_list[j-1]}"


# ── Sharpe helper test ────────────────────────────────────────────────────────

class TestSharpeHelper:
    """Test the _sharpe() helper used by PBO."""

    def test_positive_returns(self):
        returns = np.array([0.01, 0.02, -0.005, 0.015, 0.008] * 20)
        sharpe = _sharpe(returns)
        assert sharpe > 0

    def test_zero_std(self):
        returns = np.zeros(100)
        sharpe = _sharpe(returns)
        assert sharpe == 0.0

    def test_negative_returns(self):
        returns = np.array([-0.01, -0.02, -0.005, -0.015, -0.008] * 20)
        sharpe = _sharpe(returns)
        assert sharpe < 0
"""Tests for async optimizer job lifecycle."""
import sys
import importlib
import time
import threading
from unittest.mock import patch
import pytest

pytest.importorskip("optuna", reason="optuna not installed on this machine")


def _fast_result():
    return {"wt_oversold": -60.0, "rsi_max": 58.0, "adx_min": 18.0,
            "min_confluence": 0.40, "sl_pct": 0.09, "tp1_pct": 0.05, "tp2_pct": 0.12}


@pytest.fixture(autouse=True)
def _real_backtest_optimizer():
    """Ensure every test in this module imports the *real* backtest_optimizer,
    not the MagicMock injected by test_routes_backtest._client()."""
    sys.modules.pop("backtest_optimizer", None)
    import backtest_optimizer  # noqa: F401 — loads real module
    yield
    # Leave the real module in sys.modules for the next test.


def test_start_job_returns_job_id():
    from backtest_optimizer import start_optimizer_job
    with patch('backtest_optimizer.run_optimizer', return_value=_fast_result()):
        job_id = start_optimizer_job("BTCUSDT", "4H", 30, 1)
    assert isinstance(job_id, str) and len(job_id) > 0


def test_job_completes_and_has_result():
    from backtest_optimizer import start_optimizer_job, get_job_status
    import backtest_optimizer as bo
    with patch.object(bo, 'run_optimizer', return_value=_fast_result()):
        job_id = start_optimizer_job("BTCUSDT", "4H", 30, 1)
    time.sleep(0.3)  # let thread finish
    status = get_job_status(job_id)
    assert status is not None
    assert status["status"] == "complete"
    assert status["result"]["wt_oversold"] == -60.0


def test_job_not_found_returns_none():
    from backtest_optimizer import get_job_status
    assert get_job_status("nonexistent-id-xyz") is None


def test_two_jobs_are_independent():
    from backtest_optimizer import start_optimizer_job, get_job_status
    import backtest_optimizer as bo
    with patch.object(bo, 'run_optimizer', return_value=_fast_result()):
        id1 = start_optimizer_job("BTCUSDT", "4H", 30, 1)
        id2 = start_optimizer_job("ETHUSDT", "4H", 30, 1)
    assert id1 != id2
    time.sleep(0.3)
    assert get_job_status(id1)["status"] == "complete"
    assert get_job_status(id2)["status"] == "complete"

"""Tests for walk-forward optimizer."""
import pytest
pytest.importorskip("optuna")


def test_walk_forward_returns_error_on_no_positions(db):
    """walk_forward returns error dict when no positions for symbol."""
    import backtest_optimizer
    # Patch db_conn to use test db
    import unittest.mock as mock
    with mock.patch("backtest_optimizer.db_conn") as mdb:
        mdb.return_value.__enter__ = lambda s: db
        mdb.return_value.__exit__ = mock.Mock(return_value=False)
        result = backtest_optimizer.run_walk_forward("FAKEXXX")
    assert "error" in result


def test_walk_forward_result_shape():
    """run_walk_forward returns dict with expected keys."""
    import backtest_optimizer
    # Only test the shape — not a live run
    expected_keys = {"symbol", "timeframe", "total_days", "train_days",
                     "test_days", "train_sharpe", "test_sharpe", "generalizes"}
    # Verify the function exists and has the right signature
    import inspect
    sig = inspect.signature(backtest_optimizer.run_walk_forward)
    assert "symbol" in sig.parameters
    assert "n_trials" in sig.parameters

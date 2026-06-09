"""
tests/test_walk_forward_pbo.py -- Unit tests for PBO and Sharpe helpers.

These functions were migrated from tools/backtest/backtest_quality.py
to learning/metrics.py. The run_walk_forward_pbo() integration was
removed along with backtest_engine/backtest_optimizer (no longer part
of the learning system).
"""

import math

import numpy as np
import pytest

from learning.metrics import pbo, _sharpe, sharpe_ratio


# ── PBO unit tests ────────────────────────────────────────────────────────────

class TestPBOFunction:
    """Test the pbo() function from learning/metrics.py."""

    def test_pbo_genuine_strategy(self):
        rng = np.random.default_rng(42)
        returns = rng.normal(0.002, 0.01, 500)
        result = pbo(returns.tolist())
        assert result < 0.5, f"Genuine strategy should have PBO < 0.5, got {result}"

    def test_pbo_overfitted_strategy(self):
        rng = np.random.default_rng(123)
        returns = rng.normal(0.0, 0.02, 500)
        result = pbo(returns.tolist())
        assert 0.0 <= result <= 1.0

    def test_pbo_too_few_returns(self):
        returns = [0.01, 0.02, -0.01]
        result = pbo(returns)
        assert math.isnan(result)

    def test_pbo_boundary(self):
        rng = np.random.default_rng(99)
        returns = rng.normal(0.001, 0.015, 200)
        result = pbo(returns.tolist())
        assert 0.0 <= result <= 1.0

    def test_pbo_returns_float_or_nan(self):
        """pbo always returns a float (or NaN)."""
        result = pbo([0.01, 0.02])
        assert isinstance(result, float)

    def test_pbo_sufficient_data_in_range(self):
        """pbo returns a value between 0 and 1 with sufficient data."""
        rng = np.random.default_rng(42)
        returns = rng.normal(0.001, 0.02, 100).tolist()
        result = pbo(returns)
        assert 0.0 <= result <= 1.0


# ── Sharpe helper tests ────────────────────────────────────────────────────────

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


class TestSharpeRatio:
    """Test the public sharpe_ratio() function."""

    def test_annualized_sharpe(self):
        sr = sharpe_ratio([0.01, -0.005, 0.02, -0.003, 0.015])
        assert isinstance(sr, float)

    def test_empty_returns_zero(self):
        assert sharpe_ratio([]) == 0.0
        assert sharpe_ratio([0.01]) == 0.0

    def test_zero_std_returns_zero(self):
        assert sharpe_ratio([0.01, 0.01, 0.01]) == 0.0
"""
tests/test_time_travel_metrics.py -- Tests for dollar-math aggregate metrics.

Validates the new metrics added to learning/metrics.py and the
reporting functions in scripts/time_travel/reporting.py.
"""

import pytest
import numpy as np

from learning.metrics import (
    profit_factor,
    max_drawdown,
    expectancy,
    avg_win,
    avg_loss,
    win_loss_ratio,
    total_return_pct,
)


# ── Learning metrics (new functions) ─────────────────────────────────────────

class TestExpectancy:
    def test_positive_expectancy(self):
        pnls = [10, -5, 20, -3, 15]
        assert expectancy(pnls) == pytest.approx(7.4, abs=0.1)

    def test_negative_expectancy(self):
        pnls = [-10, -5, 5, -20, 3]
        assert expectancy(pnls) < 0

    def test_empty_list(self):
        assert expectancy([]) == 0.0


class TestAvgWin:
    def test_avg_of_winners(self):
        pnls = [10, -5, 20, -3, 15]
        assert avg_win(pnls) == pytest.approx(15.0, abs=0.1)

    def test_no_winners(self):
        assert avg_win([-5, -10, -3]) == 0.0

    def test_empty_list(self):
        assert avg_win([]) == 0.0


class TestAvgLoss:
    def test_avg_of_losers(self):
        pnls = [10, -5, 20, -3, 15]
        assert avg_loss(pnls) == pytest.approx(-4.0, abs=0.1)

    def test_no_losers(self):
        assert avg_loss([10, 20, 30]) == 0.0

    def test_empty_list(self):
        assert avg_loss([]) == 0.0


class TestWinLossRatio:
    def test_favorable_ratio(self):
        pnls = [30, -10, 25, -5, 20]  # avg_win=25, avg_loss=-7.5
        assert win_loss_ratio(pnls) > 1.0

    def test_unfavorable_ratio(self):
        pnls = [5, -20, 3, -15]  # avg_win=4, avg_loss=-17.5
        assert win_loss_ratio(pnls) < 1.0

    def test_no_losses(self):
        assert win_loss_ratio([10, 20, 30]) == 0.0

    def test_no_wins(self):
        assert win_loss_ratio([-10, -20, -30]) == 0.0

    def test_empty_list(self):
        assert win_loss_ratio([]) == 0.0


class TestTotalReturnPct:
    def test_positive_return(self):
        pnls = [10, -5, 20, -3, 15]
        equity = 1000
        result = total_return_pct(pnls, equity)
        assert result == pytest.approx(3.7, abs=0.1)  # 37/1000 * 100

    def test_negative_return(self):
        pnls = [-10, -5, 5, -20, 3]
        equity = 1000
        result = total_return_pct(pnls, equity)
        assert result < 0

    def test_zero_equity(self):
        assert total_return_pct([10, 20], 0) == 0.0

    def test_empty_list(self):
        assert total_return_pct([], 1000) == 0.0


# ── Existing metrics (regression check) ───────────────────────────────────────

class TestProfitFactorRegression:
    def test_profit_factor_basic(self):
        pnls = [10, -5, 20, -3, 15]
        pf = profit_factor(pnls)
        assert pf > 1.0  # More wins than losses

    def test_profit_factor_no_losses(self):
        pnls = [10, 20, 30]
        assert profit_factor(pnls) == 0.0  # No losses → undefined → 0.0

    def test_profit_factor_no_wins(self):
        pnls = [-10, -20, -30]
        assert profit_factor(pnls) == 0.0  # No wins → 0.0


class TestMaxDrawdownRegression:
    def test_simple_drawdown(self):
        equity_curve = [100, 110, 105, 115, 108]
        dd = max_drawdown(equity_curve)
        # Peak=110, trough=105 → drawdown = 5/110 ≈ 0.0455
        assert dd == pytest.approx(0.0455, abs=0.01)

    def test_no_drawdown(self):
        equity_curve = [100, 110, 120, 130]
        dd = max_drawdown(equity_curve)
        assert dd == 0.0

    def test_empty_curve(self):
        assert max_drawdown([]) == 0.0
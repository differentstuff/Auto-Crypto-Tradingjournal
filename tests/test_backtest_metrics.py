"""Pure unit tests for backtest_metrics.py — no DB, no API, no network."""


def test_profit_factor_2x():
    """2 wins of 10%, 1 loss of 10% -> PF = 2.0."""
    from backtest_metrics import profit_factor
    pnls = [0.10, 0.10, -0.10]
    assert profit_factor(pnls) == 2.0


def test_profit_factor_no_losses():
    """All wins -> profit_factor returns 0.0 (guard for division by zero)."""
    from backtest_metrics import profit_factor
    assert profit_factor([0.10, 0.20]) == 0.0


def test_profit_factor_all_losses():
    """All losses -> PF = 0.0."""
    from backtest_metrics import profit_factor
    assert profit_factor([-0.10, -0.05]) == 0.0


def test_max_drawdown_50pct():
    """Equity [100, 150, 75] -> drawdown = 0.50 (75 is 50% below 150 peak)."""
    from backtest_metrics import max_drawdown
    result = max_drawdown([100.0, 150.0, 75.0])
    assert abs(result - 0.50) < 1e-9


def test_max_drawdown_zero_on_monotonic_equity():
    """Monotonically rising equity -> drawdown = 0.0."""
    from backtest_metrics import max_drawdown
    result = max_drawdown([100.0, 110.0, 120.0, 130.0])
    assert result == 0.0


def test_max_drawdown_empty():
    """Empty list -> drawdown = 0.0."""
    from backtest_metrics import max_drawdown
    assert max_drawdown([]) == 0.0


def test_sharpe_zero_std():
    """All returns identical -> std = 0 -> returns 0.0."""
    from backtest_metrics import sharpe_ratio
    assert sharpe_ratio([0.05, 0.05, 0.05]) == 0.0


def test_sharpe_positive_on_all_wins():
    """Positive returns with variance -> Sharpe > 0."""
    from backtest_metrics import sharpe_ratio
    returns = [0.10, 0.05, 0.08, 0.12, 0.07]
    assert sharpe_ratio(returns) > 0


def test_sharpe_single_value():
    """Single return value -> returns 0.0 (can't compute std)."""
    from backtest_metrics import sharpe_ratio
    assert sharpe_ratio([0.10]) == 0.0


def test_sortino_positive_on_all_wins():
    """All positive returns -> Sortino > 0."""
    from backtest_metrics import sortino_ratio
    returns = [0.10, 0.05, 0.08]
    assert sortino_ratio(returns) > 0


def test_sortino_zero_downside():
    """No negative returns -> downside std = 0 -> returns 0.0."""
    from backtest_metrics import sortino_ratio
    assert sortino_ratio([0.10, 0.10]) == 0.0

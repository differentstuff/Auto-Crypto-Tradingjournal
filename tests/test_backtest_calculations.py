"""Tests that backtest indicators match live chart_indicators.py formulas."""
import pandas as pd
import numpy as np
import pytest


def _sample_df(n=100):
    np.random.seed(42)
    close = np.random.rand(n) * 100 + 100
    opens = close + np.random.randn(n) * 2
    df = pd.DataFrame({
        'open':   opens,
        'close':  close,
        'volume': np.random.rand(n) * 1000 + 500,
    })
    df['high'] = df[['open', 'close']].max(axis=1) + abs(np.random.rand(n)) + 0.01
    df['low']  = df[['open', 'close']].min(axis=1) - abs(np.random.rand(n)) - 0.01
    return df


def test_wavetrend_rolling4():
    """wt2 must be a 4-period rolling mean of wt1 (not 3)."""
    from backtest_engine import _compute_signals, BacktestParams
    df = _sample_df()
    params = BacktestParams()
    result = _compute_signals(df, params)
    assert 'wt1' in result.columns
    assert 'wt2' in result.columns
    expected_wt2 = result['wt1'].rolling(4, min_periods=1).mean()
    pd.testing.assert_series_equal(
        result['wt2'].round(6),
        expected_wt2.round(6),
        check_names=False
    )


def test_sharpe_uses_2190_periods():
    """Sharpe annualization must use 2190 (4H x 24/7 crypto)."""
    import inspect
    from backtest_metrics import sharpe_ratio
    sig = inspect.signature(sharpe_ratio)
    assert sig.parameters['periods_per_year'].default == 2190, \
        "periods_per_year should be 2190 for 4H 24/7 crypto"


def test_sortino_uses_2190_periods():
    """Sortino annualization must use 2190."""
    import inspect
    from backtest_metrics import sortino_ratio
    sig = inspect.signature(sortino_ratio)
    assert sig.parameters['periods_per_year'].default == 2190

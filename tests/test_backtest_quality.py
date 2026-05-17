# tests/test_backtest_quality.py
import pytest
import numpy as np


def _make_data(n=200, seed=1):
    np.random.seed(seed)
    prices  = np.cumprod(1 + np.random.normal(0.001, 0.02, n)) * 100
    signals = np.where(np.random.random(n) > 0.5, 1.0, -1.0)
    return prices, signals


def test_quality_returns_required_keys():
    prices, signals = _make_data()
    from backtest_quality import run_quality_check
    result = run_quality_check(prices, signals)
    assert isinstance(result, dict)
    for key in ("sharpe", "deflated_sharpe", "pbo", "bootstrap_ci"):
        assert key in result, f"missing key: {key}"


def test_pbo_in_range():
    prices, signals = _make_data()
    from backtest_quality import run_quality_check
    result = run_quality_check(prices, signals)
    pbo = result["pbo"]
    if pbo is not None and pbo == pbo:   # not NaN
        assert 0.0 <= pbo <= 1.0


def test_bootstrap_ci_ordered():
    prices, signals = _make_data()
    from backtest_quality import run_quality_check
    result = run_quality_check(prices, signals)
    ci = result["bootstrap_ci"]
    if ci and len(ci) == 2 and ci[0] == ci[0]:   # not NaN
        assert ci[0] <= ci[1]


def test_empty_input_returns_error():
    from backtest_quality import run_quality_check
    result = run_quality_check(np.array([]), np.array([]))
    assert "error" in result

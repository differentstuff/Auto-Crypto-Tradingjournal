"""
backtest_metrics.py — Risk-adjusted performance metrics for the embedded backtester.
Metric formulas adapted from Freqtrade optimize/optimize_reports.py (GPL-3.0).
"""
import numpy as np


def sharpe_ratio(returns: list, periods_per_year: int = 2190) -> float:
    """
    Annualised Sharpe ratio.
    periods_per_year=2190 for 4H candles (6 candles/day x 365 days, crypto trades 24/7).
    Returns 0.0 when std=0 or fewer than 2 returns.
    """
    r = np.array(returns, dtype=float)
    if len(r) < 2:
        return 0.0
    std = r.std()
    if std < 1e-10:  # tolerance for floating-point precision
        return 0.0
    return float(r.mean() / std * (periods_per_year ** 0.5))


def sortino_ratio(returns: list, periods_per_year: int = 2190) -> float:
    """
    Annualised Sortino ratio (penalises downside deviation only).
    periods_per_year=2190 for 4H candles (6 candles/day x 365 days, crypto trades 24/7).
    Returns 0.0 when no variance or fewer than 2 returns.
    When there are no negative returns, uses full std as fallback.
    """
    r = np.array(returns, dtype=float)
    if len(r) < 2:
        return 0.0
    downside = r[r < 0]
    if len(downside) == 0:
        # No downside: use full std as denominator
        full_std = r.std()
        if full_std < 1e-10:
            return 0.0
        return float(r.mean() / full_std * (periods_per_year ** 0.5))
    downside_std = downside.std()
    if downside_std < 1e-10:
        return 0.0
    return float(r.mean() / downside_std * (periods_per_year ** 0.5))


def max_drawdown(equity_curve: list) -> float:
    """
    Maximum peak-to-trough drawdown as a positive fraction (0.0 to 1.0).
    Pattern adapted from Freqtrade optimize/optimize_reports.py (GPL-3.0).
    """
    if not equity_curve:
        return 0.0
    eq = np.array(equity_curve, dtype=float)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    return float(abs(dd.min()))


def profit_factor(pnls: list) -> float:
    """
    Gross profit / gross loss. Returns 0.0 when no losses or no wins.
    """
    wins  = sum(p for p in pnls if p > 0)
    loses = sum(abs(p) for p in pnls if p < 0)
    if not loses or not wins:
        return 0.0
    return round(wins / loses, 2)

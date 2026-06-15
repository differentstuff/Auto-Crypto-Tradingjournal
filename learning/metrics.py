"""
learning/metrics.py -- Pure performance metrics for learning modules.

Extracted from tools/backtest/backtest_metrics.py and backtest_quality.py
to provide dependency-free metric functions for Karpathy, Hyperopt, and
any future learning module.

All functions operate on plain lists/arrays of pnl or return values.
No database, no enzyme, no exchange dependencies.
"""

from __future__ import annotations

import numpy as np


def profit_factor(pnls: list) -> float:
    """Gross profit / gross loss. Returns 0.0 when no losses or no wins."""
    wins = sum(p for p in pnls if p > 0)
    loses = sum(abs(p) for p in pnls if p < 0)
    if not loses or not wins:
        return 0.0
    return round(wins / loses, 3)


def sharpe_ratio(returns: list, periods_per_year: int = 252) -> float:
    """
    Annualised Sharpe ratio.

    periods_per_year=252 for daily returns (crypto 24/7 could use 365).
    Returns 0.0 when std=0 or fewer than 2 returns.
    """
    r = np.array(returns, dtype=float)
    if len(r) < 2:
        return 0.0
    std = r.std()
    if std < 1e-10:
        return 0.0
    return float(r.mean() / std * (periods_per_year ** 0.5))


def sortino_ratio(returns: list, periods_per_year: int = 252) -> float:
    """
    Annualised Sortino ratio (penalises downside deviation only).

    Returns 0.0 when no variance or fewer than 2 returns.
    When there are no negative returns, uses full std as fallback.
    """
    r = np.array(returns, dtype=float)
    if len(r) < 2:
        return 0.0
    downside = r[r < 0]
    if len(downside) == 0:
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
    """
    if not equity_curve:
        return 0.0
    eq = np.array(equity_curve, dtype=float)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    return float(abs(dd.min()))


def pbo(returns: list, n_splits: int = 8) -> float:
    """
    CSCV-based Probability of Backtest Overfitting.

    Reference: Bailey, Borwein, Lopez de Prado & Zhu (2014).
    Returns a float in [0, 1]. Higher = more likely overfitted.
    Returns NaN if insufficient data.

    How it works:
      1. Split returns into n_splits equal chunks.
      2. For every combination of chunks as "in-sample" vs "out-of-sample":
         if the out-of-sample Sharpe is negative, count as overfit.
      3. PBO = overfit_count / total_combos.
    """
    r = np.array(returns, dtype=float)
    t = len(r)
    if t < n_splits * 5:
        return float("nan")

    size = t // n_splits
    chunks = [r[i * size:(i + 1) * size] for i in range(n_splits)]

    from itertools import combinations

    overfit, total = 0, 0
    for k in range(1, n_splits):
        for combo in combinations(range(n_splits), k):
            oos = [i for i in range(n_splits) if i not in combo]
            if not oos:
                continue
            oos_sharpe = _sharpe(np.concatenate([chunks[i] for i in oos]))
            if oos_sharpe < 0:
                overfit += 1
            total += 1

    return round(overfit / total, 4) if total else float("nan")


def _sharpe(returns: np.ndarray) -> float:
    """Internal Sharpe for PBO computation (non-annualized)."""
    std = returns.std()
    return float(returns.mean() / std) if std > 0 else 0.0


# ── Dollar-math metrics (for backtest evaluation) ────────────────────────────
# These functions operate on lists of P&L values in USD.
# Used by time_travel backtest and any future dollar-math reporting.

def expectancy(pnls: list) -> float:
    """Average P&L per trade (expectancy).

    Returns 0.0 for empty lists.
    """
    if not pnls:
        return 0.0
    return round(float(np.mean(pnls)), 2)


def avg_win(pnls: list) -> float:
    """Average winning P&L.

    Returns 0.0 if no winners.
    """
    wins = [p for p in pnls if p > 0]
    if not wins:
        return 0.0
    return round(float(np.mean(wins)), 2)


def avg_loss(pnls: list) -> float:
    """Average losing P&L (negative value).

    Returns 0.0 if no losers.
    """
    losses = [p for p in pnls if p < 0]
    if not losses:
        return 0.0
    return round(float(np.mean(losses)), 2)


def win_loss_ratio(pnls: list) -> float:
    """Ratio of avg_win to abs(avg_loss).

    >1.0 = winners bigger than losers (favorable asymmetry).
    <1.0 = losers bigger than winners (unfavorable).
    Returns 0.0 if no losses or no wins.
    """
    aw = avg_win(pnls)
    al = avg_loss(pnls)
    if al == 0 or aw == 0:
        return 0.0
    return round(abs(aw / al), 3)


def total_return_pct(pnls: list, equity: float) -> float:
    """Total return as percentage of initial equity.

    Args:
        pnls: List of net P&L values in USD
        equity: Initial equity in USDT

    Returns:        Total return percentage (e.g., 12.4 for +12.4%)
    """
    if not equity or not pnls:
        return 0.0
    return round(sum(pnls) / equity * 100, 2)
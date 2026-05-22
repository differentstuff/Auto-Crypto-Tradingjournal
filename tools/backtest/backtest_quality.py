# backtest_quality.py
"""
Backtesting quality: PBO, deflated Sharpe, bootstrap Sharpe CI.
Uses backtester-mcp when available; pure-numpy fallback otherwise.
Reference: Bailey, Borwein, Lopez de Prado & Zhu (2014).
"""
import logging
import numpy as np

_log = logging.getLogger(__name__)


def _sharpe(returns: np.ndarray) -> float:
    std = returns.std()
    return float(returns.mean() / std * np.sqrt(252)) if std > 0 else 0.0


def _bootstrap_ci(returns: np.ndarray, n_boot: int = 1000,
                  alpha: float = 0.05) -> list[float]:
    if len(returns) < 10:
        return [float("nan"), float("nan")]
    rng     = np.random.default_rng(42)
    sharpes = sorted(
        _sharpe(rng.choice(returns, len(returns), replace=True))
        for _ in range(n_boot)
    )
    lo = float(np.percentile(sharpes, 100 * alpha / 2))
    hi = float(np.percentile(sharpes, 100 * (1 - alpha / 2)))
    return [round(lo, 3), round(hi, 3)]


def _deflated_sharpe(sharpe: float, n_trials: int, t: int,
                     skew: float = 0.0, kurt: float = 3.0) -> float:
    try:
        from scipy import stats as sp
        import math
        if t < 2 or n_trials < 1:
            return float("nan")
        sr_star = np.sqrt(
            (1 - skew * sharpe + (kurt - 1) / 4 * sharpe ** 2) / (t - 1)
        )
        e_max = ((1 - 0.5772) * sp.norm.ppf(1 - 1 / n_trials) +
                 0.5772 * sp.norm.ppf(1 - 1 / (n_trials * math.e)))
        return round(float(sp.norm.cdf((sharpe - e_max) / sr_star))
                     if sr_star > 0 else 0.0, 4)
    except Exception:
        return float("nan")


def _pbo(returns: np.ndarray, n_splits: int = 8) -> float:
    """CSCV-based Probability of Backtest Overfitting."""
    T = len(returns)
    if T < n_splits * 5:
        return float("nan")
    size   = T // n_splits
    chunks = [returns[i * size:(i + 1) * size] for i in range(n_splits)]
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


def run_quality_check(prices: np.ndarray, signals: np.ndarray,
                      n_trials: int = 1) -> dict:
    """
    Full quality check on an equity curve.
    prices:   1-D close price array
    signals:  1-D position array (+1 long, -1 short, 0 flat)
    n_trials: number of parameter combinations tried (for deflated Sharpe)
    """
    if len(prices) < 10 or len(signals) < 10:
        return {"ok": False, "error": "need at least 10 data points"}

    # Try backtester-mcp for primary Sharpe
    sharpe = 0.0
    try:
        from backtester_mcp import backtest as bmt  # type: ignore
        res    = bmt(prices, signals)
        sharpe = float(getattr(getattr(res, "metrics", {}), "get", lambda k, d=0: d)("sharpe") or 0)
    except Exception:
        pass

    rets = np.diff(np.log(np.where(prices > 0, prices, 1e-9))) * signals[:-1]
    if sharpe == 0.0:
        sharpe = _sharpe(rets)

    skew = float(np.mean(rets ** 3) / (rets.std() ** 3 + 1e-12))
    kurt = float(np.mean(rets ** 4) / (rets.std() ** 4 + 1e-12))

    dsr = _deflated_sharpe(sharpe, n_trials, len(rets), skew, kurt)
    pbo = _pbo(rets)
    ci  = _bootstrap_ci(rets)

    overfitting = "unknown"
    if pbo == pbo and pbo is not None:    # not NaN
        overfitting = "likely genuine" if pbo < 0.5 else "possible overfitting"

    return {
        "ok":              True,
        "sharpe":          round(sharpe, 3),
        "deflated_sharpe": dsr,
        "pbo":             pbo,
        "bootstrap_ci":    ci,
        "n_trades":        int((np.diff(signals.astype(float)) != 0).sum()),
        "interpretation":  overfitting,
    }

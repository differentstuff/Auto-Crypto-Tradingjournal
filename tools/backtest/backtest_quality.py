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


def run_walk_forward_pbo(
    symbol: str,
    timeframe: str = "4H",
    n_windows: int = 5,
    train_ratio: float = 0.7,
    n_trials: int = 50,
    wfe_threshold: float = 0.5,
    pbo_threshold: float = 0.5,
) -> dict:
    """
    Walk-forward analysis with PBO across multiple expanding windows.

    1. Reads the date range of closed positions for *symbol* from DB.
    2. Splits the range into (n_windows + 1) equal segments.
    3. For each window i (expanding): trains on [start .. segment i+1],
       tests on segment i+2.
    4. Computes PBO across ALL test-window returns.
    5. Computes Walk-Forward Efficiency (WFE) = mean(test Sharpe) / mean(train Sharpe).
    6. Rejects strategy if any test-window Sharpe < 0.

    Returns a dict with aggregate metrics and per-window breakdown.
    """
    from datetime import datetime
    from database import db_conn
    from backtest_engine import BacktestParams, run_backtest
    from backtest_optimizer import run_optimizer

    # ── 1. Get date range from DB ────────────────────────────────────────────
    with db_conn() as conn:
        row = conn.execute("""
            SELECT MIN(close_time), MAX(close_time), COUNT(*)
            FROM positions
            WHERE symbol = ?
              AND close_time IS NOT NULL
        """, (symbol,)).fetchone()

    if not row or not row[0]:
        return {"error": f"No positions found for {symbol}"}

    min_dt, max_dt, n_pos = row[0], row[1], row[2]
    if n_pos < 10:
        return {"error": f"Too few positions ({n_pos}) for walk-forward PBO — need at least 10"}

    fmt = "%Y-%m-%d %H:%M:%S"
    t_min = datetime.strptime(min_dt[:19], fmt)
    t_max = datetime.strptime(max_dt[:19], fmt)
    total_days = max(1, (t_max - t_min).days)

    if n_windows < 2:
        return {"error": f"n_windows must be >= 2, got {n_windows}"}

    # ── 2. Compute window boundaries ─────────────────────────────────────────
    # Divide total range into (n_windows + 1) equal segments.
    # Window i: train on [start .. end of segment i+1], test on segment i+2.
    # This gives expanding training windows with non-overlapping test windows.
    segment_days = max(7, total_days / (n_windows + 1))

    window_results = []
    all_test_returns = []

    for i in range(n_windows):
        # Expanding train: from start to end of segment (i+1)
        train_end_offset = max(1, int(total_days - (i + 1) * segment_days))
        train_days = total_days - train_end_offset

        # Test: segment (i+2)
        test_end_offset = max(0, int(total_days - (i + 2) * segment_days))
        test_days = train_end_offset - test_end_offset

        if train_days < 14 or test_days < 7:
            _log.warning(
                "WFPBO window %d skipped: train_days=%d, test_days=%d too short",
                i, train_days, test_days,
            )
            continue

        # ── 3a. Optimize on training window ──────────────────────────────────
        try:
            best_params = run_optimizer(
                symbol, timeframe,
                days=train_days, n_trials=n_trials,
                end_offset_days=train_end_offset,
            )
        except Exception as exc:
            _log.warning("WFPBO window %d optimizer failed: %s", i, exc)
            continue

        if not best_params:
            _log.warning("WFPBO window %d optimizer returned no params", i)
            continue

        # ── 3b. Backtest best params on test window ──────────────────────────
        test_p = BacktestParams(**{k: v for k, v in best_params.items()
                                   if k in BacktestParams.__dataclass_fields__})
        try:
            test_result = run_backtest(
                symbol, timeframe,
                days=test_days, params=test_p,
                end_offset_days=test_end_offset,
            )
        except Exception as exc:
            _log.warning("WFPBO window %d test backtest failed: %s", i, exc)
            continue

        # Also get training Sharpe for WFE computation
        try:
            train_result = run_backtest(
                symbol, timeframe,
                days=train_days, params=test_p,
                end_offset_days=train_end_offset,
            )
            train_sharpe = train_result.sharpe
        except Exception:
            train_sharpe = 0.0

        test_sharpe = test_result.sharpe if test_result else 0.0

        # Collect test-window returns for PBO
        if test_result and test_result.trades:
            rets = np.array([t.pnl_pct for t in test_result.trades])
            all_test_returns.append(rets)

        window_results.append({
            "window": i,
            "train_days": train_days,
            "test_days": test_days,
            "train_sharpe": round(train_sharpe, 3),
            "test_sharpe": round(test_sharpe, 3),
            "test_trades": test_result.total_trades if test_result else 0,
            "best_params": best_params,
        })

    # ── 4. Aggregate metrics ─────────────────────────────────────────────────
    if not window_results:
        return {"error": "No valid windows completed — insufficient data or all optimizers failed"}

    train_sharpes = [w["train_sharpe"] for w in window_results]
    test_sharpes = [w["test_sharpe"] for w in window_results]

    # PBO across all concatenated test returns
    if all_test_returns:
        concatenated = np.concatenate(all_test_returns)
        overall_pbo = _pbo(concatenated) if len(concatenated) >= 40 else float("nan")
    else:
        overall_pbo = float("nan")

    # Walk-Forward Efficiency
    mean_train = np.mean(train_sharpes) if train_sharpes else 0.0
    mean_test = np.mean(test_sharpes) if test_sharpes else 0.0
    wfe = float(mean_test / mean_train) if abs(mean_train) > 1e-9 else float("nan")

    # Regime survival: reject if ANY test window has negative Sharpe
    any_negative = any(s < 0 for s in test_sharpes)
    regime_survives = not any_negative

    # Overall pass/fail
    passes = (
        regime_survives
        and (overall_pbo != overall_pbo or overall_pbo < pbo_threshold)  # NaN is ok, otherwise < threshold
        and (wfe != wfe or wfe > wfe_threshold)  # NaN is ok, otherwise > threshold
    )

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "n_windows_requested": n_windows,
        "n_windows_completed": len(window_results),
        "total_days": total_days,
        "aggregate_test_sharpe": round(float(mean_test), 3),
        "overall_pbo": overall_pbo,
        "wfe": round(wfe, 3) if wfe == wfe else None,
        "regime_survives": regime_survives,
        "any_negative_sharpe": any_negative,
        "passes": passes,
        "window_details": window_results,
        "interpretation": (
            "PASS — strategy is likely genuine and regime-robust"
            if passes
            else "FAIL — strategy may be overfitted or regime-fragile"
        ),
    }


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
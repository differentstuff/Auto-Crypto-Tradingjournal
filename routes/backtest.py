"""routes/backtest.py — Backtest and optimizer API endpoints."""
from flask import Blueprint, request

from backtest_engine import BacktestParams, run_backtest
from helpers import _ok, _err

bp = Blueprint("backtest", __name__)


@bp.post("/api/backtest/run")
def backtest_run():
    body      = request.get_json(silent=True) or {}
    symbol    = body.get("symbol")
    timeframe = body.get("timeframe", "4H")

    if not symbol:
        return _err("symbol is required")

    try:
        days = min(int(body.get("days", 180)), 365)
        params_raw = body.get("params") or {}
        params = BacktestParams(
            sl_pct         = float(params_raw.get("sl_pct",         0.10)),
            tp1_pct        = float(params_raw.get("tp1_pct",        0.05)),
            tp2_pct        = float(params_raw.get("tp2_pct",        0.10)),
            min_confluence = float(params_raw.get("min_confluence",  0.33)),
            wt_oversold    = float(params_raw.get("wt_oversold",   -53.0)),
            rsi_max        = float(params_raw.get("rsi_max",        65.0)),
            adx_min        = float(params_raw.get("adx_min",        15.0)),
        )
    except (ValueError, TypeError):
        return _err("invalid parameter value")

    result = run_backtest(symbol, timeframe, days, params)

    return _ok({
        "symbol":        symbol,
        "timeframe":     timeframe,
        "days":          days,
        "total_trades":  result.total_trades,
        "win_rate":      result.win_rate,
        "profit_factor": result.profit_factor,
        "sharpe":        round(result.sharpe,       2),
        "sortino":       round(result.sortino,      2),
        "max_drawdown":  round(result.max_drawdown, 4),
    })


@bp.get("/api/backtest/optimize")
def backtest_optimize():
    symbol    = request.args.get("symbol",    "BTCUSDT")
    timeframe = request.args.get("timeframe", "4H")

    try:
        n_trials = min(int(request.args.get("n_trials", 50)), 200)
        days     = min(int(request.args.get("days",     180)), 365)
    except (ValueError, TypeError):
        return _err("invalid parameter value")

    from backtest_optimizer import run_optimizer
    best = run_optimizer(symbol, timeframe, days, n_trials)
    return _ok(best)

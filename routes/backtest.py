"""routes/backtest.py — Backtest and optimizer API endpoints."""
import traceback

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
    """Start async optimizer run. Returns job_id immediately — poll /<job_id> for results."""
    symbol    = request.args.get("symbol",    "BTCUSDT")
    timeframe = request.args.get("timeframe", "4H")

    try:
        n_trials = min(int(request.args.get("n_trials", 50)), 200)
        days     = min(int(request.args.get("days",     180)), 365)
    except (ValueError, TypeError):
        return _err("invalid parameter value")

    from backtest_optimizer import start_optimizer_job
    job_id = start_optimizer_job(symbol, timeframe, days, n_trials)
    return _ok({"job_id": job_id, "status": "running", "symbol": symbol,
                "message": f"Optimizer started for {symbol} ({n_trials} trials)"})


@bp.get("/api/backtest/optimize/<job_id>")
def backtest_optimize_status(job_id: str):
    """Poll optimizer job. Returns status=running|complete|error + result when done."""
    from backtest_optimizer import get_job_status
    job = get_job_status(job_id)
    if job is None:
        return _err("job not found"), 404
    if job["status"] == "error":
        return _err("Optimizer failed — check server logs", 500)
    return _ok(job)


@bp.route("/api/backtest/walk-forward", methods=["POST"])
def api_walk_forward():
    """Start async walk-forward test. Body: {symbol, timeframe, n_trials}"""
    try:
        body      = request.get_json(force=True, silent=True) or {}
        symbol    = (body.get("symbol") or "BTCUSDT").upper().strip()
        timeframe = body.get("timeframe", "4H")
        n_trials  = int(body.get("n_trials", 30))
        n_trials  = max(10, min(n_trials, 100))
        import backtest_optimizer
        job_id = backtest_optimizer.start_walk_forward_job(symbol, timeframe, n_trials)
        return _ok({"job_id": job_id, "message": f"Walk-forward started for {symbol}"})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.get("/api/backtest/optimizer-history")
def api_optimizer_history():
    """Return last 5 optimizer runs."""
    try:
        from database import db_conn
        with db_conn() as conn:
            rows = conn.execute("""
                SELECT ts, symbol, timeframe, days, n_trials,
                       best_sharpe, best_params, duration_sec
                FROM optimizer_runs
                ORDER BY id DESC
                LIMIT 5
            """).fetchall()
        import json
        history = []
        for r in rows:
            row = dict(r)
            if row.get("best_params"):
                try:
                    row["best_params"] = json.loads(row["best_params"])
                except Exception:
                    pass
            history.append(row)
        return _ok({"runs": history})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)

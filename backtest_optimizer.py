"""
backtest_optimizer.py — Bayesian optimizer for backtester parameters using Optuna.
Maximises Sharpe ratio across the parameter search space defined in BacktestParams.
"""
import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Optional
import time as _time

import optuna

optuna.logging.set_verbosity(optuna.logging.WARNING)

from backtest_engine import BacktestParams, run_backtest

_logger = logging.getLogger(__name__)

# ── Async job registry ─────────────────────────────────────────────────────────
_jobs: dict = {}
_jobs_lock = threading.Lock()
_JOB_TTL = 3600  # evict jobs older than 1 hour


@dataclass
class _OptJob:
    job_id:  str
    symbol:  str
    status:  str = "running"  # running | complete | error
    result:  Optional[dict] = None
    error:   Optional[str] = None
    started: float = field(default_factory=_time.time)


def _evict_old_jobs() -> None:
    cutoff = _time.time() - _JOB_TTL
    stale = [jid for jid, j in _jobs.items() if j.started < cutoff]
    for jid in stale:
        del _jobs[jid]


def start_optimizer_job(symbol: str, timeframe: str = "4H",
                        days: int = 180, n_trials: int = 50) -> str:
    """Start an async optimizer run in a daemon thread. Returns job_id immediately."""
    job_id = str(uuid.uuid4())
    job = _OptJob(job_id=job_id, symbol=symbol)
    with _jobs_lock:
        _evict_old_jobs()
        _jobs[job_id] = job

    def _run():
        try:
            result = run_optimizer(symbol, timeframe, days, n_trials)
            with _jobs_lock:
                if job_id in _jobs:
                    _jobs[job_id].status = "complete"
                    _jobs[job_id].result = result
        except Exception:
            _logger.exception("Optimizer job %s failed", job_id)
            with _jobs_lock:
                if job_id in _jobs:
                    _jobs[job_id].status = "error"
                    _jobs[job_id].error = "Optimizer failed — check server logs"

    threading.Thread(target=_run, daemon=True, name=f"optuna-{job_id[:8]}").start()
    return job_id


def get_job_status(job_id: str) -> Optional[dict]:
    """Return job status dict or None if job_id not found."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return None
    return {"job_id": job.job_id, "symbol": job.symbol,
            "status": job.status, "result": job.result, "error": job.error}


def _objective(trial: optuna.Trial, symbol: str, timeframe: str, days: int) -> float:
    """Optuna objective: sample params -> run backtest -> return Sharpe (or penalty)."""
    params = BacktestParams(
        wt_oversold    = trial.suggest_float("wt_oversold",    -80,   -40),
        rsi_max        = trial.suggest_float("rsi_max",         45,    70),
        adx_min        = trial.suggest_float("adx_min",         10,    25),
        min_confluence = trial.suggest_float("min_confluence",  0.25,  0.55),
        sl_pct         = trial.suggest_float("sl_pct",         0.05,  0.20),
        tp1_pct        = trial.suggest_float("tp1_pct",        0.03,  0.10),
        tp2_pct        = trial.suggest_float("tp2_pct",        0.08,  0.20),
    )
    result = run_backtest(symbol, timeframe, days, params)
    if result.total_trades < 10:
        return -999.0  # penalise strategies that barely trade
    return result.sharpe


def run_optimizer(symbol: str = "BTCUSDT", timeframe: str = "4H",
                  days: int = 180, n_trials: int = 100) -> dict:
    """
    Run Optuna Bayesian optimization. Returns best params dict.
    Typical runtime on Pi: 5-15 min with n_trials=100.
    """
    study = optuna.create_study(direction="maximize")
    study.optimize(
        lambda t: _objective(t, symbol, timeframe, days),
        n_trials=n_trials,
        n_jobs=1,
    )
    completed = [t for t in study.trials if t.state.name == "COMPLETE"]
    if not completed:
        return {}
    return study.best_params

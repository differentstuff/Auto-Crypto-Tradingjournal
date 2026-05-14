"""
backtest_optimizer.py — Bayesian optimizer for backtester parameters using Optuna.
Maximises Sharpe ratio across the parameter search space defined in BacktestParams.
"""
import optuna

optuna.logging.set_verbosity(optuna.logging.WARNING)

from backtest_engine import BacktestParams, run_backtest


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

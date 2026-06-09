"""
tests/conftest.py -- Shared test fixtures.

Provides a clean DB_PATH per test to avoid cross-contamination,
and a standard complete config dict for Substrate creation.
"""

import os
import sys

import pytest

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def make_full_config(**overrides) -> dict:
    """
    Return a complete strategy config dict with ALL required keys.

    Substrate.__init__() and Substrate.cfg() raise ValueError on missing keys.
    Every test that creates a Substrate must use this helper (or provide an
    equally complete config) to avoid KeyError/ValueError.

    Override specific sections with keyword arguments:
        config = make_full_config(strategy={"name": "my_strat"})
    """
    cfg = {
        "strategy": {
            "name": "test_strategy",
            "uid": "test-uid",
            "timeframe": "4H",
            "confirmation_tf": "1H",
            "cycle_interval_minutes": 15,
            "max_positions": 3,
        },
        "description": "Test strategy",
        "symbols": {
            "mode": "static",
            "always_watch": ["BTCUSDT", "ETHUSDT"],
            "never_trade": [],
            "dynamic_filter": {
                "universe_source": "exchange",
                "limit": 15,
                "min_volume_24h_usd": 10000000,
                "min_open_interest_usd": 2000000,
                "min_r_squared": 0.15,
                "refresh_interval_hours": 4,
            },
        },
        "portfolio": {
            "max_positions": 3,
            "risk_per_trade_pct": 1.0,
            "leverage": 5,
            "max_total_risk_pct": 3.0,
            "fallback_equity_usdt": 1000.0,
            "correlation_check": True,
            "max_same_direction": 3,
            "atr_cap_equity_pct": 2.0,
        },
        "scoring": {
            "entry_threshold": 6.5,
            "confluence_min_signals": 3,
            "rr_minimum": 2.0,
            "min_candidate_pct": 0.20,
            "rsi_signal_high": 55,
            "rsi_signal_low": 45,
            "momentum_cap": 1.5,
            "momentum_dampening": 0.5,
            "modifier_weights": {
                "volume": 0.15,
                "cvd": 0.1,
                "order_flow": 0.1,
                "volume_high_ratio": 1.5,
                "volume_low_ratio": 0.7,
            },
            "label_thresholds": {
                "strong": 0.60,
                "weak": 0.33,
            },
            "formula": {
                "rsi_midpoint": 50,
                "rsi_scale": 30.0,
                "macd_aligned_growing": 1.0,
                "macd_aligned_fading": 0.5,
                "ema_full_alignment": 1.0,
                "ema_partial_alignment": 0.5,
                "adx_scale": 50.0,
                "wavetrend_gold_signal": 1.0,
                "wavetrend_signal": 0.85,
                "wavetrend_wt1_scale": 60.0,
                "wavetrend_no_signal_cap": 0.5,
                "volume_confirm": 0.5,
                "volume_weaken": -0.25,
                "cvd_trend": 0.4,
                "order_flow_pressure": 0.15,
                "mfi_threshold": 10,
                "mfi_contribution": 0.3,
            },
        },
        "exit_rules": {
            "hard_stop": {
                "width_atr_multiplier": 1.5,
                "always_active": True,
            },
            "trailing_stop": {
                "enabled": True,
                "activation_profit_pct": 1.5,
                "trail_atr_multiplier": 1.0,
                "breakeven_at_activation": True,
            },
            "tp2_rr_ratio": 2.5,
            "soft_exit": {
                "requires_indicators_reversed": 2,
                "requires_confirmation_tf": True,
                "urgency": "soft",
            },
            "soft_reversal_profit_threshold": 0.5,
            "near_sl_urgency_pct": 0.5,
            "max_hold_hours": 72,
            "tp_exit_pct": 100.0,
        },
        "noise": {
            "conflict_max_ratio": 0.5,
            "volume_low_ratio": 0.7,
            "volume_very_low_ratio": 0.5,
            "adx_no_trend": 15,
            "adx_overextended": 40,
            "noise_severity_min_reasons": 2,
            "liquidity_filter_hours": [[7, 10], [12, 15]],
        },
        "learning": {
            "min_trades_before_adjusting": 30,
            "min_trades_per_signal": 15,
            "significance_level": 0.05,
            "contrarian_win_rate": 30.0,
            "highlight_threshold": 75.0,
            "monitor_low_threshold": 55.0,
            "suppress_range": [45.0, 55.0],
            "contrarian_threshold": 30.0,
            "adjustment_boost": 1.2,
            "adjustment_review_reduce": 0.9,
            "rulebook_max_rules": 10,
            "retrain_every_n_trades": 10,
            "trajectory_lookback_hours": 48,
            "trajectory_min_hours": 8,
            "trajectory_thresholds": {
                "stable_consensus": 10,
                "gradual_alignment": 8,
                "earlier_min": 3,
                "recent_min": 2,
                "earlier_low": 2,
                "min_alignment": 4,
            },
        },
        "indicators": [
            {"name": "rsi", "params": {"period": 14}, "weight": 0.25},
            {"name": "macd", "params": {"fast": 12, "slow": 26, "signal": 9}, "weight": 0.20},
            {"name": "ema_stack", "params": {}, "weight": 0.20},
            {"name": "adx", "params": {"period": 14}, "weight": 0.10},
            {"name": "wavetrend", "params": {}, "weight": 0.15},
            {"name": "volume", "params": {}, "weight": 0.10},
            {"name": "momentum_quality", "params": {"ranking_window": 90, "min_r_squared": 0.15, "lookback_short": 30, "lookback_long": 90, "r_squared_high": 0.5, "r_squared_low": 0.3}, "weight": 0.0},
        ],
        "hmm": {
            "enabled": False,
            "lookback_days": 30,
            "confidence_threshold": 0.70,
            "refit_interval_days": 7,
            "min_bars": 720,
            "n_restarts": 3,
        },
        "challenger": {
            "enabled": False,
            "min_trades": 5,
            "min_improvement": 0.1,
        },
        "karpathy": {
            "enabled": False,
            "step_size": 0.05,
            "max_experiments_per_cycle": 1,
            "min_trades_for_eval": 20,
            "interval_hours": 24,
        },
        "hyperopt": {
            "enabled": False,
            "n_trials": 100,
            "top_n_candidates": 3,
            "search_interval_hours": 24,
            "search_width": 0.5,
            "min_trades_for_eval": 20,
            "sharpe_alpha": 0.3,
        },
        "walk_forward_pbo": {
            "enabled": False,
            "n_windows": 5,
            "train_ratio": 0.7,
            "n_trials": 50,
            "wfe_threshold": 0.5,
            "pbo_threshold": 0.5,
        },
        "modules": {
            "macro_context": False,
            "telegram_logs": False,
            "telegram_interaction": False,
            "external_signals": False,
        },
        "external": {
            "funding_squeeze_threshold": -0.0003,
            "fgi_contrarian_threshold": 20,
            "liquidation_cascade_usd": 250000,
            "liquidation_window_seconds": 300,
            "cache_ttl": 3600,
        },
        "telegram": {
            "bot_token": "",
            "chat_id": "",
            "idle_notify_every": 10,
        },
        "sync": {
            "position_sync_every_n_cycles": 4,
        },
        "daemon": {
            "paper_mode": True,
            "max_cycle_steps": 20,
            "substrate_state_max_rows": 200,
        },
        "llm": {
            "enabled": True,
            "relax_factor": 0.8,
        },
        "risk": {
            "kelly_min": 0.05,
            "kelly_max": 0.25,
            "kelly_win_rate_base": 0.35,
            "kelly_win_rate_range": 0.40,
            "kelly_avg_win_r": 2.0,
            "max_size_pct_of_equity": 25.0,
            "min_size_pct_of_equity": 5.0,
        },
        "validity": [
            {
                "id": "ISC-001",
                "criterion": "entry_threshold met before any trade opens",
                "verification": "analysis.candidates not empty AND score >= threshold",
                "field": "analysis.candidates",
                "operator": "any_score_gte",
                "value_ref": "scoring.entry_threshold",
                "field_key": "score",
            },
            {
                "id": "ISC-002",
                "criterion": "stop loss always set before position opens",
                "verification": "decisions.trade_approved.sl_price > 0",
                "field": "decisions.trade_approved",
                "operator": "sl_set_or_no_trade",
                "value_ref": "",
                "field_key": "sl_price",
            },
            {
                "id": "ISC-003",
                "criterion": "position size within risk limit",
                "verification": "trade_approved.size_usdt <= equity * risk_per_trade_pct / 100",
                "field": "decisions.trade_approved",
                "operator": "size_within_risk",
                "value_ref": "",
                "field_key": "size_usdt",
            },
            {
                "id": "ISC-004",
                "criterion": "max concurrent positions not exceeded",
                "verification": "portfolio.open_positions count < strategy.max_positions",
                "field": "portfolio.open_positions",
                "operator": "count_lt",
                "value_ref": "strategy.max_positions",
                "field_key": "",
            },
        ],
        "soft_penalties": {
            "noise_penalty_ratio": 0.3,
            "confluence_penalty_ratio": 0.3,
            "trajectory_penalty_ratio": 0.5,
            "trajectory_medium_ratio": 0.2,
        },
    }
    if overrides:
        _deep_update(cfg, overrides)
    return cfg


def _deep_update(base: dict, overrides: dict) -> None:
    """Recursively merge overrides into base dict (mutates base)."""
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value


@pytest.fixture
def temp_db(tmp_path):
    """Use a temporary database for tests that need it."""
    import importlib
    db_path = str(tmp_path / "test.db")
    original = os.environ.get("DB_PATH")
    os.environ["DB_PATH"] = db_path

    # Reload the database module so DB_PATH picks up the new env var
    import core.database as db_mod
    importlib.reload(db_mod)

    db_mod.init_db()

    yield db_path

    # Restore original DB_PATH
    if original is not None:
        os.environ["DB_PATH"] = original
    else:
        os.environ.pop("DB_PATH", None)


@pytest.fixture
def config_dir(tmp_path):
    """Create a temporary config directory with test YAML files."""
    import yaml

    # default.yaml — must include ALL keys that Substrate.cfg() requires
    default = make_full_config()
    default["system"] = {"version": "2.0.0", "name": "auto-trader"}
    with open(tmp_path / "default.yaml", "w") as f:
        yaml.dump(default, f)

    # strategies/test_strategy.yaml
    strat_dir = tmp_path / "strategies"
    strat_dir.mkdir()
    strategy = {"strategy": {"name": "test_strategy"}}
    with open(strat_dir / "test_strategy.yaml", "w") as f:
        yaml.dump(strategy, f)

    return tmp_path
"""
tests_new/conftest.py -- Shared test fixtures.

Provides a clean DB_PATH per test to avoid cross-contamination.
"""

import os
import sys

import pytest

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture
def temp_db(tmp_path):
    """Use a temporary database for tests that need it."""
    db_path = str(tmp_path / "test.db")
    original = os.environ.get("DB_PATH")
    os.environ["DB_PATH"] = db_path

    # Import here so DB_PATH override takes effect before init_db is called
    from core.database import init_db
    init_db()

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

    # default.yaml
    default = {
        "system": {"version": "2.0.0", "name": "auto-trader"},
        "daemon": {
            "cycle_interval_minutes": 1,
            "paper_mode": True,
            "max_cycle_steps": 5,
            "substrate_state_max_rows": 50,
        },
        "strategy": {"name": "test_strategy", "timeframe": "4H", "max_positions": 3},
        "scoring": {"entry_threshold": 6.5, "confluence_min_signals": 3},
        "indicators": [
            {"name": "rsi", "params": {"period": 14}, "weight": 0.25},
        ],
        "modules": {"macro_context": False},
        "symbols": {"always_watch": ["BTCUSDT"], "never_trade": []},
        "portfolio": {"risk_per_trade_pct": 1.0, "leverage": 5},
    }
    with open(tmp_path / "default.yaml", "w") as f:
        yaml.dump(default, f)

    # strategies/test_strategy.yaml
    strat_dir = tmp_path / "strategies"
    strat_dir.mkdir()
    strategy = {"strategy": {"name": "test_strategy"}}
    with open(strat_dir / "test_strategy.yaml", "w") as f:
        yaml.dump(strategy, f)

    # exchange.yaml (minimal)
    # Contains both exchange credentials AND llm_keys.
    # The daemon strips these before passing config to the substrate.
    exchange = {
        "exchange": {"bitget": {"api_key": "test_exchange_key"}},
        "llm_keys": {
            "anthropic": [{"key": "test_key", "label": "anthropic-test"}],
        },
    }
    with open(tmp_path / "exchange.yaml", "w") as f:
        yaml.dump(exchange, f)

    return tmp_path

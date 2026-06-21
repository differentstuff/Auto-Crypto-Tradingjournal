"""
tests/test_daemon.py -- Tests for the Daemon loop.

Exchange-as-truth: substrate is always fresh, never loaded from DB.
No save_substrate or load_latest_substrate calls.
"""

import os
import sys
import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.daemon import Daemon
from core.substrate import Substrate
from enzymes.wait import WaitEnzyme


class TestDaemonInit:
    """Test daemon initialization."""

    def test_daemon_creation(self):
        daemon = Daemon(strategy_name="test_strategy")
        assert daemon.strategy_name == "test_strategy"
        assert daemon.paper_mode is False

    def test_daemon_paper_mode(self):
        daemon = Daemon(strategy_name="test_strategy", paper_mode=True)
        assert daemon.paper_mode is True

    def test_daemon_initialize(self, config_dir, temp_db):
        """Daemon initializes config, substrate, and scheduler."""
        daemon = Daemon(strategy_name="test_strategy", config_dir=str(config_dir))
        daemon.initialize()
        assert daemon.config is not None
        assert daemon.substrate is not None
        assert daemon.scheduler is not None
        assert daemon.substrate.strategy["name"] == "test_strategy"

    def test_daemon_register_enzyme(self):
        daemon = Daemon(strategy_name="test_strategy")
        wait = WaitEnzyme()
        daemon.register_enzyme(wait)
        assert len(daemon.enzymes) == 1
        assert daemon.enzymes[0].name == "Wait"


class TestDaemonCycle:
    """Test daemon cycle execution."""

    def test_single_cycle(self, config_dir, temp_db):
        """Daemon can run a single cycle (skeleton mode)."""
        daemon = Daemon(strategy_name="test_strategy", config_dir=str(config_dir))
        daemon.initialize()
        daemon.register_enzyme(WaitEnzyme())
        result = daemon.run_cycle()
        assert result["action"] == "wait"
        assert result["cycle"] == 1
        assert isinstance(result["enzymes_fired"], list)

    def test_cycle_without_enzymes(self, config_dir, temp_db):
        """Daemon handles running with no enzymes (skeleton mode)."""
        daemon = Daemon(strategy_name="test_strategy", config_dir=str(config_dir))
        daemon.initialize()
        result = daemon.run_cycle()
        assert result["action"] == "wait"
        assert result["enzymes_fired"] == ["Wait"]

    def test_substrate_always_fresh(self, config_dir, temp_db):
        """Exchange-as-truth: substrate is always fresh — never loaded from DB."""
        daemon = Daemon(strategy_name="test_strategy", config_dir=str(config_dir))
        daemon.initialize()
        # Fresh substrate has 0 positions and 0 equity
        assert daemon.substrate.portfolio["open_positions"] == []
        assert daemon.substrate.portfolio["equity"] == 0.0

    def test_config_hot_reload(self, config_dir, temp_db):
        """Daemon detects config changes on reload."""
        daemon = Daemon(strategy_name="test_strategy", config_dir=str(config_dir))
        daemon.initialize()

        strat_path = config_dir / "strategies" / "test_strategy.yaml"
        with open(strat_path, "w") as f:
            yaml.dump({
                "strategy": {
                    "name": "test_strategy",
                    "uid": "",
                    "timeframe": "4h",
                    "confirmation_tf": "1d",
                    "cycle_interval_minutes": 15,
                    "max_positions": 10,
                },
            }, f)

        daemon.run_cycle()
        assert daemon.config.get("strategy.max_positions") == 10

"""
tests_new/test_daemon.py -- Tests for the Daemon loop.

Phase A validation: daemon initialization, config loading, substrate creation,
single cycle execution.
"""

import os
import sys
import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.daemon import Daemon
from core.substrate import Substrate
from enzymes.wait import WaitEnzyme
from core.enzyme import EnzymeClass



class TestDaemonInit:
    """Test daemon initialization."""

    def test_daemon_creation(self):
        """Daemon can be created with strategy name."""
        daemon = Daemon(strategy_name="test_strategy")
        assert daemon.strategy_name == "test_strategy"
        assert daemon.paper_mode is False

    def test_daemon_paper_mode(self):
        """Daemon can be created in paper mode."""
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
        """Daemon can register enzymes."""
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
        assert isinstance(result["isc_results"], dict)
        assert isinstance(result["duration_ms"], int)

    def test_cycle_without_enzymes(self, config_dir, temp_db):
        """Daemon handles running with no enzymes (skeleton mode)."""
        daemon = Daemon(strategy_name="test_strategy", config_dir=str(config_dir))
        daemon.initialize()

        result = daemon.run_cycle()

        assert result["action"] == "wait"
        assert result["enzymes_fired"] == ["Wait"]

    def test_substrate_persists_after_cycle(self, config_dir, temp_db):
        """Substrate durable state is persisted to database after cycle."""
        daemon = Daemon(strategy_name="test_strategy", config_dir=str(config_dir))
        daemon.initialize()
        daemon.register_enzyme(WaitEnzyme())

        daemon.run_cycle()

        from core.database import load_latest_substrate
        state = load_latest_substrate("test_strategy")
        assert state is not None
        assert state["strategy"]["name"] == "test_strategy"

    def test_config_hot_reload(self, config_dir, temp_db):
        """Daemon detects config changes on reload."""
        daemon = Daemon(strategy_name="test_strategy", config_dir=str(config_dir))
        daemon.initialize()

        # Modify the strategy YAML — must include ALL required strategy keys
        # (ConfigLoader validates these pre-merge; missing keys raise SubstrateConfigError)
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

        # Run a cycle (which triggers reload)
        daemon.run_cycle()

        # Config should now reflect the change
        assert daemon.config.get("strategy.max_positions") == 10

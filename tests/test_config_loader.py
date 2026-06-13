"""
tests_new/test_config_loader.py -- Tests for the YAML config loader.

Phase A validation: config merging, hot-reload, defaults, strategy override.
"""

import os
import sys
import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config_loader import ConfigLoader, _deep_merge
from core.substrate import SubstrateConfigError


class TestDeepMerge:
    """Test the _deep_merge utility function."""

    def test_simple_merge(self):
        """Simple key override."""
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        """Nested dict merge preserves base keys."""
        base = {"strategy": {"name": "default", "timeframe": "4H"}}
        override = {"strategy": {"name": "custom"}}
        result = _deep_merge(base, override)
        assert result["strategy"]["name"] == "custom"
        assert result["strategy"]["timeframe"] == "4H"

    def test_list_override(self):
        """Lists are replaced, not appended."""
        base = {"symbols": ["BTCUSDT"]}
        override = {"symbols": ["ETHUSDT", "SOLUSDT"]}
        result = _deep_merge(base, override)
        assert result["symbols"] == ["ETHUSDT", "SOLUSDT"]

    def test_none_override(self):
        """None values override base values."""
        base = {"a": 1}
        override = {"a": None}
        result = _deep_merge(base, override)
        assert result["a"] is None

    def test_deep_nested_merge(self):
        """Three levels of nesting merge correctly."""
        base = {"a": {"b": {"c": 1, "d": 2}}}
        override = {"a": {"b": {"c": 99}}}
        result = _deep_merge(base, override)
        assert result["a"]["b"]["c"] == 99
        assert result["a"]["b"]["d"] == 2


class TestConfigLoader:
    """Test ConfigLoader with real YAML files."""

    def test_load_default_config(self, config_dir):
        """ConfigLoader loads default.yaml."""
        loader = ConfigLoader(
            strategy_name="test_strategy",
            config_dir=str(config_dir),
        )
        assert loader.config is not None
        assert loader.get("system.version") == "2.0.0"

    def test_strategy_overrides_default(self, config_dir):
        """Strategy YAML provides strategy keys (no defaults in default.yaml)."""
        loader = ConfigLoader(
            strategy_name="test_strategy",
            config_dir=str(config_dir),
        )
        # Strategy YAML defines max_positions=3 (no fallback to default.yaml)
        assert loader.get("strategy.max_positions") == 3
        # Strategy YAML defines timeframe="4h"
        assert loader.get("strategy.timeframe") == "4h"

    def test_exchange_keys(self, config_dir, monkeypatch):
        """ConfigLoader can retrieve LLM API keys from environment variables."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")
        loader = ConfigLoader(
            strategy_name="test_strategy",
            config_dir=str(config_dir),
        )
        keys = loader.get_exchange_keys("anthropic")
        assert len(keys) == 1
        assert keys[0]["key"] == "test_key"

    def test_enabled_indicators(self, config_dir):
        """enabled_indicators returns only indicators with weight > 0."""
        loader = ConfigLoader(
            strategy_name="test_strategy",
            config_dir=str(config_dir),
        )
        enabled = loader.enabled_indicators
        # default.yaml has 6 indicators with weight > 0
        assert len(enabled) == 6

    def test_paper_mode(self, config_dir):
        """Paper mode detection works."""
        loader = ConfigLoader(
            strategy_name="test_strategy",
            config_dir=str(config_dir),
        )
        assert loader.paper_mode is True  # default.yaml has paper_mode: True

    def test_symbols_always_watch(self, config_dir):
        """symbols_always_watch returns the configured list."""
        loader = ConfigLoader(
            strategy_name="test_strategy",
            config_dir=str(config_dir),
        )
        assert "BTCUSDT" in loader.symbols_always_watch

    def test_module_enabled(self, config_dir):
        """Module toggle detection works."""
        loader = ConfigLoader(
            strategy_name="test_strategy",
            config_dir=str(config_dir),
        )
        assert loader.is_module_enabled("macro_context") is False

    def test_missing_strategy(self, tmp_path):
        """ConfigLoader raises SubstrateConfigError for missing strategy file.

        A missing strategy YAML means required keys (timeframe, etc.) are
        undefined. ConfigLoader validates these pre-merge and raises
        SubstrateConfigError naming the missing keys and file path.
        """
        default = {"system": {"version": "2.0.0"}}
        with open(tmp_path / "default.yaml", "w") as f:
            yaml.dump(default, f)

        with pytest.raises(SubstrateConfigError, match="Missing required strategy key"):
            ConfigLoader(
                strategy_name="nonexistent",
                config_dir=str(tmp_path),
        )

    def test_get_with_default(self, config_dir):
        """get() returns default for missing keys."""
        loader = ConfigLoader(
            strategy_name="test_strategy",
            config_dir=str(config_dir),
        )
        assert loader.get("nonexistent.path", "fallback") == "fallback"

    def test_cfg_method_on_substrate(self, config_dir):
        """Substrate.cfg() can access config values for ISC checks."""
        loader = ConfigLoader(
            strategy_name="test_strategy",
            config_dir=str(config_dir),
        )
        from core.substrate import Substrate
        sub = Substrate(config=loader.config)
        # scoring.entry_threshold comes from default.yaml (6.5)
        assert sub.cfg("scoring.entry_threshold") == 6.5
        # portfolio.risk_per_trade_pct comes from default.yaml (1.0)
        assert sub.cfg("portfolio.risk_per_trade_pct") == 1.0

    def test_incomplete_strategy_raises(self, tmp_path):
        """ConfigLoader raises SubstrateConfigError when strategy YAML is missing
        required keys (timeframe, confirmation_tf, cycle_interval_minutes,
        max_positions). This is the pre-merge validation from Fix #3: without
        it, default.yaml's strategy: section would silently satisfy Substrate's
        validation, masking misconfiguration.
        """
        from core.substrate import SubstrateConfigError

        default = {"system": {"version": "2.0.0"}}
        with open(tmp_path / "default.yaml", "w") as f:
            yaml.dump(default, f)

        strat_dir = tmp_path / "strategies"
        strat_dir.mkdir()
        # Strategy YAML with only 'name' — missing all other required keys
        incomplete = {"strategy": {"name": "incomplete_strat"}}
        with open(strat_dir / "incomplete_strat.yaml", "w") as f:
            yaml.dump(incomplete, f)

        with pytest.raises(SubstrateConfigError, match="Missing required strategy key"):
            ConfigLoader(
                strategy_name="incomplete_strat",
                config_dir=str(tmp_path),
        )

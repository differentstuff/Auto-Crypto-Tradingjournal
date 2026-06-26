"""
tests/test_config_consistency.py -- Dynamic config YAML consistency checks.

Replaces the old hardcoded TestConfigKeys class that was scattered across
phase test files (test_phase1_data_flow.py). Uses dynamic file discovery so
that adding/removing/renaming strategy YAMLs never breaks these tests.

Tests that:
  1. Every strategy YAML has a learning section with trajectory config
  2. No config contains the deprecated retrain_every_hours key
  3. No config uses trajectory_lookback_bars (replaced by trajectory_lookback_hours)
  4. default.yaml has trajectory_lookback_hours and trajectory_min_hours
"""

from __future__ import annotations

import os
import yaml
from pathlib import Path

import pytest

# ── Dynamic discovery ──────────────────────────────────────────────────────────

BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = BASE_DIR / "config"
STRATEGY_DIR = CONFIG_DIR / "strategies"

# Discover all strategy YAML files (exclude _template.yaml — it's a scaffold)
STRATEGY_FILES = sorted(
    f for f in STRATEGY_DIR.glob("*.yaml") if f.name != "_template.yaml"
)


def _load_yaml(path: Path) -> dict:
    """Load a YAML file and return its content as a dict."""
    with open(path) as f:
        return yaml.safe_load(f)


# ── Strategy YAML consistency ──────────────────────────────────────────────────

@pytest.mark.parametrize("yaml_path", STRATEGY_FILES, ids=lambda p: p.name)
class TestStrategyYamlConsistency:
    """Dynamic checks that apply to every strategy YAML file."""

    def test_has_learning_section(self, yaml_path: Path):
        """Every strategy YAML must have a learning section."""
        config = _load_yaml(yaml_path)
        assert "learning" in config, f"{yaml_path.name} missing 'learning' section"
        assert isinstance(config["learning"], dict), f"{yaml_path.name} 'learning' is not a dict"

    def test_has_trajectory_lookback_hours(self, yaml_path: Path):
        """Every strategy YAML must use trajectory_lookback_hours (not trajectory_lookback_bars)."""
        config = _load_yaml(yaml_path)
        learning = config.get("learning", {})
        assert "trajectory_lookback_hours" in learning, (
            f"{yaml_path.name} missing 'trajectory_lookback_hours' in learning"
        )
        assert "trajectory_lookback_bars" not in learning, (
            f"{yaml_path.name} still uses deprecated 'trajectory_lookback_bars'"
        )

    def test_has_trajectory_min_hours(self, yaml_path: Path):
        """Every strategy YAML must have trajectory_min_hours."""
        config = _load_yaml(yaml_path)
        learning = config.get("learning", {})
        assert "trajectory_min_hours" in learning, (
            f"{yaml_path.name} missing 'trajectory_min_hours' in learning"
        )

    def test_no_retrain_every_hours(self, yaml_path: Path):
        """No strategy YAML should contain the deprecated retrain_every_hours key."""
        config = _load_yaml(yaml_path)
        assert "retrain_every_hours" not in config.get("learning", {}), (
            f"{yaml_path.name} still contains deprecated 'retrain_every_hours'"
        )


# ── default.yaml consistency ───────────────────────────────────────────────────

class TestDefaultYamlConsistency:
    """Checks for default.yaml (not a strategy file, but still needs consistency)."""

    def test_default_yaml_has_lookback_hours(self):
        """default.yaml uses trajectory_lookback_hours, not trajectory_lookback_bars."""
        config = _load_yaml(CONFIG_DIR / "default.yaml")
        learning = config.get("learning", {})
        assert "trajectory_lookback_hours" in learning
        assert "trajectory_min_hours" in learning
        assert "trajectory_lookback_bars" not in learning

    def test_default_yaml_no_retrain_every_hours(self):
        """default.yaml should not contain the deprecated retrain_every_hours key."""
        config = _load_yaml(CONFIG_DIR / "default.yaml")
        assert "retrain_every_hours" not in config.get("learning", {})


# ── All config files: no retrain_every_hours anywhere ──────────────────────────

class TestAllConfigNoRetrainEveryHours:
    """Broad check: retrain_every_hours must not appear in any config YAML."""

    @pytest.mark.parametrize(
        "yaml_path",
        STRATEGY_FILES + [CONFIG_DIR / "default.yaml"],
        ids=lambda p: p.name,
    )
    def test_no_retrain_every_hours(self, yaml_path: Path):
        """No config YAML should contain retrain_every_hours."""
        config = _load_yaml(yaml_path)
        assert "retrain_every_hours" not in config.get("learning", {}), (
            f"retrain_every_hours found in {yaml_path}"
        )
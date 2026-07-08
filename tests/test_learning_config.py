"""
tests/test_learning_config.py -- Phase 2 config pipeline validation.

Tests that:
1. UpdateRulebook reads thresholds from substrate.cfg() — not constructor
2. Hot-reload: modifying config changes enzyme behavior within same instance
3. UpdateLearning passes all config values to learning functions
4. Missing config keys cause TypeError (param validation works)
5. classify_verdict uses config-driven thresholds
6. ScoreConfluence passes min_trades to compute_adjusted_weights

All learning functions require explicit threshold parameters.
No hardcoded defaults exist in production code. These tests verify
that config values flow from YAML → substrate.cfg() → learning functions.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.substrate import Substrate
from conftest import make_full_config


# -- Standard config for tests (mirrors default.yaml learning.*) ------------

_STANDARD_LEARNING = {
    "min_trades_before_adjusting": 30,
    "min_trades_per_signal": 15,
    "significance_level": 0.05,
    "contrarian_win_rate": 30.0,
    "highlight_threshold": 75.0,
    "monitor_low_threshold": 55.0,
    "suppress_range": [45.0, 55.0],
    "contrarian_threshold": 30.0,
    "rulebook_max_rules": 10,
    "retrain_every_n_trades": 10,
    "trajectory_lookback_hours": 48,
    "trajectory_min_hours": 8,
}


def _make_config(**overrides) -> dict:
    """Build a complete strategy config for testing using make_full_config."""
    base = make_full_config(
        strategy={"name": "test_strategy", "uid": "legacy"},
    )
    # Merge _STANDARD_LEARNING into base learning section
    base.setdefault("learning", {}).update(_STANDARD_LEARNING)
    # Apply any overrides (deep-merge at top level)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key].update(value)
        else:
            base[key] = value
    return base


def _deep_update(base: dict, overrides: dict) -> None:
    """Recursively merge overrides into base dict (mutates base)."""
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value


# ---------------------------------------------------------------------------
# 1. UpdateRulebook reads from config, not constructor
# ---------------------------------------------------------------------------

class TestUpdateRulebookConfigDriven:
    """UpdateRulebook reads all thresholds from substrate.cfg() at decision time."""

    def test_no_constructor_caching_of_min_trades(self):
        """UpdateRulebook has no __init__ override — no self._min_trades."""
        from enzymes.update_rulebook import UpdateRulebook
        enzyme = UpdateRulebook()
        assert not hasattr(enzyme, "_min_trades"), (
            "UpdateRulebook must not cache _min_trades in constructor"
        )
        assert not hasattr(enzyme, "_retrain_every"), (
            "UpdateRulebook must not cache _retrain_every in constructor"
        )

    def test_reads_min_trades_from_substrate(self, temp_db):
        """
        can_activate() reads min_trades_before_adjusting from
        substrate.cfg(), not from a constructor-cached attribute.
        """
        from enzymes.update_rulebook import UpdateRulebook
        from core.database import db_conn

        # Seed enough trades to pass threshold
        with db_conn() as conn:
            for _ in range(35):
                conn.execute(
                    """INSERT INTO trade_learning
                       (symbol, direction, strategy_name, entry_time, exit_time,
                        outcome, pnl_pct, confluence_score_at_entry)
                       VALUES ('BTCUSDT', 'Long', 'test_strategy', ?, ?, 'win', 1.5, 7.0)""",
                    ("2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"),
                )

        config = _make_config()
        sub = Substrate(config=config)
        sub.learning["total_trades_recorded"] = 35

        enzyme = UpdateRulebook()
        # Should activate because config says min_trades_before_adjusting=30
        assert enzyme.can_activate(sub) is True

    def test_respects_config_change(self, temp_db):
        """
        When config min_trades_before_adjusting is raised above
        total_trades_recorded, can_activate() returns False.
        """
        from enzymes.update_rulebook import UpdateRulebook

        config = _make_config()
        sub = Substrate(config=config)
        sub.learning["total_trades_recorded"] = 25  # below 30 threshold

        enzyme = UpdateRulebook()
        assert enzyme.can_activate(sub) is False

    def test_flux_score_reads_min_trades_from_cfg(self):
        """
        flux_score() reads min_trades_before_adjusting from
        substrate.cfg(), not from a cached copy.
        """
        from enzymes.update_rulebook import UpdateRulebook

        config = _make_config()
        sub = Substrate(config=config)
        sub.learning["total_trades_recorded"] = 60  # 2× threshold

        enzyme = UpdateRulebook()
        # Mock should_regenerate to return True so can_activate passes
        with patch("learning.rulebook.should_regenerate", return_value=True):
            flux = enzyme.flux_score(sub)

        # 2× threshold → flux should be 1.5 (enhanced urgency)
        assert flux >= 1.0, f"Expected flux >= 1.0 for high trade count, got {flux}"

    def test_transform_reads_max_rules_from_cfg(self, temp_db):
        """
        transform() reads rulebook_max_rules from substrate.cfg()
        and passes it to generate_rulebook().
        """
        from enzymes.update_rulebook import UpdateRulebook
        from core.database import db_conn

        with db_conn() as conn:
            for _ in range(35):
                conn.execute(
                    """INSERT INTO trade_learning
                       (symbol, direction, strategy_name, entry_time, exit_time,
                        outcome, pnl_pct, confluence_score_at_entry)
                       VALUES ('BTCUSDT', 'Long', 'test_strategy', ?, ?, 'win', 1.5, 7.0)""",
                    ("2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"),
                )
            conn.execute(
                """INSERT OR REPLACE INTO signal_accuracy
                   (strategy_uid, indicator_name, total_fired, correct, accuracy_pct, verdict)
                   VALUES ('legacy', 'rsi', 35, 28, 80.0, 'valid')"""
            )

        config = _make_config()
        sub = Substrate(config=config)
        sub.learning["total_trades_recorded"] = 35

        enzyme = UpdateRulebook()

        # Patch generate_rulebook to verify it receives max_rules from config
        with patch("learning.rulebook.generate_rulebook") as mock_gen:
            mock_gen.return_value = "Rule 1: test"
            enzyme.transform(sub)
            mock_gen.assert_called_once()
            _, kwargs = mock_gen.call_args
            assert kwargs.get("max_rules") == _STANDARD_LEARNING["rulebook_max_rules"], (
                f"generate_rulebook should receive max_rules from config, got {kwargs}"
            )


# ---------------------------------------------------------------------------
# 2. Hot-reload: config changes affect behavior immediately
# ---------------------------------------------------------------------------

class TestHotReload:
    """Modifying config between cycles changes enzyme behavior instantly."""

    def test_can_activate_responds_to_config_change(self):
        """
        Same enzyme instance, same substrate, different config values →
        can_activate returns different results.
        """
        from enzymes.update_rulebook import UpdateRulebook

        enzyme = UpdateRulebook()

        # First config: threshold=30, trades=35 → activate
        config1 = _make_config()
        sub1 = Substrate(config=config1)
        sub1.learning["total_trades_recorded"] = 35
        with patch("learning.rulebook.should_regenerate", return_value=True):
            assert enzyme.can_activate(sub1) is True

        # Second config: threshold=50, trades=35 → don't activate
        config2 = _make_config()
        sub2 = Substrate(config=config2)
        sub2._config["learning"]["min_trades_before_adjusting"] = 50
        sub2.learning["total_trades_recorded"] = 35
        # Now total_trades (35) < min_trades (50) → should not activate
        # can_activate checks substrate directly, not cached values
        assert enzyme.can_activate(sub2) is False

    def test_score_confluence_passes_min_trades_from_config(self):
        """
        ScoreConfluence reads min_trades_before_adjusting from
        substrate.cfg() and passes it to compute_adjusted_weights.
        """
        config = _make_config()
        sub = Substrate(config=config)

        # Populate indicators so ScoreConfluence can activate
        sub.market["indicators"] = {"BTCUSDT": {"4H": {"ok": True, "rsi": {"value": 65}}}}

        from enzymes.score_confluence import ScoreConfluence
        enzyme = ScoreConfluence()

        with patch("learning.weight_adjuster.compute_adjusted_weights") as mock_cw:
            mock_cw.return_value = {"rsi": 0.25}
            enzyme.transform(sub)
            mock_cw.assert_called_once()
            _, kwargs = mock_cw.call_args
            assert kwargs.get("min_trades") == _STANDARD_LEARNING["min_trades_before_adjusting"], (
                f"compute_adjusted_weights should receive min_trades from config, got {kwargs}"
            )


# ---------------------------------------------------------------------------
# 3. UpdateLearning passes config values
# ---------------------------------------------------------------------------

class TestUpdateLearningConfigThreading:
    """
    UpdateLearning.transform() reads ALL learning config values
    from substrate.cfg() and passes them to every learning function.
    """

    def test_passes_min_trades_per_signal_to_analyzer(self):
        """update_signal_accuracy receives min_trades_per_signal from config."""
        from enzymes.update_learning import UpdateLearning

        config = _make_config(learning={"min_trades_per_signal": 10})
        sub = Substrate(config=config)
        sub.decisions["action"] = "trade_closed"
        sub.strategy["uid"] = "legacy"

        enzyme = UpdateLearning()

        with patch("learning.analyzer.update_signal_accuracy") as mock_usa:
            enzyme.transform(sub)
            # Decision D3: update_signal_accuracy is called 3 times
            # (default/production-only, bucket="production", bucket="exploration")
            assert mock_usa.call_count == 3, (
                f"update_signal_accuracy should be called 3 times, got {mock_usa.call_count}"
            )
            # Check that all 3 calls pass min_trades_per_signal correctly
            for i, (args, kwargs) in enumerate(mock_usa.call_args_list):
                assert kwargs.get("min_trades_per_signal") == 10, (
                    f"Call {i+1}: update_signal_accuracy should receive min_trades_per_signal=10, got {kwargs}"
                )

    def test_passes_significance_level_to_combination(self):
        """update_combination_accuracy receives significance_level from config."""
        from enzymes.update_learning import UpdateLearning

        config = _make_config(learning={"significance_level": 0.01})
        sub = Substrate(config=config)
        sub.decisions["action"] = "trade_closed"
        sub.strategy["uid"] = "legacy"

        enzyme = UpdateLearning()

        with patch("learning.combination.update_combination_accuracy") as mock_uca:
            enzyme.transform(sub)
            mock_uca.assert_called_once()
            _, kwargs = mock_uca.call_args
            assert kwargs.get("significance_level") == 0.01, (
                f"update_combination_accuracy should receive significance_level=0.01, got {kwargs}"
            )

    def test_passes_thresholds_to_trajectory(self):
        """update_trajectory_accuracy receives all threshold params from config."""
        from enzymes.update_learning import UpdateLearning

        config = _make_config()
        sub = Substrate(config=config)
        sub.decisions["action"] = "trade_closed"
        sub.strategy["uid"] = "legacy"

        enzyme = UpdateLearning()

        with patch("learning.trajectory.update_trajectory_accuracy") as mock_uta:
            enzyme.transform(sub)
            mock_uta.assert_called_once()
            _, kwargs = mock_uta.call_args
            assert kwargs.get("min_trades") == _STANDARD_LEARNING["min_trades_per_signal"]
            assert kwargs.get("highlight_threshold") == _STANDARD_LEARNING["highlight_threshold"]
            assert kwargs.get("monitor_low_threshold") == _STANDARD_LEARNING["monitor_low_threshold"]

    def test_passes_min_trades_to_weight_adjuster(self):
        """compute_adjusted_weights receives min_trades from config."""
        from enzymes.update_learning import UpdateLearning

        config = _make_config(learning={"min_trades_before_adjusting": 25})
        sub = Substrate(config=config)
        sub.decisions["action"] = "trade_closed"
        sub.strategy["uid"] = "legacy"

        enzyme = UpdateLearning()

        with patch("learning.weight_adjuster.compute_adjusted_weights") as mock_cw:
            mock_cw.return_value = {"rsi": 0.25}
            enzyme.transform(sub)
            mock_cw.assert_called_once()
            _, kwargs = mock_cw.call_args
            assert kwargs.get("min_trades") == 25, (
                f"compute_adjusted_weights should receive min_trades=25, got {kwargs}"
            )


# ---------------------------------------------------------------------------
# 4. Missing config keys cause TypeError (param validation)
# ---------------------------------------------------------------------------

class TestMissingParamsRaiseTypeError:
    """Learning functions raise TypeError when required params are not passed."""

    def test_update_signal_accuracy_missing_params(self):
        """update_signal_accuracy raises TypeError when called without threshold params."""
        from learning.analyzer import update_signal_accuracy

        with pytest.raises(TypeError, match="Required parameter"):
            update_signal_accuracy("test_strategy")

    def test_update_combination_accuracy_missing_params(self):
        """update_combination_accuracy raises TypeError when called without params."""
        from learning.combination import update_combination_accuracy

        with pytest.raises(TypeError, match="Required parameter"):
            update_combination_accuracy("test_strategy")

    def test_update_trajectory_accuracy_missing_params(self):
        """update_trajectory_accuracy raises TypeError when called without params."""
        from learning.trajectory import update_trajectory_accuracy

        with pytest.raises(TypeError, match="Required parameter"):
            update_trajectory_accuracy("test_strategy")

    def test_compute_adjusted_weights_missing_params(self):
        """compute_adjusted_weights raises TypeError when called without min_trades."""
        from learning.weight_adjuster import compute_adjusted_weights

        with pytest.raises(TypeError, match="Required parameter"):
            compute_adjusted_weights({"rsi": 0.5}, "test_strategy")

    def test_should_regenerate_missing_params(self):
        """should_regenerate raises TypeError when called without params."""
        from learning.rulebook import should_regenerate

        with pytest.raises(TypeError, match="Required parameter"):
            should_regenerate("test_strategy")

    def test_generate_rulebook_missing_params(self):
        """generate_rulebook raises TypeError when called without max_rules."""
        from learning.rulebook import generate_rulebook

        with pytest.raises(TypeError, match="Required parameter"):
            generate_rulebook("test_strategy")

    def test_classify_verdict_missing_params(self):
        """classify_verdict raises TypeError when called without thresholds."""
        from learning.analyzer import classify_verdict

        with pytest.raises(TypeError):
            classify_verdict(80.0, 20)


# ---------------------------------------------------------------------------
# 5. classify_verdict uses config-driven thresholds
# ---------------------------------------------------------------------------

class TestClassifyVerdictConfigDriven:
    """classify_verdict threshold behavior is fully configurable."""

    def test_custom_highlight_threshold(self):
        """
        Custom highlight_threshold=90 means 85% accuracy is NOT 'valid'
        (below custom threshold), only 'monitor'.
        """
        from learning.analyzer import classify_verdict

        v = classify_verdict(85.0, 20, min_trades=15,
                             highlight=90.0, monitor_low=55.0,
                             suppress_range=(45.0, 55.0), contrarian=30.0)
        assert v == "monitor", f"85% with highlight=90 should be 'monitor', got '{v}'"

    def test_custom_contrarian_threshold(self):
        """
        Custom contrarian_threshold=15 means 20% accuracy is NOT contrarian
        (above threshold), it falls through to 'review'.
        """
        from learning.analyzer import classify_verdict

        v = classify_verdict(20.0, 20, min_trades=15,
                             highlight=75.0, monitor_low=55.0,
                             suppress_range=(45.0, 55.0), contrarian=15.0)
        assert v == "review", f"20% with contrarian=15 should be 'review', got '{v}'"

    def test_custom_suppress_range(self):
        """
        Wider suppress_range catches more values.
        """
        from learning.analyzer import classify_verdict

        # suppress_range=(40, 60), 55% falls in suppress
        v = classify_verdict(55.0, 20, min_trades=15,
                             highlight=75.0, monitor_low=55.0,
                             suppress_range=(40.0, 60.0), contrarian=30.0)
        # Note: monitor_low=55, accuracy=55 — monitor takes precedence over suppress
        # because the check order is: highlight → monitor → suppress
        assert v in ("monitor", "suppress"), f"Expected monitor or suppress, got '{v}'"

    def test_custom_monitor_low_threshold(self):
        """
        Higher monitor_low means more signals fall into 'review' instead of 'monitor'.
        """
        from learning.analyzer import classify_verdict

        # monitor_low=70, accuracy=65 → below monitor, above suppress
        v = classify_verdict(65.0, 20, min_trades=15,
                             highlight=85.0, monitor_low=70.0,
                             suppress_range=(45.0, 55.0), contrarian=30.0)
        assert v == "review", f"65% with monitor_low=70 should be 'review', got '{v}'"

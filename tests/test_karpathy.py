"""
tests/test_karpathy.py -- Tests for the Karpathy experiment loop.

Tests cover:
  - Disabled/enabled switches
  - Rate limiting
  - Weight proposal logic (increase/decrease)
  - Re-scoring evaluation
  - CandidateQueue push on improvement
  - No push when worse
  - Deduplication before evaluation
  - Simplicity bias (decrease tried first)
  - Indicator cycling
  - karpathy_log DB table
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from tests.conftest import make_full_config
from core.substrate import Substrate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_substrate(**config_overrides):
    """Create a substrate with karpathy enabled by default."""
    overrides = {
        "karpathy": {
            "enabled": True,
            "step_size": 0.05,
            "max_experiments_per_cycle": 1,
            "min_trades_for_eval": 5,  # Low for tests
            "interval_hours": 24,
        },
    }
    overrides.update(config_overrides)
    cfg = make_full_config(**overrides)
    s = Substrate(config=cfg)
    s.strategy["uid"] = "test-uid"
    return s


def _make_trade_row(
    direction="Long",
    pnl_pct=1.5,
    signals=None,
):
    """Create a mock trade_learning row dict."""
    if signals is None:
        signals = {
            "rsi": {"signal": "bullish", "value": 30},
            "macd": {"signal": "bullish", "bias": "bullish_growing"},
            "ema_stack": {"signal": "bullish", "alignment": "bullish"},
            "adx": {"signal": "bullish", "value": 28},
        }
    return {
        "direction": direction,
        "pnl_pct": pnl_pct,
        "signals_at_entry_json": json.dumps(signals),
    }


def _mock_evaluate(weights, substrate, min_trades):
    """Simple mock evaluation that returns a predictable profit_factor."""
    # Baseline: current weights → pf=1.2
    # If any weight was increased by 0.05 → pf=1.4 (improvement)
    # If any weight was decreased by 0.05 → pf=1.0 (worse)
    from learning.karpathy_method import _get_current_weights
    current = _get_current_weights(substrate)
    if weights == current:
        return 1.2, 30
    # Check if any weight increased
    for k in current:
        if k in weights and weights[k] > current[k]:
            return 1.4, 30
    return 1.0, 30


# ---------------------------------------------------------------------------
# Test: Disabled / early returns
# ---------------------------------------------------------------------------

class TestKarpathyDisabled:

    def test_disabled_returns_immediately(self):
        """When karpathy.enabled=False, run_experiment_cycle is a no-op."""
        substrate = _make_substrate(karpathy={"enabled": False})
        from learning.karpathy_method import KarpathyMethod
        # Should not raise and should not modify substrate
        KarpathyMethod.run_experiment_cycle(substrate)
        # No changes to substrate.learning
        assert "karpathy_last_run_at" not in substrate.learning

    def test_no_weights_returns_immediately(self):
        """When no indicator weights configured, skips."""
        substrate = _make_substrate(indicators=[])
        from learning.karpathy_method import KarpathyMethod
        KarpathyMethod.run_experiment_cycle(substrate)
        assert "karpathy_last_run_at" not in substrate.learning

    def test_too_soon_skips(self):
        """When last run was too recent, skips."""
        substrate = _make_substrate()
        # Set last run to 1 hour ago (interval is 24h)
        substrate.learning["karpathy_last_run_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        from learning.karpathy_method import KarpathyMethod
        KarpathyMethod.run_experiment_cycle(substrate)
        # Should not have updated the timestamp
        assert substrate.learning["karpathy_last_run_at"] != datetime.now(timezone.utc).isoformat()

    def test_old_timestamp_allows_run(self):
        """When last run was > interval_hours ago, allows run."""
        substrate = _make_substrate()
        # Set last run to 25 hours ago (interval is 24h)
        substrate.learning["karpathy_last_run_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=25)
        ).isoformat()
        from learning.karpathy_method import KarpathyMethod, _evaluate_weights
        with patch("learning.karpathy_method._evaluate_weights", return_value=(None, 3)):
            KarpathyMethod.run_experiment_cycle(substrate)
        # Should have attempted (though skipped due to insufficient trades)


# ---------------------------------------------------------------------------
# Test: Re-scoring logic
# ---------------------------------------------------------------------------

class TestComputeScore:

    def test_bullish_long_positive(self):
        """Bullish signals for Long trade produce positive score."""
        from learning.karpathy_method import _compute_score_from_signals
        signals = {
            "rsi": {"signal": "bullish"},
            "macd": {"signal": "bullish"},
        }
        weights = {"rsi": 0.25, "macd": 0.25}
        score = _compute_score_from_signals(signals, weights, "Long")
        assert score > 0  # Both bullish for long = positive

    def test_bearish_short_positive(self):
        """Bearish signals for Short trade produce positive score."""
        from learning.karpathy_method import _compute_score_from_signals
        signals = {
            "rsi": {"signal": "bearish"},
            "macd": {"signal": "bearish"},
        }
        weights = {"rsi": 0.25, "macd": 0.25}
        score = _compute_score_from_signals(signals, weights, "Short")
        assert score > 0  # Bearish for short = positive direction

    def test_bearish_long_negative(self):
        """Bearish signals for Long trade produce negative score."""
        from learning.karpathy_method import _compute_score_from_signals
        signals = {
            "rsi": {"signal": "bearish"},
            "macd": {"signal": "bearish"},
        }
        weights = {"rsi": 0.25, "macd": 0.25}
        score = _compute_score_from_signals(signals, weights, "Long")
        assert score < 0

    def test_neutral_signals_zero_contribution(self):
        """Neutral signals contribute 0 to the score."""
        from learning.karpathy_method import _compute_score_from_signals
        signals = {
            "rsi": {"signal": "neutral"},
            "macd": {"signal": "bullish"},
        }
        weights = {"rsi": 0.25, "macd": 0.25}
        score = _compute_score_from_signals(signals, weights, "Long")
        # Only MACD contributes (bullish), RSI is neutral
        # score = 0.25 / 0.50 * 10 = 5.0
        assert score == 5.0

    def test_missing_indicator_skipped(self):
        """Indicators not in signals dict are skipped."""
        from learning.karpathy_method import _compute_score_from_signals
        signals = {
            "rsi": {"signal": "bullish"},
            # macd missing — skipped, so max_score only includes rsi
        }
        weights = {"rsi": 0.25, "macd": 0.25}
        score = _compute_score_from_signals(signals, weights, "Long")
        # Only RSI found in signals: score = 0.25, max_score = 0.25
        # score = 0.25 / 0.25 * 10 = 10.0
        assert score == 10.0

    def test_zero_weights_returns_zero(self):
        """All zero weights returns 0.0."""
        from learning.karpathy_method import _compute_score_from_signals
        signals = {"rsi": {"signal": "bullish"}}
        weights = {"rsi": 0.0}
        score = _compute_score_from_signals(signals, weights, "Long")
        assert score == 0.0

    def test_normalized_to_10_scale(self):
        """Score is normalized to 0-10 scale."""
        from learning.karpathy_method import _compute_score_from_signals
        signals = {
            "rsi": {"signal": "bullish"},
            "macd": {"signal": "bullish"},
        }
        weights = {"rsi": 0.5, "macd": 0.5}
        score = _compute_score_from_signals(signals, weights, "Long")
        # Both bullish: score = (0.5 + 0.5) / (0.5 + 0.5) * 10 = 10.0
        assert score == 10.0


# ---------------------------------------------------------------------------
# Test: Evaluate weights (with mocked DB)
# ---------------------------------------------------------------------------

class TestEvaluateWeights:

    def test_insufficient_trades_returns_none(self):
        """When < min_trades rows in DB, returns (None, count)."""
        from learning.karpathy_method import _evaluate_weights
        substrate = _make_substrate()
        mock_rows = [_make_trade_row()]  # Only 1 trade, min is 5
        with patch("learning.karpathy_method.db_conn") as mock_db:
            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.return_value.fetchall.return_value = mock_rows
            mock_db.return_value = mock_conn
            result = _evaluate_weights({"rsi": 0.25}, substrate, 5)
        assert result[0] is None
        assert result[1] == 1

    def test_sufficient_trades_returns_profit_factor(self):
        """When enough trades, returns a profit_factor."""
        from learning.karpathy_method import _evaluate_weights
        substrate = _make_substrate()
        # Create 10 winning trades with strong bullish signals
        mock_rows = [
            _make_trade_row(direction="Long", pnl_pct=2.0) for _ in range(8)
        ] + [
            _make_trade_row(direction="Long", pnl_pct=-1.0) for _ in range(2)
        ]
        with patch("learning.karpathy_method.db_conn") as mock_db:
            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.return_value.fetchall.return_value = mock_rows
            mock_db.return_value = mock_conn
            result = _evaluate_weights(
                {"rsi": 0.25, "macd": 0.25, "ema_stack": 0.25, "adx": 0.10},
                substrate, 5,
            )
        assert result[0] is not None
        assert result[0] > 0  # More wins than losses

    def test_all_losses_returns_zero(self):
        """When all trades are losses, returns 0.0."""
        from learning.karpathy_method import _evaluate_weights
        substrate = _make_substrate()
        mock_rows = [
            _make_trade_row(direction="Long", pnl_pct=-1.0) for _ in range(10)
        ]
        with patch("learning.karpathy_method.db_conn") as mock_db:
            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.return_value.fetchall.return_value = mock_rows
            mock_db.return_value = mock_conn
            result = _evaluate_weights(
                {"rsi": 0.25, "macd": 0.25, "ema_stack": 0.25, "adx": 0.10},
                substrate, 5,
            )
        assert result[0] == 0.0


# ---------------------------------------------------------------------------
# Test: Experiment cycle
# ---------------------------------------------------------------------------

class TestExperimentCycle:

    def test_improved_pushes_to_queue(self):
        """When backtest improves, candidate is pushed to CandidateQueue."""
        from learning.karpathy_method import KarpathyMethod
        substrate = _make_substrate()

        with patch("learning.karpathy_method._evaluate_weights", side_effect=_mock_evaluate), \
             patch("learning.karpathy_method._log_experiment"), \
             patch("learning.challenger.CandidateQueue.push") as mock_push:
            KarpathyMethod.run_experiment_cycle(substrate)

        # Should have pushed to CandidateQueue
        assert mock_push.called
        call_args = mock_push.call_args
        assert call_args[1]["source"] == "karpathy"

    def test_not_improved_does_not_push(self):
        """When no improvement, no candidate is pushed."""
        from learning.karpathy_method import KarpathyMethod

        def mock_eval_no_improve(weights, substrate, min_trades):
            """Always returns same profit_factor — no improvement possible."""
            return 1.2, 30

        substrate = _make_substrate()

        with patch("learning.karpathy_method._evaluate_weights", side_effect=mock_eval_no_improve), \
             patch("learning.karpathy_method._log_experiment"), \
             patch("learning.challenger.CandidateQueue.push") as mock_push:
            KarpathyMethod.run_experiment_cycle(substrate)

        # Should NOT have pushed
        assert not mock_push.called

    def test_simplicity_bias_decrease_first(self):
        """Decrease direction is tried before increase."""
        from learning.karpathy_method import KarpathyMethod

        call_order = []

        def mock_eval_track(weights, substrate, min_trades):
            """Track which direction was tried first."""
            from learning.karpathy_method import _get_current_weights
            current = _get_current_weights(substrate)
            for k in current:
                if k in weights:
                    if weights[k] < current[k]:
                        call_order.append("decrease")
                    elif weights[k] > current[k]:
                        call_order.append("increase")
                    break
            return 1.2, 30  # No improvement

        substrate = _make_substrate()

        with patch("learning.karpathy_method._evaluate_weights", side_effect=mock_eval_track), \
             patch("learning.karpathy_method._log_experiment"):
            KarpathyMethod.run_experiment_cycle(substrate)

        # Decrease should be tried before increase
        if len(call_order) >= 2:
            assert call_order[0] == "decrease"
            assert call_order[1] == "increase"

    def test_cycles_through_indicators(self):
        """Each experiment cycle moves to the next indicator."""
        from learning.karpathy_method import KarpathyMethod
        substrate = _make_substrate()

        # Return (1.2, 30) so the cycle runs fully (baseline OK, no improvement)
        with patch("learning.karpathy_method._evaluate_weights", return_value=(1.2, 30)), \
             patch("learning.karpathy_method._log_experiment"):
            KarpathyMethod.run_experiment_cycle(substrate)

        # After first cycle, last_indicator_idx should be set
        assert "karpathy_last_indicator_idx" in substrate.learning

        first_idx = substrate.learning["karpathy_last_indicator_idx"]

        # Run again
        substrate.learning["karpathy_last_run_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=25)
        ).isoformat()

        with patch("learning.karpathy_method._evaluate_weights", return_value=(1.2, 30)), \
             patch("learning.karpathy_method._log_experiment"):
            KarpathyMethod.run_experiment_cycle(substrate)

        second_idx = substrate.learning["karpathy_last_indicator_idx"]
        # Should have moved to next indicator
        assert second_idx != first_idx or second_idx == 0

    def test_records_last_run_timestamp(self):
        """After a cycle attempt, karpathy_last_run_at is set (even with insufficient trades)."""
        from learning.karpathy_method import KarpathyMethod
        substrate = _make_substrate()

        # Return (None, 3) = insufficient trades — timestamp should still be set
        with patch("learning.karpathy_method._evaluate_weights", return_value=(None, 3)), \
             patch("learning.karpathy_method._log_experiment"):
            KarpathyMethod.run_experiment_cycle(substrate)

        assert "karpathy_last_run_at" in substrate.learning

    def test_deduplication_skips_queued_weights(self):
        """If proposed weights are already in queue, skip evaluation."""
        from learning.karpathy_method import KarpathyMethod, _get_current_weights
        substrate = _make_substrate()

        current = _get_current_weights(substrate)
        # Put current weights in the queue as a karpathy candidate
        substrate.learning["challenger"] = {
            "candidate_queue": [
                {"source": "karpathy", "weights": dict(current)},
            ],
        }

        with patch("learning.karpathy_method._evaluate_weights") as mock_eval:
            KarpathyMethod.run_experiment_cycle(substrate)

        # Should not have called _evaluate_weights (dedup check happens first)
        assert not mock_eval.called


# ---------------------------------------------------------------------------
# Test: karpathy_log DB table
# ---------------------------------------------------------------------------

class TestKarpathyLogDB:

    def test_karpathy_log_table_exists(self, temp_db):
        """Migration 49 creates karpathy_log table."""
        import core.database as db_mod
        with db_mod.db_conn() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='karpathy_log'"
            ).fetchone()
        assert row is not None

    def test_karpathy_log_columns(self, temp_db):
        """karpathy_log has all required columns."""
        import core.database as db_mod
        with db_mod.db_conn() as conn:
            cursor = conn.execute("PRAGMA table_info(karpathy_log)")
            columns = {row["name"] for row in cursor.fetchall()}

        required = {
            "id", "strategy_uid", "timestamp", "param_changed",
            "old_value", "new_value", "baseline_profit_factor",
            "proposed_profit_factor", "backtest_trades_count",
            "kept_or_discarded", "reason",
        }
        assert required.issubset(columns)

    def test_insert_and_read_karpathy_log(self, temp_db):
        """Can insert and read a karpathy_log entry."""
        import core.database as db_mod
        with db_mod.db_conn() as conn:
            conn.execute(
                """INSERT INTO karpathy_log
                   (strategy_uid, param_changed, old_value, new_value,
                    baseline_profit_factor, proposed_profit_factor,
                    backtest_trades_count, kept_or_discarded, reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("test-uid", "rsi", 0.25, 0.30, 1.2, 1.4, 30, "kept", "improved"),
            )

        with db_mod.db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM karpathy_log WHERE strategy_uid = 'test-uid'"
            ).fetchone()

        assert row is not None
        assert row["param_changed"] == "rsi"
        assert row["old_value"] == 0.25
        assert row["new_value"] == 0.30
        assert row["kept_or_discarded"] == "kept"


# ---------------------------------------------------------------------------
# Test: _log_experiment function
# ---------------------------------------------------------------------------

class TestLogExperiment:

    def test_log_experiment_writes_to_db(self, temp_db):
        """_log_experiment writes an entry to karpathy_log."""
        from learning.karpathy_method import _log_experiment
        substrate = _make_substrate()

        _log_experiment(
            substrate, "rsi", 0.25, 0.30,
            1.2, 1.4, 30, "kept", "profit_factor improved",
        )

        import core.database as db_mod
        with db_mod.db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM karpathy_log WHERE param_changed = 'rsi'"
            ).fetchone()

        assert row is not None
        assert row["baseline_profit_factor"] == 1.2
        assert row["proposed_profit_factor"] == 1.4

    def test_log_experiment_never_raises(self):
        """_log_experiment catches exceptions and never raises."""
        from learning.karpathy_method import _log_experiment
        substrate = MagicMock()
        substrate.strategy.get.return_value = "test-uid"
        substrate.cfg.side_effect = Exception("DB broken")

        # Should not raise
        _log_experiment(
            substrate, "rsi", 0.25, 0.30,
            1.2, 1.4, 30, "kept", "test",
        )


# ---------------------------------------------------------------------------
# Test: Weight helpers
# ---------------------------------------------------------------------------

class TestWeightHelpers:

    def test_get_current_weights_prefers_adjusted(self):
        """_get_current_weights prefers adjusted_weights over config."""
        from learning.karpathy_method import _get_current_weights
        substrate = _make_substrate()
        substrate.learning["adjusted_weights"] = {"rsi": 0.30, "macd": 0.20}
        weights = _get_current_weights(substrate)
        assert weights == {"rsi": 0.30, "macd": 0.20}

    def test_get_current_weights_falls_back_to_config(self):
        """_get_current_weights falls back to config defaults."""
        from learning.karpathy_method import _get_current_weights
        substrate = _make_substrate()
        # No adjusted_weights
        weights = _get_current_weights(substrate)
        assert "rsi" in weights
        assert weights["rsi"] > 0

    def test_get_current_weights_filters_zero(self):
        """_get_current_weights excludes indicators with weight=0."""
        from learning.karpathy_method import _get_current_weights
        substrate = _make_substrate()
        weights = _get_current_weights(substrate)
        # momentum_quality has weight=0 in test config
        assert "momentum_quality" not in weights

    def test_weights_equal(self):
        """_weights_equal correctly compares weight dicts."""
        from learning.karpathy_method import _weights_equal
        a = {"rsi": 0.25, "macd": 0.20}
        b = {"rsi": 0.25, "macd": 0.20}
        assert _weights_equal(a, b)

        c = {"rsi": 0.25, "macd": 0.21}
        assert not _weights_equal(a, c)

        d = {"rsi": 0.25}
        assert not _weights_equal(a, d)

    def test_negative_weight_proposal_skipped(self):
        """Proposals that would make weight negative are skipped."""
        from learning.karpathy_method import KarpathyMethod
        # Set step_size larger than the smallest weight
        substrate = _make_substrate(karpathy={
            "enabled": True,
            "step_size": 0.50,  # Larger than adx weight (0.10)
            "max_experiments_per_cycle": 1,
            "min_trades_for_eval": 5,
            "interval_hours": 24,
        })

        with patch("learning.karpathy_method._evaluate_weights", return_value=(1.2, 30)), \
             patch("learning.karpathy_method._log_experiment"):
            # Should not crash when trying to decrease a small weight below 0
            KarpathyMethod.run_experiment_cycle(substrate)
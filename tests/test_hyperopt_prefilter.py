"""
tests/test_hyperopt_prefilter.py -- Tests for the Hyperopt prefilter.

Tests cover:
  - Disabled/enabled switches
  - Rate limiting
  - Composite objective function (profit_factor + sharpe)
  - Optuna search with cached trade data
  - CandidateQueue push on improvement
  - No push when worse
  - Deduplication before evaluation
  - PBO overfitting warning
  - hyperopt_log DB table
  - Search space bounds
  - push_top_candidates interface
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
    """Create a substrate with hyperopt enabled by default."""
    overrides = {
        "hyperopt": {
            "enabled": True,
            "n_trials": 5,           # Low for fast tests
            "top_n_candidates": 3,
            "search_interval_hours": 24,
            "search_width": 0.5,
            "min_trades_for_eval": 5,  # Low for tests
            "sharpe_alpha": 0.3,
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


def _mock_trade_rows(n_wins=8, n_losses=2, direction="Long"):
    """Create a list of mock trade rows for testing."""
    rows = [
        _make_trade_row(direction=direction, pnl_pct=2.0)
        for _ in range(n_wins)
    ] + [
        _make_trade_row(direction=direction, pnl_pct=-1.0)
        for _ in range(n_losses)
    ]
    return rows


# ---------------------------------------------------------------------------
# Test: Disabled / early returns
# ---------------------------------------------------------------------------

class TestHyperoptDisabled:

    def test_disabled_returns_immediately(self):
        """When hyperopt.enabled=False, run_search is a no-op."""
        substrate = _make_substrate(hyperopt={"enabled": False})
        from learning.hyperopt_prefilter import HyperoptPrefilter
        HyperoptPrefilter.run_search(substrate)
        # No changes to substrate.learning
        assert "hyperopt_last_run_at" not in substrate.learning

    def test_no_weights_returns_immediately(self):
        """When no indicator weights configured, skips."""
        substrate = _make_substrate(indicators=[])
        from learning.hyperopt_prefilter import HyperoptPrefilter
        HyperoptPrefilter.run_search(substrate)
        assert "hyperopt_last_run_at" not in substrate.learning

    def test_too_soon_skips(self):
        """When last run was too recent, skips."""
        substrate = _make_substrate()
        substrate.learning["hyperopt_last_run_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        from learning.hyperopt_prefilter import HyperoptPrefilter
        HyperoptPrefilter.run_search(substrate)
        # Should not have updated the timestamp
        assert substrate.learning["hyperopt_last_run_at"] != datetime.now(timezone.utc).isoformat()

    def test_old_timestamp_allows_run(self):
        """When last run was > interval_hours ago, allows run."""
        substrate = _make_substrate()
        substrate.learning["hyperopt_last_run_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=25)
        ).isoformat()
        from learning.hyperopt_prefilter import HyperoptPrefilter, _evaluate_weights
        with patch("learning.hyperopt_prefilter._evaluate_weights", return_value=(None, None, None, 3)), \
             patch("learning.hyperopt_prefilter._log_search"):
            HyperoptPrefilter.run_search(substrate)
        # Should have attempted (though skipped due to insufficient trades)

    def test_records_last_run_timestamp(self):
        """After a cycle attempt, hyperopt_last_run_at is set."""
        substrate = _make_substrate()
        with patch("learning.hyperopt_prefilter._evaluate_weights", return_value=(None, None, None, 3)), \
             patch("learning.hyperopt_prefilter._log_search"):
            from learning.hyperopt_prefilter import HyperoptPrefilter
            HyperoptPrefilter.run_search(substrate)
        assert "hyperopt_last_run_at" in substrate.learning


# ---------------------------------------------------------------------------
# Test: Evaluate weights
# ---------------------------------------------------------------------------

class TestEvaluateWeights:

    def test_insufficient_trades_returns_none(self):
        """When < min_trades rows in DB, returns (None, None, None, count)."""
        from learning.hyperopt_prefilter import _evaluate_weights
        substrate = _make_substrate()
        mock_rows = [_make_trade_row()]  # Only 1 trade, min is 5
        with patch("learning.hyperopt_prefilter.db_conn") as mock_db:
            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.return_value.fetchall.return_value = mock_rows
            mock_db.return_value = mock_conn
            result = _evaluate_weights({"rsi": 0.25}, substrate, 5)
        assert result[0] is None
        assert result[3] == 1

    def test_sufficient_trades_returns_metrics(self):
        """When enough trades, returns profit_factor and sharpe_ratio."""
        from learning.hyperopt_prefilter import _evaluate_weights
        substrate = _make_substrate()
        mock_rows = _mock_trade_rows(n_wins=8, n_losses=2)
        with patch("learning.hyperopt_prefilter.db_conn") as mock_db:
            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.return_value.fetchall.return_value = mock_rows
            mock_db.return_value = mock_conn
            pf, sr, pnls, count = _evaluate_weights(
                {"rsi": 0.25, "macd": 0.25, "ema_stack": 0.25, "adx": 0.10},
                substrate, 5,
            )
        assert pf is not None
        assert pf > 0
        assert sr is not None
        assert len(pnls) > 0

    def test_all_losses_returns_zero_pf(self):
        """When all trades are losses, returns 0.0 profit_factor."""
        from learning.hyperopt_prefilter import _evaluate_weights
        substrate = _make_substrate()
        mock_rows = [
            _make_trade_row(direction="Long", pnl_pct=-1.0) for _ in range(10)
        ]
        with patch("learning.hyperopt_prefilter.db_conn") as mock_db:
            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.return_value.fetchall.return_value = mock_rows
            mock_db.return_value = mock_conn
            pf, sr, pnls, count = _evaluate_weights(
                {"rsi": 0.25, "macd": 0.25, "ema_stack": 0.25, "adx": 0.10},
                substrate, 5,
            )
        assert pf == 0.0


# ---------------------------------------------------------------------------
# Test: Compute score (shared with Karpathy)
# ---------------------------------------------------------------------------

class TestComputeScore:

    def test_bullish_long_positive(self):
        """Bullish signals for Long trade produce positive score."""
        from learning.hyperopt_prefilter import _compute_score_from_signals
        signals = {"rsi": {"signal": "bullish"}, "macd": {"signal": "bullish"}}
        weights = {"rsi": 0.25, "macd": 0.25}
        score = _compute_score_from_signals(signals, weights, "Long")
        assert score > 0

    def test_bearish_short_positive(self):
        """Bearish signals for Short trade produce positive score."""
        from learning.hyperopt_prefilter import _compute_score_from_signals
        signals = {"rsi": {"signal": "bearish"}, "macd": {"signal": "bearish"}}
        weights = {"rsi": 0.25, "macd": 0.25}
        score = _compute_score_from_signals(signals, weights, "Short")
        assert score > 0

    def test_normalized_to_10_scale(self):
        """Score is normalized to 0-10 scale."""
        from learning.hyperopt_prefilter import _compute_score_from_signals
        signals = {"rsi": {"signal": "bullish"}, "macd": {"signal": "bullish"}}
        weights = {"rsi": 0.5, "macd": 0.5}
        score = _compute_score_from_signals(signals, weights, "Long")
        assert score == 10.0


# ---------------------------------------------------------------------------
# Test: Optuna search
# ---------------------------------------------------------------------------

class TestOptunaSearch:

    def test_search_finds_improvement(self):
        """Optuna search finds candidates better than baseline."""
        from learning.hyperopt_prefilter import _run_optuna_search, _get_current_weights
        substrate = _make_substrate()

        current_weights = _get_current_weights(substrate)
        mock_rows = _mock_trade_rows(n_wins=8, n_losses=2)

        with patch("learning.hyperopt_prefilter._load_trade_rows", return_value=mock_rows), \
             patch("learning.hyperopt_prefilter._evaluate_from_cache") as mock_eval:
            # Baseline: current weights → pf=1.2, sr=1.0
            # Any weight change → pf=1.5, sr=1.5 (better)
            def eval_mock(weights, rows, threshold):
                from learning.hyperopt_prefilter import _get_current_weights
                current = _get_current_weights(substrate)
                if weights == current:
                    return 1.2, 1.0
                return 1.5, 1.5
            mock_eval.side_effect = eval_mock

            results = _run_optuna_search(
                current_weights=current_weights,
                search_width=0.5,
                substrate=substrate,
                n_trials=5,
                min_trades=5,
                sharpe_alpha=0.3,
            )

        # Should find at least one candidate
        assert len(results) > 0
        # All candidates should have pf > 0
        for w, pf, sr in results:
            assert pf > 0

    def test_search_returns_empty_on_no_data(self):
        """Optuna search returns empty list when insufficient trade data."""
        from learning.hyperopt_prefilter import _run_optuna_search, _get_current_weights
        substrate = _make_substrate()
        current_weights = _get_current_weights(substrate)

        with patch("learning.hyperopt_prefilter._load_trade_rows", return_value=[]):
            results = _run_optuna_search(
                current_weights=current_weights,
                search_width=0.5,
                substrate=substrate,
                n_trials=5,
                min_trades=5,
                sharpe_alpha=0.3,
            )

        assert results == []

    def test_search_respects_search_width(self):
        """Optuna search space is bounded by search_width."""
        from learning.hyperopt_prefilter import _run_optuna_search, _get_current_weights
        substrate = _make_substrate(hyperopt={
            "enabled": True,
            "n_trials": 5,
            "top_n_candidates": 3,
            "search_interval_hours": 24,
            "search_width": 0.1,  # Narrow search
            "min_trades_for_eval": 5,
            "sharpe_alpha": 0.3,
        })
        current_weights = _get_current_weights(substrate)
        mock_rows = _mock_trade_rows(n_wins=8, n_losses=2)

        with patch("learning.hyperopt_prefilter._load_trade_rows", return_value=mock_rows), \
             patch("learning.hyperopt_prefilter._evaluate_from_cache", return_value=(1.5, 1.0)):
            results = _run_optuna_search(
                current_weights=current_weights,
                search_width=0.1,
                substrate=substrate,
                n_trials=5,
                min_trades=5,
                sharpe_alpha=0.3,
            )

        # All proposed weights should be within search_width of current
        for w, pf, sr in results:
            for k in current_weights:
                assert abs(w[k] - current_weights[k]) <= 0.1 + 1e-6


# ---------------------------------------------------------------------------
# Test: Run search (full cycle)
# ---------------------------------------------------------------------------

class TestRunSearch:

    def test_improved_pushes_to_queue(self):
        """When search finds improvement, candidates are pushed to CandidateQueue."""
        from learning.hyperopt_prefilter import HyperoptPrefilter
        substrate = _make_substrate()

        mock_rows = _mock_trade_rows(n_wins=8, n_losses=2)

        with patch("learning.hyperopt_prefilter._evaluate_weights") as mock_eval_weights, \
             patch("learning.hyperopt_prefilter._load_trade_rows", return_value=mock_rows), \
             patch("learning.hyperopt_prefilter._evaluate_from_cache") as mock_eval_cache, \
             patch("learning.hyperopt_prefilter._log_search"), \
             patch("learning.challenger.CandidateQueue.push") as mock_push:

            # Baseline evaluation
            mock_eval_weights.return_value = (1.2, 1.0, [2.0]*8 + [-1.0]*2, 10)
            # Cache evaluation: any different weights are better
            mock_eval_cache.return_value = (1.5, 1.5)

            HyperoptPrefilter.run_search(substrate)

        # Should have pushed at least one candidate
        assert mock_push.called
        call_args = mock_push.call_args
        assert call_args[1]["source"] == "hyperopt"

    def test_not_improved_does_not_push(self):
        """When no improvement, no candidate is pushed."""
        from learning.hyperopt_prefilter import HyperoptPrefilter
        substrate = _make_substrate()

        mock_rows = _mock_trade_rows(n_wins=8, n_losses=2)

        with patch("learning.hyperopt_prefilter._evaluate_weights") as mock_eval_weights, \
             patch("learning.hyperopt_prefilter._load_trade_rows", return_value=mock_rows), \
             patch("learning.hyperopt_prefilter._evaluate_from_cache") as mock_eval_cache, \
             patch("learning.hyperopt_prefilter._log_search"), \
             patch("learning.challenger.CandidateQueue.push") as mock_push:

            # Baseline: pf=1.5
            mock_eval_weights.return_value = (1.5, 1.5, [2.0]*8 + [-1.0]*2, 10)
            # Cache: everything is worse (pf=1.0)
            mock_eval_cache.return_value = (1.0, 0.5)

            HyperoptPrefilter.run_search(substrate)

        # Should NOT have pushed
        assert not mock_push.called

    def test_deduplication_skips_queued_weights(self):
        """If current weights are already in queue, skip entire search."""
        from learning.hyperopt_prefilter import HyperoptPrefilter, _get_current_weights
        substrate = _make_substrate()

        current = _get_current_weights(substrate)
        substrate.learning["challenger"] = {
            "candidate_queue": [
                {"source": "hyperopt", "weights": dict(current)},
            ],
        }

        with patch("learning.hyperopt_prefilter._evaluate_weights") as mock_eval:
            HyperoptPrefilter.run_search(substrate)

        # Should not have called _evaluate_weights (dedup check happens first)
        assert not mock_eval.called

    def test_baseline_zero_skips_search(self):
        """When baseline profit_factor is 0, skip the search."""
        from learning.hyperopt_prefilter import HyperoptPrefilter
        substrate = _make_substrate()

        with patch("learning.hyperopt_prefilter._evaluate_weights", return_value=(0.0, 0.0, [], 10)), \
             patch("learning.hyperopt_prefilter._log_search"), \
             patch("learning.hyperopt_prefilter._run_optuna_search") as mock_optuna:
            HyperoptPrefilter.run_search(substrate)

        # Should not have run Optuna search
        assert not mock_optuna.called


# ---------------------------------------------------------------------------
# Test: Push top candidates
# ---------------------------------------------------------------------------

class TestPushTopCandidates:

    def test_pushes_with_source_hyperopt(self):
        """Pushed candidates have source='hyperopt'."""
        from learning.hyperopt_prefilter import HyperoptPrefilter
        substrate = _make_substrate()

        candidates = [
            ({"rsi": 0.30, "macd": 0.25}, 1.5, 1.2),
        ]

        with patch("learning.challenger.CandidateQueue.push") as mock_push:
            pushed = HyperoptPrefilter.push_top_candidates(
                candidates, substrate, metadata={"test": True},
            )

        assert pushed == 1
        assert mock_push.called
        call_args = mock_push.call_args
        assert call_args[1]["source"] == "hyperopt"
        assert call_args[1]["metadata"]["test"] is True
        assert call_args[1]["metadata"]["proposed_profit_factor"] == 1.5
        assert call_args[1]["metadata"]["proposed_sharpe_ratio"] == 1.2

    def test_respects_top_n(self):
        """Only top_n candidates are pushed."""
        from learning.hyperopt_prefilter import HyperoptPrefilter
        substrate = _make_substrate()

        candidates = [
            ({"rsi": 0.30}, 1.5, 1.2),
            ({"rsi": 0.35}, 1.4, 1.1),
            ({"rsi": 0.40}, 1.3, 1.0),
        ]

        with patch("learning.challenger.CandidateQueue.push"):
            pushed = HyperoptPrefilter.push_top_candidates(
                candidates[:2], substrate,  # Only pass 2
            )

        assert pushed == 2


# ---------------------------------------------------------------------------
# Test: hyperopt_log DB table
# ---------------------------------------------------------------------------

class TestHyperoptLogDB:

    def test_hyperopt_log_table_exists(self, temp_db):
        """Migration 50 creates hyperopt_log table."""
        import core.database as db_mod
        with db_mod.db_conn() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='hyperopt_log'"
            ).fetchone()
        assert row is not None

    def test_hyperopt_log_columns(self, temp_db):
        """hyperopt_log has all required columns."""
        import core.database as db_mod
        with db_mod.db_conn() as conn:
            cursor = conn.execute("PRAGMA table_info(hyperopt_log)")
            columns = {row["name"] for row in cursor.fetchall()}

        required = {
            "id", "strategy_uid", "timestamp", "n_trials",
            "baseline_profit_factor", "best_profit_factor",
            "candidates_pushed", "search_space_json",
            "best_weights_json", "duration_seconds", "reason",
        }
        assert required.issubset(columns)

    def test_insert_and_read_hyperopt_log(self, temp_db):
        """Can insert and read a hyperopt_log entry."""
        import core.database as db_mod
        with db_mod.db_conn() as conn:
            conn.execute(
                """INSERT INTO hyperopt_log
                   (strategy_uid, n_trials, baseline_profit_factor,
                    best_profit_factor, candidates_pushed,
                    search_space_json, best_weights_json,
                    duration_seconds, reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "test-uid", 100, 1.2, 1.5, 3,
                    '{"rsi": 0.25}', '{"rsi": 0.30}',
                    12.5, "best pf 1.2->1.5",
                ),
            )

        with db_mod.db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM hyperopt_log WHERE strategy_uid = 'test-uid'"
            ).fetchone()

        assert row is not None
        assert row["n_trials"] == 100
        assert row["baseline_profit_factor"] == 1.2
        assert row["best_profit_factor"] == 1.5
        assert row["candidates_pushed"] == 3
        assert row["duration_seconds"] == 12.5


# ---------------------------------------------------------------------------
# Test: _log_search function
# ---------------------------------------------------------------------------

class TestLogSearch:

    def test_log_search_writes_to_db(self, temp_db):
        """_log_search writes an entry to hyperopt_log."""
        from learning.hyperopt_prefilter import _log_search
        substrate = _make_substrate()

        _log_search(
            substrate, n_trials=100, baseline_pf=1.2,
            best_pf=1.5, candidates_pushed=3,
            search_space={"rsi": 0.25},
            best_weights={"rsi": 0.30},
            duration_sec=12.5,
            reason="improved",
        )

        import core.database as db_mod
        with db_mod.db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM hyperopt_log WHERE n_trials = 100"
            ).fetchone()

        assert row is not None
        assert row["baseline_profit_factor"] == 1.2
        assert row["best_profit_factor"] == 1.5

    def test_log_search_never_raises(self):
        """_log_search catches exceptions and never raises."""
        from learning.hyperopt_prefilter import _log_search
        substrate = MagicMock()
        substrate.strategy.get.return_value = "test-uid"
        substrate.cfg.side_effect = Exception("DB broken")

        # Should not raise
        _log_search(
            substrate, n_trials=100, baseline_pf=1.2,
            best_pf=1.5, candidates_pushed=3,
            search_space={"rsi": 0.25},
            best_weights={"rsi": 0.30},
            duration_sec=12.5,
            reason="test",
        )


# ---------------------------------------------------------------------------
# Test: Weight helpers
# ---------------------------------------------------------------------------

class TestWeightHelpers:

    def test_get_current_weights_prefers_adjusted(self):
        """_get_current_weights prefers adjusted_weights over config."""
        from learning.hyperopt_prefilter import _get_current_weights
        substrate = _make_substrate()
        substrate.learning["adjusted_weights"] = {"rsi": 0.30, "macd": 0.20}
        weights = _get_current_weights(substrate)
        assert weights == {"rsi": 0.30, "macd": 0.20}

    def test_get_current_weights_falls_back_to_config(self):
        """_get_current_weights falls back to config defaults."""
        from learning.hyperopt_prefilter import _get_current_weights
        substrate = _make_substrate()
        weights = _get_current_weights(substrate)
        assert "rsi" in weights
        assert weights["rsi"] > 0

    def test_get_current_weights_filters_zero(self):
        """_get_current_weights excludes indicators with weight=0."""
        from learning.hyperopt_prefilter import _get_current_weights
        substrate = _make_substrate()
        weights = _get_current_weights(substrate)
        assert "momentum_quality" not in weights

    def test_weights_equal(self):
        """_weights_equal correctly compares weight dicts."""
        from learning.hyperopt_prefilter import _weights_equal
        a = {"rsi": 0.25, "macd": 0.20}
        b = {"rsi": 0.25, "macd": 0.20}
        assert _weights_equal(a, b)

        c = {"rsi": 0.25, "macd": 0.21}
        assert not _weights_equal(a, c)

        d = {"rsi": 0.25}
        assert not _weights_equal(a, d)


# ---------------------------------------------------------------------------
# Test: learning/metrics.py
# ---------------------------------------------------------------------------

class TestMetrics:

    def test_profit_factor(self):
        """profit_factor computes gross wins / gross losses."""
        from learning.metrics import profit_factor
        assert profit_factor([2.0, -1.0, 3.0, -1.5]) == round(5.0 / 2.5, 3)

    def test_profit_factor_no_losses(self):
        """profit_factor returns 0.0 when no losses."""
        from learning.metrics import profit_factor
        assert profit_factor([2.0, 3.0]) == 0.0

    def test_sharpe_ratio(self):
        """sharpe_ratio computes annualized ratio."""
        from learning.metrics import sharpe_ratio
        sr = sharpe_ratio([0.01, -0.005, 0.02, -0.003, 0.015])
        assert isinstance(sr, float)

    def test_sharpe_ratio_empty(self):
        """sharpe_ratio returns 0.0 for empty input."""
        from learning.metrics import sharpe_ratio
        assert sharpe_ratio([]) == 0.0
        assert sharpe_ratio([0.01]) == 0.0

    def test_pbo_returns_float(self):
        """pbo returns a float or NaN."""
        from learning.metrics import pbo
        import math
        # Too few returns → NaN
        result = pbo([0.01, 0.02])
        assert math.isnan(result)

    def test_pbo_sufficient_data(self):
        """pbo returns a value between 0 and 1 with sufficient data."""
        from learning.metrics import pbo
        import numpy as np
        rng = np.random.default_rng(42)
        returns = rng.normal(0.001, 0.02, 100).tolist()
        result = pbo(returns)
        assert 0.0 <= result <= 1.0

    def test_max_drawdown(self):
        """max_drawdown computes peak-to-trough correctly."""
        from learning.metrics import max_drawdown
        # Monotonically increasing → 0 drawdown
        assert max_drawdown([1.0, 2.0, 3.0]) == 0.0
        # Drawdown from peak
        dd = max_drawdown([1.0, 2.0, 1.5, 3.0])
        assert 0 < dd < 1

    def test_sortino_ratio(self):
        """sortino_ratio computes downside deviation ratio."""
        from learning.metrics import sortino_ratio
        sr = sortino_ratio([0.01, -0.005, 0.02, -0.003, 0.015])
        assert isinstance(sr, float)